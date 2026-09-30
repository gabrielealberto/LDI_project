"""Explicit entry point for generating plots after a complete LDI run."""

from core.plots import plot_portfolio
from main import main as run_pipeline


def main():
    """Run the model and generate charts only from this explicit entry point."""
    result = run_pipeline()
    outputs = plot_portfolio(result, stress_report=result.get("inflation_stress"))
    for path in outputs:
        print(f"Chart exported: {path}")
    return outputs


if __name__ == "__main__":
    main()
