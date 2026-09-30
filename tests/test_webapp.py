import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from Webapp.app import create_app
from Webapp.repository import RunRepository
from Webapp import server
from Webapp.service import RunService
from Webapp.worker import _persist_result, run as run_worker


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.storage = Path(self.temporary_directory.name)
        self.app = create_app(
            {"TESTING": True, "WEBAPP_STORAGE_DIR": str(self.storage)}
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_health_and_static_shell_are_available(self):
        self.assertEqual(self.client.get("/api/health").get_json(), {"status": "ok"})
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"LDI Portfolio Manager", response.data)

    def test_parameters_are_editable_validated_and_persisted(self):
        parameters = self.client.get("/api/config/parameters").get_json()
        self.assertTrue(parameters["editable"])
        response = self.client.post(
            "/api/config/parameters", json={"max_positions": 12}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get("/api/config/parameters").get_json()["max_positions"],
            12,
        )
        invalid = self.client.post(
            "/api/config/parameters", json={"max_issuer_weight": 2}
        )
        self.assertEqual(invalid.status_code, 400)

    def test_corrupt_json_configuration_returns_a_structured_error(self):
        import Webapp.api.routes_config as routes_config

        liabilities = self.storage / "liabilities.json"
        scenarios = self.storage / "inflation_stress_scenarios.json"
        liabilities.write_text("{corrupt", encoding="utf-8")
        scenarios.write_text("[corrupt", encoding="utf-8")
        with (
            patch.object(routes_config, "LIABILITIES_PATH", liabilities),
            patch.object(routes_config, "SCENARIOS_PATH", scenarios),
        ):
            for endpoint in (
                "/api/config/liabilities",
                "/api/config/inflation/scenarios",
            ):
                with self.subTest(endpoint=endpoint):
                    response = self.client.get(endpoint)
                    self.assertEqual(response.status_code, 500)
                    self.assertIn("Invalid", response.get_json()["error"])

    def test_universe_filters_are_available_for_runs(self):
        response = self.client.get("/api/config/universe")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["editable"])
        self.assertIn("BBB-", payload["ratings"])
        self.assertLess(payload["ratings"].index("BBB-"), len(payload["ratings"]))
        self.assertIn({"code": "GOV_RO", "label": "Romania"}, payload["issuers"])
        self.assertEqual(
            payload["ratings"],
            sorted(
                payload["ratings"],
                key=lambda r: (
                    (
                        "AAA",
                        "AA+",
                        "AA",
                        "AA-",
                        "A+",
                        "A",
                        "A-",
                        "BBB+",
                        "BBB",
                        "BBB-",
                    ).index(r)
                ),
            ),
        )
        self.assertEqual(
            [item["label"] for item in payload["issuers"]],
            sorted((item["label"] for item in payload["issuers"]), key=str.casefold),
        )

    def test_methodology_is_generic_and_available_without_a_run(self):
        response = self.client.get("/api/results/methodology")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["title"], "How the LDI programme works")
        self.assertGreaterEqual(len(payload["sections"]), 6)

    def test_run_service_launches_an_isolated_worker(self):
        repository = RunRepository(self.storage)
        process = Mock(pid=12345)
        with (
            patch("Webapp.service.PROCESSED_DIR", self.storage),
            patch("Webapp.service.subprocess.Popen", return_value=process) as popen,
        ):
            record = RunService(repository).start({})
        self.assertEqual(record["status"], "queued")
        self.assertEqual(record["pid"], 12345)
        command = popen.call_args.args[0]
        self.assertEqual(command[1:3], ["-m", "Webapp.worker"])
        self.assertTrue(
            (repository.run_dir(record["run_id"]) / "request.json").exists()
        )

    def test_run_endpoint_snapshots_the_saved_parameters(self):
        self.client.post("/api/config/parameters", json={"max_positions": 7})
        process = Mock(pid=12345)
        with (
            patch("Webapp.service.PROCESSED_DIR", self.storage),
            patch("Webapp.service.subprocess.Popen", return_value=process),
        ):
            response = self.client.post("/api/run/start", json={})
        self.assertEqual(response.status_code, 202)
        run_id = response.get_json()["run_id"]
        request_payload = json.loads(
            (self.storage / "runs" / run_id / "request.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(request_payload["parameters"]["max_positions"], 7)

    def test_run_endpoint_validates_and_snapshots_unsaved_parameters(self):
        process = Mock(pid=12345)
        with (
            patch("Webapp.service.PROCESSED_DIR", self.storage),
            patch("Webapp.service.subprocess.Popen", return_value=process),
        ):
            response = self.client.post(
                "/api/run/start", json={"parameters": {"max_positions": 9}}
            )
        self.assertEqual(response.status_code, 202)
        run_id = response.get_json()["run_id"]
        request_payload = json.loads(
            (self.storage / "runs" / run_id / "request.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(request_payload["parameters"]["max_positions"], 9)

        invalid = self.client.post(
            "/api/run/start", json={"parameters": {"coupon_tax_rate": 2}}
        )
        self.assertEqual(invalid.status_code, 400)

    def test_second_run_is_rejected_while_one_is_queued(self):
        repository = RunRepository(self.storage)
        process = Mock(pid=12345)
        with (
            patch("Webapp.service.PROCESSED_DIR", self.storage),
            patch("Webapp.service.subprocess.Popen", return_value=process),
        ):
            RunService(repository).start({})
            with self.assertRaisesRegex(RuntimeError, "already queued"):
                RunService(repository).start({})

    def test_result_endpoints_survive_application_restart(self):
        repository = self.app.extensions["run_repository"]
        run_id = "11111111-1111-1111-1111-111111111111"
        repository.create(run_id)
        artifacts = repository.run_dir(run_id) / "artifacts"
        artifacts.mkdir()
        (artifacts / "summary.json").write_text(
            json.dumps({"positions": 1, "solver_status": "Optimal"}),
            encoding="utf-8",
        )
        pd.DataFrame(
            {
                "isincode": ["IT0000000001"],
                "cost_eur": [1000.0],
                "issuercode": ["GOV_IT"],
            }
        ).to_parquet(artifacts / "portfolio.parquet", index=False)
        pd.DataFrame(
            {
                "month": ["2030-01"],
                "liability_eur": [100.0],
                "asset_cashflow_eur": [100.0],
                "external_cash_eur": [0.0],
                "net_cashflow_eur": [0.0],
                "cash_balance_eur": [0.0],
            }
        ).to_parquet(artifacts / "cashflow_match.parquet", index=False)
        repository.update(run_id, status="success")

        restarted = create_app(
            {"TESTING": True, "WEBAPP_STORAGE_DIR": str(self.storage)}
        ).test_client()
        self.assertEqual(
            restarted.get("/api/results/summary").get_json()["positions"], 1
        )
        self.assertEqual(len(restarted.get("/api/results/portfolio").get_json()), 1)
        self.assertEqual(
            len(restarted.get("/api/results/cashflows/annual").get_json()), 1
        )

    def test_worker_archives_complete_web_result_projections(self):
        result = {
            "portfolio": pd.DataFrame(
                {"isincode": ["IT0000000001"], "cost_eur": [1000.0]}
            ),
            "cashflow_match": pd.DataFrame(
                {
                    "month": ["2030-01"],
                    "liability_eur": [100.0],
                    "asset_cashflow_eur": [100.0],
                    "external_cash_eur": [0.0],
                    "net_cashflow_eur": [0.0],
                    "cash_balance_eur": [0.0],
                }
            ),
            "tax_breakdown": pd.DataFrame({"year": [2030], "total_taxes_eur": [1.0]}),
            "selection_explanations": pd.DataFrame(
                {"isincode": ["IT0000000001"], "selection_reason": ["test"]}
            ),
            "status": "Optimal",
            "terminal_portfolio_cash_eur": 0.0,
            "inflation_stress": {
                "summary": pd.DataFrame(
                    {"scenario_id": ["baseline"], "external_funding_eur": [0.0]}
                ),
                "monthly": pd.DataFrame(
                    {"scenario_id": ["baseline"], "month": ["2030-01"]}
                ),
                "scenario_paths": {
                    "baseline": pd.DataFrame(
                        {
                            "scenario_id": ["stale-value"],
                            "date": pd.to_datetime(["2030-01-01"]),
                            "foi_yoy": [0.02],
                        }
                    )
                },
            },
        }
        artifacts = self.storage / "artifacts"
        _persist_result(result, artifacts)
        expected = {
            "portfolio.parquet",
            "cashflow_match.parquet",
            "tax_breakdown.parquet",
            "selection_explanations.parquet",
            "stress_summary.parquet",
            "stress_monthly.parquet",
            "stress_paths.parquet",
            "summary.json",
        }
        self.assertTrue(expected.issubset({path.name for path in artifacts.iterdir()}))
        self.assertFalse(any(path.suffix == ".png" for path in artifacts.iterdir()))
        paths = pd.read_parquet(artifacts / "stress_paths.parquet")
        self.assertEqual(paths["scenario_id"].tolist(), ["baseline"])

    def test_worker_delegates_to_the_canonical_manifested_main(self):
        repository = RunRepository(self.storage)
        run_id = "22222222-2222-2222-2222-222222222222"
        repository.create(run_id)
        result = {
            "portfolio": pd.DataFrame(columns=["cost_eur"]),
            "cashflow_match": pd.DataFrame(),
            "status": "Optimal",
            "terminal_portfolio_cash_eur": 0.0,
        }
        with (
            patch(
                "Webapp.worker.execute_with_manifest", return_value=result
            ) as execute,
            patch("Webapp.worker._persist_result") as persist,
            patch("Webapp.worker.MANIFEST_PATH", self.storage / "absent.json"),
        ):
            exit_code = run_worker(run_id, self.storage)
        self.assertEqual(exit_code, 0)
        self.assertEqual(repository.get(run_id)["status"], "success")
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[0].__name__, "configured_main")
        persist.assert_called_once()

    def test_worker_log_contains_phases_but_not_main_prints(self):
        repository = RunRepository(self.storage)
        run_id = "33333333-3333-3333-3333-333333333333"
        repository.create(run_id)
        (repository.run_dir(run_id) / "request.json").write_text(
            json.dumps({"parameters": {}}), encoding="utf-8"
        )
        result = {
            "portfolio": pd.DataFrame(columns=["cost_eur"]),
            "cashflow_match": pd.DataFrame(),
            "status": "Optimal",
            "terminal_portfolio_cash_eur": 0.0,
        }

        def fake_main(*, audit, parameters, progress):
            print("RAW MAIN PRINT")
            progress("Solving the cash-flow matching portfolio")
            return result

        captured = io.StringIO()
        with (
            patch("Webapp.worker.main", side_effect=fake_main),
            patch(
                "Webapp.worker.execute_with_manifest",
                side_effect=lambda workflow: workflow(Mock()),
            ),
            patch("Webapp.worker._persist_result"),
            patch("Webapp.worker.MANIFEST_PATH", self.storage / "absent.json"),
            patch("sys.stdout", captured),
        ):
            exit_code = run_worker(run_id, self.storage)

        self.assertEqual(exit_code, 0)
        self.assertIn("PHASE |", captured.getvalue())
        self.assertIn("Solving the cash-flow matching portfolio", captured.getvalue())
        self.assertNotIn("RAW MAIN PRINT", captured.getvalue())

    def test_server_opens_chrome_with_the_configured_url(self):
        chrome = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
        with (
            patch("Webapp.server._chrome_path", return_value=chrome),
            patch("Webapp.server.subprocess.Popen") as popen,
        ):
            server._open_chrome("http://192.168.3.164:5000/")
        popen.assert_called_once_with(
            [str(chrome), "--new-window", "http://192.168.3.164:5000/"]
        )

    def test_browser_is_opened_only_after_the_server_accepts_connections(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        with (
            patch("Webapp.server.socket.create_connection", return_value=connection),
            patch("Webapp.server._open_chrome") as open_chrome,
        ):
            server._wait_until_ready_and_open(
                "192.168.3.164", 5000, "http://192.168.3.164:5000/"
            )
        open_chrome.assert_called_once_with("http://192.168.3.164:5000/")


if __name__ == "__main__":
    unittest.main()
