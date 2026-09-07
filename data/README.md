# Data directory

`config/` contains the versioned inputs required by the LDI workflow:

- `liabilities.json`: liability schedule and FOI indexation;
- `inflation_linked_bonds.json`: contractual terms for inflation-linked BTPs;
- `standard_cashflows.json` and `step_up_down_cashflows.json`: validated
  contractual overrides for non-standard nominal bonds. Their root-level
  helpers are importable maintenance functions, not executable applications.
- `inflation_stress_scenarios.json`: deterministic, versioned FOI/HICP stress
  definitions for frozen-portfolio post-optimisation analysis. They are not
  inputs to the baseline optimisation.

`foi_xt_it.parquet` and `hicp_xt_ea.parquet` are the rebased official monthly
histories used to construct the dynamic inflation baseline. They are refreshed
by the dedicated downloaders when absent, malformed, or older than the
pipeline's two-month cache window. `inflation_baseline.parquet` is generated
from both histories and is consumed by indexed liabilities and
inflation-linked bond cash flows.

The remaining directories are local, reproducible working areas:

| Directory | Created by | Contents |
| --- | --- | --- |
| `raw/` | main workflow | Source bond and ECB curve datasets. |
| `processed/` | main workflow | Cleaned data, baseline and cash-flow Parquet files, stress audit files, Excel workbooks, and PNG charts. |

Run `main.py` through Visual Studio to refresh all monthly LDI inputs and
generate the stress analysis automatically. Do not commit market-data downloads,
generated reports, or charts: they can be stale, may be subject to source-data
terms, and make reviews unnecessarily large.

No generated file under `data/processed/` or `data/raw/` is required to
bootstrap the project. A fresh clone needs the versioned `config/` files, the
two official rebased index histories under `data/`, source code, pinned
dependencies, and internet access for the canonical workflow.
