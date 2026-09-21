FROM cloudflare/cloudflared:latest AS cloudflared

FROM python:3.12-slim
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 7004
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7004"]
