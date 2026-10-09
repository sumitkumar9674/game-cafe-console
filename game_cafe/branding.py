"""Optional, packaged application-branding assets."""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage


BRANDING_DIR = Path(__file__).resolve().parent / "assets" / "branding"
APP_ICON_PATH = BRANDING_DIR / "app_icon.ico"
APP_LOGO_PATH = BRANDING_DIR / "app_logo.png"


def valid_image(path: Path) -> bool:
    """Return whether Qt can decode an existing branding image."""
    return path.is_file() and not QImage(str(path)).isNull()


def app_logo_source() -> str:
    """Expose only a valid logo URL so QML never attempts a broken file URL."""
    if not valid_image(APP_LOGO_PATH):
        return ""
    return QUrl.fromLocalFile(str(APP_LOGO_PATH)).toString()
