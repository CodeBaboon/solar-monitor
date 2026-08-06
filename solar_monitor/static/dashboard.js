/**
 * dashboard.js — Solar Monitor frontend logic
 *
 * Responsibilities:
 *   1. WebSocket connection — receives live readings, updates metric cards
 *   2. Sparkline — rolling 30-minute PV watt history
 *   3. History tab — fetches readings via REST, renders Chart.js charts
 *   4. Log tab — fetches log lines via REST, colour-codes them, auto-refreshes
 *
 * No build tools, no npm. Chart.js and the date adapter are loaded from CDN
 * in index.html before this script.
 */

'use strict';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Maximum data points kept in the sparkline buffer (30 min at 10 s = 180). */
const SPARKLINE_MAX_POINTS = 180;

/** After this many seconds without a reading, show the stale-data banner. */
const STALE_THRESHOLD_SECONDS = 30;

/** How often the log tab auto-refreshes (ms). */
const LOG_REFRESH_INTERVAL_MS = 5000;

/** Chart.js default global styles (dark theme). */
const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: true,
  animation: { duration: 300 },
  plugins: {
    legend: { display: false },
    tooltip: {
      backgroundColor: '#1a1d27',
      borderColor: '#2e3250',
      borderWidth: 1,
      titleColor: '#e2e8f0',
      bodyColor: '#8892a4',
    },
  },
  scales: {
    x: {
      type: 'time',
      ticks: { color: '#8892a4', maxTicksLimit: 6 },
      grid:  { color: '#1e2235' },
    },
    y: {
      ticks: { color: '#8892a4' },
      grid:  { color: '#1e2235' },
    },
  },
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** UTC timestamp (Date) of the last reading received via WebSocket. */
let lastUpdateTime = null;

/** Sparkline data buffer: array of { x: Date, y: number }. */
const sparklineData = [];

/** Chart.js instances indexed by canvas id. */
const charts = {};

/** Currently selected history range in hours. */
let selectedHours = 24;

/** setInterval handle for log auto-refresh. */
let logRefreshTimer = null;

/** setInterval handle for the stale-data checker. */
let staleCheckTimer = null;

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

/**
 * Safely set the text content of an element, leaving it unchanged if null.
 * @param {string} id - Element id.
 * @param {string} text - New text content.
 */
function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

/**
 * Set the CSS class on an element after removing a set of mutually
 * exclusive classes.
 * @param {string} id - Element id.
 * @param {string} newClass - Class to add.
 * @param {string[]} removeClasses - Classes to remove first.
 */
function setClass(id, newClass, removeClasses) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove(...removeClasses);
  if (newClass) el.classList.add(newClass);
}

// ---------------------------------------------------------------------------
// Connection status UI
// ---------------------------------------------------------------------------

/**
 * Update the header status pill.
 * @param {'live'|'stale'|'dead'} state
 * @param {string} label - Text shown in the pill.
 */
function setConnectionState(state, label) {
  setClass('status-dot', state, ['live', 'stale', 'dead']);
  setText('status-text', label);
}

/**
 * Show or hide the stale-data warning banner.
 * @param {boolean} visible
 * @param {Date|null} lastSeen
 */
function setStaleBanner(visible, lastSeen) {
  const banner = document.getElementById('stale-banner');
  if (!banner) return;
  banner.classList.toggle('visible', visible);
  if (lastSeen) {
    setText('stale-last-seen', lastSeen.toLocaleTimeString());
  }
}

// ---------------------------------------------------------------------------
// Metric card updater
// ---------------------------------------------------------------------------

const SOC_CLASSES = ['soc-high', 'soc-mid', 'soc-low', 'soc-crit'];
const CURRENT_CLASSES = ['current-pos', 'current-neg', 'current-zero'];

/**
 * Apply a colour class to the SOC metric based on thresholds.
 * @param {number} soc - Battery SOC 0–100.
 */
function applySocColour(soc) {
  let cls = 'soc-high';
  if (soc < 20)      cls = 'soc-crit';
  else if (soc < 50) cls = 'soc-low';
  else if (soc < 80) cls = 'soc-mid';
  setClass('m-soc', cls, SOC_CLASSES);
}

/**
 * Apply a colour class to the net current based on sign.
 * @param {number} current - Net battery current (positive = charging).
 */
function applyCurrentColour(current) {
  let cls = 'current-zero';
  if (current > 0.5)  cls = 'current-pos';
  else if (current < -0.5) cls = 'current-neg';
  setClass('m-net-current', cls, CURRENT_CLASSES);
}

/**
 * Update all metric cards from a new reading object.
 * @param {Object} r - Reading received from WebSocket or REST.
 */
function updateMetrics(r) {
  // SOC
  const soc = r.battery_soc ?? 0;
  setText('m-soc', soc.toFixed(0) + '%');
  applySocColour(soc);
  setText('m-ah-remaining', (r.battery_ah_remaining ?? 0).toFixed(1) + ' AH remaining');

  // PV
  setText('m-pv-watts', (r.pv_watts ?? 0).toFixed(0) + 'W');
  setText('m-pv-voltage', (r.pv_voltage ?? 0).toFixed(1) + 'V');
  setText('m-pv-current', (r.pv_current ?? 0).toFixed(1) + ' A input');

  // Battery
  setText('m-batt-v', (r.battery_voltage ?? 0).toFixed(2) + 'V');
  setText('m-batt-current', (r.battery_current ?? 0).toFixed(1) + ' A (charger out)');

  // Net current
  const netA = r.battery_net_current ?? 0;
  setText('m-net-current', (netA >= 0 ? '+' : '') + netA.toFixed(1) + 'A');
  applyCurrentColour(netA);
  setText('m-net-current-dir', netA >= 0.5 ? 'Charging' : netA <= -0.5 ? 'Discharging' : 'Balanced');

  // Charge state
  setText('m-charge-state', r.charge_state ?? '—');
  setText('m-output-watts', (r.output_watts ?? 0).toFixed(0) + ' W to battery');

  // Energy
  setText('m-daily-kwh', (r.daily_kwh ?? 0).toFixed(2) + ' kWh');
  setText('m-lifetime-kwh', ((r.lifetime_kwh ?? 0) / 1000).toFixed(1) + ' MWh lifetime');

  // Temps
  setText('m-heatsink', (r.heatsink_temp ?? 0).toFixed(1) + '°C');
  const battTemp = r.battery_temp != null ? r.battery_temp.toFixed(1) + '°C' : 'no sensor';
  setText('m-batt-temp', 'Batt temp: ' + battTemp);

  // Last update
  const ts = r.timestamp ? new Date(r.timestamp) : new Date();
  lastUpdateTime = ts;
  setText('last-update-time', 'Last update: ' + ts.toLocaleTimeString());
}

// ---------------------------------------------------------------------------
// Sparkline
// ---------------------------------------------------------------------------

/**
 * Initialise the PV-watts sparkline Chart.js instance.
 */
function initSparkline() {
  const canvas = document.getElementById('sparkline-pv');
  if (!canvas) return;

  charts['sparkline'] = new Chart(canvas, {
    type: 'line',
    data: {
      datasets: [{
        data: sparklineData,
        borderColor: '#3b82f6',
        borderWidth: 1.5,
        pointRadius: 0,
        fill: true,
        backgroundColor: 'rgba(59,130,246,.15)',
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false, type: 'time' },
        y: { display: false, beginAtZero: true },
      },
    },
  });
}

/**
 * Add a data point to the sparkline and trim old data.
 * @param {Date} time
 * @param {number} watts
 */
function pushSparkline(time, watts) {
  sparklineData.push({ x: time, y: watts });
  while (sparklineData.length > SPARKLINE_MAX_POINTS) {
    sparklineData.shift();
  }
  if (charts['sparkline']) {
    charts['sparkline'].update('none'); // 'none' = no animation for performance
  }
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

/**
 * Open a WebSocket connection and wire up message/error handlers.
 * Reconnects automatically on close or error using exponential backoff.
 * @param {number} [delayMs=0] - Initial delay before connecting.
 */
function connectWebSocket(delayMs = 0) {
  setTimeout(() => {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${protocol}://${location.host}/ws`);

    ws.addEventListener('open', () => {
      setConnectionState('live', 'Live');
      setStaleBanner(false, null);
      console.log('[WS] Connected');
    });

    ws.addEventListener('message', (event) => {
      let data;
      try { data = JSON.parse(event.data); }
      catch { return; }

      // Status-only message (sent when Modbus is down)
      if (data.type === 'status') {
        if (!data.connected) {
          const lastSeen = data.last_seen ? new Date(data.last_seen) : null;
          setConnectionState('stale', 'Stale');
          setStaleBanner(true, lastSeen);
        }
        return;
      }

      // Full reading
      updateMetrics(data);
      pushSparkline(new Date(data.timestamp), data.pv_watts ?? 0);
      setConnectionState('live', 'Live');
      setStaleBanner(false, null);
    });

    ws.addEventListener('close', () => {
      setConnectionState('dead', 'Disconnected');
      console.warn('[WS] Closed — reconnecting in 5s');
      connectWebSocket(5000);
    });

    ws.addEventListener('error', (err) => {
      console.error('[WS] Error', err);
      ws.close();
    });

  }, delayMs);
}

// ---------------------------------------------------------------------------
// Stale-data checker
// ---------------------------------------------------------------------------

/**
 * Periodically check whether the last WebSocket reading is too old.
 * If so, show the stale banner even if the WebSocket is still open
 * (e.g. Modbus is down but the Pi is still running).
 */
function startStaleChecker() {
  staleCheckTimer = setInterval(() => {
    if (lastUpdateTime == null) return;
    const ageSeconds = (Date.now() - lastUpdateTime.getTime()) / 1000;
    if (ageSeconds > STALE_THRESHOLD_SECONDS) {
      setConnectionState('stale', 'Stale');
      setStaleBanner(true, lastUpdateTime);
    }
  }, 5000);
}

// ---------------------------------------------------------------------------
// History charts
// ---------------------------------------------------------------------------

/**
 * Build the common Chart.js options for a line chart.
 * @param {string} yLabel - Y-axis unit label.
 * @param {Object} [overrides] - Optional overrides merged into the options.
 * @returns {Object} Chart.js options object.
 */
function lineChartOptions(yLabel, overrides = {}) {
  return {
    ...CHART_DEFAULTS,
    scales: {
      ...CHART_DEFAULTS.scales,
      y: {
        ...CHART_DEFAULTS.scales.y,
        title: { display: true, text: yLabel, color: '#8892a4', font: { size: 11 } },
      },
    },
    ...overrides,
  };
}

/**
 * Create or update a Chart.js line chart with new data.
 * @param {string} canvasId - ID of the <canvas> element.
 * @param {Object[]} readings - Array of reading objects from the REST API.
 * @param {string} field - The reading field to plot on the Y axis.
 * @param {string} colour - CSS colour string for the line.
 * @param {string} yLabel - Y-axis label.
 * @param {string} [chartType='line'] - 'line' or 'bar'.
 */
function renderLineChart(canvasId, readings, field, colour, yLabel, chartType = 'line') {
  const data = readings.map(r => ({ x: new Date(r.timestamp), y: r[field] ?? null }));

  if (charts[canvasId]) {
    charts[canvasId].data.datasets[0].data = data;
    charts[canvasId].update();
    return;
  }

  const canvas = document.getElementById(canvasId);
  if (!canvas) return;

  const isFill = chartType === 'line';
  charts[canvasId] = new Chart(canvas, {
    type: chartType,
    data: {
      datasets: [{
        data: data,
        borderColor: colour,
        backgroundColor: isFill ? colour.replace(')', ', .15)').replace('rgb', 'rgba') : colour,
        borderWidth: chartType === 'bar' ? 0 : 1.5,
        pointRadius: 0,
        fill: isFill,
        tension: 0.3,
        spanGaps: true,
      }],
    },
    options: lineChartOptions(yLabel),
  });
}

/**
 * Render the daily kWh bar chart (uses daily summary data, not raw readings).
 * @param {Object[]} summaries - Array of DailySummary objects from /api/daily.
 */
function renderDailyKwhChart(summaries) {
  // s.date is a LOCAL calendar date from the API.  Appending 'Z' would parse
  // it as UTC midnight and shift every bar into the wrong day west of GMT
  // (CDT rendered 2026-08-06T12:00:00Z as "Aug 6, 7:00 AM").  No suffix means
  // the browser parses it as local time, so noon keeps the bar centred on its
  // own day regardless of timezone.
  const data = summaries.map(s => ({ x: new Date(s.date + 'T12:00:00'), y: s.total_kwh }));

  if (charts['chart-daily-kwh']) {
    charts['chart-daily-kwh'].data.datasets[0].data = data;
    charts['chart-daily-kwh'].update();
    return;
  }

  const canvas = document.getElementById('chart-daily-kwh');
  if (!canvas) return;

  charts['chart-daily-kwh'] = new Chart(canvas, {
    type: 'bar',
    data: {
      datasets: [{
        data: data,
        backgroundColor: '#22c55e',
        borderRadius: 3,
      }],
    },
    options: {
      ...CHART_DEFAULTS,
      scales: {
        ...CHART_DEFAULTS.scales,
        x: {
          ...CHART_DEFAULTS.scales.x,
          time: { unit: 'day' },
        },
        y: {
          ...CHART_DEFAULTS.scales.y,
          title: { display: true, text: 'kWh', color: '#8892a4', font: { size: 11 } },
          beginAtZero: true,
        },
      },
    },
  });
}

/**
 * Fetch readings from the REST API and re-render all history charts.
 * @param {number} hours - Number of hours of history to request.
 */
async function loadHistoryCharts(hours) {
  try {
    // Decide how many days to request for the daily summary
    const days = Math.max(1, Math.ceil(hours / 24));

    // Always downsample server-side.  Requesting raw rows caps out at the
    // API's 10,000-row limit, which is under 28 hours at 10s polling — the
    // reason the 7d and 30d charts used to stop a day in the past.
    const MAX_POINTS = 2000;

    const [readingsResp, dailyResp] = await Promise.all([
      fetch(`/api/readings?hours=${hours}&max_points=${MAX_POINTS}`),
      fetch(`/api/daily?days=${Math.max(days, 30)}`),
    ]);

    if (!readingsResp.ok || !dailyResp.ok) {
      console.error('Failed to load history data');
      return;
    }

    const readings = await readingsResp.json();
    const daily    = await dailyResp.json();

    renderLineChart('chart-soc',         readings, 'battery_soc',         '#22c55e', '% SOC');
    renderLineChart('chart-pv',          readings, 'pv_watts',             '#f59e0b', 'Watts');
    renderLineChart('chart-batt-v',      readings, 'battery_voltage',      '#3b82f6', 'Volts');
    renderLineChart('chart-net-current', readings, 'battery_net_current',  '#a78bfa', 'Amps');
    renderLineChart('chart-heatsink',    readings, 'heatsink_temp',        '#f87171', '°C');
    renderDailyKwhChart(daily);

  } catch (err) {
    console.error('Error loading history charts:', err);
  }
}

// ---------------------------------------------------------------------------
// Log viewer
// ---------------------------------------------------------------------------

/**
 * Determine the CSS class for a log line based on its level.
 * @param {string} line - Raw log line text.
 * @returns {string} CSS class name.
 */
function logLineClass(line) {
  if (line.includes('] [CRITICAL]')) return 'log-CRITICAL';
  if (line.includes('] [ERROR]'))    return 'log-ERROR';
  if (line.includes('] [WARNING]'))  return 'log-WARNING';
  if (line.includes('] [INFO]'))     return 'log-INFO';
  if (line.includes('] [DEBUG]'))    return 'log-DEBUG';
  return '';
}

/**
 * Fetch log lines from /api/logs and render them into the log panel.
 */
async function fetchLogs() {
  const level = document.getElementById('log-level-select')?.value ?? 'ALL';
  const lines = document.getElementById('log-lines-select')?.value ?? '200';

  try {
    const resp = await fetch(`/api/logs?level=${level}&lines=${lines}`);
    if (!resp.ok) { return; }
    const data = await resp.json();

    const output = document.getElementById('log-output');
    if (!output) return;

    // Build coloured spans for each line then join with newlines.
    const html = data.lines.map(line => {
      const cls = logLineClass(line);
      const escaped = line
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      return cls ? `<span class="${cls}">${escaped}</span>` : escaped;
    }).join('\n');

    output.innerHTML = html;

    // Auto-scroll to bottom
    output.scrollTop = output.scrollHeight;

  } catch (err) {
    console.error('Error fetching logs:', err);
  }
}

/**
 * Start the log auto-refresh timer.
 */
function startLogAutoRefresh() {
  if (logRefreshTimer) clearInterval(logRefreshTimer);
  logRefreshTimer = setInterval(fetchLogs, LOG_REFRESH_INTERVAL_MS);
}

/**
 * Stop the log auto-refresh timer.
 */
function stopLogAutoRefresh() {
  if (logRefreshTimer) {
    clearInterval(logRefreshTimer);
    logRefreshTimer = null;
  }
}

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------

/**
 * Activate a tab panel and its nav button.
 * @param {string} tabName - The data-tab value of the button to activate.
 */
function activateTab(tabName) {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.id === `tab-${tabName}`);
  });

  if (tabName === 'history') {
    loadHistoryCharts(selectedHours);
  }

  if (tabName === 'logs') {
    fetchLogs();
    startLogAutoRefresh();
    setText('log-auto-label', 'Auto-refresh: on');
  } else {
    stopLogAutoRefresh();
  }
}

// ---------------------------------------------------------------------------
// Bootstrap — runs after DOM is ready
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {

  // ---- Tab buttons ----
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => activateTab(btn.dataset.tab));
  });

  // ---- History range buttons ----
  document.querySelectorAll('.range-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      selectedHours = parseInt(btn.dataset.hours, 10);
      loadHistoryCharts(selectedHours);
    });
  });

  // ---- Log controls ----
  document.getElementById('log-refresh-btn')?.addEventListener('click', fetchLogs);
  document.getElementById('log-level-select')?.addEventListener('change', fetchLogs);
  document.getElementById('log-lines-select')?.addEventListener('change', fetchLogs);
  document.getElementById('log-download-btn')?.addEventListener('click', () => {
    window.location.href = '/api/logs/download';
  });

  // ---- Sparkline ----
  initSparkline();

  // ---- Initial data load from REST (populates cards before first WS msg) ----
  fetch('/api/latest')
    .then(r => r.ok ? r.json() : null)
    .then(data => { if (data) { updateMetrics(data); pushSparkline(new Date(data.timestamp), data.pv_watts ?? 0); } })
    .catch(() => {});  // Silently ignore — WS will populate soon anyway

  // ---- WebSocket ----
  connectWebSocket();

  // ---- Stale checker ----
  startStaleChecker();
});
