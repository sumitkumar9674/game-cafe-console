"""Qt window ownership and the existing two-process Win32 desktop lifecycle."""

from __future__ import annotations

import base64
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QIcon, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider, QQuickWindow, QSGRendererInterface
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from . import desktops
from .branding import APP_ICON_PATH
from .lifecycle import lifecycle_event
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
        if not self.app.windowIcon().isNull():
            self.window.setIcon(self.app.windowIcon())

    def configure_window(self, mode: str):
        if self.child:
            self.window.showFullScreen()
            return
        if mode == "widget":
            loader = self.window.findChild(QObject, "mainLoader")
            content = loader.property("item") if loader else None
            required_width = int(content.property("implicitWidth")) if content else 480
            required_height = int(content.property("implicitHeight")) if content else 400
            self.update_widget_size(required_width, required_height, resize=True)
        else:
            self.window.setMinimumWidth(1100 if mode == "admin" else 720)
            self.window.setMinimumHeight(650 if mode == "admin" else 560)
            self.window.resize(1440 if mode == "admin" else 1000,
                               900 if mode == "admin" else 680)
        if mode != "widget" or self.bridge.view.get("accessAllowed"):
            self.window.show()
            if mode != "widget":
                self.window.raise_()
        else:
            self.window.hide()

    def _widget_dimensions(self, required_width: int,
                           required_height: int) -> tuple[int, int]:
        area = (self.window.screen() or self.app.primaryScreen()).availableGeometry()
        width = min(max(required_width, 480), max(240, area.width() - 40))
        height = min(max(required_height, 400), max(240, area.height() - 40))
        return width, height

    def update_widget_size(self, required_width: int, required_height: int,
                           resize: bool = False) -> None:
        if not hasattr(self, "window") or self.bridge.mode != "widget":
            return
        width, height = self._widget_dimensions(required_width, required_height)
        self.window.setMinimumWidth(width)
        self.window.setMinimumHeight(height)
        if resize:
            self.window.resize(width, height)
        else:
            area = (self.window.screen() or self.app.primaryScreen()).availableGeometry()
            maximum_width = max(240, area.width() - 40)
            maximum_height = max(240, area.height() - 40)
            target_width = min(max(self.window.width(), width), maximum_width)
            target_height = min(max(self.window.height(), height), maximum_height)
            if (target_width, target_height) != (self.window.width(),
                                                  self.window.height()):
                self.window.resize(target_width, target_height)
        self._keep_widget_in_work_area()

    def _keep_widget_in_work_area(self) -> None:
        area = (self.window.screen() or self.app.primaryScreen()).availableGeometry()
        position = self.window.position()
        x = max(area.left(), min(position.x(), area.right() - self.window.width() + 1))
        y = max(area.top(), min(position.y(), area.bottom() - self.window.height() + 1))
        if position != QPoint(x, y):
            self.window.setPosition(QPoint(x, y))

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
        self.shutdown_state = "idle"
        self.runtime_stopped = False
        self.admin_released = False
        self._load_ui()
        self.window.setFlags(
            Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint |
            Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint)
        self.window.screenChanged.connect(self._widget_screen_changed)
        for screen in self.app.screens():
            screen.availableGeometryChanged.connect(self._widget_screen_changed)

    def _widget_screen_changed(self, *_):
        if self.bridge.mode != "widget":
            return
        loader = self.window.findChild(QObject, "mainLoader")
        content = loader.property("item") if loader else None
        required_width = int(content.property("implicitWidth")) if content else 480
        required_height = int(content.property("implicitHeight")) if content else 400
        QTimer.singleShot(0, lambda: self.update_widget_size(
            required_width, required_height))

    def run(self):
        self.configure_window("splash")
        QTimer.singleShot(0, self.bridge.begin)
        try:
            self.app.exec()
        finally:
            self.trace_shutdown("qt_event_loop_exited")
            self._cleanup()
            self.trace_shutdown("cleanup_returned",
                                remaining_threads=self._thread_summary())

    def trace_shutdown(self, stage: str, **details) -> None:
        lifecycle_event(stage, **details)

    @staticmethod
    def _thread_summary() -> str:
        return ",".join(
            f"{thread.name}:{'daemon' if thread.daemon else 'non_daemon'}"
            for thread in threading.enumerate())

    def start_console(self):
        if self.console_process:
            return
        try:
            self.runtime.default_handle = desktops.open_default()
            self.runtime.console_handle = desktops.create_console()
            ready_name, self.ready_event = desktops.create_event("CafeReady")
            stop_name, self.stop_event = desktops.create_event("CafeStop")
            self.console_process = desktops.launch_child(ready_name, stop_name)
            self.trace_shutdown("console_child_launched",
                                child_pid=desktops.process_id(self.console_process))
            desktops.wait_child_ready(self.ready_event, self.console_process)
            self.runtime.console_available = True
            self.runtime._reconcile_local_access()
        except Exception:
            self.stop_console()
            raise

    def stop_console(self):
        started = time.monotonic()
        self.trace_shutdown("desktop_restoration_requested")
        if (self.runtime.default_handle and
                desktops.active_desktop_name() != "Default"):
            desktops.switch(self.runtime.default_handle)
        self.trace_shutdown("default_desktop_restored")
        if self.console_process:
            child_pid = desktops.process_id(self.console_process)
            self.trace_shutdown("console_child_stop_requested",
                                child_pid=child_pid)
            desktops.stop_child(self.console_process, self.stop_event)
            self.trace_shutdown("console_child_exited", child_pid=child_pid,
                                elapsed_ms=int((time.monotonic() - started) * 1000))
        self.runtime.console_available = False
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

    def update_widget_access(self, allowed: bool):
        if self.bridge.mode != "widget":
            return
        if self.shutdown_state == "failed":
            if not self.window.isVisible():
                self.window.show()
            return
        if allowed and not self.window.isVisible():
            # Refreshes must never repeatedly activate the widget over a game.
            self.window.show()
        elif not allowed:
            self.window.hide()

    def shutdown(self, finalize_local_session: bool = True,
                 source: str = "application"):
        self.trace_shutdown("shutdown_requested", source=source,
                            state=self.shutdown_state,
                            finalize_local_session=finalize_local_session)
        if self.shutdown_state in ("finalizing", "restoring_desktop", "closing"):
            self.trace_shutdown("duplicate_shutdown_ignored",
                                state=self.shutdown_state)
            return
        if self.bridge.busy:
            if self.shutdown_state != "waiting":
                self.shutdown_state = "waiting"
                QTimer.singleShot(100, lambda: self._retry_shutdown(
                    finalize_local_session))
            return
        self.shutdown_state = "finalizing"

        def work():
            if finalize_local_session:
                self.trace_shutdown("session_finalization_started")
                had_session = self.runtime.prepare_local_shutdown()
                self.trace_shutdown("session_finalization_completed",
                                    had_session=had_session)
                self.trace_shutdown("session_history_persisted")
            self.shutdown_state = "restoring_desktop"
            self.stop_console()
            self.runtime.release_admin()
            self.admin_released = True
            self.trace_shutdown("network_shutdown_started")
            self.runtime.stop()
            self.runtime_stopped = True
            self.trace_shutdown("network_background_workers_stopped")

        def done(result, error):
            if error:
                self.shutdown_state = "failed"
                self.trace_shutdown("shutdown_failed",
                                    error_type=type(error).__name__,
                                    error=str(error))
                message = f"Close failed: {error}"
                self.store.set_local("customer_feedback", message)
                self.bridge._show_notice("Cannot close software", message, True)
                self.window.showNormal()
                self.window.raise_()
                self.window.requestActivate()
                return
            self.shutdown_state = "closing"
            self.shutting_down = True
            self.trace_shutdown("qt_quit_requested")
            QTimer.singleShot(0, self.app.quit)

        if not self.bridge.begin_shutdown(work, done):
            self.shutdown_state = "waiting"
            QTimer.singleShot(100, lambda: self._retry_shutdown(
                finalize_local_session))

    def _retry_shutdown(self, finalize_local_session: bool):
        if self.shutdown_state == "waiting":
            self.shutdown_state = "idle"
            self.shutdown(finalize_local_session)

    def _cleanup(self):
        def attempt(label, action):
            try:
                action()
                return True
            except Exception as error:
                print(f"Could not {label}: {error}")
                self.trace_shutdown("cleanup_operation_failed", operation=label,
                                    error_type=type(error).__name__, error=str(error))
                return False

        self.trace_shutdown("cleanup_started")
        if attempt("stop UI timers and jobs", self.bridge.shutdown):
            self.trace_shutdown("ui_jobs_stopped")
        if self.console_process or self.runtime.default_handle:
            attempt("stop CafeConsole", self.stop_console)
        if not self.admin_released:
            attempt("release Admin authority", self.runtime.release_admin)
        if not self.runtime_stopped:
            attempt("stop networking and worker threads", self.runtime.stop)
        attempt("dispose of the Qt window", self.window.hide)
        attempt("dispose of the Qt window", self.window.deleteLater)
        attempt("dispose of the QML engine", self.engine.deleteLater)
        attempt("complete Qt cleanup", lambda: QCoreApplication.sendPostedEvents(
            None, QEvent.DeferredDelete))
        attempt("process final Qt events", self.app.processEvents)
        if attempt("close the local database", self.store.close):
            self.trace_shutdown("database_closed")


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
        self.parent_pid = parent_pid
        self.shutdown_seen = False
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
            self.trace_shutdown("child_qt_event_loop_exited")
            self.bridge.shutdown()
            self.trace_shutdown("child_ui_jobs_stopped")
            try:
                if desktops.active_desktop_name() != "Default":
                    desktops.switch(self.default_handle)
            except OSError as error:
                print(f"Could not restore Default: {error}")
            for handle in (self.ready_event, self.stop_event, self.parent_process):
                desktops.close_handle(handle)
            desktops.close_desktop(self.default_handle)
            self.store.close()
            self.trace_shutdown("child_cleanup_completed")

    def trace_shutdown(self, stage: str, **details) -> None:
        lifecycle_event(stage, parent_pid=self.parent_pid, **details)

    def check_child_liveness(self):
        if desktops.signaled(self.stop_event) or desktops.exited(self.parent_process):
            if self.shutdown_seen:
                return
            self.shutdown_seen = True
            self.trace_shutdown("child_stop_signal_observed")
            if desktops.active_desktop_name() != "Default":
                desktops.switch(self.default_handle)
            self.trace_shutdown("child_qt_exit_requested")
            # This timer runs on Qt's GUI thread. exit() ends the event loop
            # without asking the QML window to close, which it rejects to keep
            # Alt+F4 from dismissing CafeConsole.
            self.app.exit(0)
            self.trace_shutdown("child_qt_exit_call_returned")
