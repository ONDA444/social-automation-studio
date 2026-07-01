from __future__ import annotations

import unittest

from backend.agents.drive_library import (
    DriveLibraryService,
    FOLDER_MIME,
    SHORTCUT_MIME,
    extract_search_query,
    is_audio_file,
    is_video_file,
    normalize_drive_name,
)


class _FakeListRequest:
    def __init__(self, files):
        self._files = files

    def execute(self):
        return {"files": self._files}


class _FakeGetRequest:
    def __init__(self, item):
        self._item = item

    def execute(self):
        return self._item or {}


class _FakeFailingGetRequest:
    def execute(self):
        raise RuntimeError("metadata unavailable")


class _FakeFiles:
    def __init__(self, tree, names=None):
        self._tree = tree
        self._names = names or {}

    def list(self, *, q, **_kwargs):
        folder_id = q.split("'")[1]
        return _FakeListRequest(self._tree.get(folder_id, []))

    def get(self, *, fileId, **_kwargs):
        return _FakeGetRequest({"id": fileId, "name": self._names.get(fileId, fileId), "mimeType": FOLDER_MIME})


class _FakeFilesWithoutGet(_FakeFiles):
    def get(self, *, fileId, **_kwargs):
        return _FakeFailingGetRequest()


class _FakeDrive:
    def __init__(self, tree, names=None):
        self._tree = tree
        self._names = names or {}

    def files(self):
        return _FakeFiles(self._tree, self._names)


class _FakeDriveWithoutGet(_FakeDrive):
    def files(self):
        return _FakeFilesWithoutGet(self._tree, self._names)


class DriveLibraryTests(unittest.TestCase):
    def test_video_detection_accepts_generic_mime_with_video_extension(self) -> None:
        self.assertTrue(is_video_file("Monetize - Religiosos (85).mp4", "application/octet-stream"))
        self.assertTrue(is_video_file("clip.MKV", None))
        self.assertFalse(is_video_file("audio.mp3", "application/octet-stream"))
        self.assertTrue(is_audio_file("voice.M4A", None))

    def test_extract_search_query_from_drive_search_url(self) -> None:
        self.assertEqual(
            extract_search_query("https://drive.google.com/drive/search?q=CORTES%20FAMILY%20GUY"),
            "CORTES FAMILY GUY",
        )
        self.assertIsNone(extract_search_query("https://drive.google.com/drive/folders/abc123DEF456"))

    def test_normalize_drive_name_ignores_accents(self) -> None:
        self.assertEqual(normalize_drive_name("VÍDEOS RELIGIOSOS"), "videos religiosos")

    def test_walk_folder_descends_nested_subfolders_in_order(self) -> None:
        svc = _FakeDrive(
            {
                "root": [
                    {"id": "folder-series", "name": "Cortes series", "mimeType": FOLDER_MIME},
                    {"id": "root-video", "name": "01.mp4", "mimeType": "video/mp4"},
                ],
                "folder-series": [
                    {"id": "folder-videos", "name": "Videos", "mimeType": FOLDER_MIME},
                    {"id": "folder-audios", "name": "Audios", "mimeType": FOLDER_MIME},
                ],
                "folder-videos": [
                    {"id": "nested-video", "name": "serie-final.mp4", "mimeType": "application/octet-stream"},
                ],
                "folder-audios": [
                    {"id": "nested-audio", "name": "voz.mp3", "mimeType": "audio/mpeg"},
                ],
            }
        )

        rows = list(DriveLibraryService(db=None)._walk_folder(svc, "root", recursive=True))

        self.assertEqual(
            [(item["id"], path) for item, path in rows],
            [
                ("nested-video", ["Cortes series", "Videos", "serie-final.mp4"]),
                ("nested-audio", ["Cortes series", "Audios", "voz.mp3"]),
                ("root-video", ["01.mp4"]),
            ],
        )

    def test_walk_folder_follows_drive_folder_shortcuts(self) -> None:
        svc = _FakeDrive(
            {
                "root": [
                    {
                        "id": "shortcut-weekly",
                        "name": "Atualizados Semanalmente",
                        "mimeType": SHORTCUT_MIME,
                        "shortcutDetails": {"targetId": "weekly-target", "targetMimeType": FOLDER_MIME},
                    },
                ],
                "weekly-target": [
                    {"id": "weekly-video", "name": "Family Guy semana 01.mp4", "mimeType": "application/octet-stream"},
                ],
            }
        )

        rows = list(DriveLibraryService(db=None)._walk_folder(svc, "root", recursive=True))

        self.assertEqual(
            [(item["id"], path) for item, path in rows],
            [("weekly-video", ["Atualizados Semanalmente", "Family Guy semana 01.mp4"])],
        )

    def test_resolve_niche_root_inside_package_folder(self) -> None:
        svc = _FakeDrive(
            {
                "package-root": [
                    {"id": "religious", "name": "VÍDEOS RELIGIOSOS", "mimeType": FOLDER_MIME},
                    {"id": "cars", "name": "CARROS E CAMINHÕES", "mimeType": FOLDER_MIME},
                ],
                "religious": [
                    {"id": "videos", "name": "Videos", "mimeType": FOLDER_MIME},
                    {"id": "series", "name": "Cortes séries", "mimeType": FOLDER_MIME},
                    {"id": "root-video", "name": "Monetize - Religiosos (85).mp4", "mimeType": "video/mp4"},
                ],
                "videos": [{"id": "video-a", "name": "01.mp4", "mimeType": "video/mp4"}],
                "series": [{"id": "video-b", "name": "serie-01.mp4", "mimeType": "application/octet-stream"}],
            },
            names={"package-root": "80 MIL CORTES VIRAIS", "religious": "VÍDEOS RELIGIOSOS"},
        )
        service = DriveLibraryService(db=None)

        roots = service._resolve_niche_roots(svc, "package-root", "VIDEOS RELIGIOSOS")
        rows = list(service._walk_folder(svc, roots[0][0], recursive=True))

        self.assertEqual(roots, [("religious", "VÍDEOS RELIGIOSOS")])
        self.assertEqual(
            [(item["id"], path) for item, path in rows],
            [
                ("video-a", ["Videos", "01.mp4"]),
                ("video-b", ["Cortes séries", "serie-01.mp4"]),
                ("root-video", ["Monetize - Religiosos (85).mp4"]),
            ],
        )

    def test_resolve_niche_does_not_choose_generic_child_folder(self) -> None:
        svc = _FakeDriveWithoutGet(
            {
                "religious": [
                    {"id": "videos", "name": "Videos", "mimeType": FOLDER_MIME},
                    {"id": "series", "name": "Cortes séries", "mimeType": FOLDER_MIME},
                ],
                "videos": [{"id": "video-a", "name": "01.mp4", "mimeType": "video/mp4"}],
                "series": [{"id": "video-b", "name": "serie-01.mp4", "mimeType": "video/mp4"}],
            }
        )

        roots = DriveLibraryService(db=None)._resolve_niche_roots(svc, "religious", "VIDEOS RELIGIOSOS")

        self.assertEqual(roots, [])


if __name__ == "__main__":
    unittest.main()
