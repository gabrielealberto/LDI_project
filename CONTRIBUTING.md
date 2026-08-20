# Contributing

Contributions are welcome when they preserve the project’s reproducible,
cash-flow-first design.

## Before opening a pull request

1. Create a focused branch and keep unrelated formatting or generated files out
   of the change.
2. Add or update regression tests for changed behavior.
3. Run the required checks:

   ```powershell
   python -m unittest discover -s tests -v
   ruff format . --check
   ruff check .
   ```

4. Document changes to public functions, configuration fields, input data, or
   generated output in the README and CHANGELOG.

## Guidelines

- Keep existing script entry points and public function signatures compatible.
- Treat solver constraints and numerical outputs as regression-sensitive.
- Prefer vectorized NumPy/Pandas operations when they preserve numerical results.
- Do not commit files from `data/raw/` or `data/processed/`.
- Keep pull requests small, tested, and clearly explained.
