# Changelog

All notable changes to this project are documented in this file.

## Unreleased

### Changed

- The optimizer now solves the complete eligible universe, applies issuer and
  position constraints even when external funding is required, and enforces a
  single-issuer concentration limit correctly.
- Purchase commissions are represented exactly in the MILP and included in
  acquisition cost, ROI, XIRR, concentration, and exported reporting.
- Weighted average maturity is now weighted by each complete position cost.
- The canonical workflow now refreshes bond data, the ECB curve, the cleaned
  universe, and generated cash flows on every run.
- The sovereign-bond cleaner now requires a traded price and at least EUR
  20,000 of reported daily nominal volume by default.
- Productionized repository documentation, contribution guidance, data policy,
  dependency metadata, and GitHub automation.
- Centralized shared paths, mandate defaults, and cash-flow helpers.
- Made `ldi_engine.py` the official monthly LDI engine and removed the
  exact-date optimizer. The command-line workflow and charts now use it.
