"""Threaded Python-to-QML bridge. Runtime and SQLite remain authoritative."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import time
from typing import Callable
import uuid

from PySide6.QtCore import (QAbstractListModel, QByteArray, QModelIndex,
                            QObject, Property, Qt, QTimer, QUrl, Signal, Slot)
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFileDialog

from . import desktops, sessions
from .lifecycle import lifecycle_event
from .branding import app_logo_source
from .network import discover
from .presentation import (add_time_preview, duration, history_rows,
                           pc_row_data, sort_pc_rows, validate_add,
                           validate_start)
from .runtime import Runtime
from .security import open_local_password, seal_local_password
from .storage import Store, new_pool, verify_password


DEVELOPER_NAME = "Sumit Kumar"
DEVELOPER_BRAND = "StickForYou"
DEVELOPER_WEBSITE = "https://sfysumit.app"
DEVELOPER_EMAIL = "contact@sfysumit.app"


class RecordListModel(QAbstractListModel):
    """A stable model: live PC updates change rows without recreating delegates."""

    RowRole = Qt.UserRole + 1

    def __init__(self, key: str, parent: QObject | None = None):
        super().__init__(parent)
        self.key = key
        self.rows: list[dict] = []

    def roleNames(self):
        return {self.RowRole: b"rowData"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role == self.RowRole and 0 <= index.row() < len(self.rows):
            return self.rows[index.row()]
        return None

    def update_rows(self, rows: list[dict]) -> None:
        old_ids = [row[self.key] for row in self.rows]
        new_ids = [row[self.key] for row in rows]
        if old_ids != new_ids:
            self.beginResetModel()
            self.rows = rows
            self.endResetModel()
            return
        for index, row in enumerate(rows):
            if row != self.rows[index]:
                self.rows[index] = row
                model_index = self.index(index, 0)
                self.dataChanged.emit(model_index, model_index, [self.RowRole])


class CafeBridge(QObject):
    modeChanged = Signal()
    viewChanged = Signal()
    selectionChanged = Signal()
    busyChanged = Signal()
    statusChanged = Signal()
    noticeChanged = Signal()
    staffAuthChanged = Signal()
    workFinished = Signal(str, object, object)

    def __init__(self, store: Store, host, runtime: Runtime | None = None,
                 child: bool = False):
        super().__init__()
        self.store = store
        self.host = host
        self.runtime = runtime
        self.child = child
        self._mode = "console" if child else "splash"
        self._view: dict = {"cafeName": "Game Cafe Console", "hasAvatar": False,
                            "avatarSource": "", "developer": DEVELOPER_NAME,
                            "brand": DEVELOPER_BRAND, "website": DEVELOPER_WEBSITE,
                            "email": DEVELOPER_EMAIL,
                            "appLogoSource": app_logo_source(),
                            "adminLoginFailed": False,
                            "activeAdminDetected": False}
        self._status = "Starting Game Cafe Console"
        self._notice: dict = {}
        self._busy = False
        self._selected_pc = ""
        self._expanded_pc = ""
        self._history_all = True
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cafe-ui")
        self._jobs: dict[str, Callable] = {}
        self._job_number = 0
        self._refresh_pending = False
        self._refresh_again = False
        self._poll_pending = False
        self._join: dict | None = None
        self._join_generation = 0
        self._cooldown_until = 0.0
        self._previous_session_id = None
        self._shutting_down = False
        self._staff_authorized_until = 0.0
        self._staff_credential: dict | None = None
        self._staff_verifier: dict | None = None
        self._staff_failed_attempts = 0
        self._staff_blocked_until = 0.0
        self._runtime_started = False
        self.pc_model = RecordListModel("pcId", self)
        self.history_model = RecordListModel("id", self)
        self.dashboard_history_model = RecordListModel("id", self)
        self.join_model = RecordListModel("requestId", self)
        self.member_model = RecordListModel("pcId", self)
        self.workFinished.connect(self._on_work_finished)
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(300 if child else 250)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(500 if child else 1000)
        self.join_timer = QTimer(self)
        self.join_timer.timeout.connect(self._poll_join)
        self.join_timer.start(3000)

    @Property(str, notify=modeChanged)
    def mode(self):
        return self._mode

    @Property("QVariantMap", notify=viewChanged)
    def view(self):
        return self._view

    @Property(str, notify=statusChanged)
    def statusText(self):
        return self._status

    @Property("QVariantMap", notify=noticeChanged)
    def notice(self):
        return self._notice

    @Property(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @Property(str, notify=selectionChanged)
    def selectedPcId(self):
        return self._selected_pc

    @Property(str, notify=selectionChanged)
    def expandedPcId(self):
        return self._expanded_pc

    @Property(bool, notify=selectionChanged)
    def historyAll(self):
        return self._history_all

    @Property(QObject, constant=True)
    def pcModel(self):
        return self.pc_model

    @Property(QObject, constant=True)
    def historyModel(self):
        return self.history_model

    @Property(QObject, constant=True)
    def dashboardHistoryModel(self):
        return self.dashboard_history_model

    @Property(QObject, constant=True)
    def joinModel(self):
        return self.join_model

    @Property(QObject, constant=True)
    def memberModel(self):
        return self.member_model

    @Property(bool, constant=True)
    def childMode(self):
        return self.child

    @Property(bool, notify=staffAuthChanged)
    def staffAuthorized(self):
        return self._staff_is_authorized()

    def _set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode = mode
            self.modeChanged.emit()
            self.host.configure_window(mode)

    def _set_status(self, status: str) -> None:
        if self._status != status:
            self._status = status
            self.statusChanged.emit()

    def _set_busy(self, value: bool) -> None:
        if self._busy != value:
            self._busy = value
            self.busyChanged.emit()

    def _set_view(self, **values) -> None:
        updated = {**self._view, **values}
        if updated != self._view:
            self._view = updated
            self.viewChanged.emit()

    def _show_notice(self, title: str, message: str, error: bool = False) -> None:
        self._notice = {"title": title, "message": str(message), "error": error,
                        "serial": time.monotonic_ns()}
        self.noticeChanged.emit()

    @Slot()
    def clearNotice(self):
        self._notice = {}
        self.noticeChanged.emit()

    def _submit(self, operation: str, work: Callable, done: Callable,
                blocking: bool = False) -> None:
        if self._shutting_down:
            return
        if blocking and self._busy:
            return
        if blocking:
            self._set_busy(True)
        self._job_number += 1
        key = f"{operation}:{self._job_number}"
        self._jobs[key] = (done, blocking)

        def run() -> None:
            try:
                self.workFinished.emit(key, work(), None)
            except Exception as error:
                self.workFinished.emit(key, None, error)

        self._executor.submit(run)

    def _on_work_finished(self, key: str, result, error) -> None:
        job = self._jobs.pop(key, None)
        if not job or self._shutting_down:
            return
        done, blocking = job
        if blocking:
            self._set_busy(False)
        done(result, error)

    def _action(self, name: str, work: Callable, success: str = "") -> None:
        def done(result, error):
            if error:
                pending = str(error).startswith("Saved on the Admin")
                self._show_notice("Delivery pending" if pending else "Action failed",
                                  str(error), not pending)
            elif success:
                self._show_notice("Done", success)
            self.refresh()
        self._submit(name, work, done, blocking=True)

    def begin_shutdown(self, work: Callable, done: Callable) -> bool:
        if self._shutting_down or self._busy:
            return False
        self._submit("shutdown", work, done, blocking=True)
        return True

    @staticmethod
    def _branding(snapshot: dict | None) -> dict:
        if not snapshot:
            return {}
        logo = snapshot.get("logo")
        return {"cafeName": snapshot["cafe_name"], "hasAvatar": bool(logo),
                "avatarSource": "image://cafe/avatar/" + str(hash(logo)) if logo else ""}

    @Slot()
    def begin(self) -> None:
        if self.child:
            self.refresh()
            return
        def work():
            if not self._runtime_started:
                self.runtime.start()
                self._runtime_started = True
            if self.store.current_pool_id:
                live = self.runtime.verified_admin_online()
                return "candidate", [], self._branding(self.store.snapshot()), live
            return "onboarding", discover(timeout=1.5), {}, False
        def done(result, error):
            if error:
                self._set_status("Connection failed")
                self._show_notice("Could not start", str(error), True)
                self._set_mode("splash")
                return
            mode, found, branding, active_admin = result
            self._set_view(pools=self._distinct_pools(found),
                           activeAdminDetected=active_admin, **branding)
            if mode == "candidate":
                self._set_status("Admin connected - continue as User"
                                 if active_admin else
                                 "Choose Admin or User access")
                self._set_mode(mode)
                self.refresh()
            else:
                self._set_status("Choose a café or create a new one")
                self._set_mode(mode)
                self.refresh()
        self._set_status("Connecting to café" if self.store.current_pool_id
                         else "Searching for cafés")
        self._submit("startup", work, done, blocking=True)

    @staticmethod
    def _distinct_pools(found: list[dict], exclude: str | None = None) -> list[dict]:
        pools: dict[str, dict] = {}
        for item in found:
            pool_id = item.get("pool_id")
            if not pool_id or pool_id == exclude:
                continue
            current = pools.get(pool_id)
            if current is None or item.get("pc_id") == item.get("active_admin"):
                pools[pool_id] = {"poolId": pool_id,
                                  "cafeName": item.get("cafe_name", "Café"),
                                  "ip": item.get("ip", "")}
        return list(pools.values())

    @Slot()
    def searchPools(self) -> None:
        self._set_status("Searching for cafés")
        def done(found, error):
            if error:
                self._show_notice("Search failed", str(error), True)
            else:
                self._set_view(pools=self._distinct_pools(found))
                self._set_mode("onboarding")
        self._submit("search", lambda: discover(timeout=1.5), done, blocking=True)

    @Slot(str, str, str, str, str)
    def createCafe(self, cafe: str, pc_name: str, admin_name: str,
                   password: str, confirmation: str) -> None:
        if password != confirmation:
            self._show_notice("Passwords differ", "Enter the same Admin password twice.", True)
            return
        def work():
            snap, secret = new_pool(cafe, self.store.pc_id, pc_name, admin_name,
                                    password, self.store.node_public_key())
            snap["developer_website"] = DEVELOPER_WEBSITE
            snap["developer_contact"] = DEVELOPER_EMAIL
            self.store.save_pool(snap, secret, join=True)
            self.runtime.login_admin(password)
            return self._branding(self.store.snapshot())
        self._set_status("Creating café and loading Admin Dashboard")
        def done(result, error):
            if error:
                self._show_notice("Could not create café", str(error), True)
            else:
                self._set_view(**result)
                self._enter_admin()
        self._submit("create", work, done, blocking=True)

    @Slot(str)
    def claimAdmin(self, password: str) -> None:
        if not password:
            self._show_notice("Admin password required",
                              "Enter the Admin password, then try again.", True)
            return
        self._set_view(adminLoginFailed=False)
        self._set_status("Authenticating Admin, connecting, and synchronizing cafe data")
        # Show the responsive splash while authentication runs off the Qt thread.
        self._set_mode("splash")
        def done(result, error):
            if error:
                self._set_view(adminLoginFailed=True)
                self._set_status("Admin login failed. Enter the password and retry.")
                self._set_mode("candidate")
                self._show_notice("Admin login failed", str(error), True)
            else:
                self._set_status("Admin authenticated. Preparing the dashboard")
                self._enter_admin()
        self._submit("claim", lambda: self.runtime.take_admin(password),
                     done, blocking=True)

    @Slot()
    def stayUser(self) -> None:
        if self.store.current_pool_id:
            self._enter_user()

    def _enter_admin(self) -> None:
        if self._busy:
            QTimer.singleShot(250, self._enter_admin)
            return
        if self.host.console_transitioning:
            return
        self._set_status("Preparing the Admin Dashboard")
        self._set_mode("splash")
        self.host.console_transitioning = True
        def done(result, error):
            self.host.console_transitioning = False
            if error:
                self._show_notice("Admin transition failed", str(error), True)
                return
            self.runtime.admin_ui_open = True
            self._set_mode("admin")
            self.refresh()
        self._submit("admin_window", self.host.stop_console, done, blocking=True)

    def _enter_user(self) -> None:
        if self._busy or self.host.console_transitioning:
            return
        self.runtime.admin_ui_open = False
        self._set_status("Preparing User Console")
        self._set_mode("splash")
        self.host.console_transitioning = True
        def done(result, error):
            self.host.console_transitioning = False
            if error:
                self._show_notice("User Console failed", str(error), True)
                return
            self._set_mode("widget")
            self.refresh()
        self._submit("user_window", self.host.start_console, done, blocking=True)

    @Slot(str, str)
    def startJoin(self, pool_id: str, requested_name: str) -> None:
        if self.store.current_pool_id:
            self._show_notice("Already registered",
                              "Use Admin Settings and authenticate to change cafes.", True)
            return
        pool = next((item for item in self._view.get("pools", [])
                     if item["poolId"] == pool_id), None)
        if not pool or not requested_name.strip():
            self._show_notice("Join request", "Choose a café and enter a PC name.", True)
            return
        self._begin_join(pool, requested_name.strip(), None)

    def _begin_join(self, pool: dict, name: str, old_pool: str | None) -> None:
        self._join_generation += 1
        generation = self._join_generation
        self._join = {"pool": pool, "name": name, "oldPool": old_pool,
                      "generation": generation, "requestId": None,
                      "private": None}
        self._set_view(pairingCode="", pairingState="Sending request",
                       pairingCafe=pool["cafeName"])
        self._set_mode("joining")
        def done(result, error):
            if not self._join or self._join["generation"] != generation:
                return
            if error:
                self._set_view(pairingState=f"Request failed: {error}")
                return
            self._join["requestId"], self._join["private"] = result
            self._set_view(pairingState="Waiting for Admin approval")
            self._poll_join()
        self._submit("request_join", lambda: self.runtime.request_join(
            pool["ip"], pool["poolId"], name), done, blocking=True)

    @Slot()
    def renewJoin(self) -> None:
        if self._join:
            item = self._join
            self._begin_join(item["pool"], item["name"], item["oldPool"])

    @Slot()
    def cancelJoin(self) -> None:
        self._join_generation += 1
        old_pool = self._join.get("oldPool") if self._join else None
        self._join = None
        self._set_mode("admin" if old_pool else "onboarding")

    def _poll_join(self) -> None:
        if not self._join or not self._join["requestId"] or self._poll_pending:
            return
        item = dict(self._join)
        self._poll_pending = True
        def done(result, error):
            self._poll_pending = False
            if not self._join or self._join["generation"] != item["generation"]:
                return
            if error:
                self._set_view(pairingState=f"Connection failed: {error}")
                return
            state, code, branding = result
            if state == "verify":
                self._set_view(pairingCode=code,
                               pairingState="Read this code to the Admin · Expires in five minutes")
            elif state in ("expired", "rejected"):
                self._set_view(pairingCode="", pairingState=
                               "Request expired or rejected · Generate a new code")
                self._join["requestId"] = None
            elif state == "accepted":
                old_pool = item["oldPool"]
                self._join = None
                self._set_view(**branding)
                if old_pool:
                    self._show_notice("Café changed", "Destination data saved successfully.")
                self._enter_user()
        def work():
            state, code = self.runtime.finish_join(
                item["pool"]["ip"], item["requestId"], item["private"],
                item["oldPool"])
            return state, code, self._branding(self.store.snapshot()) if state == "accepted" else {}
        self._submit("poll_join", work, done)

    def _collect_state(self, selected: str, all_history: bool) -> dict:
        snap = self.store.snapshot()
        if not snap:
            return {}
        own = self.store.pc_id
        now = time.time()
        own_status = self.runtime.status() if self.runtime else None
        rows = [pc_row_data(snap, pc_id,
                            own_status if pc_id == own else
                            self.runtime.peer_status(pc_id) if self.runtime else None,
                            now)
                for pc_id in snap["members"]] if self.runtime else []
        computer_sort = snap.get("settings", {}).get("computer_sort", "recent")
        rows = sort_pc_rows(rows, snap, computer_sort)
        session = snap["sessions"].get(own)
        phase = sessions.phase(session, now, snap["settings"]["grace_minutes"])
        remaining = sessions.remaining_seconds(
            session, now, snap["settings"]["grace_minutes"]
        )
        feedback = self.store.local("customer_feedback") or ""
        pending = self.store.pending_joins() if self.runtime and self.runtime.is_admin() else []
        peers = {item["pc_id"]: item for item in self.store.peers()} if self.runtime else {}
        active_admin = bool(
            self.runtime and self._mode == "candidate"
            and self.runtime.confirmed_remote_admin_online()
        )
        return {
            "snapshot": snap, "rows": rows,
            "history": history_rows(snap, None if all_history else selected),
            "dashboardHistory": history_rows(snap, selected) if selected else [],
            "joins": [{"requestId": item["request_id"],
                       "pcId": item["pc_id"], "name": item["proposed_name"],
                       "ip": item["ip"], "attempts": item["attempts"]}
                      for item in pending],
            "members": [{"pcId": pc_id, "name": member["name"],
                         "online": pc_id == own or
                         (self.runtime.peer_status(pc_id) is not None if self.runtime else False),
                         "ip": peers.get(pc_id, {}).get("ip", "")}
                        for pc_id, member in snap["members"].items()],
            "historyTargets": [{"pcId": pc_id, "name": member["name"],
                                "count": len(snap["history"].get(pc_id, [])),
                                "online": pc_id == own or
                                (self.runtime.peer_status(pc_id) is not None if self.runtime else False)}
                               for pc_id, member in snap["members"].items()],
            "cafeName": snap["cafe_name"],
            "ownName": snap["members"].get(own, {}).get("name", "PC"),
            "session": session, "phase": phase.upper(),
            "kind": session["kind"] if session else "none",
            "player": session["player"] if session else "Guest",
            "timeText": duration(remaining),
            "remainingSeconds": remaining,
            "paidMinutes": session["paid_minutes"] if session else 0,
            "bufferMinutes": session["buffer_minutes"] if session else 0,
            "hasSession": bool(session),
            "accessAllowed": phase in ("buffer", "timed", "open", "paused"),
            "canSwitchAdmin": session is None,
            "activeAdminDetected": active_admin,
            "feedback": feedback,
            "connectionNote": self.store.local("admin_connection_note") or "",
            "hasAvatar": bool(snap.get("logo")),
            "avatarSource": ("image://cafe/avatar/" + str(hash(snap.get("logo"))))
                            if snap.get("logo") else "",
            "settings": {"cafeName": snap["cafe_name"],
                         "adminName": snap["admin_name"],
                         "graceMinutes": snap["settings"]["grace_minutes"],
                         "signoutMinutes": snap["settings"]["auto_signout_minutes"]},
            "computerSort": computer_sort,
            "poolId": snap["pool_id"],
            "unlockRequests": sum(bool(value) for value in snap["unlock_requests"].values()),
            "onlineCount": sum(row["online"] for row in rows),
            "totalCount": len(snap["members"]),
            "recentCount": sum(len(group) for group in snap["history"].values()),
            "pendingCount": len(pending),
        }

    @Slot()
    def refresh(self) -> None:
        if self._shutting_down or not self.store.current_pool_id:
            return
        if self._refresh_pending:
            self._refresh_again = True
            return
        self._refresh_pending = True
        selected, all_history = self._selected_pc, self._history_all
        def done(result, error):
            self._refresh_pending = False
            again = self._refresh_again
            self._refresh_again = False
            if again:
                QTimer.singleShot(0, self.refresh)
            if error:
                self._set_status(f"Synchronizing café data: {error}")
                return
            if not result:
                return
            self.pc_model.update_rows(result.pop("rows"))
            self.history_model.update_rows(result.pop("history"))
            self.dashboard_history_model.update_rows(
                result.pop("dashboardHistory"))
            self.join_model.update_rows(result.pop("joins"))
            self.member_model.update_rows(result.pop("members"))
            snap = result.pop("snapshot")
            if self.child:
                session_id = result["session"]["id"] if result["session"] else None
                if (self._previous_session_id is not None
                        and session_id != self._previous_session_id):
                    self._revoke_staff()
                if session_id != self._previous_session_id:
                    self._previous_session_id = session_id
                    self.store.set_local("customer_feedback", "")
                result["requestCooldown"] = max(0, int(self._cooldown_until - time.time()))
            result.pop("session")
            self._set_view(**result)
            if self._mode == "candidate":
                self._set_status(
                    "Admin connected - continue as User"
                    if result["activeAdminDetected"] else
                    "Choose Admin or User access")
            if self._mode == "widget":
                self.host.update_widget_access(bool(result["accessAllowed"]))
        self._submit("refresh", lambda: self._collect_state(selected, all_history), done)

    @Slot(str)
    def selectPc(self, pc_id: str) -> None:
        if pc_id not in {row["pcId"] for row in self.pc_model.rows}:
            return
        self._selected_pc = pc_id
        self._expanded_pc = "" if self._expanded_pc == pc_id else pc_id
        self._history_all = False
        self.selectionChanged.emit()
        self.refresh()

    @Slot()
    def showAllHistory(self) -> None:
        self._history_all = True
        self.selectionChanged.emit()
        self.refresh()

    @Slot(str)
    def setComputerSort(self, option: str) -> None:
        if option not in ("recent", "name"):
            self._show_notice("Sorting unavailable",
                              "Choose Recent or Name (A–Z).", True)
            return
        if self._view.get("computerSort", "recent") == option:
            return
        self._action("computer_sort", lambda: self.runtime.admin_action(
            "computer_sort", option=option))

    @Slot()
    def selectFirstUnlock(self) -> None:
        row = next((item for item in self.pc_model.rows
                    if item["unlockRequested"]), None)
        if row:
            self.selectPc(row["pcId"])

    def _eligible(self, pc_id: str, action: str) -> dict:
        row = next((item for item in self.pc_model.rows if item["pcId"] == pc_id), None)
        if not row or not row.get(action):
            raise ValueError("This action is unavailable for this PC now.")
        return row

    @Slot(str, str, str, str)
    def startSession(self, pc_id: str, kind: str, paid: str, buffer: str) -> None:
        try:
            self._eligible(pc_id, "start")
            paid_minutes, buffer_minutes = validate_start(kind, paid, buffer)
        except Exception as error:
            self._show_notice("Check session details", str(error), True)
            return
        self._action("start", lambda: self.runtime.admin_action(
            "start", pc_id, kind=kind, paid_minutes=paid_minutes,
            buffer_minutes=buffer_minutes))

    @Slot(str, int, result=str)
    def previewAddTime(self, pc_id: str, minutes: int) -> str:
        try:
            self._eligible(pc_id, "add")
            return add_time_preview(self.runtime.snapshot(), pc_id, minutes)
        except Exception as error:
            return str(error)

    @Slot(str, int)
    def addTime(self, pc_id: str, minutes: int) -> None:
        try:
            self._eligible(pc_id, "add")
            validate_add(minutes)
        except Exception as error:
            self._show_notice("Cannot add time", str(error), True)
            return
        self._action("add", lambda: self.runtime.admin_action(
            "add", pc_id, minutes=minutes))

    @Slot(str)
    def endSession(self, pc_id: str) -> None:
        try:
            row = next(item for item in self.pc_model.rows if item["pcId"] == pc_id)
            if not (row["end"] or row["lock"]):
                raise ValueError("This PC is unavailable for locking.")
        except (StopIteration, ValueError) as error:
            self._show_notice("Cannot lock PC", str(error), True)
            return
        self._action("end", lambda: self.runtime.admin_action(
            "end", pc_id, reason="admin"))

    @Slot(str)
    def pauseSession(self, pc_id: str) -> None:
        self._action("pause", lambda: self.runtime.admin_action("pause", pc_id))

    @Slot(str)
    def resumeSession(self, pc_id: str) -> None:
        self._action("resume", lambda: self.runtime.admin_action("resume", pc_id))

    @Slot(str)
    def exitRemoteSoftware(self, pc_id: str) -> None:
        try:
            self._eligible(pc_id, "exitSoftware")
        except ValueError as error:
            self._show_notice("Cannot exit software", str(error), True)
            return
        self._action("remote_exit", lambda: self.runtime.remote_exit(pc_id),
                     "Target accepted shutdown; waiting for it to disconnect.")

    @Slot(str)
    def preparePairing(self, request_id: str) -> None:
        self._action("prepare_pairing", lambda:
                     self.runtime.prepare_join_pairing(request_id))

    @Slot(str, str, str, str)
    def approvePairing(self, request_id: str, pc_id: str,
                       code: str, name: str) -> None:
        self._action("approve_pairing", lambda: self.runtime.approve_join(
            request_id, pc_id, code, name), "PC approved. It can now join the café.")

    @Slot(str)
    def rejectPairing(self, request_id: str) -> None:
        self._action("reject_pairing", lambda: self.runtime.reject_join(request_id))

    @Slot(str, str)
    def renamePc(self, pc_id: str, name: str) -> None:
        self._action("rename_pc", lambda: self.runtime.admin_action(
            "rename_pc", pc_id, name=name))

    @Slot(str)
    def clearHistory(self, pc_id: str) -> None:
        target = next((item for item in self._view.get("historyTargets", [])
                       if item["pcId"] == pc_id), None)
        if not target or not target["online"] or not target["count"]:
            self._show_notice("Cannot clear history",
                              "Select an online PC with completed sessions.", True)
            return
        self._action("clear_history", lambda: self.runtime.clear_history(pc_id),
                     f"Completed history cleared for {target['name']}.")

    @Slot(str, str, str, str)
    def saveSettings(self, cafe_name: str, admin_name: str,
                     grace: str, signout: str) -> None:
        try:
            grace_minutes, signout_minutes = int(grace), int(signout)
        except ValueError:
            self._show_notice("Invalid settings", "Enter whole-number minutes.", True)
            return
        self._action("settings", lambda: self.runtime.admin_action(
            "settings", cafe_name=cafe_name, admin_name=admin_name,
            grace_minutes=grace_minutes, auto_signout_minutes=signout_minutes,
            developer_website=DEVELOPER_WEBSITE,
            developer_contact=DEVELOPER_EMAIL), "Settings saved for the café.")

    @Slot(str, str, str)
    def changePassword(self, old: str, new: str, confirmation: str) -> None:
        if new != confirmation:
            self._show_notice("Passwords differ", "Enter the same new password twice.", True)
            return
        def work():
            if not verify_password(old, self.runtime.snapshot()["admin_password"]):
                raise PermissionError("Current Admin password is incorrect.")
            self.runtime.admin_action("password", new_password=new)
        self._action("password", work, "Admin password changed.")

    @Slot(str)
    def setAvatar(self, path: str) -> None:
        def work():
            clean = path.strip().strip('"')
            if clean.startswith("file:///"):
                from PySide6.QtCore import QUrl
                clean = QUrl(clean).toLocalFile()
            source = Path(clean)
            if source.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                raise ValueError("Choose a PNG or JPG/JPEG file.")
            if not source.is_file() or source.stat().st_size > 5_000_000:
                raise ValueError("Choose an existing image smaller than 5 MB.")
            image = QImage(str(source))
            if image.isNull():
                raise ValueError("This image cannot be read.")
            image = image.scaled(512, 512, Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
            from PySide6.QtCore import QBuffer, QIODevice
            def encoded_image(format_name, quality=-1):
                data = QByteArray()
                buffer = QBuffer(data)
                buffer.open(QIODevice.WriteOnly)
                image.save(buffer, format_name, quality)
                buffer.close()
                return data
            data = encoded_image("PNG")
            if data.size() > 220_000 and not image.hasAlphaChannel():
                data = encoded_image("JPG", 82)
            if data.size() > 256_000:
                raise ValueError("Image is still too large after resizing.")
            encoded = base64.b64encode(bytes(data)).decode("ascii")
            self.runtime.admin_action("logo", logo=encoded)
        self._action("avatar", work, "Café avatar saved and shared.")

    @Slot()
    def removeAvatar(self) -> None:
        self._action("remove_avatar", lambda: self.runtime.admin_action(
            "logo", logo=None), "Café avatar removed.")

    @Slot(result=str)
    def browseAvatar(self) -> str:
        path, _ = QFileDialog.getOpenFileName(
            None, "Choose cafe image", "", "Images (*.png *.jpg *.jpeg)")
        return path

    @Slot(str, result=str)
    def avatarPreviewSource(self, path: str) -> str:
        """Return a QML-safe URL only when the selected image is readable."""
        clean = path.strip().strip('"')
        if clean.startswith("file:"):
            clean = QUrl(clean).toLocalFile()
        source = Path(clean) if clean else None
        if (not source or source.suffix.lower() not in (".png", ".jpg", ".jpeg")
                or not source.is_file() or source.stat().st_size > 5_000_000
                or QImage(str(source)).isNull()):
            return ""
        return QUrl.fromLocalFile(str(source)).toString()

    @Slot(str)
    def searchOtherPools(self, password: str) -> None:
        def work():
            if not verify_password(password, self.runtime.snapshot()["admin_password"]):
                raise PermissionError("Incorrect Admin password.")
            return self._distinct_pools(discover(), self.store.current_pool_id)
        def done(found, error):
            if error:
                self._show_notice("Cannot change café", str(error), True)
            else:
                self._set_view(otherPools=found)
        self._submit("other_pools", work, done, blocking=True)

    @Slot(str)
    def moveToPool(self, pool_id: str) -> None:
        pool = next((item for item in self._view.get("otherPools", [])
                     if item["poolId"] == pool_id), None)
        snap = self.runtime.snapshot()
        if not pool or not snap:
            self._show_notice("Choose a café", "Select a destination pool.", True)
            return
        self._begin_join(pool, snap["members"][self.store.pc_id]["name"],
                         snap["pool_id"])

    @Slot(str)
    def renamePlayer(self, name: str) -> None:
        clean = name.strip()
        if not 1 <= len(clean) <= 40:
            self._show_notice("Invalid player name", "Use 1 to 40 characters.", True)
            return
        if self.child:
            self._post_child("rename", {"name": clean})
        else:
            self._action("rename_player", lambda:
                         self.runtime.submit_customer_action("rename", name=clean))

    @Slot()
    def requestUnlock(self) -> None:
        if not self.child or time.time() < self._cooldown_until:
            return
        self._cooldown_until = time.time() + 10
        self._post_child("request_unlock")
        self._set_view(requestCooldown=10)

    @Slot()
    def customerEnd(self) -> None:
        if self.child:
            self._post_child("end")

    def _post_child(self, action: str, payload: dict | None = None) -> None:
        if payload and "password" in payload:
            public = self.store.local("controller_command_public")
            if not public:
                self._show_notice("Staff access unavailable",
                                  "The controller password channel is unavailable.", True)
                return
            payload = {"password_sealed": seal_local_password(
                public, payload["password"])}
        self.store.set_local("customer_feedback", "Saving on this PC…" if action in
                             ("end", "rename", "close_software", "staff_start",
                              "staff_add") else "Sending request to Admin…")
        self.store.post_local_command(action, payload)
        self._set_view(feedback=self.store.local("customer_feedback"))

    def _staff_is_authorized(self) -> bool:
        if (not self.child or not self._staff_credential
                or time.monotonic() >= self._staff_authorized_until):
            return False
        snap = self.store.snapshot()
        return bool(snap and snap.get("admin_password") == self._staff_verifier)

    def _revoke_staff(self) -> None:
        was_authorized = bool(self._staff_credential)
        self._staff_authorized_until = 0.0
        self._staff_credential = None
        self._staff_verifier = None
        if was_authorized:
            self.staffAuthChanged.emit()

    def _require_staff(self) -> dict | None:
        if not self._staff_is_authorized():
            self._revoke_staff()
            self._show_notice("Staff access locked",
                              "Enter the Admin password again.", True)
            return None
        self._staff_authorized_until = time.monotonic() + 60
        return dict(self._staff_credential or {})

    @Slot(str)
    def authenticateStaff(self, password: str) -> None:
        if not self.child or self._busy:
            return
        if time.monotonic() < self._staff_blocked_until:
            remaining = max(1, int(self._staff_blocked_until - time.monotonic()))
            self._show_notice("Staff access temporarily locked",
                              f"Wait {remaining} seconds before retrying.", True)
            return

        def work():
            snap = self.store.snapshot()
            verifier = snap.get("admin_password") if snap else None
            public = self.store.local("controller_command_public")
            if not verifier or not public:
                raise PermissionError("A trusted local Staff verifier is unavailable.")
            if not verify_password(password, verifier):
                raise PermissionError("Incorrect Admin password.")
            return verifier, seal_local_password(public, password)

        def done(result, error):
            if error:
                self._revoke_staff()
                self._staff_failed_attempts += 1
                if self._staff_failed_attempts >= 5:
                    self._staff_failed_attempts = 0
                    self._staff_blocked_until = time.monotonic() + 30
                    message = "Too many attempts. Staff access is locked for 30 seconds."
                else:
                    message = str(error)
                self._show_notice("Staff authentication failed", message, True)
                return
            self._staff_failed_attempts = 0
            self._staff_blocked_until = 0.0
            self._staff_verifier, self._staff_credential = result
            self._staff_authorized_until = time.monotonic() + 60
            self.staffAuthChanged.emit()

        self._submit("staff_auth", work, done, blocking=True)

    @Slot()
    def touchStaffAccess(self) -> None:
        if self._staff_is_authorized():
            self._staff_authorized_until = time.monotonic() + 60

    @Slot()
    def lockStaffAccess(self) -> None:
        self._revoke_staff()

    @Slot(str, str, str)
    def staffStartSession(self, kind: str, paid: str, buffer: str) -> None:
        credential = self._require_staff()
        if credential is None:
            return
        try:
            paid_minutes, buffer_minutes = validate_start(kind, paid, buffer)
        except Exception as error:
            self._show_notice("Invalid session", str(error), True)
            self._revoke_staff()
            return
        self._post_child("staff_start", {
            "password_sealed": credential,
            "operation_id": uuid.uuid4().hex,
            "kind": kind, "paid_minutes": paid_minutes,
            "buffer_minutes": buffer_minutes,
        })
        self._revoke_staff()

    @Slot(int, result=str)
    def previewStaffAdd(self, minutes: int) -> str:
        if not self._staff_is_authorized():
            return ""
        try:
            return add_time_preview(self.store.snapshot(), self.store.pc_id,
                                    validate_add(minutes))
        except Exception as error:
            return str(error)

    @Slot(int)
    def staffAddTime(self, minutes: int) -> None:
        credential = self._require_staff()
        if credential is None:
            return
        try:
            minutes = validate_add(minutes)
        except Exception as error:
            self._show_notice("Invalid added time", str(error), True)
            self._revoke_staff()
            return
        self._post_child("staff_add", {
            "password_sealed": credential,
            "operation_id": uuid.uuid4().hex, "minutes": minutes,
        })
        self._revoke_staff()

    @Slot()
    def switchAdmin(self) -> None:
        if self.child:
            credential = self._require_staff()
            if credential is None:
                return
            if self._view.get("hasSession"):
                self._show_notice("Session active",
                                  "End this session before switching to Admin.", True)
                self._revoke_staff()
                return
            self._post_child("open_admin", {"password_sealed": credential})
            self._revoke_staff()

    @Slot()
    def closeSoftware(self) -> None:
        if self.child:
            self.host.trace_shutdown("staff_shutdown_requested")
            credential = self._require_staff()
            if credential is not None:
                self.host.trace_shutdown("staff_authorization_confirmed")
                self._post_child("close_software", {"password_sealed": credential})
                self.host.trace_shutdown("staff_shutdown_command_posted")
                self._revoke_staff()

    @Slot(result=bool)
    def hasLocalSession(self) -> bool:
        snap = self.store.snapshot()
        return bool(snap and snap["sessions"].get(self.store.pc_id))

    @Slot()
    def goDesktop(self) -> None:
        if not self.child or not self._view.get("accessAllowed"):
            return
        self._revoke_staff()
        try:
            desktops.switch(self.host.default_handle)
        except OSError as error:
            self._show_notice("Desktop switch failed", str(error), True)

    @Slot()
    def openConsole(self) -> None:
        if not self.child:
            self.host.open_console()

    @Slot(int, int)
    def updateWidgetSize(self, required_width: int, required_height: int) -> None:
        if not self.child:
            self.host.update_widget_size(required_width, required_height)

    @Slot()
    def closeAdmin(self) -> None:
        self.closeApplication()

    @Slot()
    def closeApplication(self) -> None:
        if not self.child:
            self.host.shutdown()

    def tick(self) -> None:
        if self._shutting_down:
            return
        if self.child:
            active_desktop = desktops.active_desktop_name()
            if (self._staff_credential and active_desktop
                    and active_desktop != "CafeConsole"):
                self._revoke_staff()
            if self._staff_credential and not self._staff_is_authorized():
                self._revoke_staff()
            self.host.check_child_liveness()
            return
        try:
            if (self.host.shutdown_state == "idle" and
                    not self.host.console_transitioning and self.host.console_process
                    and desktops.exited(self.host.console_process)):
                self._show_notice("User Console stopped",
                                  "CafeConsole exited. Default will be restored.", True)
                # Keep the checkpoint for interrupted-session recovery on restart.
                self.host.shutdown(finalize_local_session=False)
                return
            while not self._busy and self.host.shutdown_state in ("idle", "failed"):
                commands = self.store.take_local_commands(limit=1)
                if not commands:
                    break
                self._handle_local_command(*commands[0])
            while True:
                try:
                    kind, value = self.runtime.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "role_lost":
                    if self._mode == "admin":
                        self._enter_user()
                elif kind == "takeover_ready":
                    self._enter_admin()
                elif kind == "takeover_failed":
                    self.store.set_local("customer_feedback",
                                         f"Admin transfer failed: {value}")
                    self._show_notice("Admin transfer failed", str(value), True)
                elif kind == "error":
                    print(f"Game Cafe Console: {value}")
                elif kind == "remote_exit":
                    self.host.shutdown(source="remote_exit")
            if self._mode == "admin" and not self.runtime.is_admin():
                self._enter_user()
        except Exception as error:
            print(f"Controller UI error: {error}")

    def _handle_local_command(self, action: str, payload: dict) -> None:
        if action in ("request_unlock", "rename", "end"):
            def work():
                self.runtime.submit_customer_action(action, **payload)
                self.store.set_local("customer_feedback",
                                     "Request sent to Admin." if action == "request_unlock"
                                     else "Saved locally; synchronizing when available.")
            def done(result, error):
                if error:
                    self.store.set_local("customer_feedback", f"Request failed: {error}")
                self.refresh()
            self._submit("customer_command", work, done)
        elif action == "open_admin":
            def work():
                password = open_local_password(self.host.command_private_key,
                                               payload["password_sealed"])
                self.runtime.take_admin(password)
            def done(result, error):
                if error:
                    self.store.set_local("customer_feedback",
                                         f"Admin transfer failed: {error}")
                    self._show_notice("Admin transfer failed", str(error), True)
                else:
                    self._enter_admin()
            self._submit("take_admin", work, done, blocking=True)
        elif action == "close_software":
            def work():
                password = open_local_password(self.host.command_private_key,
                                               payload["password_sealed"])
                snap = self.runtime.snapshot()
                if not verify_password(password, snap["admin_password"]):
                    raise PermissionError("Incorrect Admin password.")
            def done(result, error):
                if error:
                    self.store.set_local("customer_feedback", f"Close failed: {error}")
                    self._show_notice("Cannot close software", str(error), True)
                else:
                    self.host.trace_shutdown("staff_command_verified")
                    self.host.shutdown(source="staff_access")
            self._submit("close_software", work, done, blocking=True)
        elif action in ("staff_start", "staff_add"):
            def work():
                password = open_local_password(self.host.command_private_key,
                                               payload["password_sealed"])
                details = ({"kind": payload["kind"],
                            "paid_minutes": payload["paid_minutes"],
                            "buffer_minutes": payload["buffer_minutes"]}
                           if action == "staff_start" else
                           {"minutes": payload["minutes"]})
                return self.runtime.local_staff_session_action(
                    "start" if action == "staff_start" else "add",
                    password, payload["operation_id"], **details)

            def done(result, error):
                self.store.set_local(
                    "customer_feedback",
                    f"Staff action failed: {error}" if error else
                    "Staff session change saved locally.")
                if error:
                    self._show_notice("Staff action failed", str(error), True)
                self.refresh()

            self._submit(action, work, done, blocking=True)

    def shutdown(self) -> None:
        lifecycle_event("qt_bridge_shutdown_started", child=self.child,
                        pending_jobs=len(self._jobs))
        self._shutting_down = True
        self._revoke_staff()
        self.tick_timer.stop()
        self.refresh_timer.stop()
        self.join_timer.stop()
        # Finish in-flight workers before the host closes the shared SQLite store.
        self._executor.shutdown(wait=True, cancel_futures=True)
        lifecycle_event("qt_bridge_shutdown_completed", child=self.child)
