"""Qt window ownership and the existing two-process Win32 desktop lifecycle."""

from __future__ import annotations

import base64
from pathlib import Path
import sys

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QIcon, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider, QQuickWindow, QSGRendererInterface
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from . import desktops
from .branding import APP_ICON_PATH
from .qt_bridge import CafeBridge
from .runtime import Runtime
from .security import create_local_command_key
from .storage import Store


class AvatarProvider(QQuickImageProvider):
    def __init__(self, store: Store):
        super().__init__(QQuickImageProvider.Image)
        self.store = store

    def requestImage(self, image_id, size, requested_size):
        snap = self.store.snapshot()
        try:
            image = QImage.fromData(base64.b64decode(snap["logo"])) if snap and snap.get("logo") else QImage()
        except (ValueError, TypeError):
            image = QImage()
        if not image.isNull() and requested_size.width() > 0 and requested_size.height() > 0:
            image = image.scaled(requested_size, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
        return image


class QtHost:
    def _load_ui(self):
        branded_icon = QIcon(str(APP_ICON_PATH)) if APP_ICON_PATH.is_file() else QIcon()
        if not branded_icon.isNull():
            self.app.setWindowIcon(branded_icon)
        QQuickStyle.setStyle("Material")
        self.engine = QQmlApplicationEngine()
        self.engine.addImageProvider("cafe", AvatarProvider(self.store))
        self.bridge = CafeBridge(self.store, self, self.runtime, self.child)
        self.engine.rootContext().setContextProperty("bridge", self.bridge)
        qml = Path(__file__).resolve().parent / "qml" / "App.qml"
        self.engine.load(QUrl.fromLocalFile(str(qml)))
        roots = self.engine.rootObjects()
        if not roots:
            raise RuntimeError(f"Could not load Qt UI: {qml}")
        self.window = roots[0]
        self.standard_window_flags = self.window.flags()
        self.current_window_mode = ""
        if not self.app.windowIcon().isNull():
            self.window.setIcon(self.app.windowIcon())

    def configure_window(self, mode: str):
        if self.child:
            self.window.showFullScreen()
            return
        previous_mode = self.current_window_mode
        self.current_window_mode = mode
        # Only the full User widget needs a tray entry for Hide / reopen.
        if hasattr(self, "tray"):
            self.tray.setVisible(mode == "widget" and
                                 QSystemTrayIcon.isSystemTrayAvailable())
        if mode == "admin":
            self.widget_user_hidden = False
        if mode == "compact":
            self.window.setFlags(Qt.Tool | Qt.FramelessWindowHint |
                                 Qt.WindowStaysOnTopHint |
                                 Qt.WindowDoesNotAcceptFocus)
            self.window.setMinimumWidth(250)
            self.window.setMinimumHeight(88)
            self.window.resize(270, 96)
            position = getattr(self, "compact_position", None)
            if position is None:
                screen = self.window.screen() or self.app.primaryScreen()
                area = screen.availableGeometry()
                position = QPoint(area.right() - self.window.width() - 20,
                                  area.top() + 20)
            self.window.setPosition(self._visible_position(position))
            self.window.show()
            return
        if previous_mode == "compact":
            self.compact_position = self.window.position()
        if self.window.flags() != self.standard_window_flags:
            self.window.setFlags(self.standard_window_flags)
        if mode == "widget":
            self.window.setMinimumWidth(390)
            self.window.setMinimumHeight(250)
            self.window.resize(410, 310)
            if getattr(self, "widget_position", None) is not None:
                self.window.setPosition(self._visible_position(self.widget_position))
        else:
            self.window.setMinimumWidth(1100 if mode == "admin" else 720)
            self.window.setMinimumHeight(650 if mode == "admin" else 560)
            self.window.resize(1440 if mode == "admin" else 1000,
                               900 if mode == "admin" else 680)
        if mode != "widget" or (self.bridge.view.get("accessAllowed")
                                and not self.widget_user_hidden):
            self.window.show()
            self.window.raise_()
        else:
            self.window.hide()

    def _visible_position(self, position: QPoint) -> QPoint:
        screen = self.app.screenAt(position) or self.window.screen() or \
            self.app.primaryScreen()
        area = screen.availableGeometry()
        return QPoint(max(area.left(), min(position.x(),
                                           area.right() - self.window.width() + 1)),
                      max(area.top(), min(position.y(),
                                          area.bottom() - self.window.height() + 1)))


class Application(QtHost):
    def __init__(self):
        self.child = False
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.store = Store()
        self.command_private_key, command_public = create_local_command_key()
        self.store.set_local("controller_command_public", command_public)
        self.runtime = Runtime(self.store)
        self.console_process = self.ready_event = self.stop_event = None
        self.console_transitioning = False
        self.shutting_down = False
        self.widget_user_hidden = False
        self.widget_position = None
        self.compact_position = None
        self._load_ui()
        self.tray = QSystemTrayIcon(self.app)
        self.tray.setIcon(self.app.windowIcon() if not self.app.windowIcon().isNull()
                          else self.app.style().standardIcon(
                              QStyle.StandardPixmap.SP_ComputerIcon))
        self.tray_menu = QMenu()
        self.tray_menu.addAction("Open Game Cafe Console", self._show_window)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda reason: self._show_window()
                                    if reason == QSystemTrayIcon.DoubleClick else None)

    def _show_window(self):
        if self.bridge.mode == "widget" and not self.bridge.view.get("accessAllowed"):
            self.open_console()
            return
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()

    def run(self):
        self.configure_window("splash")
        QTimer.singleShot(0, self.bridge.begin)
        try:
            self.app.exec()
        finally:
            self._cleanup()

    def start_console(self):
        if self.console_process:
            return
        try:
            self.runtime.default_handle = desktops.open_default()
            self.runtime.console_handle = desktops.create_console()
            ready_name, self.ready_event = desktops.create_event("CafeReady")
            stop_name, self.stop_event = desktops.create_event("CafeStop")
            self.console_process = desktops.launch_child(ready_name, stop_name)
            desktops.wait_child_ready(self.ready_event, self.console_process)
            self.runtime.console_available = True
            self.runtime._reconcile_local_access()
        except Exception:
            self.stop_console()
            raise

    def stop_console(self):
        if self.runtime.default_handle:
            desktops.switch(self.runtime.default_handle)
        self.runtime.console_available = False
        try:
            desktops.stop_child(self.console_process, self.stop_event)
        finally:
            for handle in (self.console_process, self.ready_event, self.stop_event):
                desktops.close_handle(handle)
            desktops.close_desktop(self.runtime.console_handle)
            desktops.close_desktop(self.runtime.default_handle)
            self.runtime.console_handle = self.runtime.default_handle = None
            self.runtime.last_access_allowed = None
            self.console_process = self.ready_event = self.stop_event = None

    def open_console(self):
        if self.runtime.console_available and self.runtime.console_handle:
            try:
                desktops.switch(self.runtime.console_handle)
            except OSError as error:
                self.bridge._show_notice("Desktop switch failed", str(error), True)

    def hide_widget(self):
        if self.tray.isVisible():
            self.widget_user_hidden = True
            self.window.hide()
        else:
            self.bridge._show_notice("Tray unavailable",
                                     "This Windows session has no notification area.", True)

    def show_compact_timer(self):
        if (self.bridge.mode != "widget"
                or not self.bridge.view.get("accessAllowed")):
            return
        self.widget_position = self.window.position()
        self.widget_user_hidden = False
        self.bridge._set_mode("compact")

    def expand_widget(self):
        if self.bridge.mode != "compact":
            return
        self.compact_position = self.window.position()
        self.bridge._set_mode("widget")
        self._show_window()

    def clamp_compact_timer(self):
        if self.bridge.mode == "compact":
            position = self._visible_position(self.window.position())
            self.window.setPosition(position)
            self.compact_position = position

    def update_widget_access(self, allowed: bool):
        if self.bridge.mode not in ("widget", "compact"):
            return
        if allowed and not self.widget_user_hidden:
            self._show_window()
        elif not allowed:
            if self.bridge.mode == "compact":
                self.bridge._set_mode("widget")
            self.window.hide()

    def shutdown(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        QTimer.singleShot(0, self.app.quit)

    def _cleanup(self):
        def attempt(label, action):
            try:
                action()
            except Exception as error:
                print(f"Could not {label}: {error}")

        attempt("stop UI timers and jobs", self.bridge.shutdown)
        if self.shutting_down:
            attempt("finalize local session", lambda:
                    self.runtime.finish_local_session("close_software"))
        attempt("stop CafeConsole", self.stop_console)
        if self.shutting_down:
            attempt("sync local session record", self.runtime.flush_owner_record)
            attempt("release Admin authority", self.runtime.release_admin)
        attempt("stop networking and worker threads", self.runtime.stop)
        attempt("hide the tray icon", self.tray.hide)
        attempt("dispose of the Qt window", self.window.hide)
        attempt("dispose of the Qt window", self.window.deleteLater)
        attempt("dispose of the QML engine", self.engine.deleteLater)
        attempt("complete Qt cleanup", lambda: QCoreApplication.sendPostedEvents(
            None, QEvent.DeferredDelete))
        attempt("process final Qt events", self.app.processEvents)
        attempt("close the local database", self.store.close)


class ConsoleChildScreen(QtHost):
    def __init__(self, store: Store, ready_name: str, stop_name: str,
                 parent_pid: int):
        self.child = True
        self.store = store
        self.runtime = None
        self.default_handle = desktops.open_default()
        self.parent_process = desktops.open_parent_process(parent_pid)
        self.ready_event = desktops.open_event(ready_name, desktops.EVENT_MODIFY_STATE)
        self.stop_event = desktops.open_event(stop_name, desktops.SYNCHRONIZE)
        # Software renderer avoids a second-desktop GPU dependency; set before QQuickWindow.
        QQuickWindow.setGraphicsApi(QSGRendererInterface.Software)
        self.app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        self._load_ui()

    def run(self):
        try:
            self.configure_window("console")
            self.app.processEvents()
            desktops.signal(self.ready_event)
            self.bridge.refresh()
            self.app.exec()
        finally:
            self.bridge.shutdown()
            try:
                desktops.switch(self.default_handle)
            except OSError as error:
                print(f"Could not restore Default: {error}")
            for handle in (self.ready_event, self.stop_event, self.parent_process):
                desktops.close_handle(handle)
            desktops.close_desktop(self.default_handle)
            self.store.close()

    def check_child_liveness(self):
        if desktops.signaled(self.stop_event) or desktops.exited(self.parent_process):
            desktops.switch(self.default_handle)
            self.app.quit()
