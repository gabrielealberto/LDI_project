/**
 * config.js — Configuration tab: parameters, liabilities, baseline chart, scenarios.
 */

/* ──────────────────────────────────────────────
   Parameters
   ────────────────────────────────────────────── */
let _currentParams = {};

async function loadParams() {
  try {
    _currentParams = await App.apiFetch('/api/config/parameters');
    renderParams(_currentParams);
  } catch (e) {
    showConfigAlert('error', 'Could not load parameters: ' + e.message);
  }
}

function renderParams(p) {
  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = !!val;
    else el.value = val != null ? val : '';
  };
  setVal('param-nominal', p.nominal);
  setVal('param-max_nominal_per_bond', p.max_nominal_per_bond);
  setVal('param-coupon_tax_rate', p.coupon_tax_rate);
  setVal('param-max_issuer_weight', p.max_issuer_weight);
  setVal('param-max_positions', p.max_positions);
  setVal('param-terminal_capital_ratio', p.terminal_capital_ratio);
  setVal('param-prefer_short_maturity', p.prefer_short_maturity);
  setVal('param-broker_fee_rate', p.broker_fee_rate);
  setVal('param-broker_min_fee', p.broker_min_fee);
  setVal('param-broker_max_fee', p.broker_max_fee);
  setVal('param-baseline_annual_target', p.baseline_annual_target);
  setVal('param-baseline_convergence_half_life_months', p.baseline_convergence_half_life_months);
  setVal('param-baseline_spread_half_life_months', p.baseline_spread_half_life_months);
  setVal('param-baseline_seasonal_years', p.baseline_seasonal_years);
  document.querySelectorAll('[id^="param-"]').forEach(el => {
    el.disabled = p.editable === false;
  });
  const reset = document.getElementById('btn-reset-params');
  if (reset) reset.disabled = p.editable === false;
}

function collectParams() {
  const getVal = id => {
    const el = document.getElementById(id);
    if (!el) return undefined;
    if (el.type === 'checkbox') return el.checked;
    return el.value !== '' ? Number(el.value) : undefined;
  };
  return {
    nominal: getVal('param-nominal'),
    max_nominal_per_bond: getVal('param-max_nominal_per_bond'),
    coupon_tax_rate: getVal('param-coupon_tax_rate'),
    max_issuer_weight: getVal('param-max_issuer_weight'),
    max_positions: getVal('param-max_positions'),
    terminal_capital_ratio: getVal('param-terminal_capital_ratio'),
    prefer_short_maturity: document.getElementById('param-prefer_short_maturity')?.checked ?? true,
    broker_fee_rate: getVal('param-broker_fee_rate'),
    broker_min_fee: getVal('param-broker_min_fee'),
    broker_max_fee: getVal('param-broker_max_fee'),
    baseline_annual_target: getVal('param-baseline_annual_target'),
    baseline_convergence_half_life_months: getVal('param-baseline_convergence_half_life_months'),
    baseline_spread_half_life_months: getVal('param-baseline_spread_half_life_months'),
    baseline_seasonal_years: getVal('param-baseline_seasonal_years'),
  };
}

window.collectRunParameters = collectParams;

document.getElementById('btn-reset-params')?.addEventListener('click', async () => {
  try {
    _currentParams = await App.apiFetch('/api/config/parameters/reset', { method: 'POST' });
    renderParams(_currentParams);
    showConfigAlert('info', 'Parameters reset to defaults.');
  } catch (e) {
    showConfigAlert('error', e.message);
  }
});

/* ──────────────────────────────────────────────
   Save configuration
   ────────────────────────────────────────────── */
document.getElementById('btn-save-config')?.addEventListener('click', async () => {
  const status = document.getElementById('config-save-status');
  status.textContent = 'Saving…';
  try {
    // Save parameters
    const params = collectParams();
    await App.apiFetch('/api/config/parameters', { method: 'POST', body: JSON.stringify(params) });
    // Save liabilities
    await App.apiFetch('/api/config/liabilities', {
      method: 'POST',
      body: JSON.stringify(_liabilities),
    });
    // Save scenarios
    await App.apiFetch('/api/config/inflation/scenarios', {
      method: 'POST',
      body: JSON.stringify(_scenarios),
    });
    status.textContent = '✓ Saved';
    status.style.color = 'var(--green)';
    setTimeout(() => { status.textContent = ''; }, 3000);
  } catch (e) {
    status.textContent = '✗ ' + e.message;
    status.style.color = 'var(--red)';
  }
});

/* ──────────────────────────────────────────────
   Config alerts
   ────────────────────────────────────────────── */
function showConfigAlert(type, msg) {
  const area = document.getElementById('config-alert-area');
  const alert = document.createElement('div');
  alert.className = `alert alert-${type}`;
  alert.textContent = msg;
  area.replaceChildren(alert);
  setTimeout(() => { area.innerHTML = ''; }, 5000);
}

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const overlay = document.querySelector('.modal-overlay:not(.hidden)');
  overlay?.querySelector('.modal-close')?.click();
});

/* ──────────────────────────────────────────────
   Liabilities
   ────────────────────────────────────────────── */
let _liabilities = [];
let _editingLiabilityIndex = -1;

async function loadLiabilities() {
  try {
    _liabilities = await App.apiFetch('/api/config/liabilities');
    renderLiabilitiesTable();
  } catch (e) {
    showConfigAlert('error', 'Could not load liabilities: ' + e.message);
  }
}

function renderLiabilitiesTable() {
  const tbody = document.getElementById('liabilities-tbody');
  const empty = document.getElementById('liabilities-empty');
  const wrap  = document.getElementById('liabilities-table-wrap');
  if (!_liabilities.length) {
    empty.classList.remove('hidden');
    wrap.classList.add('hidden');
    return;
  }
  empty.classList.add('hidden');
  wrap.classList.remove('hidden');
  tbody.innerHTML = _liabilities.map((l, i) => `
    <tr>
      <td>${escHtml(l.name || '')}</td>
      <td>${escHtml(l.category || '')}</td>
      <td class="mono">${l.start_date || ''}</td>
      <td class="mono">${l.end_date || ''}</td>
      <td class="num">${App.fmt.eur0(l.initial_cashflow)}</td>
      <td>${l.frequency || ''}</td>
      <td class="num">${App.fmt.pct(l.inflation_rate)}</td>
      <td>${l.indexation ? escHtml(l.indexation.index_id || '') : '—'}</td>
      <td class="num">${l.probability != null ? l.probability : '1'}</td>
      <td>
        <button class="btn btn-sm btn-secondary" onclick="openLiabilityModal(${i})">Edit</button>
        <button class="btn btn-sm btn-danger" onclick="deleteLiability(${i})" style="margin-left:4px">Delete</button>
      </td>
    </tr>
  `).join('');
}

function deleteLiability(index) {
  if (!confirm('Delete this liability?')) return;
  _liabilities.splice(index, 1);
  renderLiabilitiesTable();
}

function openLiabilityModal(index) {
  _editingLiabilityIndex = index;
  const l = index >= 0 ? _liabilities[index] : null;
  document.getElementById('liability-modal-title').textContent = l ? 'Edit Liability' : 'Add Liability';

  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val != null ? val : '';
  };
  set('liab-name', l?.name ?? '');
  set('liab-category', l?.category ?? 'liability');
  set('liab-start_date', l?.start_date ?? '');
  set('liab-end_date', l?.end_date ?? '');
  set('liab-initial_cashflow', l?.initial_cashflow ?? '');
  set('liab-frequency', l?.frequency ?? 'annual');
  set('liab-inflation_rate', l?.inflation_rate ?? 0.025);
  set('liab-probability', l?.probability ?? 1);
  set('liab-interval_years', l?.interval_years ?? 2);

  const hasIdx = !!l?.indexation;
  document.getElementById('liab-use-indexation').checked = hasIdx;
  document.getElementById('liab-indexation-fields').classList.toggle('hidden', !hasIdx);

  if (hasIdx) {
    set('liab-index_id', l.indexation.index_id ?? 'FOI_XT_IT');
    set('liab-base_reference_date', l.indexation.base_reference_date ?? '');
    set('liab-observation_lag_months', l.indexation.observation_lag_months ?? 0);
  }

  updateIntervalVisibility();
  document.getElementById('liability-modal-error').classList.add('hidden');
  document.getElementById('liability-modal-overlay').classList.remove('hidden');
}

function updateIntervalVisibility() {
  const freq = document.getElementById('liab-frequency').value;
  document.getElementById('liab-interval-group').classList.toggle('hidden', freq !== 'every_n_years');
}

document.getElementById('liab-frequency')?.addEventListener('change', updateIntervalVisibility);
document.getElementById('liab-use-indexation')?.addEventListener('change', e => {
  document.getElementById('liab-indexation-fields').classList.toggle('hidden', !e.target.checked);
});

['liability-modal-close', 'liability-modal-cancel'].forEach(id => {
  document.getElementById(id)?.addEventListener('click', () => {
    document.getElementById('liability-modal-overlay').classList.add('hidden');
  });
});

document.getElementById('liability-modal-save')?.addEventListener('click', () => {
  const get = id => document.getElementById(id)?.value?.trim() ?? '';
  const liab = {
    name: get('liab-name'),
    category: get('liab-category'),
    start_date: get('liab-start_date'),
    end_date: get('liab-end_date'),
    initial_cashflow: parseFloat(get('liab-initial_cashflow')),
    frequency: get('liab-frequency'),
    inflation_rate: parseFloat(get('liab-inflation_rate')),
    probability: parseFloat(get('liab-probability')),
  };
  if (liab.frequency === 'every_n_years') {
    liab.interval_years = parseInt(get('liab-interval_years'));
  }
  if (document.getElementById('liab-use-indexation').checked) {
    liab.indexation = {
      index_id: get('liab-index_id'),
      base_reference_date: get('liab-base_reference_date'),
      observation_lag_months: parseInt(get('liab-observation_lag_months')),
    };
  }
  // Basic validation
  const errors = [];
  if (!liab.name) errors.push('Name is required');
  if (!liab.start_date) errors.push('Start date is required');
  if (!liab.end_date) errors.push('End date is required');
  if (isNaN(liab.initial_cashflow) || liab.initial_cashflow < 0) errors.push('Invalid initial cashflow');
  if (errors.length) {
    const errEl = document.getElementById('liability-modal-error');
    errEl.textContent = errors.join(' · ');
    errEl.classList.remove('hidden');
    return;
  }
  if (_editingLiabilityIndex >= 0) {
    _liabilities[_editingLiabilityIndex] = liab;
  } else {
    _liabilities.push(liab);
  }
  renderLiabilitiesTable();
  document.getElementById('liability-modal-overlay').classList.add('hidden');
});

document.getElementById('btn-add-liability')?.addEventListener('click', () => openLiabilityModal(-1));

/* ──────────────────────────────────────────────
   Baseline chart
   ────────────────────────────────────────────── */
let _baselineChart = null;

async function loadBaselineChart() {
  const alertEl = document.getElementById('baseline-alert');
  try {
    const data = await App.apiFetch('/api/config/inflation/baseline');
    alertEl.classList.add('hidden');
    renderBaselineChart(data);
  } catch (e) {
    alertEl.textContent = e.message.includes('not found')
      ? 'Inflation baseline not available. Run the full pipeline at least once to generate it.'
      : 'Error loading baseline: ' + e.message;
    alertEl.classList.remove('hidden');
  }
}

function renderBaselineChart(data) {
  const ctx = document.getElementById('baseline-chart')?.getContext('2d');
  if (!ctx) return;
  if (_baselineChart) _baselineChart.destroy();

  const labels = data.map(d => d.date.substring(0, 7));
  const foiYoy  = data.map(d => d.foi_yoy != null  ? +(d.foi_yoy  * 100).toFixed(3) : null);
  const hicpYoy = data.map(d => d.hicp_yoy != null ? +(d.hicp_yoy * 100).toFixed(3) : null);

  // Sample every 3 months for readability
  const step = 3;
  const sampledLabels = labels.filter((_, i) => i % step === 0);
  const sampledFOI    = foiYoy.filter((_,  i) => i % step === 0);
  const sampledHICP   = hicpYoy.filter((_,  i) => i % step === 0);

  _baselineChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: sampledLabels,
      datasets: [
        {
          label: 'FOI Italy YoY %',
          data: sampledFOI,
          borderColor: App.CHART_COLORS.navy,
          backgroundColor: 'transparent',
          borderWidth: 1.8,
          pointRadius: 0,
          tension: 0.2,
        },
        {
          label: 'HICP Euro Area YoY %',
          data: sampledHICP,
          borderColor: App.CHART_COLORS.teal,
          backgroundColor: 'transparent',
          borderWidth: 1.8,
          pointRadius: 0,
          tension: 0.2,
          borderDash: [4, 3],
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { boxWidth: 12, padding: 14 } },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(2)}%`,
          },
        },
      },
      scales: {
        x: { ticks: { maxTicksLimit: 12, maxRotation: 0 }, grid: { color: '#D7E0E3', lineWidth: 0.5 } },
        y: {
          ticks: { callback: v => v.toFixed(1) + '%' },
          grid: { color: '#D7E0E3', lineWidth: 0.5 },
          title: { display: true, text: 'YoY %' },
        },
      },
    },
  });
}

document.getElementById('btn-refresh-baseline')?.addEventListener('click', loadBaselineChart);

/* ──────────────────────────────────────────────
   Stress scenarios
   ────────────────────────────────────────────── */
let _scenarios = [];
let _editingScenarioIndex = -1;
let _scenarioPreviewChart = null;

async function loadScenarios() {
  try {
    _scenarios = await App.apiFetch('/api/config/inflation/scenarios');
    renderScenariosTable();
    populateScenarioPreviewSelect();
  } catch (e) {
    console.error('Could not load scenarios', e);
  }
}

function renderScenariosTable() {
  const tbody = document.getElementById('scenarios-tbody');
  const count = document.getElementById('scenario-count');
  count.textContent = `${_scenarios.length} scenario${_scenarios.length !== 1 ? 's' : ''} configured`;
  tbody.innerHTML = _scenarios.map((s, i) => `
    <tr>
      <td class="mono">${escHtml(s.scenario_id)}</td>
      <td><span class="badge-family badge-${s.family}">${s.family}</span></td>
      <td><span class="badge-family badge-severity-${s.severity}">${s.severity}</span></td>
      <td class="num">${s.common_annual_shock_bp ?? '—'}</td>
      <td class="num">${s.foi_hicp_spread_shock_bp ?? '—'}</td>
      <td>${s.start_rule}</td>
      <td class="mono">${s.family !== 'regime_shift' ? `${s.ramp_months}/${s.hold_months}/${s.decay_half_life_months}` : 'N/A'}</td>
      <td>
        <button class="btn btn-sm btn-secondary" onclick="openScenarioModal(${i})">Edit</button>
        <button class="btn btn-sm btn-danger" onclick="deleteScenario(${i})" style="margin-left:4px">Delete</button>
      </td>
    </tr>
  `).join('');
}

function deleteScenario(index) {
  if (!confirm('Delete this scenario?')) return;
  _scenarios.splice(index, 1);
  renderScenariosTable();
  populateScenarioPreviewSelect();
}

function openScenarioModal(index) {
  _editingScenarioIndex = index;
  const s = index >= 0 ? _scenarios[index] : null;
  document.getElementById('scenario-modal-title').textContent = s ? 'Edit Scenario' : 'Add Scenario';

  const set = (id, val) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = val != null ? val : '';
  };
  set('scen-scenario_id', s?.scenario_id ?? '');
  set('scen-family', s?.family ?? 'transitory');
  set('scen-severity', s?.severity ?? 'adverse');
  set('scen-start_rule', s?.start_rule ?? 'forecast_start');
  set('scen-start_offset_months', s?.start_offset_months ?? 3);
  set('scen-start_date', s?.start_date ? s.start_date.substring(0,10) : '');
  set('scen-common_annual_shock_bp', s?.common_annual_shock_bp ?? 0);
  set('scen-foi_hicp_spread_shock_bp', s?.foi_hicp_spread_shock_bp ?? 0);
  set('scen-ramp_months', s?.ramp_months ?? 6);
  set('scen-hold_months', s?.hold_months ?? 12);
  set('scen-decay_half_life_months', s?.decay_half_life_months ?? 24);
  set('scen-long_run_hicp_target', s?.long_run_hicp_target ?? '');
  set('scen-long_run_foi_hicp_spread_bp', s?.long_run_foi_hicp_spread_bp ?? 0);
  set('scen-convergence_half_life_months', s?.convergence_half_life_months ?? 60);
  set('scen-rationale', s?.rationale ?? '');
  set('scen-calibration_basis', s?.calibration_basis ?? '');
  set('scen-review_frequency_months', s?.review_frequency_months ?? 12);

  updateScenarioFamilyFields();
  document.getElementById('scenario-modal-error').classList.add('hidden');
  document.getElementById('scenario-modal-overlay').classList.remove('hidden');
}

function updateScenarioFamilyFields() {
  const family = document.getElementById('scen-family').value;
  const isRegime = family === 'regime_shift';
  document.getElementById('scen-shock-fields').classList.toggle('hidden', isRegime);
  document.getElementById('scen-regime-fields').classList.toggle('hidden', !isRegime);
  // start_rule must be forecast_start for regime_shift
  const startRuleEl = document.getElementById('scen-start_rule');
  if (isRegime) {
    startRuleEl.value = 'forecast_start';
    startRuleEl.disabled = true;
  } else {
    startRuleEl.disabled = false;
  }
  updateStartDateVisibility();
}

function updateStartDateVisibility() {
  const rule = document.getElementById('scen-start_rule').value;
  document.getElementById('scen-start-date-group').classList.toggle('hidden', rule !== 'absolute');
}

document.getElementById('scen-family')?.addEventListener('change', updateScenarioFamilyFields);
document.getElementById('scen-start_rule')?.addEventListener('change', updateStartDateVisibility);

['scenario-modal-close', 'scenario-modal-cancel'].forEach(id => {
  document.getElementById(id)?.addEventListener('click', () => {
    document.getElementById('scenario-modal-overlay').classList.add('hidden');
  });
});

document.getElementById('scenario-modal-save')?.addEventListener('click', () => {
  const get = id => document.getElementById(id)?.value?.trim() ?? '';
  const family = get('scen-family');
  const isRegime = family === 'regime_shift';

  const scen = {
    scenario_id: get('scen-scenario_id'),
    family,
    severity: get('scen-severity'),
    start_rule: get('scen-start_rule'),
    start_offset_months: parseInt(get('scen-start_offset_months')) || 0,
    rationale: get('scen-rationale'),
    calibration_basis: get('scen-calibration_basis'),
    review_frequency_months: parseInt(get('scen-review_frequency_months')) || 12,
    model_version: 'inflation_stress_v2',
  };

  if (get('scen-start_date')) {
    scen.start_date = get('scen-start_date');
  }

  if (isRegime) {
    scen.long_run_hicp_target = parseFloat(get('scen-long_run_hicp_target'));
    scen.long_run_foi_hicp_spread_bp = parseFloat(get('scen-long_run_foi_hicp_spread_bp')) || 0;
    scen.convergence_half_life_months = parseFloat(get('scen-convergence_half_life_months')) || 60;
  } else {
    scen.common_annual_shock_bp = parseFloat(get('scen-common_annual_shock_bp')) || 0;
    scen.foi_hicp_spread_shock_bp = parseFloat(get('scen-foi_hicp_spread_shock_bp')) || 0;
    scen.ramp_months = parseInt(get('scen-ramp_months')) || 6;
    scen.hold_months = parseInt(get('scen-hold_months')) || 12;
    scen.decay_half_life_months = parseFloat(get('scen-decay_half_life_months')) || 24;
  }

  const errors = [];
  if (!scen.scenario_id) errors.push('Scenario ID is required');
  if (!scen.rationale) errors.push('Rationale is required');
  if (!scen.calibration_basis) errors.push('Calibration basis is required');

  if (errors.length) {
    const errEl = document.getElementById('scenario-modal-error');
    errEl.textContent = errors.join(' · ');
    errEl.classList.remove('hidden');
    return;
  }

  if (_editingScenarioIndex >= 0) {
    _scenarios[_editingScenarioIndex] = scen;
  } else {
    _scenarios.push(scen);
  }
  renderScenariosTable();
  populateScenarioPreviewSelect();
  document.getElementById('scenario-modal-overlay').classList.add('hidden');
});

document.getElementById('btn-add-scenario')?.addEventListener('click', () => openScenarioModal(-1));

/* ── Advanced panel toggle ── */
document.getElementById('advanced-header')?.addEventListener('click', () => {
  // Handled by initCollapsibles in app.js
});

/* ──────────────────────────────────────────────
   Scenario preview chart (against baseline)
   ────────────────────────────────────────────── */
function populateScenarioPreviewSelect() {
  const sel = document.getElementById('scenario-preview-select');
  if (!sel) return;
  sel.innerHTML = '<option value="">— select scenario —</option>' +
    _scenarios.map(s => `<option value="${s.scenario_id}">${escHtml(s.scenario_id)}</option>`).join('');
}

document.getElementById('scenario-preview-select')?.addEventListener('change', async e => {
  const id = e.target.value;
  if (!id) return;
  // Try to load the stress path from the server (only available post-run)
  try {
    const paths = await App.apiFetch('/api/stress/paths');
    if (paths[id] && paths.baseline) {
      renderScenarioPreviewChart(paths.baseline, paths[id], id);
    } else {
      // Show synthetic preview from local scenario config
      showSyntheticPreview(id);
    }
  } catch (_) {
    showSyntheticPreview(id);
  }
});

function showSyntheticPreview(scenarioId) {
  const scen = _scenarios.find(s => s.scenario_id === scenarioId);
  if (!scen) return;
  // Draw a simplified shock profile
  const months = 120;
  const profile = [];
  for (let m = 0; m < months; m++) {
    let shock = 0;
    const offset = scen.start_offset_months ?? 3;
    const t = m - offset;
    if (t >= 0 && scen.family !== 'regime_shift') {
      const ramp = scen.ramp_months || 6;
      const hold = scen.hold_months || 12;
      const decay = scen.decay_half_life_months || 24;
      const rampFactor = Math.min((t + 1) / ramp, 1);
      const afterHold = Math.max(t - ramp - hold + 1, 0);
      shock = rampFactor * Math.pow(0.5, afterHold / decay) * (scen.common_annual_shock_bp || 0) / 10000;
    }
    profile.push(shock);
  }
  const labels = Array.from({ length: months }, (_, i) => `M+${i}`);
  const ctx = document.getElementById('scenario-preview-chart')?.getContext('2d');
  if (!ctx) return;
  if (_scenarioPreviewChart) _scenarioPreviewChart.destroy();
  _scenarioPreviewChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: `${scenarioId} — shock profile (annualised)`,
        data: profile.map(v => +(v * 100).toFixed(3)),
        borderColor: App.CHART_COLORS.gold,
        backgroundColor: 'rgba(185,138,67,.1)',
        fill: true,
        borderWidth: 2,
        pointRadius: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'bottom' },
        title: { display: true, text: 'Shock profile preview (estimated — run pipeline for actual paths)' },
      },
      scales: {
        y: {
          title: { display: true, text: 'Additional annual inflation %' },
          ticks: { callback: v => v.toFixed(2) + '%' },
        },
        x: { ticks: { maxTicksLimit: 12 } },
      },
    },
  });
}

function renderScenarioPreviewChart(baselinePath, scenPath, scenId) {
  const ctx = document.getElementById('scenario-preview-chart')?.getContext('2d');
  if (!ctx) return;
  if (_scenarioPreviewChart) _scenarioPreviewChart.destroy();

  const step = 3;
  const bLabels = baselinePath.filter((_, i) => i % step === 0).map(d => d.date.substring(0, 7));
  const bFOI    = baselinePath.filter((_, i) => i % step === 0).map(d => d.foi_yoy != null ? +(d.foi_yoy * 100).toFixed(3) : null);
  const sFOI    = scenPath.filter((_, i) => i % step === 0).map(d => d.foi_yoy != null ? +(d.foi_yoy * 100).toFixed(3) : null);

  _scenarioPreviewChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: bLabels,
      datasets: [
        { label: 'Baseline FOI YoY %', data: bFOI, borderColor: App.CHART_COLORS.navy, borderWidth: 1.8, pointRadius: 0 },
        { label: `${scenId} FOI YoY %`, data: sFOI, borderColor: App.CHART_COLORS.gold, borderWidth: 1.8, pointRadius: 0, borderDash: [5,3] },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom' } },
      scales: {
        y: { ticks: { callback: v => v.toFixed(1) + '%' } },
        x: { ticks: { maxTicksLimit: 12, maxRotation: 0 } },
      },
    },
  });
}

/* ──────────────────────────────────────────────
   Utility
   ────────────────────────────────────────────── */
function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ──────────────────────────────────────────────
   Universe Filters
   ────────────────────────────────────────────── */
async function loadUniverseFilters() {
  try {
    const data = await App.apiFetch('/api/config/universe');
    const includeIlb = document.getElementById('universe-include-ilb');
    if (includeIlb) includeIlb.disabled = data.editable === false;
    
    // Ratings
    const rb = document.getElementById('universe-ratings-box');
    if (rb) {
      rb.innerHTML = '';
      data.ratings.forEach(r => {
        const lbl = document.createElement('label');
        lbl.className = 'flex items-center gap-4';
        lbl.style.fontSize = '12px';
        lbl.style.cursor = 'pointer';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = r;
        cb.checked = true;
        cb.disabled = data.editable === false;
        cb.className = 'filter-rating-cb';
        cb.addEventListener('change', () => {
          updateUniverseToggleLabels();
          document.dispatchEvent(new CustomEvent('universeFiltersChanged'));
        });
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(r));
        rb.appendChild(lbl);
      });
    }

    // Issuers
    const ib = document.getElementById('universe-issuers-box');
    if (ib) {
      ib.innerHTML = '';
      data.issuers.forEach(i => {
        const lbl = document.createElement('label');
        lbl.className = 'flex items-center gap-4';
        lbl.style.fontSize = '12px';
        lbl.style.cursor = 'pointer';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = i.code;
        cb.checked = true;
        cb.disabled = data.editable === false;
        cb.className = 'filter-issuer-cb';
        cb.addEventListener('change', () => {
          updateUniverseToggleLabels();
          document.dispatchEvent(new CustomEvent('universeFiltersChanged'));
        });
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(i.label));
        ib.appendChild(lbl);
      });
    }
  } catch (e) {
    console.error('Failed to load universe options', e);
    const rb = document.getElementById('universe-ratings-box');
    if (rb) rb.innerHTML = '<span class="text-xs" style="color:var(--red)">Error loading data</span>';
    const ib = document.getElementById('universe-issuers-box');
    if (ib) ib.innerHTML = '<span class="text-xs" style="color:var(--red)">Error loading data</span>';
  }
  updateUniverseToggleLabels();
  document.dispatchEvent(new CustomEvent('universeFiltersChanged'));
}

function collectUniverseFilters() {
  const selectedRatings = [...document.querySelectorAll('.filter-rating-cb:checked')]
    .map(cb => cb.value);
  const selectedIssuers = [...document.querySelectorAll('.filter-issuer-cb:checked')]
    .map(cb => cb.value);
  return {
    allowed_ratings: selectedRatings,
    allowed_issuers: selectedIssuers,
    include_inflation_linked: document.getElementById('universe-include-ilb')?.checked ?? true,
  };
}

function updateUniverseToggleLabels() {
  [
    ['.filter-rating-cb', 'btn-universe-ratings-toggle'],
    ['.filter-issuer-cb', 'btn-universe-issuers-toggle'],
  ].forEach(([selector, id]) => {
    const boxes = [...document.querySelectorAll(selector)];
    const button = document.getElementById(id);
    if (!button || !boxes.length) return;
    button.textContent = boxes.every(cb => cb.checked) ? 'Clear all' : 'Select all';
  });
}

window.collectUniverseFilters = collectUniverseFilters;

document.getElementById('btn-universe-ratings-toggle')?.addEventListener('click', e => {
  e.preventDefault();
  const cbs = document.querySelectorAll('.filter-rating-cb');
  if (Array.from(cbs).some(cb => cb.disabled)) return;
  const anyChecked = Array.from(cbs).some(cb => cb.checked);
  cbs.forEach(cb => cb.checked = !anyChecked);
  updateUniverseToggleLabels();
});

document.getElementById('btn-universe-issuers-toggle')?.addEventListener('click', e => {
  e.preventDefault();
  const cbs = document.querySelectorAll('.filter-issuer-cb');
  if (Array.from(cbs).some(cb => cb.disabled)) return;
  const anyChecked = Array.from(cbs).some(cb => cb.checked);
  cbs.forEach(cb => cb.checked = !anyChecked);
  updateUniverseToggleLabels();
});

/* ──────────────────────────────────────────────
   Init
   ────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', async () => {
  await loadUniverseFilters();
  loadParams();
  loadLiabilities();
  loadBaselineChart();
  loadScenarios();
});

document.addEventListener('tabActivated', e => {
  if (e.detail === 'configuration') {
    loadBaselineChart();
  }
});

// Expose for inline onclick handlers
window.openLiabilityModal = openLiabilityModal;
window.deleteLiability    = deleteLiability;
window.openScenarioModal  = openScenarioModal;
window.deleteScenario     = deleteScenario;

/* ══════════════════════════════════════════════
   JSON EDITOR MODULE
   Shared modal editor for liabilities.json and
   inflation_stress_scenarios.json
   ══════════════════════════════════════════════ */

// Current editor context: { mode: 'liabilities'|'scenarios', apiGet, apiPost, onApply }
let _jsonEditorCtx = null;

const _jsonEditorEls = {
  overlay:    () => document.getElementById('json-editor-overlay'),
  title:      () => document.getElementById('json-editor-title'),
  textarea:   () => document.getElementById('json-editor-textarea'),
  error:      () => document.getElementById('json-editor-error'),
  charCount:  () => document.getElementById('json-editor-char-count'),
  btnFormat:  () => document.getElementById('json-editor-format'),
  btnValidate:() => document.getElementById('json-editor-validate'),
  btnApply:   () => document.getElementById('json-editor-apply'),
  btnSave:    () => document.getElementById('json-editor-save-file'),
  btnCancel:  () => document.getElementById('json-editor-cancel'),
  btnClose:   () => document.getElementById('json-editor-close'),
};

function _jsonEditorOpen(ctx) {
  _jsonEditorCtx = ctx;
  _jsonEditorEls.title().textContent = ctx.title;
  const ta = _jsonEditorEls.textarea();
  ta.value = JSON.stringify(ctx.data, null, 2);
  _jsonEditorUpdateCharCount();
  _jsonEditorClearError();
  _jsonEditorEls.overlay().classList.remove('hidden');
  ta.focus();
}

function _jsonEditorClose() {
  _jsonEditorEls.overlay().classList.add('hidden');
  _jsonEditorCtx = null;
}

function _jsonEditorClearError() {
  const el = _jsonEditorEls.error();
  el.textContent = '';
  el.classList.add('hidden');
}

function _jsonEditorShowError(msg) {
  const el = _jsonEditorEls.error();
  el.textContent = msg;
  el.classList.remove('hidden');
}

function _jsonEditorUpdateCharCount() {
  const ta = _jsonEditorEls.textarea();
  const lines = (ta.value.match(/\n/g) || []).length + 1;
  _jsonEditorEls.charCount().textContent = `${ta.value.length} chars · ${lines} lines`;
}

function _jsonEditorParse() {
  try {
    return { ok: true, data: JSON.parse(_jsonEditorEls.textarea().value) };
  } catch (e) {
    return { ok: false, error: `JSON syntax error: ${e.message}` };
  }
}

// Format (pretty-print)
_jsonEditorEls.btnFormat()?.addEventListener('click', () => {
  const result = _jsonEditorParse();
  if (!result.ok) { _jsonEditorShowError(result.error); return; }
  _jsonEditorClearError();
  _jsonEditorEls.textarea().value = JSON.stringify(result.data, null, 2);
  _jsonEditorUpdateCharCount();
});

// Validate only (no apply)
_jsonEditorEls.btnValidate()?.addEventListener('click', async () => {
  const result = _jsonEditorParse();
  if (!result.ok) { _jsonEditorShowError(result.error); return; }
  // Server-side validation
  try {
    await App.apiFetch(_jsonEditorCtx.apiPost, {
      method: 'POST',
      body: JSON.stringify(result.data),
      headers: { 'X-Validate-Only': '1' },
    });
    _jsonEditorClearError();
    _jsonEditorShowError('✓ Valid — no errors found.');
    _jsonEditorEls.error().style.background = 'var(--pale-green)';
    _jsonEditorEls.error().style.color = 'var(--green)';
    _jsonEditorEls.error().style.borderColor = '#A5D6B8';
  } catch (e) {
    _jsonEditorEls.error().style.background = '';
    _jsonEditorEls.error().style.color = '';
    _jsonEditorEls.error().style.borderColor = '';
    _jsonEditorShowError('Server validation error: ' + e.message);
  }
});

// Apply to in-memory state (doesn't write file yet)
_jsonEditorEls.btnApply()?.addEventListener('click', () => {
  const result = _jsonEditorParse();
  if (!result.ok) { _jsonEditorShowError(result.error); return; }
  _jsonEditorClearError();
  if (_jsonEditorCtx?.onApply) {
    const applyErr = _jsonEditorCtx.onApply(result.data);
    if (applyErr) { _jsonEditorShowError(applyErr); return; }
  }
  _jsonEditorClose();
  showConfigAlert('info', `${_jsonEditorCtx?.title || 'JSON'} applied. Click "Save Configuration" to persist.`);
});

// Save directly to file via API
_jsonEditorEls.btnSave()?.addEventListener('click', async () => {
  const result = _jsonEditorParse();
  if (!result.ok) { _jsonEditorShowError(result.error); return; }
  try {
    const resp = await App.apiFetch(_jsonEditorCtx.apiPost, {
      method: 'POST',
      body: JSON.stringify(result.data),
    });
    // Also apply to in-memory state
    if (_jsonEditorCtx?.onApply) _jsonEditorCtx.onApply(result.data);
    _jsonEditorClose();
    showConfigAlert('info', `✓ Saved ${resp.saved ?? result.data.length} item(s) to file.`);
  } catch (e) {
    _jsonEditorShowError('Save failed: ' + e.message);
  }
});

// Close handlers
_jsonEditorEls.btnClose()?.addEventListener('click', _jsonEditorClose);
_jsonEditorEls.btnCancel()?.addEventListener('click', _jsonEditorClose);
_jsonEditorEls.overlay()?.addEventListener('click', e => {
  if (e.target === _jsonEditorEls.overlay()) _jsonEditorClose();
});

// Tab key → indent with 2 spaces
_jsonEditorEls.textarea()?.addEventListener('keydown', e => {
  if (e.key === 'Tab') {
    e.preventDefault();
    const ta = e.target;
    const start = ta.selectionStart;
    const end   = ta.selectionEnd;
    ta.value = ta.value.substring(0, start) + '  ' + ta.value.substring(end);
    ta.selectionStart = ta.selectionEnd = start + 2;
  }
});

// Live char counter
_jsonEditorEls.textarea()?.addEventListener('input', _jsonEditorUpdateCharCount);

// Keyboard shortcut: Escape to close
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !_jsonEditorEls.overlay()?.classList.contains('hidden')) {
    _jsonEditorClose();
  }
});

// ── Open for Liabilities ──
document.getElementById('btn-edit-liabilities-json')?.addEventListener('click', () => {
  _jsonEditorOpen({
    title: 'Edit liabilities.json',
    data: _liabilities,
    apiPost: '/api/config/liabilities',
    onApply(data) {
      if (!Array.isArray(data)) return 'Root must be a JSON array.';
      _liabilities = data;
      renderLiabilitiesTable();
      return null;
    },
  });
});

// ── Open for Stress Scenarios ──
document.getElementById('btn-edit-scenarios-json')?.addEventListener('click', () => {
  _jsonEditorOpen({
    title: 'Edit inflation_stress_scenarios.json',
    data: _scenarios,
    apiPost: '/api/config/inflation/scenarios',
    onApply(data) {
      if (!Array.isArray(data)) return 'Root must be a JSON array.';
      _scenarios = data;
      renderScenariosTable();
      populateScenarioPreviewSelect();
      return null;
    },
  });
});
