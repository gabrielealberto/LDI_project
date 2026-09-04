"""Load the versioned classification of inflation-linked bond ISINs."""

import json
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config" / "inflation_linked_bonds.json"


def load_inflation_linked_bond_types(path=CONFIG_PATH):
    """Return the configured mapping from ISIN to inflation-linked payoff type."""
    with Path(path).open(encoding="utf-8") as file:
        bonds = json.load(file)
    if not isinstance(bonds, dict) or any(
        not isinstance(details, dict) or not details.get("type") for details in bonds.values()
    ):
        raise ValueError("Inflation-linked bond configuration must map each ISIN to a payoff type.")
    return {str(isincode): str(details["type"]) for isincode, details in bonds.items()}
