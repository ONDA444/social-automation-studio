"""Minimal regression test for QualityControlAgent short-file QC coverage."""
from unittest.mock import patch

from backend.agents.quality_control import QualityControlAgent

MAIN_META = {
    "width": 1920,
    "height": 1080,
    "duration": 60.0,
    "bitrate": 5_000_000,
    "vcodec": "h264",
    "has_audio": True,
}


def test_missing_short_produces_warning_not_silent_pass(tmp_path):
    """A short whose file is missing/corrupt (probe -> None) must surface a
    warning instead of being silently skipped and passing QC.
    """
    main_path = tmp_path / "main.mp4"
    main_path.write_bytes(b"fake")

    agent = QualityControlAgent(job_id=1, context={}, emit=False)

    def fake_probe(path):
        if str(path) == str(main_path):
            return dict(MAIN_META)
        return None  # simulates missing/corrupt short file

    with patch.object(QualityControlAgent, "_probe", side_effect=fake_probe), \
         patch.object(QualityControlAgent, "_mean_volume", return_value=-10.0), \
         patch.object(QualityControlAgent, "_has_long_blackframes", return_value=False), \
         patch("backend.agents.quality_control.Path.exists", return_value=True):
        report = agent._check(
            path=str(main_path),
            visuals={"thumbnails": {"A": {"landscape": str(main_path)}}},
            shorts=[{"num": 1, "path": str(tmp_path / "short1.mp4")}],
            script={},
            narration={},
        )

    assert report["status"] == "qc_warning"
    assert any("short 1" in w and ("ausente" in w or "corrompido" in w) for w in report["warnings"])
