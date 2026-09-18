/**
 * stress.js — Inflation Shocks Analysis tab.
 */

let _stressData     = {};  // { summary, monthly, paths, scenarios }
let _selectedScenarios = new Set();

// Charts
let _foiChart      = null;
let _hicpChart     = null;
let _fundingChart  = null;
let _cashChart     = null;
let _stressCoverageChart = null;

/* ──────────────────────────────────────────────
   Load all stress data
   ────────────────────────────────────────────── */
async function loadStressData() {
  const noData = document.getElementById('stress-no-data');
  try {
    const avail = await App.apiFetch('/api/results/available');
    if (!avail.stress_available) {
      noData.classList.remove('hidden');
      return;
    }
    noData.classList.add('hidden');
  } catch (_) { return; }

  try {
    const [summary, monthly, paths, scenarioList] = await Promise.all([
      App.apiFetch('/api/stress/summary'),
      App.apiFetch('/api/stress/monthly'),
      App.apiFetch('/api/stress/paths'),
      App.apiFetch('/api/stress/scenarios'),
    ]);

    _stressData = { summary, monthly, paths, scenarioList };

    // 'baseline' is always shown on charts as the reference line.
    // scenarioList already excludes 'baseline' (filtered server-side).
    // Pre-select up to the first 3 stress scenarios.
    _selectedScenarios = new Set();
    scenarioList.slice(0, 3).forEach(s => _selectedScenarios.add(s.scenario_id));

    renderSummaryTable(summary);
    renderScenarioCheckboxes(scenarioList);
    renderAllCharts();
  } catch (e) {
    noData.textContent = 'Error loading stress data: ' + e.message;
    noData.classList.remove('hidden');
  }
}

/* ──────────────────────────────────────────────
   Summary table
   ────────────────────────────────────────────── */
function renderSummaryTable(summary) {
  const tbody = document.getElementById('stress-summary-tbody');
  // Sort: baseline first, then by external funding descending
  const sorted = [...summary].sort((a, b) => {
    if (a.scenario_id === 'baseline') return -1;
    if (b.scenario_id === 'baseline') return 1;
    return (b.external_funding_eur || 0) - (a.external_funding_eur || 0);
  });

  tbody.innerHTML = sorted.map(row => {
    const covered = (row.external_funding_eur || 0) === 0;
    const isBase = row.scenario_id === 'baseline';
    return `
      <tr ${isBase ? 'style="background:var(--sky)"' : ''}>
        <td class="mono">${esc(row.scenario_id)}</td>
        <td><span class="badge-family badge-${row.scenario_family}">${row.scenario_family || '—'}</span></td>
        <td><span class="badge-family badge-severity-${row.severity}">${row.severity || '—'}</span></td>
        <td class="num">${row.common_annual_shock_bp != null ? row.common_annual_shock_bp : '—'}</td>
        <td class="num">${App.fmt.eur0(row.total_liabilities_eur)}</td>
        <td class="num">${App.fmt.eur0(row.total_asset_cashflows_eur)}</td>
        <td class="num ${!covered ? 'text-red font-600' : ''}">${App.fmt.eur0(row.external_funding_eur)}</td>
        <td class="num ${row.minimum_pre_funding_cash_balance_eur < 0 ? 'text-red' : ''}">${App.fmt.eur0(row.minimum_pre_funding_cash_balance_eur)}</td>
        <td class="num ${row.deficit_months > 0 ? 'text-red' : ''}">${row.deficit_months ?? '—'}</td>
        <td class="mono">${row.first_deficit_month || '—'}</td>
        <td class="${covered ? 'outcome-covered' : 'outcome-warning'}">${covered ? '✓ Covered' : '⚠ Funding needed'}</td>
      </tr>
    `;
  }).join('');
}

/* ──────────────────────────────────────────────
   Scenario checkboxes
   ────────────────────────────────────────────── */
function renderScenarioCheckboxes(scenarioList) {
  const container = document.getElementById('scenario-checkboxes');
  container.innerHTML = scenarioList.map(s => `
    <label class="checkbox-group" style="cursor:pointer;padding:4px 10px;border:1px solid var(--border);border-radius:3px">
      <input type="checkbox" value="${s.scenario_id}" 
             ${_selectedScenarios.has(s.scenario_id) ? 'checked' : ''}
             onchange="onScenarioToggle(this)">
      <span>${esc(s.scenario_id)}</span>
      <span class="badge-family badge-${s.family}" style="margin-left:4px">${s.family}</span>
    </label>
  `).join('');
}

window.onScenarioToggle = function(checkbox) {
  if (checkbox.checked) {
    _selectedScenarios.add(checkbox.value);
  } else {
    _selectedScenarios.delete(checkbox.value);
  }
  renderAllCharts();
};

/* ──────────────────────────────────────────────
   Charts
   ────────────────────────────────────────────── */
function renderAllCharts() {
  const selected = [..._selectedScenarios];
  renderInflationPaths(selected);
  renderFundingAndCashCharts(selected);
  renderCoverageChart(selected);
}

function scenarioColor(id, index) {
  if (id === 'baseline') return App.CHART_COLORS.navy;
  return App.SCENARIO_COLORS[index % App.SCENARIO_COLORS.length];
}

/* Inflation paths */
function renderInflationPaths(selected) {
  const { paths } = _stressData;
  if (!paths) return;

  const step = 4; // sample every 4 months for readability

  // Find labels from baseline
  const baseData = paths['baseline'] || [];
  if (!baseData.length) return;
  const labels = baseData.filter((_, i) => i % step === 0).map(d => d.date ? d.date.substring(0,7) : '');

  const foiDatasets  = [];
  const hicpDatasets = [];

  // Always draw baseline first as the solid navy reference
  const bSampled = baseData.filter((_, i) => i % step === 0);
  foiDatasets.push({
    label: 'Baseline (reference)',
    data: bSampled.map(d => d.foi_yoy != null ? +(d.foi_yoy * 100).toFixed(3) : null),
    borderColor: App.CHART_COLORS.navy,
    borderWidth: 2.5,
    pointRadius: 0,
    borderDash: [],
  });
  hicpDatasets.push({
    label: 'Baseline (reference)',
    data: bSampled.map(d => d.hicp_yoy != null ? +(d.hicp_yoy * 100).toFixed(3) : null),
    borderColor: App.CHART_COLORS.navy,
    borderWidth: 2.5,
    pointRadius: 0,
    borderDash: [],
  });

  // Add selected stress scenarios
  selected.forEach((id, idx) => {
    const data = paths[id] || [];
    if (!data.length) return;
    const color = App.SCENARIO_COLORS[(idx + 1) % App.SCENARIO_COLORS.length];
    const sampled = data.filter((_, i) => i % step === 0);
    foiDatasets.push({
      label: id,
      data: sampled.map(d => d.foi_yoy != null ? +(d.foi_yoy * 100).toFixed(3) : null),
      borderColor: color, borderWidth: 1.8, pointRadius: 0, borderDash: [4, 3],
    });
    hicpDatasets.push({
      label: id,
      data: sampled.map(d => d.hicp_yoy != null ? +(d.hicp_yoy * 100).toFixed(3) : null),
      borderColor: color, borderWidth: 1.8, pointRadius: 0, borderDash: [4, 3],
    });
  });

  const pathOpts = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, padding: 10 } } },
    scales: {
      x: { ticks: { maxTicksLimit: 8, maxRotation: 0 } },
      y: { ticks: { callback: v => v.toFixed(1) + '%' } },
    },
  };

  const foiCtx = document.getElementById('stress-foi-chart')?.getContext('2d');
  if (foiCtx) {
    if (_foiChart) _foiChart.destroy();
    _foiChart = new Chart(foiCtx, { type: 'line', data: { labels, datasets: foiDatasets }, options: pathOpts });
  }

  const hicpCtx = document.getElementById('stress-hicp-chart')?.getContext('2d');
  if (hicpCtx) {
    if (_hicpChart) _hicpChart.destroy();
    _hicpChart = new Chart(hicpCtx, { type: 'line', data: { labels, datasets: hicpDatasets }, options: pathOpts });
  }
}

/* External funding and cash balance */
function renderFundingAndCashCharts(selected) {
  const { monthly } = _stressData;
  if (!monthly) return;

  // Group by scenario
  const byScen = {};
  monthly.forEach(row => {
    if (!byScen[row.scenario_id]) byScen[row.scenario_id] = [];
    byScen[row.scenario_id].push(row);
  });

  // Get all months from baseline
  const baseMonths = (byScen['baseline'] || []).map(r => r.month);
  if (!baseMonths.length) return;
  const step = Math.max(1, Math.floor(baseMonths.length / 40));
  const labels = baseMonths.filter((_, i) => i % step === 0);

  const fundingDatasets = [];
  const cashDatasets    = [];

  // Always render baseline first as navy reference
  const baseRows = byScen['baseline'] || [];
  const baseByMonth = {};
  baseRows.forEach(r => { baseByMonth[r.month] = r; });
  fundingDatasets.push({
    label: 'Baseline (reference)',
    data: labels.map(m => baseByMonth[m]?.external_cash_eur || 0),
    borderColor: App.CHART_COLORS.navy, backgroundColor: 'transparent',
    borderWidth: 2.5, pointRadius: 0, borderDash: [],
  });
  cashDatasets.push({
    label: 'Baseline (reference)',
    data: labels.map(m => baseByMonth[m]?.cash_balance_eur ?? null),
    borderColor: App.CHART_COLORS.navy, backgroundColor: 'transparent',
    borderWidth: 2.5, pointRadius: 0, borderDash: [],
  });

  // Layer selected stress scenarios
  selected.forEach((id, idx) => {
    const rows = byScen[id] || [];
    const color = App.SCENARIO_COLORS[(idx + 1) % App.SCENARIO_COLORS.length];
    // Align to baseline months
    const rowByMonth = {};
    rows.forEach(r => { rowByMonth[r.month] = r; });
    const sampledFunding = labels.map(m => rowByMonth[m]?.external_cash_eur || 0);
    const sampledCash    = labels.map(m => rowByMonth[m]?.cash_balance_eur ?? null);

    fundingDatasets.push({
      label: id, data: sampledFunding, borderColor: color, backgroundColor: 'transparent',
      borderWidth: 1.8, pointRadius: 0, borderDash: [4, 3],
    });
    cashDatasets.push({
      label: id, data: sampledCash, borderColor: color, backgroundColor: 'transparent',
      borderWidth: 1.8, pointRadius: 0, borderDash: [4, 3],
    });
  });

  const lineOpts = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, padding: 10 } } },
    scales: {
      x: { ticks: { maxTicksLimit: 10, maxRotation: 0 } },
      y: { ticks: { callback: v => eurK(v) } },
    },
  };

  const fundCtx = document.getElementById('stress-funding-chart')?.getContext('2d');
  if (fundCtx) {
    if (_fundingChart) _fundingChart.destroy();
    _fundingChart = new Chart(fundCtx, { type: 'line', data: { labels, datasets: fundingDatasets }, options: lineOpts });
  }

  const cashCtx = document.getElementById('stress-cash-chart')?.getContext('2d');
  if (cashCtx) {
    if (_cashChart) _cashChart.destroy();
    _cashChart = new Chart(cashCtx, { type: 'line', data: { labels, datasets: cashDatasets }, options: lineOpts });
  }
}

/* Annual coverage */
function renderCoverageChart(selected) {
  const { monthly } = _stressData;
  if (!monthly) return;

  // Annual aggregation per scenario
  const byScen = {};
  monthly.forEach(row => {
    if (!byScen[row.scenario_id]) byScen[row.scenario_id] = {};
    const year = row.month.substring(0, 4);
    if (!byScen[row.scenario_id][year]) byScen[row.scenario_id][year] = { liab: 0, asset: 0 };
    byScen[row.scenario_id][year].liab  += row.liability_eur || 0;
    byScen[row.scenario_id][year].asset += row.asset_cashflow_eur || 0;
  });

  const years = Object.keys(byScen['baseline'] || {}).sort();
  const datasets = [];

  // Always render baseline first as navy reference
  if (byScen['baseline']) {
    const baseColor = App.CHART_COLORS.navy;
    datasets.push({
      label: `Baseline (reference) — liabilities`,
      data: years.map(y => byScen['baseline'][y]?.liab || 0),
      backgroundColor: 'transparent',
      borderColor: baseColor,
      borderWidth: 2.5,
      borderDash: [],
      pointRadius: 0,
      type: 'line',
    });
    datasets.push({
      label: `Baseline (reference) — asset CF`,
      data: years.map(y => byScen['baseline'][y]?.asset || 0),
      backgroundColor: baseColor + '33',
      borderColor: baseColor,
      borderWidth: 1.5,
      type: 'bar',
    });
  }

  // Layer selected stress scenarios
  selected.forEach((id, idx) => {
    const color = App.SCENARIO_COLORS[(idx + 1) % App.SCENARIO_COLORS.length];
    datasets.push({
      label: `${id} — liabilities`,
      data: years.map(y => byScen[id]?.[y]?.liab || 0),
      backgroundColor: 'transparent',
      borderColor: color,
      borderWidth: 1.8,
      borderDash: [4, 3],
      pointRadius: 0,
      type: 'line',
    });
    datasets.push({
      label: `${id} — asset CF`,
      data: years.map(y => byScen[id]?.[y]?.asset || 0),
      backgroundColor: color + '33',
      borderColor: color,
      borderWidth: 1,
      type: 'bar',
    });
  });

  const ctx = document.getElementById('stress-coverage-chart')?.getContext('2d');
  if (ctx) {
    if (_stressCoverageChart) _stressCoverageChart.destroy();
    _stressCoverageChart = new Chart(ctx, {
      type: 'bar',
      data: { labels: years, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, padding: 8 } } },
        scales: {
          x: { ticks: { maxRotation: 0 } },
          y: { ticks: { callback: v => eurK(v) } },
        },
      },
    });
  }
}

/* ──────────────────────────────────────────────
   Utilities
   ────────────────────────────────────────────── */
function eurK(v) {
  const a = Math.abs(v);
  if (a >= 1e6) return (v/1e6).toFixed(1)+'M';
  if (a >= 1e3) return (v/1e3).toFixed(0)+'k';
  return v.toFixed(0);
}
function esc(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* ──────────────────────────────────────────────
   Init
   ────────────────────────────────────────────── */
document.addEventListener('tabActivated', e => {
  if (e.detail === 'stress') {
    loadStressData();
  }
});
