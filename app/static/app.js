const body = document.querySelector('#services');
const errorBox = document.querySelector('#load-error');

function isPortainer(name) { return name.toLowerCase().includes('portainer'); }
function displayName(name) { return name.replace(/[-_]+/g, ' ').replace(/\b\w/g, letter => letter.toUpperCase()); }

async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function statusMarkup(tunnel) {
  if (!tunnel) return '<span class="status local">● Local</span>';
  const labels = { creating: '● Creando...', published: '● Publicado', error: '● Error', disconnected: '● Error / Desconectado' };
  const extra = tunnel.error ? `<small>${escapeHtml(tunnel.error)}</small>` : '';
  return `<span class="status ${tunnel.status}">${labels[tunnel.status] || '● Error'}</span>${extra}`;
}

function escapeHtml(value) {
  const element = document.createElement('div');
  element.textContent = value || '';
  return element.innerHTML;
}

function actionButton(label, callback, variant = '') {
  const button = document.createElement('button');
  button.textContent = label;
  button.className = variant;
  button.addEventListener('click', callback);
  return button;
}

async function publish(port, button) {
  button.disabled = true;
  try { await request(`/api/tunnels/${port}`, { method: 'POST' }); await refresh(); }
  catch (error) { alert(error.message); button.disabled = false; }
}

async function stop(port) {
  try { await request(`/api/tunnels/${port}`, { method: 'DELETE' }); await refresh(); }
  catch (error) { alert(error.message); }
}

async function copy(url) {
  try { await navigator.clipboard.writeText(url); }
  catch { window.prompt('Copia este enlace:', url); }
}

function render(services, tunnels) {
  const byPort = new Map(tunnels.map(tunnel => [tunnel.port, tunnel]));
  body.replaceChildren();
  for (const service of services) {
    const tunnel = byPort.get(service.port);
    const row = document.createElement('tr');
    row.innerHTML = `<td><strong>${escapeHtml(displayName(service.name))}</strong>${isPortainer(service.name) ? document.querySelector('#portainer-warning').innerHTML : ''}</td><td>${service.port}</td><td>${statusMarkup(tunnel)}</td><td></td><td></td><td></td>`;
    const publishCell = row.cells[3];
    const linkCell = row.cells[4];
    const actionsCell = row.cells[5];
    if (tunnel && tunnel.status !== 'disconnected' && tunnel.status !== 'error') {
      publishCell.append(actionButton('Detener', () => stop(service.port), 'danger'));
    } else {
      publishCell.append(actionButton('Publicar', event => publish(service.port, event.currentTarget)));
    }
    if (tunnel?.url) {
      const link = document.createElement('a'); link.href = tunnel.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = tunnel.url;
      linkCell.append(link);
      actionsCell.append(actionButton('Copiar', () => copy(tunnel.url)));
      actionsCell.append(actionButton('Abrir', () => window.open(tunnel.url, '_blank', 'noopener,noreferrer')));
    } else linkCell.textContent = '—';
    body.append(row);
  }
  document.querySelector('#service-count').textContent = services.length;
  document.querySelector('#tunnel-count').textContent = tunnels.filter(tunnel => ['creating', 'published'].includes(tunnel.status)).length;
}

async function refresh() {
  try {
    const [serviceData, tunnelData] = await Promise.all([request('/api/services'), request('/api/tunnels')]);
    render(serviceData.services, tunnelData.tunnels);
    errorBox.hidden = true;
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
}

refresh();
setInterval(refresh, 2500);
