/**
 * dashboard.js – Client-side logic for ruuvi-homedisplay.
 *
 * Polls the Flask JSON API endpoints every REFRESH_INTERVAL seconds and
 * updates the DOM without a full page reload.
 */

/* ── Clock ─────────────────────────────────────────────────────────────── */
function updateClock() {
  const now = new Date();
  const hh  = String(now.getHours()).padStart(2, '0');
  const mm  = String(now.getMinutes()).padStart(2, '0');
  const dd  = String(now.getDate()).padStart(2, '0');
  const mo  = String(now.getMonth() + 1).padStart(2, '0');
  const yy  = now.getFullYear();

  document.getElementById('clock').textContent = `${hh}:${mm}`;
  document.getElementById('date').textContent  = `${dd}.${mo}.${yy}`;
}
updateClock();
setInterval(updateClock, 1000);

/* ── Helper ─────────────────────────────────────────────────────────────── */
function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function priceClass(price) {
  if (price === null || price === undefined) return '';
  if (price < 5)   return 'price-cheap';
  if (price < 12)  return 'price-ok';
  if (price < 20)  return 'price-mid';
  if (price < 30)  return 'price-high';
  return 'price-vhigh';
}

function tempLevel(temp, tag) {
  if (temp === null) return '';
  // Normalise 0-1 in range, then bucket.
  const range = tag.temp_max - tag.temp_min;
  const norm  = (temp - tag.temp_min) / range;
  if (norm < 0.2) return 'cold';
  if (norm < 0.4) return 'cool';
  if (norm < 0.65) return 'warm';
  if (norm < 0.85) return 'hot';
  return 'vhot';
}

/* ── Ruuvi ──────────────────────────────────────────────────────────────── */
async function fetchRuuvi() {
  try {
    const resp = await fetch('/api/ruuvi');
    const data = await resp.json();
    updateRuuvi(data);
  } catch (e) {
    console.warn('Ruuvi fetch error:', e);
  }
}

function updateRuuvi(data) {
  for (const key of RUUVI_KEYS) {
    const tag  = TAG_CONFIG[key];
    const d    = data[key];
    if (!d) continue;

    const card = document.getElementById(`card-${key}`);

    const temp = d.temperature;
    if (temp !== null && temp !== undefined) {
      setText(`${key}-temp`, temp.toFixed(1));
      // Gauge fill (clamp 0–100%).
      const range = tag.temp_max - tag.temp_min;
      const pct   = Math.max(0, Math.min(100, ((temp - tag.temp_min) / range) * 100));
      const gauge = document.getElementById(`${key}-gauge`);
      if (gauge) gauge.style.width = `${pct.toFixed(1)}%`;
      // Colour level via data attribute.
      if (card) card.dataset.tempLevel = tempLevel(temp, tag);
    } else {
      setText(`${key}-temp`, '--');
    }

    setText(`${key}-hum`,  d.humidity  !== null ? `${d.humidity.toFixed(0)}%`     : '--%');
    setText(`${key}-pres`, d.pressure  !== null ? `${d.pressure.toFixed(0)} hPa`  : '-- hPa');
    setText(`${key}-bat`,  d.battery   !== null ? `${d.battery.toFixed(2)} V`     : '-- V');

    if (d.updated_at) {
      const dt = new Date(d.updated_at);
      const hh = String(dt.getHours()).padStart(2, '0');
      const mm = String(dt.getMinutes()).padStart(2, '0');
      const ss = String(dt.getSeconds()).padStart(2, '0');
      setText(`${key}-updated`, `Updated ${hh}:${mm}:${ss}`);
    }
  }
}

/* ── Electricity ────────────────────────────────────────────────────────── */
let elecChart = null;

async function fetchElectricity() {
  try {
    const resp = await fetch('/api/electricity');
    const data = await resp.json();
    updateElectricity(data);
  } catch (e) {
    console.warn('Electricity fetch error:', e);
  }
}

function updateElectricity(data) {
  const current = data.current;
  const elecEl  = document.getElementById('elec-current');

  if (current) {
    const price = current.price_with_tax;
    elecEl.textContent = `${price.toFixed(2)} c/kWh`;
    elecEl.className   = `elec-current ${priceClass(price)}`;
    setText('elec-rank', `Hour ${current.hour}:00 – ${current.is_current ? 'current' : ''}`);
  } else {
    elecEl.textContent = '-- c/kWh';
    setText('elec-rank', data.error ? `Error: ${data.error}` : '');
  }

  if (data.hours && data.hours.length) {
    const prices = data.hours.map(h => h.price_with_tax);
    const min    = Math.min(...prices);
    const max    = Math.max(...prices);
    setText('elec-range', `min ${min.toFixed(2)} / max ${max.toFixed(2)} c/kWh`);
    renderElecChart(data.hours);
  }
}

function renderElecChart(hours) {
  const labels = hours.map(h => `${String(h.hour).padStart(2, '0')}:00`);
  const prices = hours.map(h => h.price_with_tax);
  const colors = hours.map(h => {
    const p = h.price_with_tax;
    if (h.is_current)  return 'rgba(88, 166, 255, 0.9)';  // accent blue
    if (p < 5)   return 'rgba(63, 185, 80, 0.6)';
    if (p < 12)  return 'rgba(56, 139, 253, 0.6)';
    if (p < 20)  return 'rgba(210, 153, 34, 0.6)';
    if (p < 30)  return 'rgba(240, 136, 62, 0.6)';
    return 'rgba(248, 81, 73, 0.6)';
  });

  // Border colours use full opacity, derived from background colours independently.
  const borderColors = hours.map(h => {
    const p = h.price_with_tax;
    if (h.is_current)  return 'rgba(88, 166, 255, 1)';
    if (p < 5)   return 'rgba(63, 185, 80, 1)';
    if (p < 12)  return 'rgba(56, 139, 253, 1)';
    if (p < 20)  return 'rgba(210, 153, 34, 1)';
    if (p < 30)  return 'rgba(240, 136, 62, 1)';
    return 'rgba(248, 81, 73, 1)';
  });

  const chartData = {
    labels,
    datasets: [{
      data: prices,
      backgroundColor: colors,
      borderColor: borderColors,
      borderWidth: 1,
      borderRadius: 3,
    }],
  };

  if (elecChart) {
    elecChart.data = chartData;
    elecChart.update('none');
    return;
  }

  const ctx = document.getElementById('elec-chart').getContext('2d');
  elecChart = new Chart(ctx, {
    type: 'bar',
    data: chartData,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.raw.toFixed(2)} c/kWh`,
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#8b949e', font: { size: 10 }, maxRotation: 0 },
          grid:  { color: '#30363d' },
        },
        y: {
          ticks: { color: '#8b949e', font: { size: 10 } },
          grid:  { color: '#30363d' },
        },
      },
    },
  });
}

/* ── Buses ──────────────────────────────────────────────────────────────── */
async function fetchBuses() {
  try {
    const resp = await fetch('/api/buses');
    const data = await resp.json();
    updateBuses(data);
  } catch (e) {
    console.warn('Buses fetch error:', e);
  }
}

function updateBuses(stops) {
  stops.forEach((stop, idx) => {
    const tbodyId = `stop-${idx + 1}-rows`;
    const nameEl  = document.querySelector(`#stop-${idx + 1} .stop-name`);
    const idEl    = document.querySelector(`#stop-${idx + 1} .stop-id`);
    const tbody   = document.getElementById(tbodyId);
    if (!tbody) return;

    if (nameEl && stop.name) {
      nameEl.textContent = stop.code ? `${stop.name} (${stop.code})` : stop.name;
    }
    if (idEl) idEl.textContent = `ID: ${stop.id}`;

    if (stop.error) {
      tbody.innerHTML = `<tr><td colspan="4" class="error">⚠ ${stop.error}</td></tr>`;
      return;
    }

    if (!stop.departures || stop.departures.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" class="loading">No upcoming departures</td></tr>';
      return;
    }

    tbody.innerHTML = stop.departures.map(dep => {
      const min    = dep.minutes_until;
      let minClass = '';
      let minText  = min >= 0 ? `${min}` : 'left';
      if (min < 0)        { minClass = 'minutes-gone'; minText = '--'; }
      else if (min <= 2)  { minClass = 'minutes-now';  }
      else if (min <= 8)  { minClass = 'minutes-soon'; }

      const rtDot = dep.is_realtime ? '<span class="realtime-dot" title="Real-time"></span>' : '';

      return `
        <tr>
          <td><span class="route-badge">${escHtml(dep.route)}</span></td>
          <td>${rtDot}${escHtml(dep.destination)}</td>
          <td>${escHtml(dep.time)}</td>
          <td class="${minClass}">${minText}</td>
        </tr>`;
    }).join('');
  });
}

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ── Bootstrap all fetches ──────────────────────────────────────────────── */
function refreshAll() {
  fetchRuuvi();
  fetchElectricity();
  fetchBuses();
}

refreshAll();
setInterval(refreshAll, REFRESH_INTERVAL * 1000);
