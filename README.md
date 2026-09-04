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
- Exports an Excel audit trail, a three-chart analytical dashboard, and a
  square presentation graphic designed for LinkedIn.
- Defines liabilities in JSON, without editing Python source.

## Repository structure

| Path | Purpose |
| --- | --- |
| `data/config/` | Versioned liability configuration. |
| `data/raw/` | Local source-data cache, ignored by Git. |
| `data/processed/` | Local clean data, reports, and charts, ignored by Git. |
| `scripts/downloaders/` | Market-data download commands. |
| `scripts/cleaners/` | Investable-universe preparation command. |
| `ldi_engine.py` | Official monthly integer cash-flow-matching optimizer. |
| `main.py` | Canonical workflow: prepares inputs, solves, and exports the result. |
| `pipeline.py` | Refreshes all market inputs for the official workflow. |
| `bond_cash_flow_creator.py` | Cash-flow generation and yield validation. |
| `future_liabilities.py` | Liability configuration and aggregation. |
| `plots.py` | PNG dashboard generation for the monthly LDI result. |
| `utils.py` | Shared project paths, mandate defaults, and numerical helpers. |
| `tests/` | Fast, deterministic regression tests. |

## Requirements

- Python 3.11 (the version exercised by CI and the pinned dependencies).
- Internet access: the canonical workflow refreshes all market data on every run.

Install the pinned runtime dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For linting and development checks:

```powershell
python -m pip install -r requirements-dev.txt
```

## Quick start

Run the official workflow from the repository root:

```powershell
python main.py
```

`main.py` refreshes every prerequisite on each run, in order: bond download,
ECB yield-curve download, bond cleaning, and cash-flow generation. It does not
silently reuse an existing market-data cache. The result is written to
`data/processed/ldi_optimization.xlsx`.

Generate the dashboard after a successful optimization:

```powershell
python plots.py
```

The command also creates `data/processed/plots/04_linkedin_summary.png`, a
high-resolution 1:1 project summary suitable for a LinkedIn post.

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

The PNG dashboard is a separate, reproducible step run with `python plots.py`.

## Configure liabilities

Edit [data/config/liabilities.json](data/config/liabilities.json). Each object
requires `name`, `category`, `start_date`, `end_date`, `initial_cashflow`,
`frequency`, and `inflation_rate`. Dates use `YYYY-MM-DD`.

`frequency` supports `annual` and `every_n_years`; the latter requires
`interval_years`.

## Use the optimizer in Python

```python
import pandas as pd

from ldi_engine import load_bond_inputs, optimize_cashflow_matching

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

## Main controls

| Control | Default |
| --- | ---: |
| Bond lot size | EUR 1,000 |
| Maximum nominal per ISIN | EUR 20,000 |
| Minimum daily nominal volume | EUR 20,000 |
| Broker commission | 0.19%, min EUR 2.95, max EUR 19 per order |
| Coupon tax rate | 12.5% |
| Maximum issuer weight | 40% |
| Maximum positions | 30 |

Use the public function arguments—not source edits—to model a different
mandate. See [ldi_engine.py](ldi_engine.py) for the complete signature.

The current strategy buys bonds and holds them to redemption. Purchase
commissions are therefore charged immediately. A redemption is not treated as
a sale; `sale_commission_eur` remains zero unless an explicit sale workflow is
introduced.

## Manual data refresh

```powershell
python scripts/downloaders/bond_downloader.py
python scripts/downloaders/yield_curve_downloader.py
python scripts/cleaners/bond_cleaner.py
python bond_cash_flow_creator.py
```

The full `main.py` refresh also updates the ECB curve used by the present-value
utilities in `future_liabilities.py`.

## Reproducible data policy

A fresh clone does not need any ignored data file. `python main.py` downloads
the bond and ECB inputs, rebuilds every Parquet dataset, solves the portfolio,
and recreates the Excel report. `python plots.py` then recreates the PNG charts.

The versioned inputs required to reproduce the project are the Python source,
dependency files, and `data/config/liabilities.json`. Files under `data/raw/`
and generated files under `data/processed/` must remain unversioned.

## Development

```powershell
python -m unittest discover -s tests -v
ruff format . --check
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## License

Released under the [MIT License](LICENSE).
