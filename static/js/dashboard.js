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

function toNumber(v) {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
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

/* ── Display battery (E1001) ───────────────────────────────────────────── */
async function fetchDeviceBattery() {
  try {
    const resp = await fetch('/api/device');
    const data = await resp.json();
    updateDeviceBattery(data);
  } catch (e) {
    console.warn('Device battery fetch error:', e);
  }
}

function updateDeviceBattery(data) {
  const el = document.getElementById('device-battery');
  if (!el) return;

  const level = toNumber(data?.battery_level);
  const volt = toNumber(data?.battery_voltage);
  const updated = data?.updated_at ? new Date(data.updated_at) : null;
  const ageMin = updated ? Math.max(0, Math.floor((Date.now() - updated.getTime()) / 60000)) : null;

  const parts = ['Display battery'];
  parts.push(level !== null ? `${Math.round(level)}%` : '--%');
  if (volt !== null) parts.push(`(${volt.toFixed(2)} V)`);
  if (ageMin !== null && Number.isFinite(ageMin) && ageMin >= 5) {
    parts.push(`stale ${ageMin} min`);
  }

  el.textContent = parts.join(' ');
}

function updateRuuvi(data) {
  for (const key of RUUVI_KEYS) {
    const tag  = TAG_CONFIG[key];
    const d    = data[key];
    const card = document.getElementById(`card-${key}`);

    if (!d) {
      setText(`${key}-temp`, '--');
      setText(`${key}-hum`, '--%');
      setText(`${key}-pres`, '-- hPa');
      setText(`${key}-bat`, '-- V');
      setText(`${key}-updated`, 'No signal');
      const gauge = document.getElementById(`${key}-gauge`);
      if (gauge) gauge.style.width = '0%';
      if (card) {
        card.dataset.tempLevel = '';
        card.classList.add('sensor-stale');
      }
      continue;
    }

    const temp = toNumber(d.temperature);
    if (temp !== null) {
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
      const gauge = document.getElementById(`${key}-gauge`);
      if (gauge) gauge.style.width = '0%';
      if (card) card.dataset.tempLevel = '';
    }

    const humidity = toNumber(d.humidity);
    const pressure = toNumber(d.pressure);
    const battery  = toNumber(d.battery);
    setText(`${key}-hum`,  humidity !== null ? `${humidity.toFixed(1)}%`    : '--%');
    setText(`${key}-pres`, pressure !== null ? `${pressure.toFixed(1)} hPa` : '-- hPa');
    setText(`${key}-bat`,  battery  !== null ? `${battery.toFixed(2)} V`    : '-- V');

    if (d.updated_at) {
      const dt = new Date(d.updated_at);
      const hh = String(dt.getHours()).padStart(2, '0');
      const mm = String(dt.getMinutes()).padStart(2, '0');
      const ss = String(dt.getSeconds()).padStart(2, '0');
      const ageMin = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 60000));
      const staleMin = (typeof RUUVI_STALE_MINUTES !== 'undefined') ? RUUVI_STALE_MINUTES : 10;
      const isStale = Number.isFinite(ageMin) && ageMin >= staleMin;
      const staleTxt = isStale ? ` (stale ${ageMin} min)` : '';
      setText(`${key}-updated`, `Updated ${hh}:${mm}:${ss}${staleTxt}`);
      if (card) card.classList.toggle('sensor-stale', isStale);
    } else {
      setText(`${key}-updated`, 'No signal');
      if (card) card.classList.add('sensor-stale');
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
  fetchDeviceBattery();
  fetchElectricity();
  fetchBuses();
}

refreshAll();
setInterval(refreshAll, REFRESH_INTERVAL * 1000);
