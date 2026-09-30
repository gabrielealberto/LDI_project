import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.orchestration import RunLock, execute_with_manifest, pipeline_lock_active


class OrchestrationTests(unittest.TestCase):
    def test_successful_run_writes_manifest_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "run_manifest.json"
            lock = root / "run.lock"

            result = execute_with_manifest(
                lambda: ["bond data", "yield curve"], manifest, lock
            )

            self.assertEqual(result, ["bond data", "yield curve"])
            self.assertFalse(lock.exists())
            self.assertEqual(json.loads(manifest.read_text())["status"], "succeeded")

    def test_audit_manifest_is_historic_and_contains_run_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "run_manifest.json"
            lock = root / "run.lock"

            execute_with_manifest(
                lambda audit: audit.record_stage("refresh") or ["refresh"],
                manifest,
                lock,
            )

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            archive = root / "run_manifests" / f"{payload['run_id']}.json"
            self.assertTrue(archive.exists())
            self.assertEqual(
                json.loads(archive.read_text())["run_id"], payload["run_id"]
            )
            self.assertIn("application", payload)
            self.assertIn("solver", payload)

    def test_failed_run_writes_error_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "run_manifest.json"
            lock = root / "run.lock"

            def fail():
                raise ValueError("source unavailable")

            with self.assertRaisesRegex(ValueError, "source unavailable"):
                execute_with_manifest(fail, manifest, lock)

            payload = json.loads(manifest.read_text())
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error_type"], "ValueError")
            self.assertFalse(lock.exists())

    def test_existing_lock_blocks_a_second_run(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "run.lock"
            with RunLock(lock):
                with self.assertRaisesRegex(RuntimeError, "Another LDI run"):
                    with RunLock(lock):
                        pass

    def test_stale_local_lock_is_removed_before_a_new_run(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "run.lock"
            lock.write_text(
                f"pid=12345\nhost={socket.gethostname()}\n",
                encoding="utf-8",
            )

            with patch("core.orchestration.is_process_alive", return_value=False):
                self.assertFalse(pipeline_lock_active(lock))

            self.assertFalse(lock.exists())
            with RunLock(lock):
                self.assertTrue(lock.exists())

    def test_stale_incomplete_lock_is_removed_after_initialization_grace_period(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "run.lock"
            lock.write_text("", encoding="utf-8")
            old_timestamp = lock.stat().st_mtime - 31
            os.utime(lock, (old_timestamp, old_timestamp))

            self.assertFalse(pipeline_lock_active(lock))
            self.assertFalse(lock.exists())
