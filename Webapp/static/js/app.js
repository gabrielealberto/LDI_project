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
    const nav = document.querySelector(`#nav-${t}`);
    nav?.classList.toggle('active', t === name);
    nav?.setAttribute('aria-current', t === name ? 'page' : 'false');
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
  const activate = () => {
    if (item.classList.contains('disabled')) return;
    activateTab(item.dataset.tab);
  };
  item.addEventListener('click', activate);
  item.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      activate();
    }
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
  loadedRun: null,
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
  navStress.setAttribute('aria-disabled', stressOk ? 'false' : 'true');
}

function setLoadedRun(run) {
  AppState.loadedRun = run || null;
  document.dispatchEvent(new CustomEvent('runLoaded', { detail: AppState.loadedRun }));
}

function getLoadedRun() {
  return AppState.loadedRun;
}

async function refreshAvailability() {
  try {
    const availability = await apiFetch('/api/results/available');
    // Archived results are only candidates until the user explicitly loads them.
    // Inflation Shocks is enabled by loadResults() or by a newly completed run.
    enableResultsTab(availability.results_available, false);
    return availability;
  } catch (_) {
    return null;
  }
}

/* ──────────────────────────────────────────────
   API helpers
   ────────────────────────────────────────────── */
async function apiFetch(url, options = {}) {
  const { timeout = 15000, ...fetchOptions } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const defaults = {
    headers: { 'Content-Type': 'application/json' },
  };
  try {
    const res = await fetch(url, { ...defaults, ...fetchOptions, signal: controller.signal });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(json.error || `HTTP ${res.status}`);
    return json;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error(`Request timed out: ${url}`);
    throw error;
  } finally {
    clearTimeout(timer);
  }
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
    const activate = () => {
      const group = tab.closest('.panel');
      group.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const name = tab.dataset.subtab;
      tab.setAttribute('aria-selected', 'true');
      group.querySelectorAll('.sub-tab').forEach(t => {
        if (t !== tab) t.setAttribute('aria-selected', 'false');
      });
      group.querySelectorAll('.sub-panel').forEach(p => p.classList.add('hidden'));
      document.getElementById(`subpanel-${name}`)?.classList.remove('hidden');
    };
    tab.addEventListener('click', activate);
    tab.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        activate();
      }
    });
  });
}

/* ──────────────────────────────────────────────
   Collapsible panels
   ────────────────────────────────────────────── */
function initCollapsibles() {
  document.querySelectorAll('.panel-header.collapsible').forEach(header => {
    const toggle = () => {
      const body = header.nextElementSibling;
      const collapsed = header.classList.toggle('collapsed');
      body.classList.toggle('collapsed', collapsed);
      header.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    };
    header.addEventListener('click', toggle);
    header.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggle();
      }
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
if (window.Chart?.defaults) {
  Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
  Chart.defaults.font.size = 11.5;
  Chart.defaults.color = '#667783';
}

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
  if (window.Chart?.isFallback) {
    const note = document.createElement('div');
    note.className = 'alert alert-warn';
    note.setAttribute('role', 'status');
    note.textContent = 'Charts are unavailable offline; tables and analysis remain available.';
    document.getElementById('content')?.prepend(note);
  }
  refreshAvailability();
});

// Expose globals for sub-modules
window.App = {
  apiFetch, fmt, updateRunStatus, enableResultsTab, refreshAvailability,
  setLoadedRun, getLoadedRun,
  CHART_COLORS, SCENARIO_COLORS, initSortableTable, initTableSearch,
};
