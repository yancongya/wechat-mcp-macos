#!/usr/bin/env python3
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cleanup


class CleanupTests(unittest.TestCase):
    def make_file(self, path: Path, size: int, age_days: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def policy(self, intermediate=None, images=None):
        disabled = {"enabled": False, "max_age_days": 0, "max_size_mb": 0, "keep_newest": 0}
        return {
            "categories": {
                "summary_intermediates": intermediate or disabled,
                "summary_images": images or disabled,
                "decrypted": disabled,
                "logs": disabled,
            }
        }

    def test_dry_run_does_not_delete_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old_json = self.make_file(root / "task" / "context.json", 10, 10)
            policy = self.policy(intermediate={"enabled": True, "max_age_days": 7, "max_size_mb": 50, "keep_newest": 0})
            with patch.object(cleanup, "SUMMARY_DIR", root), patch.object(cleanup, "DECRYPTED_DIR", root / "none1"), patch.object(cleanup, "LOGS_DIR", root / "none2"):
                report = cleanup.run_cleanup(policy, dry_run=True)
            self.assertTrue(old_json.exists())
            self.assertEqual(report["deleted_files"], 1)

    def test_age_threshold_deletes_intermediate_but_keeps_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old_json = self.make_file(root / "task" / "context.json", 10, 10)
            old_png = self.make_file(root / "task" / "summary.png", 10, 10)
            policy = self.policy(
                intermediate={"enabled": True, "max_age_days": 7, "max_size_mb": 50, "keep_newest": 0},
                images={"enabled": True, "max_age_days": 30, "max_size_mb": 100, "keep_newest": 0},
            )
            with patch.object(cleanup, "SUMMARY_DIR", root), patch.object(cleanup, "DECRYPTED_DIR", root / "none1"), patch.object(cleanup, "LOGS_DIR", root / "none2"):
                cleanup.run_cleanup(policy)
            self.assertFalse(old_json.exists())
            self.assertTrue(old_png.exists())

    def test_size_threshold_removes_oldest_and_protects_newest(self):
        now = time.time()
        files = [
            cleanup.Candidate(Path("old.json"), 700_000, now - 300, "summary_intermediates"),
            cleanup.Candidate(Path("middle.json"), 700_000, now - 200, "summary_intermediates"),
            cleanup.Candidate(Path("new.json"), 700_000, now - 100, "summary_intermediates"),
        ]
        config = {"enabled": True, "max_age_days": 0, "max_size_mb": 1, "keep_newest": 1}
        deletions, survivors = cleanup.select_deletions(files, config, now)
        self.assertEqual({item.path.name for item in deletions}, {"old.json", "middle.json"})
        self.assertEqual([item.path.name for item in survivors], ["new.json"])


if __name__ == "__main__":
    unittest.main()
