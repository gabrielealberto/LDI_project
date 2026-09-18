/**
 * run.js — Run tab: preflight checks, pipeline execution, live log, status.
 */

let _pollInterval = null;
let _currentRunId = null;

/* ──────────────────────────────────────────────
   Preflight checks
   ────────────────────────────────────────────── */
async function runPreflightChecks() {
  const list = document.getElementById('preflight-list');
  list.innerHTML = '<div class="check-item"><span class="check-icon warn"><span class="spinner dark"></span></span> Checking…</div>';
  try {
    const readiness = await App.apiFetch('/api/config/readiness');
    const checks = readiness.checks;
    const labels = {
      liabilities_config: 'Liabilities configuration file',
      scenarios_config: 'Inflation scenarios configuration',
      inflation_baseline: 'Inflation baseline data',
      bond_cashflows: 'Bond cashflow data',
      bond_metadata: 'Bond metadata',
      liabilities_defined: 'At least one liability defined',
      excel_output_writable: 'Excel output is not open in another application',
    };
    // Run is possible even without pre-computed data (pipeline regenerates it)
    // Treat missing processed files as warnings, not blockers
    const blockers = ['liabilities_config', 'liabilities_defined', 'excel_output_writable'];
    let allGood = true;
    list.innerHTML = Object.entries(checks).map(([key, ok]) => {
      const isBlocker = blockers.includes(key);
      const icon = ok ? 'pass' : (isBlocker ? 'fail' : 'warn');
      const symbol = ok ? '✓' : (isBlocker ? '✗' : '⚠');
      if (!ok && isBlocker) allGood = false;
      return `
        <div class="check-item">
          <span class="check-icon ${icon}">${symbol}</span>
          <span>${labels[key] || key}</span>
          ${!ok ? `<span class="text-muted text-sm">${isBlocker ? '(required)' : '(will be generated)'}</span>` : ''}
        </div>
      `;
    }).join('');
    document.getElementById('btn-run').disabled = !allGood;
    if (!allGood) {
      showRunError('Some required inputs are missing. Configure at least one liability before running.');
    } else {
      hideRunError();
    }
    return allGood;
  } catch (e) {
    list.innerHTML = `<div class="check-item"><span class="check-icon fail">✗</span> Could not check readiness: ${e.message}</div>`;
    document.getElementById('btn-run').disabled = true;
    return false;
  }
}

/* ──────────────────────────────────────────────
   Run pipeline
   ────────────────────────────────────────────── */
document.getElementById('btn-run')?.addEventListener('click', async () => {
  const btn = document.getElementById('btn-run');
  btn.disabled = true;
  clearLog();
  hideRunError();
  setRunStatus('running', 'Running full pipeline…');

  try {
    const resp = await App.apiFetch('/api/run/start', {
      method: 'POST',
      body: JSON.stringify({
        parameters: window.collectRunParameters ? window.collectRunParameters() : undefined,
      }),
    });
    _currentRunId = resp.run_id;
    appendLog('Pipeline started — run ID: ' + _currentRunId, 'stage');
    showProgress(true);
    startPolling(_currentRunId);
  } catch (e) {
    setRunStatus('error', 'Failed to start: ' + e.message);
    showRunError(e.message);
    btn.disabled = false;
  }
});


function startPolling(runId) {
  if (_pollInterval) clearInterval(_pollInterval);
  _pollInterval = setInterval(() => pollStatus(runId), 1200);
}

async function pollStatus(runId) {
  try {
    const status = await App.apiFetch(`/api/run/status/${runId}`);
    // Render new log lines
    const log = status.log || [];
    const logEl = document.getElementById('run-log');
    // We replace the entire log content (simpler)
    logEl.innerHTML = log.map(line => {
      const cls = lineClass(line);
      return `<span class="log-line ${cls}">${escHtml(line)}</span>`;
    }).join('\n');
    logEl.scrollTop = logEl.scrollHeight;

    if (status.status === 'success') {
      clearInterval(_pollInterval);
      _pollInterval = null;
      setRunStatus('success', 'Pipeline completed successfully');
      showProgress(false);
      document.getElementById('btn-run').disabled = false;
      App.updateRunStatus('success');
      App.enableResultsTab(true, true);
      appendLog('Results and stress analysis are now available.', 'ok');
    } else if (status.status === 'error') {
      clearInterval(_pollInterval);
      _pollInterval = null;
      setRunStatus('error', 'Pipeline failed');
      showProgress(false);
      showRunError(status.error || 'Unknown error');
      document.getElementById('btn-run').disabled = false;
      App.updateRunStatus('error');
    }
  } catch (e) {
    console.error('Polling error', e);
  }
}

function lineClass(line) {
  if (line.startsWith('PHASE')) return 'stage';
  if (line.startsWith('SUCCESS')) return 'ok';
  if (line.startsWith('===') || line.startsWith('---')) return 'stage';
  if (line.startsWith('  ✓') || line.startsWith('Results')) return 'ok';
  if (line.startsWith('ERROR')) return 'error';
  if (line.startsWith('WARNING') || line.startsWith('WARN')) return 'warn';
  return '';
}

/* ──────────────────────────────────────────────
   UI helpers
   ────────────────────────────────────────────── */
function setRunStatus(status, message) {
  const label = document.getElementById('run-status-label');
  label.textContent = message;
  label.style.color = {
    running: 'var(--gold)',
    success: 'var(--green)',
    error:   'var(--red)',
    idle:    'var(--muted)',
  }[status] || 'var(--muted)';

  const icon = document.getElementById('run-btn-icon');
  icon.innerHTML = status === 'running' ? '<span class="spinner"></span>' : '▶';
}

function showProgress(show) {
  document.getElementById('run-progress-wrap').classList.toggle('hidden', !show);
}

function showRunError(msg) {
  const box = document.getElementById('run-error-box');
  box.textContent = msg;
  box.classList.remove('hidden');
}

function hideRunError() {
  document.getElementById('run-error-box').classList.add('hidden');
}

function appendLog(msg, cls = '') {
  const logEl = document.getElementById('run-log');
  const span = document.createElement('span');
  span.className = 'log-line' + (cls ? ' ' + cls : '');
  span.textContent = msg;
  logEl.appendChild(span);
  logEl.appendChild(document.createTextNode('\n'));
  logEl.scrollTop = logEl.scrollHeight;
}

function clearLog() {
  document.getElementById('run-log').innerHTML = '';
}

document.getElementById('btn-clear-log')?.addEventListener('click', clearLog);

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/* ──────────────────────────────────────────────
   Init
   ────────────────────────────────────────────── */
document.addEventListener('tabActivated', e => {
  if (e.detail === 'run') {
    runPreflightChecks();
  }
});

document.addEventListener('DOMContentLoaded', () => {
  // Check if there's already a running job
  App.apiFetch('/api/run/latest').then(run => {
    if (run.status === 'running' && run.run_id) {
      _currentRunId = run.run_id;
      setRunStatus('running', 'Pipeline is running…');
      showProgress(true);
      startPolling(_currentRunId);
    }
  }).catch(() => {});
});
