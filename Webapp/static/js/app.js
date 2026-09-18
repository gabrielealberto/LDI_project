/**
 * app.js — Core router, utilities, and global state for the LDI web app.
 */

/* ──────────────────────────────────────────────
   Tab router
   ────────────────────────────────────────────── */
const TABS = ['configuration', 'run', 'results', 'stress'];

function activateTab(name) {
  TABS.forEach(t => {
    document.querySelector(`#tab-${t}`)?.classList.toggle('active', t === name);
    document.querySelector(`#nav-${t}`)?.classList.toggle('active', t === name);
  });
  const titles = {
    configuration: 'Configuration',
    run: 'Run',
    results: 'Results',
    stress: 'Inflation Shocks Analysis',
  };
  document.getElementById('header-title').textContent = titles[name] || name;
  // Notify sub-modules
  document.dispatchEvent(new CustomEvent('tabActivated', { detail: name }));
}

document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    if (item.classList.contains('disabled')) return;
    activateTab(item.dataset.tab);
  });
});

/* ──────────────────────────────────────────────
   Global run status tracker
   ────────────────────────────────────────────── */
const AppState = {
  currentRunId: null,
  runStatus: 'idle',   // idle | running | success | error
  resultsAvailable: false,
  stressAvailable: false,
};

function updateRunStatus(status) {
  AppState.runStatus = status;
  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  const badge = document.getElementById('header-badge');

  dot.className = `status-dot ${status}`;
  badge.className = `header-badge badge-${status}`;

  const labels = { idle: 'Idle', running: 'Running…', success: 'Ready', error: 'Error' };
  text.textContent = labels[status] || status;
  badge.textContent = labels[status] || status;
}

function enableResultsTab(resultsOk, stressOk) {
  AppState.resultsAvailable = resultsOk;
  AppState.stressAvailable = stressOk;
  const navResults = document.getElementById('nav-results');
  const navStress = document.getElementById('nav-stress');
  // Results is always navigable. Loading archived results is an explicit user action.
  navResults.classList.remove('disabled');
  navStress.classList.toggle('disabled', !stressOk);
}

/* ──────────────────────────────────────────────
   API helpers
   ────────────────────────────────────────────── */
async function apiFetch(url, options = {}) {
  const defaults = {
    headers: { 'Content-Type': 'application/json' },
  };
  const res = await fetch(url, { ...defaults, ...options });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
  return json;
}

/* ──────────────────────────────────────────────
   Formatters
   ────────────────────────────────────────────── */
const fmt = {
  eur: v => v == null ? '—' : new Intl.NumberFormat('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v),
  eur0: v => v == null ? '—' : new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 }).format(v),
  pct: v => v == null ? '—' : (v * 100).toFixed(2) + '%',
  pct1: v => v == null ? '—' : (v * 100).toFixed(1) + '%',
  num: v => v == null ? '—' : new Intl.NumberFormat('en-GB').format(v),
  dec2: v => v == null ? '—' : (+v).toFixed(2),
};

/* ──────────────────────────────────────────────
   Sub-tab router (inside Results panel)
   ────────────────────────────────────────────── */
function initSubTabs() {
  document.querySelectorAll('.sub-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const group = tab.closest('.panel');
      group.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const name = tab.dataset.subtab;
      group.querySelectorAll('.sub-panel').forEach(p => p.classList.add('hidden'));
      document.getElementById(`subpanel-${name}`)?.classList.remove('hidden');
    });
  });
}

/* ──────────────────────────────────────────────
   Collapsible panels
   ────────────────────────────────────────────── */
function initCollapsibles() {
  document.querySelectorAll('.panel-header.collapsible').forEach(header => {
    header.addEventListener('click', () => {
      const body = header.nextElementSibling;
      const collapsed = header.classList.toggle('collapsed');
      body.classList.toggle('collapsed', collapsed);
    });
  });
}

/* ──────────────────────────────────────────────
   Sortable tables
   ────────────────────────────────────────────── */
function initSortableTable(tableId) {
  const table = document.getElementById(tableId);
  if (!table) return;
  let sortCol = null, sortAsc = true;
  table.querySelectorAll('thead th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (sortCol === col) {
        sortAsc = !sortAsc;
      } else {
        sortCol = col;
        sortAsc = true;
      }
      table.querySelectorAll('thead th').forEach(h => {
        h.classList.remove('sort-asc', 'sort-desc');
      });
      th.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
      // Re-sort the body rows
      const tbody = table.querySelector('tbody');
      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort((a, b) => {
        const ai = th.cellIndex;
        const av = a.cells[ai]?.dataset.val ?? a.cells[ai]?.textContent ?? '';
        const bv = b.cells[ai]?.dataset.val ?? b.cells[ai]?.textContent ?? '';
        const an = parseFloat(av), bn = parseFloat(bv);
        const cmp = isNaN(an) || isNaN(bn)
          ? av.localeCompare(bv)
          : an - bn;
        return sortAsc ? cmp : -cmp;
      });
      rows.forEach(r => tbody.appendChild(r));
    });
  });
}

/* ──────────────────────────────────────────────
   Table search filter
   ────────────────────────────────────────────── */
function initTableSearch(inputId, tbodyId) {
  const input = document.getElementById(inputId);
  const tbody = document.getElementById(tbodyId);
  if (!input || !tbody) return;
  input.addEventListener('input', () => {
    const q = input.value.toLowerCase();
    tbody.querySelectorAll('tr').forEach(row => {
      row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  });
}

/* ──────────────────────────────────────────────
   Chart defaults
   ────────────────────────────────────────────── */
Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
Chart.defaults.font.size = 11.5;
Chart.defaults.color = '#667783';

const CHART_COLORS = {
  navy:  '#182B3A',
  teal:  '#167C80',
  gold:  '#B98A43',
  green: '#277A52',
  red:   '#B44A4A',
  muted: '#667783',
  violet:'#5E4FA2',
  orange:'#B25A1A',
};

const SCENARIO_COLORS = [
  '#182B3A','#167C80','#B98A43','#277A52','#B44A4A','#5E4FA2','#B25A1A','#1A5C9A'
];

/* ──────────────────────────────────────────────
   Init
   ────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  initSubTabs();
  initCollapsibles();
});

// Expose globals for sub-modules
window.App = {
  apiFetch, fmt, updateRunStatus, enableResultsTab,
  CHART_COLORS, SCENARIO_COLORS, initSortableTable, initTableSearch,
};
