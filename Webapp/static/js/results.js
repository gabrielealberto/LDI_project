/**
 * results.js — Results tab: KPIs, portfolio table, cashflow charts, issuer allocation, coverage.
 */

let _cfMonthlyChart  = null;
let _cfAnnualChart   = null;
let _issuerChart     = null;
let _coverageChart   = null;
let _surplusChart    = null;

/* ──────────────────────────────────────────────
   Load all results
   ────────────────────────────────────────────── */
async function loadResults() {
  const emptyState = document.getElementById('results-empty-state');
  const content = document.getElementById('results-content');
  try {
    const avail = await App.apiFetch('/api/results/available');
    if (!avail.results_available) {
      emptyState.querySelector('h2').textContent = 'No completed run available';
      emptyState.querySelector('p').textContent =
        'Run the pipeline first. Once it has completed successfully, click this button to load its results.';
      content.classList.add('hidden');
      return;
    }
    emptyState.classList.add('hidden');
    content.classList.remove('hidden');
  } catch (_) {
    return;
  }

  await Promise.all([
    loadSummaryKPIs(),
    loadPortfolioTable(),
    loadCashflowsMonthly(),
    loadCashflowsAnnual(),
    loadIssuerAllocation(),
    loadMethodology(),
  ]);

  App.initSortableTable('portfolio-table');
  App.initTableSearch('portfolio-search', 'portfolio-tbody');
  App.initTableSearch('cf-search', 'cf-tbody');
}

/* ── KPIs ── */
async function loadSummaryKPIs() {
  const s = await App.apiFetch('/api/results/summary');
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };
  set('kpi-investment', App.fmt.eur0(s.total_investment_eur));
  set('kpi-shortfall',  App.fmt.eur0(s.uncovered_eur));
  set('kpi-terminal',   App.fmt.eur0(s.terminal_portfolio_cash_eur));
  set('kpi-xirr',       s.annualized_return != null ? App.fmt.pct(s.annualized_return) : '—');
  set('kpi-positions',  s.positions);
  set('kpi-wam',        s.weighted_average_maturity_years != null ? App.fmt.dec2(s.weighted_average_maturity_years) + ' yr' : '—');
  set('kpi-commission', App.fmt.eur0(s.purchase_commission_eur));
  set('kpi-mipgap',     s.solver_mip_gap != null ? App.fmt.pct(s.solver_mip_gap) : '—');

  // Colour shortfall KPI
  const shortEl = document.getElementById('kpi-shortfall');
  if (shortEl) {
    shortEl.className = 'kpi-value ' + (s.uncovered_eur === 0 ? 'positive' : 'negative');
  }
}

/* ── Portfolio ── */
async function loadPortfolioTable() {
  const rows = await App.apiFetch('/api/results/portfolio');
  const tbody = document.getElementById('portfolio-tbody');
  const tfoot = document.getElementById('portfolio-tfoot');
  const count = document.getElementById('portfolio-count');

  count.textContent = `${rows.length} position${rows.length !== 1 ? 's' : ''}`;
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td class="mono">${esc(r.isincode || '')}</td>
      <td>${esc(r.description || '')}</td>
      <td>${getFlag(r.issuerdescription)}${esc(r.issuerdescription || '')}</td>
      <td>${esc(r.ratingsp || '—')}</td>
      <td class="mono">${r.redemptiondate || '—'}</td>
      <td class="num" data-val="${r.lots ?? 0}">${App.fmt.num(r.lots)}</td>
      <td class="num" data-val="${r.nominal_eur ?? 0}">${App.fmt.eur0(r.nominal_eur)}</td>
      <td class="num" data-val="${r.purchase_value_eur ?? 0}">${App.fmt.eur(r.purchase_value_eur)}</td>
      <td class="num" data-val="${r.purchase_commission_eur ?? 0}">${App.fmt.eur(r.purchase_commission_eur)}</td>
      <td class="num" data-val="${r.cost_eur ?? 0}">${App.fmt.eur(r.cost_eur)}</td>
      <td class="num" data-val="${r.maturity_years ?? 0}">${App.fmt.dec2(r.maturity_years)}</td>
    </tr>
  `).join('');

  // Footer totals
  const totalCost    = rows.reduce((s, r) => s + (r.cost_eur || 0), 0);
  const totalNominal = rows.reduce((s, r) => s + (r.nominal_eur || 0), 0);
  const totalComm    = rows.reduce((s, r) => s + (r.purchase_commission_eur || 0), 0);
  tfoot.innerHTML = `
    <tr>
      <td colspan="6" class="font-600">Total</td>
      <td class="num font-600">${App.fmt.eur0(totalNominal)}</td>
      <td></td>
      <td class="num font-600">${App.fmt.eur(totalComm)}</td>
      <td class="num font-600">${App.fmt.eur(totalCost)}</td>
      <td></td>
    </tr>
  `;
}

/* ── Monthly cashflows ── */
async function loadCashflowsMonthly() {
  const rows = await App.apiFetch('/api/results/cashflows/monthly');
  const tbody = document.getElementById('cf-tbody');
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td class="mono">${r.month || ''}</td>
      <td class="num ${r.liability_eur > 0 ? 'text-red' : ''}">${App.fmt.eur(r.liability_eur)}</td>
      <td class="num ${r.asset_cashflow_eur > 0 ? 'text-teal' : ''}">${App.fmt.eur(r.asset_cashflow_eur)}</td>
      <td class="num">${App.fmt.eur(r.external_cash_eur)}</td>
      <td class="num ${r.net_cashflow_eur < 0 ? 'text-red' : ''}">${App.fmt.eur(r.net_cashflow_eur)}</td>
      <td class="num ${r.cash_balance_eur < 0 ? 'text-red' : 'text-green'}">${App.fmt.eur(r.cash_balance_eur)}</td>
    </tr>
  `).join('');

  // Chart
  renderMonthlyChart(rows);
  renderCoverageCharts(rows);
}

function renderMonthlyChart(rows) {
  const ctx = document.getElementById('cf-monthly-chart')?.getContext('2d');
  if (!ctx) return;
  if (_cfMonthlyChart) _cfMonthlyChart.destroy();

  const step = Math.max(1, Math.floor(rows.length / 60));
  const sampled = rows.filter((_, i) => i % step === 0);
  const labels  = sampled.map(r => r.month);

  _cfMonthlyChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: 'Liabilities',
          data: sampled.map(r => r.liability_eur || 0),
          borderColor: App.CHART_COLORS.red,
          backgroundColor: 'rgba(180,74,74,.07)',
          fill: true,
          borderWidth: 1.5,
          pointRadius: 0,
        },
        {
          label: 'Asset CF',
          data: sampled.map(r => r.asset_cashflow_eur || 0),
          borderColor: App.CHART_COLORS.teal,
          backgroundColor: 'rgba(22,124,128,.07)',
          fill: true,
          borderWidth: 1.5,
          pointRadius: 0,
        },
        {
          label: 'Cash balance',
          data: sampled.map(r => r.cash_balance_eur || 0),
          borderColor: App.CHART_COLORS.navy,
          backgroundColor: 'transparent',
          borderWidth: 2,
          pointRadius: 0,
          yAxisID: 'y2',
        },
      ],
    },
    options: chartOptions('Monthly Cash Flows (EUR)', 'y2'),
  });
}

function renderCoverageCharts(rows) {
  // Coverage chart: cumulative liabilities vs cumulative assets
  const cumLiab  = [];
  const cumAsset = [];
  let cl = 0, ca = 0;
  rows.forEach(r => {
    cl += r.liability_eur || 0;
    ca += r.asset_cashflow_eur || 0;
    cumLiab.push(cl);
    cumAsset.push(ca);
  });

  const step = Math.max(1, Math.floor(rows.length / 60));
  const sampled = rows.filter((_, i) => i % step === 0);
  const labels = sampled.map(r => r.month);
  const sCumLiab  = cumLiab.filter((_, i) => i % step === 0);
  const sCumAsset = cumAsset.filter((_, i) => i % step === 0);

  const ctx1 = document.getElementById('coverage-chart')?.getContext('2d');
  if (ctx1) {
    if (_coverageChart) _coverageChart.destroy();
    _coverageChart = new Chart(ctx1, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Cumulative liabilities', data: sCumLiab, borderColor: App.CHART_COLORS.red, borderWidth: 2, pointRadius: 0 },
          { label: 'Cumulative asset CF', data: sCumAsset, borderColor: App.CHART_COLORS.teal, borderWidth: 2, pointRadius: 0 },
        ],
      },
      options: chartOptions('Cumulative Coverage (EUR)'),
    });
  }

  // Surplus/deficit chart
  const ctx2 = document.getElementById('surplus-chart')?.getContext('2d');
  if (ctx2) {
    if (_surplusChart) _surplusChart.destroy();
    const surplus = rows.filter((_, i) => i % step === 0).map(r => r.cash_balance_eur || 0);
    _surplusChart = new Chart(ctx2, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Cumulative cash balance',
          data: surplus,
          backgroundColor: surplus.map(v => v >= 0 ? 'rgba(39,122,82,.6)' : 'rgba(180,74,74,.6)'),
          borderWidth: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom' }, title: { display: true, text: 'Cash Balance (Surplus / Deficit) — EUR' } },
        scales: {
          x: { ticks: { maxTicksLimit: 12, maxRotation: 0 } },
          y: { ticks: { callback: v => eurK(v) } },
        },
      },
    });
  }
}

/* ── Annual cashflows ── */
async function loadCashflowsAnnual() {
  const rows = await App.apiFetch('/api/results/cashflows/annual');
  const tbody = document.getElementById('annual-tbody');
  const tfoot = document.getElementById('annual-tfoot');

  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${r.year}</td>
      <td class="num">${App.fmt.eur0(r.liabilities_eur)}</td>
      <td class="num">${App.fmt.eur0(r.asset_cashflows_eur)}</td>
      <td class="num">${App.fmt.eur0(r.external_cash_eur)}</td>
      <td class="num ${r.net_cashflow_eur < 0 ? 'text-red' : ''}">${App.fmt.eur0(r.net_cashflow_eur)}</td>
      <td class="num ${r.year_end_cash_eur < 0 ? 'text-red' : ''}">${App.fmt.eur0(r.year_end_cash_eur)}</td>
    </tr>
  `).join('');

  const totLiab  = rows.reduce((s, r) => s + (r.liabilities_eur || 0), 0);
  const totAsset = rows.reduce((s, r) => s + (r.asset_cashflows_eur || 0), 0);
  const totExt   = rows.reduce((s, r) => s + (r.external_cash_eur || 0), 0);
  const totNet   = rows.reduce((s, r) => s + (r.net_cashflow_eur || 0), 0);
  tfoot.innerHTML = `
    <tr>
      <td class="font-600">Total</td>
      <td class="num font-600">${App.fmt.eur0(totLiab)}</td>
      <td class="num font-600">${App.fmt.eur0(totAsset)}</td>
      <td class="num font-600">${App.fmt.eur0(totExt)}</td>
      <td class="num font-600">${App.fmt.eur0(totNet)}</td>
      <td></td>
    </tr>
  `;

  // Annual chart
  const ctx = document.getElementById('cf-annual-chart')?.getContext('2d');
  if (ctx) {
    if (_cfAnnualChart) _cfAnnualChart.destroy();
    _cfAnnualChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: rows.map(r => r.year),
        datasets: [
          { label: 'Liabilities', data: rows.map(r => r.liabilities_eur || 0), backgroundColor: 'rgba(180,74,74,.7)', borderWidth: 0 },
          { label: 'Asset CF',    data: rows.map(r => r.asset_cashflows_eur || 0), backgroundColor: 'rgba(22,124,128,.7)', borderWidth: 0 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: 'bottom' },
          title: { display: true, text: 'Annual Cash Flows (EUR)' },
        },
        scales: {
          x: { ticks: { maxRotation: 0 } },
          y: { ticks: { callback: v => eurK(v) } },
        },
      },
    });
  }
}

/* ── Issuer allocation ── */
async function loadIssuerAllocation() {
  const rows = await App.apiFetch('/api/results/issuer-allocation');
  const tbody = document.getElementById('issuer-tbody');
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${getFlag(r.issuer)}${esc(r.issuer || '')}</td>
      <td class="num">${App.fmt.eur0(r.invested_eur)}</td>
      <td class="num">${App.fmt.pct(r.weight)}</td>
      <td class="num">${r.positions}</td>
    </tr>
  `).join('');

  const ctx = document.getElementById('issuer-chart')?.getContext('2d');
  if (ctx) {
    if (_issuerChart) _issuerChart.destroy();
    _issuerChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: rows.map(r => r.issuer),
        datasets: [{
          label: 'Invested (EUR)',
          data: rows.map(r => r.invested_eur || 0),
          backgroundColor: App.CHART_COLORS.teal,
          borderWidth: 0,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          title: { display: true, text: 'Portfolio allocation by issuer (EUR)' },
        },
        scales: {
          x: { ticks: { callback: v => eurK(v) } },
        },
      },
    });
  }
}

/* ── Methodology ── */
async function loadMethodology() {
  const methodology = await App.apiFetch('/api/results/methodology');
  const intro = document.getElementById('methodology-intro');
  const sections = document.getElementById('methodology-sections');
  if (intro) {
    intro.innerHTML = `
      <h3>${esc(methodology.title)}</h3>
      <p>${esc(methodology.intro)}</p>
    `;
  }
  if (sections) {
    sections.innerHTML = (methodology.sections || []).map(section => `
      <article class="methodology-section">
        <h4>${esc(section.title)}</h4>
        <p>${esc(section.content)}</p>
      </article>
    `).join('');
  }
}

/* ── Excel export ── */
document.getElementById('btn-export-excel')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-export-excel');
  btn.disabled = true;
  btn.textContent = 'Exporting…';
  try {
    await App.apiFetch('/api/results/export/excel', { method: 'POST' });
    window.location.href = '/api/results/download/excel';
  } catch (e) {
    alert('Export failed: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Export Excel';
  }
});

/* ──────────────────────────────────────────────
   Chart helper
   ────────────────────────────────────────────── */
function chartOptions(title, y2id) {
  const opts = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: 'bottom', labels: { boxWidth: 12, padding: 14 } },
      title: { display: !!title, text: title },
    },
    scales: {
      x: { ticks: { maxTicksLimit: 12, maxRotation: 0 }, grid: { color: '#D7E0E3', lineWidth: 0.5 } },
      y: { ticks: { callback: v => eurK(v) }, grid: { color: '#D7E0E3', lineWidth: 0.5 } },
    },
  };
  if (y2id) {
    opts.scales.y2 = {
      position: 'right',
      ticks: { callback: v => eurK(v) },
      grid: { drawOnChartArea: false },
    };
  }
  return opts;
}

function eurK(v) {
  const a = Math.abs(v);
  if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (a >= 1e3) return (v / 1e3).toFixed(0) + 'k';
  return v.toFixed(0);
}

function esc(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

const ISSUER_FLAGS = {
  'italia': 'it',
  'francia': 'fr',
  'germania': 'de',
  'spagna': 'es',
  'paesi bassi': 'nl',
  'olanda': 'nl',
  'belgio': 'be',
  'austria': 'at',
  'polonia': 'pl',
  'portogallo': 'pt',
  'irlanda': 'ie',
  'grecia': 'gr',
  'finlandia': 'fi',
  'slovenia': 'si',
  'slovacchia': 'sk',
  'lituania': 'lt',
  'lettonia': 'lv',
  'estonia': 'ee',
  'lussemburgo': 'lu',
  'cipro': 'cy',
  'malta': 'mt',
  'unione europea': 'eu',
  'european union': 'eu',
  'eu': 'eu',
  'europa': 'eu',
  'romania': 'ro',
  'bulgaria': 'bg',
  'croazia': 'hr',
  'svezia': 'se',
  'danimarca': 'dk',
  'rep. ceca': 'cz',
  'repubblica ceca': 'cz',
  'ungheria': 'hu'
};

function getFlag(issuer) {
  if (!issuer) return '';
  const key = String(issuer).toLowerCase().trim();
  const code = ISSUER_FLAGS[key];
  if (code) {
    return `<img src="https://flagcdn.com/w20/${code}.png" srcset="https://flagcdn.com/w40/${code}.png 2x" width="16" alt="${code}" style="vertical-align: middle; margin-right: 6px; margin-top: -3px; border-radius: 2px;">`;
  }
  return '';
}

/* ──────────────────────────────────────────────
   Explicit loading
   ────────────────────────────────────────────── */
const loadLatestResultsButton = document.getElementById('btn-load-latest-results');
if (loadLatestResultsButton) {
  loadLatestResultsButton.addEventListener('click', async () => {
    loadLatestResultsButton.disabled = true;
    loadLatestResultsButton.textContent = 'Loading…';
    try {
      await loadResults();
    } finally {
      loadLatestResultsButton.disabled = false;
      loadLatestResultsButton.textContent = 'Load latest results';
    }
  });
}
