"""Refresh every local input required by the monthly LDI engine."""

from bond_cash_flow_creator import build_cashflow_outputs
from scripts.cleaners import bond_cleaner
from scripts.downloaders.bond_downloader import BondDownloader
from scripts.downloaders.yield_curve_downloader import ECBDownloader
from utils import BOND_CASHFLOWS_PATH, BOND_CASHFLOW_MATRIX_PATH, CURVE_PATH


CASHFLOWS_PATH = BOND_CASHFLOWS_PATH


def _missing(paths):
    return [path for path in paths if not path.exists()]


def _require_outputs(paths, stage):
    missing = _missing(paths)
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise RuntimeError(f"Pipeline stage {stage} did not produce: {names}")


def refresh_ldi_inputs():
    """Download fresh market data and rebuild every derived LDI input."""
    completed = []
    raw_bonds = [bond_cleaner.FD_INPUT, bond_cleaner.BI_INPUT]
    clean_bonds = [bond_cleaner.FD_OUTPUT, bond_cleaner.BI_OUTPUT]

    BondDownloader().run()
    _require_outputs(raw_bonds, "bond download")
    completed.append("bond data")

    ECBDownloader().run()
    _require_outputs([CURVE_PATH], "yield-curve download")
    completed.append("yield curve")

    bond_cleaner.run()
    _require_outputs(clean_bonds, "bond cleaning")
    completed.append("investable universe")

    build_cashflow_outputs()
    _require_outputs(
        [CASHFLOWS_PATH, BOND_CASHFLOW_MATRIX_PATH],
        "bond cash-flow generation",
    )
    completed.append("bond cash flows")

    return completed


def ensure_ldi_inputs():
    """Backward-compatible name for the full input refresh."""
    return refresh_ldi_inputs()
