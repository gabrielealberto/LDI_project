import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import core.pipeline as pipeline
from scripts.downloaders.bond_downloader import BondSnapshot
from scripts.downloaders.yield_curve_downloader import CurveSnapshot


class PipelineRefreshTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "data").mkdir()
        self.raw_fd = root / "bonds_fd.parquet"
        self.raw_bi = root / "bonds_bi.parquet"
        self.curve = root / "ecb_svensson.parquet"
        self.clean_fd = root / "fd_clean.parquet"
        self.clean_bi = root / "bi_clean.parquet"
        self.cashflows = root / "bond_cashflows.parquet"
        self.matrix = root / "bond_cashflow_matrix.parquet"
        self.foi = root / "data" / "foi_xt_it.parquet"
        self.hicp = root / "data" / "hicp_xt_ea.parquet"
        self.baseline = root / "inflation_baseline.parquet"

    def _patch_paths(self):
        return patch.multiple(
            pipeline.bond_cleaner,
            FD_INPUT=self.raw_fd,
            BI_INPUT=self.raw_bi,
            FD_OUTPUT=self.clean_fd,
            BI_OUTPUT=self.clean_bi,
        )

    def _run_with_mocks(self):
        root = Path(self.directory.name)
        actions = []
        bond_downloader = Mock()

        def create_bond_snapshot():
            actions.append("bonds")
            self.raw_fd.touch()
            self.raw_bi.touch()
            return BondSnapshot(date.today(), self.raw_fd, self.raw_bi, True, False)

        bond_downloader.run.side_effect = create_bond_snapshot
        curve_downloader = Mock()

        def create_curve_snapshot():
            actions.append("curve")
            self.curve.touch()
            return CurveSnapshot(date.today(), self.curve, True, False)

        curve_downloader.run.side_effect = create_curve_snapshot
        cleaner = Mock(
            side_effect=lambda *_args: (
                actions.append("clean"),
                self.clean_fd.touch(),
                self.clean_bi.touch(),
            )
        )
        cashflow_builder = Mock(
            side_effect=lambda: (
                actions.append("cashflows"),
                self.cashflows.touch(),
                self.matrix.touch(),
            )
        )
        foi_downloader = Mock(
            side_effect=lambda: (actions.append("foi"), self.foi.touch())
        )
        hicp_downloader = Mock(
            side_effect=lambda: (actions.append("hicp"), self.hicp.touch())
        )
        baseline_builder = Mock(
            side_effect=lambda: (actions.append("baseline"), self.baseline.touch())
        )

        with (
            patch.multiple(
                pipeline,
                CASHFLOWS_PATH=self.cashflows,
                BOND_CASHFLOW_MATRIX_PATH=self.matrix,
                INFLATION_BASELINE_PATH=self.baseline,
                PROJECT_ROOT=root,
            ),
            self._patch_paths(),
            patch.object(pipeline, "BondDownloader", return_value=bond_downloader),
            patch.object(pipeline, "ECBDownloader", return_value=curve_downloader),
            patch.object(pipeline.bond_cleaner, "run", cleaner),
            patch.object(pipeline, "download_foi", foi_downloader),
            patch.object(pipeline, "download_hicp", hicp_downloader),
            patch.object(pipeline, "build_inflation_baseline", baseline_builder),
            patch.object(pipeline, "build_cashflow_outputs", cashflow_builder),
        ):
            completed = pipeline.refresh_ldi_inputs()

        return actions, completed

    def test_refreshes_every_stage_in_dependency_order(self):
        actions, completed = self._run_with_mocks()

        self.assertEqual(
            actions, ["bonds", "curve", "clean", "foi", "hicp", "baseline", "cashflows"]
        )
        self.assertEqual(
            completed,
            [
                "bond data",
                "yield curve",
                "investable universe",
                "inflation indices",
                "inflation baseline",
                "bond cash flows",
            ],
        )

    def test_refreshes_even_when_all_outputs_already_exist(self):
        for path in (
            self.raw_fd,
            self.raw_bi,
            self.curve,
            self.clean_fd,
            self.clean_bi,
            self.cashflows,
            self.matrix,
            self.foi,
            self.hicp,
            self.baseline,
        ):
            path.touch()

        actions, _ = self._run_with_mocks()

        self.assertEqual(
            actions, ["bonds", "curve", "clean", "foi", "hicp", "baseline", "cashflows"]
        )

    def test_backward_compatible_entry_point_performs_full_refresh(self):
        with patch.object(
            pipeline, "refresh_ldi_inputs", return_value=["refreshed"]
        ) as refresh:
            completed = pipeline.ensure_ldi_inputs()

        self.assertEqual(completed, ["refreshed"])
        refresh.assert_called_once_with()
