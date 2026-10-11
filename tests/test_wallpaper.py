"""Managed locked-wallpaper persistence and authenticated LAN transfer."""

import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QImage

from game_cafe.qt_app import WallpaperProvider
from game_cafe.runtime import Runtime
from game_cafe.storage import MAX_WALLPAPER_BYTES, Store, new_pool


PASSWORD = "long test password"


def _test_png() -> bytes:
    image = QImage(2, 2, QImage.Format_RGB32)
    image.fill(0xFF245A78)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Could not create test PNG.")
    buffer.close()
    return bytes(data)


PNG = _test_png()


class WallpaperStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / "wallpaper.sqlite3"
        self.store = Store(self.path)
        snapshot, secret = new_pool(
            "Cafe", self.store.pc_id, "PC-01", "Sahil", PASSWORD,
            self.store.node_public_key())
        self.store.save_pool(snapshot, secret, join=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_wallpaper_persists_as_bounded_blob_and_resets(self):
        metadata = self.store.set_wallpaper(PNG, "image/png", now=100)
        self.assertEqual(self.store.snapshot()["wallpaper"], metadata)
        self.assertEqual(self.store.wallpaper_content(), (metadata, PNG))
        with self.assertRaisesRegex(ValueError, "500 KB"):
            self.store.set_wallpaper(
                b"\x89PNG\r\n\x1a\n" + b"x" * MAX_WALLPAPER_BYTES,
                "image/png")
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.wallpaper_content(), (metadata, PNG))
        self.store.reset_wallpaper()
        self.assertIsNone(self.store.snapshot()["wallpaper"])
        self.assertIsNone(self.store.wallpaper_content())

    def test_provider_returns_safe_empty_image_for_corrupt_local_blob(self):
        self.store.set_wallpaper(PNG, "image/png")
        provider = WallpaperProvider(self.store)
        self.assertFalse(provider.requestImage(
            "current", QSize(), QSize(100, 100)).isNull())
        self.store.db.execute(
            "UPDATE wallpapers SET content=? WHERE pool_id=?",
            (b"not an image", self.store.current_pool_id))
        self.store.db.commit()
        self.assertTrue(provider.requestImage(
            "current", QSize(), QSize(100, 100)).isNull())


class WallpaperReplicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.admin_store = Store(root / "admin.sqlite3")
        self.user_store = Store(root / "user.sqlite3")
        snapshot, secret = new_pool(
            "Cafe", self.admin_store.pc_id, "PC-01", "Sahil", PASSWORD,
            self.admin_store.node_public_key())
        snapshot["members"][self.user_store.pc_id] = {
            "name": "PC-02", "public_key": self.user_store.node_public_key(),
            "last_connected_at": time.time(),
        }
        for store in (self.admin_store, self.user_store):
            store.save_pool(snapshot, secret, join=True)
        self.admin = Runtime(self.admin_store)
        self.user = Runtime(self.user_store)
        self.admin_store.note_peer(self.user_store.pc_id, "user")
        self.admin.login_admin(PASSWORD)
        self.admin.admin_ui_open = True
        self.operations = []

        def route(ip, operation, data):
            self.operations.append(operation)
            return self.user.handle_member(
                operation, data, self.admin_store.pc_id, "admin")

        self.network_patch = patch.object(
            self.admin.network, "call", side_effect=route)
        self.reconcile_patch = patch.object(
            self.user, "_reconcile_local_access")
        self.network_patch.start()
        self.reconcile_patch.start()

    def tearDown(self):
        self.reconcile_patch.stop()
        self.network_patch.stop()
        self.admin_store.close()
        self.user_store.close()
        self.temp.cleanup()

    def test_only_missing_wallpaper_bytes_are_transferred_and_reset_propagates(self):
        metadata = self.admin.set_locked_wallpaper(PNG, "image/png")
        self.admin._replicate_once()
        self.assertEqual(self.operations.count("wallpaper_sync"), 1)
        self.assertEqual(self.user_store.wallpaper_content(), (metadata, PNG))

        self.operations.clear()
        self.admin._replicate_once()
        self.assertNotIn("wallpaper_sync", self.operations)

        self.admin.reset_locked_wallpaper()
        self.admin._replicate_once()
        self.assertIsNone(self.user_store.snapshot()["wallpaper"])
        self.assertIsNone(self.user_store.wallpaper_content())

    def test_wallpaper_sync_rejects_non_admin_sender(self):
        metadata = self.admin.set_locked_wallpaper(PNG, "image/png")
        self.user_store.save_pool(self.admin_store.snapshot(),
                                  self.admin_store.secret())
        with self.assertRaises(PermissionError):
            self.user.handle_member(
                "wallpaper_sync",
                {"metadata": metadata,
                 "content": base64.b64encode(PNG).decode("ascii")},
                self.user_store.pc_id, "user")


if __name__ == "__main__":
    unittest.main()
