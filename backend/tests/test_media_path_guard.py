from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import app


class MediaPathGuardTests(unittest.TestCase):
    """GET /media must never serve a file outside output/temp/cache/assets.

    The guard (backend/main.py) is path-segment containment, specifically
    chosen over `str.startswith` because a sibling directory sharing a string
    prefix (e.g. 'output-secret' vs 'output') would otherwise slip through.
    Nothing exercised this before — a regression here is arbitrary file read.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.allowed_dir = root / "output"
        self.allowed_dir.mkdir()
        self.sibling_dir = root / "output-secret"
        self.sibling_dir.mkdir()

        self.video = self.allowed_dir / "video.mp4"
        self.video.write_text("fake video bytes")
        self.secret = self.sibling_dir / "secret.txt"
        self.secret.write_text("top secret")

        patcher = patch.multiple(
            settings,
            output_dir=str(self.allowed_dir),
            temp_dir=str(root / "tmp"),
            cache_dir=str(root / "cache"),
            assets_dir=str(root / "assets"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

        self.client = TestClient(app)

    def test_file_inside_allowed_dir_is_served(self) -> None:
        resp = self.client.get("/media", params={"path": str(self.video)})
        self.assertEqual(resp.status_code, 200)

    def test_sibling_dir_with_shared_prefix_is_blocked(self) -> None:
        """'output-secret' must NOT pass just because it starts with 'output'."""
        resp = self.client.get("/media", params={"path": str(self.secret)})
        self.assertEqual(resp.status_code, 403)

    def test_dotdot_traversal_out_of_allowed_dir_is_blocked(self) -> None:
        traversal = str(self.allowed_dir / ".." / "output-secret" / "secret.txt")
        resp = self.client.get("/media", params={"path": traversal})
        self.assertEqual(resp.status_code, 403)

    def test_missing_file_inside_allowed_dir_is_404_not_403(self) -> None:
        resp = self.client.get(
            "/media", params={"path": str(self.allowed_dir / "does-not-exist.mp4")}
        )
        self.assertEqual(resp.status_code, 404)

    def test_symlink_escaping_allowed_dir_is_blocked(self) -> None:
        link = self.allowed_dir / "escape.mp4"
        try:
            link.symlink_to(self.secret)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted in this environment")
        resp = self.client.get("/media", params={"path": str(link)})
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
