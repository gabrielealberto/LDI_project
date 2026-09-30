import unittest
from unittest.mock import patch

import plot


class PlotEntrypointTests(unittest.TestCase):
    def test_plots_are_generated_only_by_the_explicit_entrypoint(self):
        result = {"inflation_stress": {"summary": "stress"}}
        with (
            patch.object(plot, "run_pipeline", return_value=result) as pipeline,
            patch.object(plot, "plot_portfolio", return_value=[]) as plots,
        ):
            output = plot.main()

        self.assertEqual(output, [])
        pipeline.assert_called_once_with()
        plots.assert_called_once_with(result, stress_report=result["inflation_stress"])


if __name__ == "__main__":
    unittest.main()
