# LDI Portfolio Engine

An end-to-end Python workflow for building a euro-denominated bond portfolio
that matches a schedule of future liabilities on a monthly basis. It downloads
market data, prepares an investable sovereign-bond universe, creates per-lot
cash flows, and solves an integer LDI portfolio.

It is intended for research, prototyping, and transparent cash-flow analysis.
It is not investment, tax, or legal advice.

## Features

- Downloads and cleans a sovereign-bond universe with traded prices and a
  configurable minimum daily volume.
- Generates gross and coupon-tax-adjusted cash flows for EUR 1,000 bond lots.
- Matches monthly liabilities with integer lots, issuer and position limits.
- Solves the final MILP on the complete eligible universe without candidate pruning.
- Includes the broker purchase commission in selection, cost, ROI, and XIRR.
- Carries earlier bond cash flows into later monthly liabilities.
- Reports any external funding need explicitly as `uncovered_eur`.
- Exports an Excel audit trail, a complete analytical dashboard, and a
  square presentation graphic designed for LinkedIn.
- Defines liabilities in JSON, without editing Python source.
- Uses one dynamic, coherent FOI/HICP baseline for inflation-linked assets and
  Italian inflation-indexed liabilities.

## Repository structure

| Path | Purpose |
| --- | --- |
| `data/config/` | Versioned liability, inflation-linked, and contractual cash-flow configuration. |
| `data/raw/` | Local source-data cache, ignored by Git. |
| `data/processed/` | Local clean data, reports, and charts, ignored by Git. |
| `scripts/downloaders/` | Market-data download commands. |
| `scripts/cleaners/` | Investable-universe preparation command. |
| `core/ldi_engine.py` | Official monthly integer cash-flow-matching optimizer. |
| `main.py` | Canonical workflow: prepares inputs, solves, and exports the result. |
| `core/pipeline.py` | Refreshes all market inputs for the official workflow. |
| `core/bond_cash_flow_creator.py` | Cash-flow generation and yield validation. |
| `core/future_liabilities.py` | Liability configuration and aggregation. |
| `core/inflation_baseline.py` | Builds the dynamic, coherent FOI/HICP baseline. |
| `core/inflation_linked_cashflows.py` | Contractual FOI/HICP baseline cash flows for BTP€i, BTP Italia, and BTP Italia Sì. |
| `core/plots.py` | PNG dashboard generation for the monthly LDI result. |
| `core/utils.py` | Shared project paths, mandate defaults, and numerical helpers. |
| `tests/` | Fast, deterministic regression tests. |

## Requirements and installation

- Python 3.11 (the version exercised by CI and the pinned dependencies).
- Internet access for the canonical refresh: the workflow reads official ISTAT,
  Eurostat, ECB, and market-data endpoints.
- The commands below assume the repository root as the current directory.

The pinned runtime dependencies in [requirements.txt](requirements.txt) cover
the optimizer, Parquet I/O, HTTP downloaders, plots, Excel export, and official
FOI workbook parsing. Create an isolated environment and install them with:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install the additional development dependency (`ruff`) with:

```powershell
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` includes the runtime file with `-r requirements.txt`;
installing it is sufficient for CI and local development.

## Quick start

Open the repository in Visual Studio, select [main.py](main.py) as the startup
file, then use **Run** (or `F5`). `main.py` is the sole application entry
point: internal modules have no standalone CLI execution path.

`main.py` refreshes every prerequisite on each run in this order:

1. Download bond-market data.
2. Download the ECB Svensson yield-curve parameters.
3. Clean the investable bond universe.
4. Reuse a valid FOI/HICP history only when it is monthly, gap-free, positive,
   and no more than two months old; otherwise download it again.
5. Build the single FOI/HICP inflation baseline.
6. Generate and validate nominal and inflation-linked bond cash flows.
7. Solve the integer cash-flow-matching problem and export the workbook.
8. Replay the frozen portfolio under each configured inflation shock, save the
   audit files, and generate the dashboard.

The Excel result is written to `data/processed/ldi_optimization.xlsx`.
Generated Parquet inputs and reports are local artifacts and are ignored by Git.

The run also creates `data/processed/plots/00_inflation_baseline.png`, a
two-panel chart of the sole FOI/HICP baseline, and
`data/processed/plots/04_linkedin_summary.png`, a high-resolution 1:1 project
summary suitable for a LinkedIn post.

The complete dashboard also contains cash-account, coverage, and allocation
charts when the portfolio is non-empty. It additionally creates
`05_inflation_stress_rates.png` and `06_inflation_stress_funding.png`, showing
the FOI/HICP rate paths and frozen-portfolio funding/liquidity effects. All
charts are written under `data/processed/plots/`.

## Workflow

```text
Bond market data
       |
       v
Universe cleaning
       |
       v
Bond cash-flow generation ----+
                              |
Liability JSON --> Schedule --+--> Monthly LDI engine --> Excel audit trail

ECB curve ------------------------> PV and duration utilities
```

The FOI/HICP histories feed the baseline, which is shared by indexed liabilities
and inflation-linked bond cash flows. The PNG dashboard and stress analysis
are generated by the same Visual Studio run of `main.py`.

## Configure liabilities

Edit [data/config/liabilities.json](data/config/liabilities.json). Each object
requires `name`, `category`, `start_date`, `end_date`, `initial_cashflow`,
`frequency`, and `inflation_rate`. Dates use `YYYY-MM-DD`.

`frequency` supports `annual` and `every_n_years`; the latter requires
`interval_years`.

Optional `indexation` fields support `index_id`, `base_reference_date`, and
`observation_lag_months`. Configured FOI liabilities use the same baseline
provider as FOI-indexed assets, so both sides of the optimization remain
coherent.

## Inflation baseline

The workflow uses one dynamic FOI/HICP baseline; no inflation scenario can be
selected for optimisation. It starts from the latest official index levels,
combines robust recent inflation observations with monthly seasonality, and
converges gradually towards the ECB's 2% medium-term target. FOI is linked to
HICP through a temporary, mean-reverting Italy/euro-area inflation spread; both
indices converge to the common long-term target. The same
baseline drives HICP-indexed BTP€i, FOI-indexed BTP Italia instruments, and
liabilities carrying an `indexation` block. Prices and valuation dates always
remain those in the cleaned bond-market parquet.

The refresh pipeline downloads FOI/HICP from their official dynamic endpoints,
rebuilds the baseline through the later of the liability horizon and the longest
inflation-linked maturity, and then creates native `isincode/date/l1/l2/l3`
flows for the optimizer.

The generated baseline is stored at
`data/processed/inflation_baseline.parquet`. It contains monthly `date`,
`foi_xt_it`, `hicp_xt_ea`, derived `foi_yoy` and `hicp_yoy`, the FOI/HICP log
spread, and a model version. Historical observations remain in
`data/foi_xt_it.parquet` and `data/hicp_xt_ea.parquet`.

## Inflation stress testing

The baseline remains the only path used for the optimisation. Deterministic
inflation stresses are a separate, post-optimisation replay of the frozen
portfolio; they never change the portfolio lots or silently select a different
path for the main workflow.

Stress definitions are versioned in
`data/config/inflation_stress_scenarios.json` and have no probabilities. The
library deliberately contains three families:

- `transitory`: a short liquidity and timing stress with a linear ramp, brief
  hold, and explicit decay;
- `persistent`: the same mathematically coherent overlay with a multi-year
  hold and slow decay, used for structural cash-flow risk;
- `regime_shift`: a separate strategic path built from the official history
  with a new long-run HICP and optional FOI/HICP spread anchor. It is not an
  endlessly compounding temporary shock.

Every scenario records severity, a forecast-relative start rule, rationale,
calibration basis, review frequency, and model version. Transitory and
persistent shocks are applied to monthly log changes rather than directly to
index levels, preserving continuity, positivity, and baseline seasonality.
Regime shifts retain the same history and seasonal method but re-anchor the
long-run inflation assumption. The configured magnitudes are rounded
management stresses informed by official FOI/HICP history; they are explicitly
not probability forecasts.

The main workflow writes `data/processed/inflation_stress_summary.parquet` and
`data/processed/inflation_stress_monthly.parquet`. The summary includes total
liabilities and asset cash flows, external funding, funding in the first 36
months after the resolved shock start, minimum pre-funding cash, first and total
deficit months, deltas versus baseline, governance metadata, and a fingerprint
of the baseline used. The monthly file is the audit trail for every scenario.

The module is intentionally limited to inflation cash-flow risk. Yield-curve,
market-price, credit, and liquidity shocks require a separate repricing model
and are not mixed into this analysis.

## Use the optimizer in Python

```python
import pandas as pd

from core.ldi_engine import load_bond_inputs, optimize_cashflow_matching

target = pd.DataFrame(
    {"date": ["2030-01-01", "2031-01-01"], "cashflow": [10_000, 10_000]}
)
matrix, bonds = load_bond_inputs()
result = optimize_cashflow_matching(target, matrix, bonds)

print(result["portfolio"])
print(result["cashflow_match"])
```

The engine purchases non-negative integer EUR 1,000 lots. Bond cash flows
received before a liability can cover later monthly liabilities. Any remaining
funding requirement is shown in `uncovered_eur`; it is never hidden.

The optimizer consumes the validated monthly matrix at
`data/processed/bond_cashflow_matrix.parquet` and the detailed flows at
`data/processed/bond_cashflows.parquet`. These files are rebuilt by the
pipeline and should not be edited manually.

## Main controls

| Control | Default |
| --- | ---: |
| Bond lot size | EUR 1,000 |
| Maximum nominal per ISIN | EUR 20,000 |
| Minimum daily nominal volume | EUR 1,000 |
| Broker commission | 0.19%, min EUR 2.95, max EUR 19 per order |
| Coupon tax rate | 12.5% |
| Maximum issuer weight | 40% |
| Maximum positions | 30 |

Use the public function arguments—not source edits—to model a different
mandate. See [core/ldi_engine.py](core/ldi_engine.py) for the complete signature.

The current strategy buys bonds and holds them to redemption. Purchase
commissions are therefore charged immediately. A redemption is not treated as
a sale; `sale_commission_eur` remains zero unless an explicit sale workflow is
introduced.

## Execution model

There are no stage-by-stage CLI commands. Use Visual Studio Run on `main.py`;
it refreshes the full dependency chain, including the ECB curve used by the
present-value utilities in `core/future_liabilities.py`. The root-level
contractual-cash-flow helpers expose importable maintenance functions only; the
validated JSON overrides under `data/config/` are sufficient for normal runs.

## Reproducible data policy

A fresh clone does not need any generated data file. Running `main.py` from
Visual Studio downloads
the bond and ECB inputs, obtains or refreshes the official index histories,
rebuilds the baseline and every derived Parquet dataset, solves the portfolio,
and recreates the Excel report, stress audit trail, and PNG charts.

The versioned inputs required to reproduce the project are the Python source,
dependency files, `data/config/`, and the two official rebased index histories
under `data/`. Files under `data/raw/` and generated files under
`data/processed/` must remain unversioned.

## Development

```powershell
python -m unittest discover -s tests -v
ruff format . --check
ruff check .
```

The tests are deterministic and use synthetic inputs or local fixtures; the
canonical end-to-end workflow additionally requires network access and current
official data. There is no separate contribution guide or changelog in this
repository; the source files and test suite are the authoritative references.

## Repository status

This repository is a research and prototyping workflow. No separate license
file is currently included; use and redistribution should therefore follow the
terms established by the project owner.
