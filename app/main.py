import asyncio
import contextlib
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import docker
from docker.errors import DockerException
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

PANEL_PORT = 7004
TUNNEL_TARGET_HOST = os.getenv("TUNNEL_TARGET_HOST", "host.docker.internal")
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


@dataclass
class Tunnel:
    port: int
    container: str
    process: asyncio.subprocess.Process
    status: Literal["creating", "published", "error", "disconnected"] = "creating"
    url: str | None = None
    error: str | None = None
    output_task: asyncio.Task | None = None
    monitor_task: asyncio.Task | None = None


tunnels: dict[int, Tunnel] = {}
tunnel_lock = asyncio.Lock()
docker_client = docker.from_env()


def detected_services() -> list[dict]:
    """Return one row per host TCP port published by an active container."""
    try:
        containers = docker_client.containers.list(filters={"status": "running"})
    except DockerException as exc:
        raise HTTPException(status_code=503, detail=f"Docker unavailable: {exc}") from exc

    services: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for container in containers:
        ports = container.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
        for container_port, binding in ports.items():
            # Quick Tunnels proxy HTTP/TCP services; ignore UDP-only mappings.
            if not container_port.endswith("/tcp"):
                continue
            if not binding:
                continue
            for item in binding:
                host_port = item.get("HostPort")
                if host_port and host_port.isdigit():
                    port = int(host_port)
                    key = (container.name, port)
                    # Docker can report the same host port for IPv4 and IPv6.
                    if port != PANEL_PORT and key not in seen:
                        seen.add(key)
                        services.append({"name": container.name, "port": port})
    return sorted(services, key=lambda service: (service["port"], service["name"]))


def service_for_port(port: int) -> dict | None:
    return next((service for service in detected_services() if service["port"] == port), None)


async def read_cloudflared_output(tunnel: Tunnel) -> None:
    assert tunnel.process.stdout is not None
    async for raw_line in tunnel.process.stdout:
        line = raw_line.decode(errors="replace").strip()
        match = URL_PATTERN.search(line)
        if match and tunnel.process.returncode is None:
            tunnel.url = match.group(0)
            tunnel.status = "published"


async def monitor_tunnel(tunnel: Tunnel) -> None:
    return_code = await tunnel.process.wait()
    if tunnels.get(tunnel.port) is not tunnel:
        return
    if tunnel.status not in {"error", "disconnected"}:
        tunnel.status = "disconnected"
        tunnel.error = f"cloudflared stopped unexpectedly (exit code {return_code})."


async def stop_tunnel(tunnel: Tunnel) -> None:
    if tunnel.process.returncode is None:
        tunnel.process.terminate()
        try:
            await asyncio.wait_for(tunnel.process.wait(), timeout=5)
        except TimeoutError:
            tunnel.process.kill()
            await tunnel.process.wait()
    for task in (tunnel.output_task, tunnel.monitor_task):
        if task and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def tunnel_response(tunnel: Tunnel) -> dict:
    return {
        "port": tunnel.port,
        "container": tunnel.container,
        "status": tunnel.status,
        "url": tunnel.url,
        "error": tunnel.error,
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    async with tunnel_lock:
        active_tunnels = list(tunnels.values())
        tunnels.clear()
    await asyncio.gather(*(stop_tunnel(tunnel) for tunnel in active_tunnels), return_exceptions=True)
    with contextlib.suppress(DockerException):
        docker_client.close()


app = FastAPI(title="Tunnel Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("app/templates/index.html", encoding="utf-8") as template:
        return template.read()


@app.get("/api/services")
async def get_services():
    return {"services": detected_services()}


@app.get("/api/tunnels")
async def get_tunnels():
    async with tunnel_lock:
        return {"tunnels": [tunnel_response(tunnel) for tunnel in tunnels.values()]}


@app.post("/api/tunnels/{port}", status_code=202)
async def create_tunnel(port: int):
    if port == PANEL_PORT:
        raise HTTPException(status_code=404, detail="Port not found")
    service = service_for_port(port)
    if service is None:
        raise HTTPException(status_code=404, detail="Port not found")

    async with tunnel_lock:
        existing = tunnels.get(port)
        if existing and existing.process.returncode is None:
            raise HTTPException(status_code=409, detail="Tunnel already running")
        if existing:
            tunnels.pop(port, None)

        try:
            process = await asyncio.create_subprocess_exec(
                "cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://{TUNNEL_TARGET_HOST}:{port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not start cloudflared: {exc}") from exc

        tunnel = Tunnel(port=port, container=service["name"], process=process)
        tunnels[port] = tunnel
        tunnel.output_task = asyncio.create_task(read_cloudflared_output(tunnel))
        tunnel.monitor_task = asyncio.create_task(monitor_tunnel(tunnel))
        return tunnel_response(tunnel)


@app.delete("/api/tunnels/{port}", status_code=204)
async def delete_tunnel(port: int):
    async with tunnel_lock:
        tunnel = tunnels.pop(port, None)
    if tunnel is None:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    await stop_tunnel(tunnel)
