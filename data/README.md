# Data directory

`config/` contains the versioned inputs required by the LDI workflow:

- `liabilities.json`: liability schedule and FOI indexation;
- `inflation_linked_bonds.json`: contractual terms for inflation-linked BTPs;
- `standard_cashflows.json` and `step_up_down_cashflows.json`: validated
  contractual overrides for non-standard nominal bonds.

`foi_xt_it.parquet` and `hicp_xt_ea.parquet` are the rebased official monthly
histories used to construct scenarios. They are refreshed by the dedicated
downloaders.

The remaining directories are local, reproducible working areas:

| Directory | Created by | Contents |
| --- | --- | --- |
| `raw/` | downloader scripts | Source bond and ECB curve datasets. |
| `processed/` | cleaner, cash-flow, optimizer, and plotting scripts | Cleaned data, Parquet matrices, Excel workbooks, and PNG charts. |

Run `python main.py` to refresh all monthly LDI inputs automatically, or use
the individual commands in the root README for a stage-by-stage refresh. Do not
commit market-data downloads, generated reports, or charts: they can be stale,
may be subject to source-data terms, and make reviews unnecessarily large.

No ignored file is required to bootstrap the project. A fresh clone needs the
versioned `config/` and index-history files, source code, pinned dependencies,
and internet access for the canonical workflow.
