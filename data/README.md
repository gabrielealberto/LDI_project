# Data directory

`data/config/liabilities.json` is the versioned scenario configuration required
by the liability engine.

The remaining directories are local, reproducible working areas:

| Directory | Created by | Contents |
| --- | --- | --- |
| `raw/` | downloader scripts | Source bond and ECB curve datasets. |
| `processed/` | cleaner, cash-flow, optimizer, and plotting scripts | Cleaned data, Parquet matrices, Excel workbooks, and PNG charts. |

Run `python main.py` to refresh all monthly LDI inputs automatically, or use
the individual commands in the root README for a stage-by-stage refresh. Do not
commit market-data downloads, generated reports, or charts: they can be stale,
may be subject to source-data terms, and make reviews unnecessarily large.

No ignored file is required to bootstrap the project. A fresh clone needs only
`config/liabilities.json`, the source code, the pinned dependencies, and
internet access for the canonical workflow.
