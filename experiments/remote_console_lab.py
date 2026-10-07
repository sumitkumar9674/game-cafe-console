"""Unauthenticated private-LAN feasibility test for Game Cafe Console."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
import ipaddress
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import uuid


if sys.platform != "win32":
    raise SystemExit("Remote Console Lab can only run on Windows.")


# This protocol has no authentication or encryption. It is strictly for a
# temporary test on a trusted private LAN and must not be exposed to the internet.
PROTOCOL_ID = "GAME_CAFE_REMOTE_TEST/1"
UDP_DISCOVERY_PORT = 47990
TCP_COMMAND_PORT = 47991

DISCOVERY_INTERVAL_SECONDS = 3.0
DISCOVERY_RESPONSE_WINDOW_SECONDS = 1.0
STATUS_POLL_INTERVAL_SECONDS = 2.0
OFFLINE_AFTER_SECONDS = 7.0
NETWORK_TIMEOUT_SECONDS = 2.0
MAXIMUM_MESSAGE_BYTES = 4096

DEFAULT_DESKTOP_NAME = "Default"
CAFE_DESKTOP_NAME = "CafeConsole"
CAFE_DESKTOP_PATH = rf"WinSta0\{CAFE_DESKTOP_NAME}"

DESKTOP_READOBJECTS = 0x0001
DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_SWITCHDESKTOP = 0x0100
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
UOI_NAME = 2

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF

CHILD_READY_TIMEOUT_MS = 15_000
CHILD_EXIT_TIMEOUT_MS = 3_000
UI_POLL_INTERVAL_MS = 200

STATE_LOCKED = "LOCKED"
STATE_UNLOCKED = "UNLOCKED"
STATE_UNKNOWN = "UNKNOWN"


class STARTUPINFOW(ctypes.Structure):
    """ctypes representation of the Win32 STARTUPINFOW structure."""

    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    """ctypes representation of Win32 PROCESS_INFORMATION."""

    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenDesktopW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]
user32.OpenDesktopW.restype = wintypes.HANDLE

user32.CreateDesktopW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
]
user32.CreateDesktopW.restype = wintypes.HANDLE

user32.OpenInputDesktop.argtypes = [
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
]
user32.OpenInputDesktop.restype = wintypes.HANDLE

user32.GetUserObjectInformationW.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetUserObjectInformationW.restype = wintypes.BOOL

user32.SwitchDesktop.argtypes = [wintypes.HANDLE]
user32.SwitchDesktop.restype = wintypes.BOOL

user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.CloseDesktop.restype = wintypes.BOOL

kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
]
kernel32.CreateEventW.restype = wintypes.HANDLE

kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE

kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL

kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD

kernel32.WaitForMultipleObjects.argtypes = [
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.BOOL,
    wintypes.DWORD,
]
kernel32.WaitForMultipleObjects.restype = wintypes.DWORD

kernel32.GetExitCodeProcess.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL

kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.GetCurrentProcessId.argtypes = []
kernel32.GetCurrentProcessId.restype = wintypes.DWORD

kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFOW),
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wintypes.BOOL


def win32_error(operation: str) -> OSError:
    """Return an OSError containing the last Win32 error message."""
    error_code = ctypes.get_last_error()
    return OSError(
        error_code,
        f"{operation} failed: {ctypes.FormatError(error_code).strip()}",
    )


def get_computer_name() -> str:
    """Return the Windows computer name used in discovery and status replies."""
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


def get_local_ipv4(peer_ip: str | None = None) -> str:
    """Find a useful LAN IPv4 address without requiring internet access."""
    if peer_ip:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((peer_ip, 9))
            address = probe.getsockname()[0]
            if not address.startswith("127."):
                return address
        except OSError:
            pass
        finally:
            probe.close()

    try:
        addresses = socket.getaddrinfo(
            socket.gethostname(),
            None,
            socket.AF_INET,
            socket.SOCK_DGRAM,
        )
        for address_info in addresses:
            address = address_info[4][0]
            if not address.startswith("127."):
                return address
    except OSError:
        pass
    return "Unavailable"


def open_desktop(name: str) -> wintypes.HANDLE:
    """Open a desktop handle that may be made visible."""
    desktop_handle = user32.OpenDesktopW(name, 0, False, DESKTOP_SWITCHDESKTOP)
    if not desktop_handle:
        raise win32_error(f'OpenDesktopW("{name}")')
    return desktop_handle


def create_cafe_desktop() -> wintypes.HANDLE:
    """Create or open CafeConsole in the current interactive window station."""
    desktop_handle = user32.CreateDesktopW(
        CAFE_DESKTOP_NAME,
        None,
        None,
        0,
        DESKTOP_CREATEWINDOW | DESKTOP_SWITCHDESKTOP,
        None,
    )
    if not desktop_handle:
        raise win32_error(f'CreateDesktopW("{CAFE_DESKTOP_NAME}")')
    return desktop_handle


def switch_desktop(desktop_handle: wintypes.HANDLE, name: str) -> None:
    """Make the desktop represented by desktop_handle visible."""
    if not user32.SwitchDesktop(desktop_handle):
        raise win32_error(f'SwitchDesktop("{name}")')


def close_desktop_handle(desktop_handle: wintypes.HANDLE | None) -> None:
    """Close a desktop handle and report cleanup failures."""
    if desktop_handle and not user32.CloseDesktop(desktop_handle):
        print(win32_error("CloseDesktop"), file=sys.stderr)


def close_kernel_handle(handle: wintypes.HANDLE | None) -> None:
    """Close a kernel handle and report cleanup failures."""
    if handle and not kernel32.CloseHandle(handle):
        print(win32_error("CloseHandle"), file=sys.stderr)


def get_active_desktop_name() -> str | None:
    """Query the actual input desktop name, returning None when unavailable."""
    desktop_handle = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not desktop_handle:
        return None

    try:
        required_bytes = wintypes.DWORD()
        user32.GetUserObjectInformationW(
            desktop_handle,
            UOI_NAME,
            None,
            0,
            ctypes.byref(required_bytes),
        )
        if required_bytes.value == 0:
            return None

        character_count = (
            required_bytes.value + ctypes.sizeof(ctypes.c_wchar) - 1
        ) // ctypes.sizeof(ctypes.c_wchar)
        name_buffer = ctypes.create_unicode_buffer(character_count)
        if not user32.GetUserObjectInformationW(
            desktop_handle,
            UOI_NAME,
            name_buffer,
            required_bytes.value,
            ctypes.byref(required_bytes),
        ):
            return None
        return name_buffer.value
    finally:
        close_desktop_handle(desktop_handle)


def get_current_state() -> str:
    """Map the real input desktop name to the experiment's public state."""
    desktop_name = get_active_desktop_name()
    if desktop_name is None:
        return STATE_UNKNOWN
    if desktop_name.casefold() == CAFE_DESKTOP_NAME.casefold():
        return STATE_LOCKED
    if desktop_name.casefold() == DEFAULT_DESKTOP_NAME.casefold():
        return STATE_UNLOCKED
    return STATE_UNKNOWN


def create_named_event(name: str) -> wintypes.HANDLE:
    """Create a manual-reset, initially non-signaled local Win32 event."""
    event_handle = kernel32.CreateEventW(None, True, False, name)
    if not event_handle:
        raise win32_error(f'CreateEventW("{name}")')
    return event_handle


def open_named_event(name: str, access: int) -> wintypes.HANDLE:
    """Open a named event belonging to the Client controller."""
    event_handle = kernel32.OpenEventW(access, False, name)
    if not event_handle:
        raise win32_error(f'OpenEventW("{name}")')
    return event_handle


def set_event(event_handle: wintypes.HANDLE) -> None:
    """Signal a Win32 event."""
    if not kernel32.SetEvent(event_handle):
        raise win32_error("SetEvent")


def event_is_signaled(event_handle: wintypes.HANDLE) -> bool:
    """Check a Win32 event without blocking."""
    wait_result = kernel32.WaitForSingleObject(event_handle, 0)
    if wait_result == WAIT_OBJECT_0:
        return True
    if wait_result == WAIT_TIMEOUT:
        return False
    if wait_result == WAIT_FAILED:
        raise win32_error("WaitForSingleObject")
    raise RuntimeError(f"Unexpected event wait result: 0x{wait_result:08X}")


def process_has_exited(process_handle: wintypes.HANDLE) -> bool:
    """Check a child process without blocking."""
    wait_result = kernel32.WaitForSingleObject(process_handle, 0)
    if wait_result == WAIT_OBJECT_0:
        return True
    if wait_result == WAIT_TIMEOUT:
        return False
    if wait_result == WAIT_FAILED:
        raise win32_error("WaitForSingleObject")
    raise RuntimeError(f"Unexpected process wait result: 0x{wait_result:08X}")


def get_process_exit_code(process_handle: wintypes.HANDLE) -> int:
    """Read the exit code from a process that has finished."""
    exit_code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
        raise win32_error("GetExitCodeProcess")
    return exit_code.value


def self_launch_arguments(internal_arguments: list[str]) -> tuple[str, list[str], str]:
    """Build the correct child command for source and PyInstaller execution."""
    executable_path = str(Path(sys.executable).resolve())
    if getattr(sys, "frozen", False):
        arguments = [executable_path, *internal_arguments]
        working_directory = str(Path(executable_path).parent)
    else:
        script_path = str(Path(__file__).resolve())
        arguments = [executable_path, script_path, *internal_arguments]
        working_directory = str(Path(script_path).parent.parent)
    return executable_path, arguments, working_directory


def launch_console_child(
    ready_event_name: str,
    shutdown_event_name: str,
) -> wintypes.HANDLE:
    """Launch this same application on WinSta0\\CafeConsole."""
    executable_path, arguments, working_directory = self_launch_arguments(
        [
            "--console-child",
            "--ready-event",
            ready_event_name,
            "--shutdown-event",
            shutdown_event_name,
        ]
    )

    # CreateProcessW may modify this buffer. list2cmdline correctly quotes both
    # source and packaged paths containing spaces.
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
    startup_info = STARTUPINFOW()
    startup_info.cb = ctypes.sizeof(startup_info)
    # Assigning lpDesktop here places the process on CafeConsole before Tk starts.
    startup_info.lpDesktop = CAFE_DESKTOP_PATH
    process_info = PROCESS_INFORMATION()

    if not kernel32.CreateProcessW(
        executable_path,
        command_line,
        None,
        None,
        False,
        0,
        None,
        working_directory,
        ctypes.byref(startup_info),
        ctypes.byref(process_info),
    ):
        raise win32_error("CreateProcessW")

    close_kernel_handle(process_info.hThread)
    return process_info.hProcess


def wait_for_console_ready(
    ready_event: wintypes.HANDLE,
    process_handle: wintypes.HANDLE,
) -> None:
    """Wait until the child UI is ready or the child exits early."""
    handles = (wintypes.HANDLE * 2)(ready_event, process_handle)
    wait_result = kernel32.WaitForMultipleObjects(
        len(handles),
        handles,
        False,
        CHILD_READY_TIMEOUT_MS,
    )

    if wait_result == WAIT_OBJECT_0:
        if process_has_exited(process_handle):
            code = get_process_exit_code(process_handle)
            raise RuntimeError(
                f"CafeConsole exited after initialization with code {code}."
            )
        return
    if wait_result == WAIT_OBJECT_0 + 1:
        code = get_process_exit_code(process_handle)
        raise RuntimeError(f"CafeConsole exited before it was ready (code {code}).")
    if wait_result == WAIT_TIMEOUT:
        raise TimeoutError(
            "CafeConsole did not become ready within "
            f"{CHILD_READY_TIMEOUT_MS // 1000} seconds."
        )
    if wait_result == WAIT_FAILED:
        raise win32_error("WaitForMultipleObjects")
    raise RuntimeError(f"Unexpected startup wait result: 0x{wait_result:08X}")


def send_json_line(connection: socket.socket, message: dict[str, object]) -> None:
    """Send one compact UTF-8 JSON message terminated by a newline."""
    payload = json.dumps(message, separators=(",", ":")) + "\n"
    connection.sendall(payload.encode("utf-8"))


def receive_json_line(connection: socket.socket) -> dict[str, object]:
    """Receive one size-limited UTF-8 JSON-line object."""
    data = bytearray()
    while b"\n" not in data:
        chunk = connection.recv(1024)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAXIMUM_MESSAGE_BYTES:
            raise ValueError("Network message is too large.")

    if not data:
        raise ValueError("Empty network message.")
    line = bytes(data).split(b"\n", 1)[0]
    message = json.loads(line.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("Network message must be a JSON object.")
    return message


class ClientNetworkServer:
    """Listen for unauthenticated test discovery and command traffic."""

    def __init__(self, command_handler) -> None:
        self.command_handler = command_handler
        self.stop_event = threading.Event()
        self.udp_socket: socket.socket | None = None
        self.tcp_socket: socket.socket | None = None
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        """Bind both ports before reporting that the Client is listening."""
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            udp_socket.bind(("", UDP_DISCOVERY_PORT))
            udp_socket.settimeout(0.5)

            tcp_socket.bind(("", TCP_COMMAND_PORT))
            tcp_socket.listen()
            tcp_socket.settimeout(0.5)
        except Exception:
            udp_socket.close()
            tcp_socket.close()
            raise

        self.udp_socket = udp_socket
        self.tcp_socket = tcp_socket
        self.threads = [
            threading.Thread(
                target=self._discovery_loop,
                name="client-discovery",
                daemon=True,
            ),
            threading.Thread(
                target=self._command_accept_loop,
                name="client-commands",
                daemon=True,
            ),
        ]
        for thread in self.threads:
            thread.start()

    def stop(self) -> None:
        """Stop accepting new network work and join listener threads."""
        self.stop_event.set()
        for network_socket in (self.udp_socket, self.tcp_socket):
            if network_socket is not None:
                try:
                    network_socket.close()
                except OSError:
                    pass
        for thread in self.threads:
            thread.join(timeout=1.5)
        self.threads.clear()
        self.udp_socket = None
        self.tcp_socket = None

    def _discovery_loop(self) -> None:
        """Reply directly to valid UDP discovery requests."""
        if self.udp_socket is None:
            return
        while not self.stop_event.is_set():
            try:
                payload, sender = self.udp_socket.recvfrom(MAXIMUM_MESSAGE_BYTES)
                request = json.loads(payload.decode("utf-8"))
                if not isinstance(request, dict):
                    continue
                if request.get("protocol") != PROTOCOL_ID:
                    continue
                if request.get("type") != "discover":
                    continue

                response = {
                    "protocol": PROTOCOL_ID,
                    "type": "discovery_response",
                    "computer_name": get_computer_name(),
                    "ip": get_local_ipv4(sender[0]),
                    "tcp_port": TCP_COMMAND_PORT,
                    "state": get_current_state(),
                }
                encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
                self.udp_socket.sendto(encoded, sender)
            except socket.timeout:
                continue
            except (OSError, UnicodeError, json.JSONDecodeError):
                if not self.stop_event.is_set():
                    continue

    def _command_accept_loop(self) -> None:
        """Accept TCP connections without blocking shutdown."""
        if self.tcp_socket is None:
            return
        while not self.stop_event.is_set():
            try:
                connection, peer = self.tcp_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            threading.Thread(
                target=self._handle_connection,
                args=(connection, peer),
                name=f"client-command-{peer[0]}",
                daemon=True,
            ).start()

    def _handle_connection(
        self,
        connection: socket.socket,
        peer: tuple[str, int],
    ) -> None:
        """Validate one request, execute it locally, and return one response."""
        del peer
        with connection:
            connection.settimeout(NETWORK_TIMEOUT_SECONDS)
            try:
                request = receive_json_line(connection)
                if request.get("protocol") != PROTOCOL_ID:
                    raise ValueError("Unsupported protocol.")
                if request.get("type") != "command":
                    raise ValueError("Expected a command message.")

                command = request.get("command")
                if not isinstance(command, str):
                    raise ValueError("Command must be a string.")
                response = self.command_handler(command.upper())
            except Exception as error:
                response = {
                    "ok": False,
                    "message": str(error),
                    "state": get_current_state(),
                }

            response.update(
                {
                    "protocol": PROTOCOL_ID,
                    "type": "command_response",
                    "computer_name": get_computer_name(),
                }
            )
            try:
                send_json_line(connection, response)
            except OSError:
                pass


@dataclass
class AdminClientRecord:
    """The Admin UI's latest view of one Client PC."""

    ip_address: str
    tcp_port: int = TCP_COMMAND_PORT
    computer_name: str = "Unknown"
    state: str = STATE_UNKNOWN
    console_available: bool | None = None
    last_contact_monotonic: float = 0.0
    last_contact_text: str = "Never"


class AdminNetworkWorkers:
    """Run blocking Admin discovery, polling, and commands off the Tk thread."""

    def __init__(self, ui_events: queue.Queue) -> None:
        self.ui_events = ui_events
        self.stop_event = threading.Event()
        self.discovery_wakeup = threading.Event()
        self.command_queue: queue.Queue[tuple[str, int, str] | None] = queue.Queue()
        self.targets: dict[str, int] = {}
        self.targets_lock = threading.Lock()
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        """Start Admin background workers."""
        self.threads = [
            threading.Thread(
                target=self._discovery_loop,
                name="admin-discovery",
                daemon=True,
            ),
            threading.Thread(
                target=self._status_loop,
                name="admin-status",
                daemon=True,
            ),
            threading.Thread(
                target=self._command_loop,
                name="admin-commands",
                daemon=True,
            ),
        ]
        for thread in self.threads:
            thread.start()

    def stop(self) -> None:
        """Stop Admin workers without blocking Tk indefinitely."""
        self.stop_event.set()
        self.discovery_wakeup.set()
        self.command_queue.put(None)
        for thread in self.threads:
            thread.join(timeout=NETWORK_TIMEOUT_SECONDS + 0.5)
        self.threads.clear()

    def remember_target(self, ip_address: str, tcp_port: int) -> None:
        """Add or update a Client endpoint for status polling."""
        with self.targets_lock:
            self.targets[ip_address] = tcp_port

    def discover_now(self) -> None:
        """Wake the periodic discovery thread immediately."""
        self.discovery_wakeup.set()

    def send_command(self, ip_address: str, tcp_port: int, command: str) -> None:
        """Queue a command for the single command worker."""
        self.command_queue.put((ip_address, tcp_port, command))

    def _discovery_loop(self) -> None:
        """Broadcast discovery periodically and collect direct responses."""
        while not self.stop_event.is_set():
            self._perform_discovery()
            self.discovery_wakeup.wait(DISCOVERY_INTERVAL_SECONDS)
            self.discovery_wakeup.clear()

    def _perform_discovery(self) -> None:
        discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            discovery_socket.settimeout(0.2)
            request = {
                "protocol": PROTOCOL_ID,
                "type": "discover",
            }
            payload = json.dumps(request, separators=(",", ":")).encode("utf-8")
            discovery_socket.sendto(payload, ("255.255.255.255", UDP_DISCOVERY_PORT))

            deadline = time.monotonic() + DISCOVERY_RESPONSE_WINDOW_SECONDS
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                try:
                    response_bytes, sender = discovery_socket.recvfrom(
                        MAXIMUM_MESSAGE_BYTES
                    )
                    response = json.loads(response_bytes.decode("utf-8"))
                    if not isinstance(response, dict):
                        continue
                    if response.get("protocol") != PROTOCOL_ID:
                        continue
                    if response.get("type") != "discovery_response":
                        continue

                    port = int(response.get("tcp_port", TCP_COMMAND_PORT))
                    self.remember_target(sender[0], port)
                    self.ui_events.put(
                        {
                            "kind": "contact",
                            "ip": sender[0],
                            "port": port,
                            "response": response,
                        }
                    )
                except socket.timeout:
                    continue
                except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                    continue
        except OSError as error:
            self.ui_events.put({"kind": "network_error", "message": str(error)})
        finally:
            discovery_socket.close()

    def _status_loop(self) -> None:
        """Periodically request authoritative status from known Clients."""
        while not self.stop_event.wait(STATUS_POLL_INTERVAL_SECONDS):
            with self.targets_lock:
                targets = list(self.targets.items())
            for ip_address, tcp_port in targets:
                if self.stop_event.is_set():
                    return
                self._request_and_report(ip_address, tcp_port, "STATUS")

    def _command_loop(self) -> None:
        """Send button-initiated commands in their queued order."""
        while not self.stop_event.is_set():
            try:
                command_item = self.command_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if command_item is None:
                return
            ip_address, tcp_port, command = command_item
            self._request_and_report(ip_address, tcp_port, command)

    def _request_and_report(
        self,
        ip_address: str,
        tcp_port: int,
        command: str,
    ) -> None:
        try:
            response = request_client_command(ip_address, tcp_port, command)
            self.ui_events.put(
                {
                    "kind": "contact",
                    "ip": ip_address,
                    "port": tcp_port,
                    "response": response,
                }
            )
        except Exception as error:
            self.ui_events.put(
                {
                    "kind": "request_failed",
                    "ip": ip_address,
                    "command": command,
                    "message": str(error),
                }
            )


def request_client_command(
    ip_address: str,
    tcp_port: int,
    command: str,
) -> dict[str, object]:
    """Send one TCP command and return its validated JSON-line response."""
    with socket.create_connection(
        (ip_address, tcp_port),
        timeout=NETWORK_TIMEOUT_SECONDS,
    ) as connection:
        connection.settimeout(NETWORK_TIMEOUT_SECONDS)
        send_json_line(
            connection,
            {
                "protocol": PROTOCOL_ID,
                "type": "command",
                "command": command,
            },
        )
        response = receive_json_line(connection)

    if response.get("protocol") != PROTOCOL_ID:
        raise ValueError("Client returned an unsupported protocol.")
    if response.get("type") != "command_response":
        raise ValueError("Client returned an unexpected response type.")
    return response


class ModeSelectionApplication:
    """Present the normal user-selectable Admin and Client modes."""

    def __init__(self) -> None:
        self.selected_mode: str | None = None
        self.root = tk.Tk()
        self.root.title("Game Cafe Remote Test")
        self.root.geometry("390x330")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._exit)
        self._build_ui()

    def _build_ui(self) -> None:
        content = tk.Frame(self.root, padx=32, pady=28)
        content.pack(fill="both", expand=True)

        tk.Label(
            content,
            text="GAME CAFE REMOTE TEST",
            font=("Segoe UI", 19, "bold"),
        ).pack(pady=(4, 8))
        tk.Label(
            content,
            text=f"This PC: {get_computer_name()}",
            font=("Segoe UI", 11),
        ).pack(pady=(0, 22))
        tk.Button(
            content,
            text="ADMIN MODE",
            command=lambda: self._choose("admin"),
            width=25,
        ).pack(pady=6)
        tk.Button(
            content,
            text="CLIENT MODE",
            command=lambda: self._choose("client"),
            width=25,
        ).pack(pady=6)
        tk.Button(content, text="EXIT", command=self._exit, width=25).pack(pady=6)

    def _choose(self, mode: str) -> None:
        self.selected_mode = mode
        self.root.destroy()

    def _exit(self) -> None:
        self.selected_mode = None
        self.root.destroy()

    def run(self) -> str | None:
        self.root.mainloop()
        return self.selected_mode


class AdminApplication:
    """Tk dashboard for discovering and controlling test Client PCs."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Game Cafe Remote Test - Admin")
        self.root.geometry("860x480")
        self.root.minsize(760, 420)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self.ui_events: queue.Queue = queue.Queue()
        self.network = AdminNetworkWorkers(self.ui_events)
        self.clients: dict[str, AdminClientRecord] = {}
        self.closing = False
        self.status_text = tk.StringVar(value="Starting discovery...")
        self.manual_ip = tk.StringVar()
        self._build_ui()

    def _build_ui(self) -> None:
        content = ttk.Frame(self.root, padding=16)
        content.pack(fill="both", expand=True)

        ttk.Label(
            content,
            text="GAME CAFE REMOTE TEST - ADMIN",
            font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        columns = ("computer", "ip", "online", "state", "last_contact")
        self.client_table = ttk.Treeview(
            content,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=11,
        )
        headings = {
            "computer": "Computer",
            "ip": "IP address",
            "online": "Network",
            "state": "State",
            "last_contact": "Last successful contact",
        }
        widths = {
            "computer": 160,
            "ip": 125,
            "online": 90,
            "state": 100,
            "last_contact": 180,
        }
        for column in columns:
            self.client_table.heading(column, text=headings[column])
            self.client_table.column(column, width=widths[column], anchor="center")
        self.client_table.pack(fill="both", expand=True)

        command_row = ttk.Frame(content)
        command_row.pack(fill="x", pady=(12, 8))
        ttk.Button(
            command_row,
            text="LOCK",
            command=lambda: self._send_selected_command("LOCK"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            command_row,
            text="UNLOCK",
            command=lambda: self._send_selected_command("UNLOCK"),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            command_row,
            text="REFRESH / DISCOVER",
            command=self._discover_now,
        ).pack(side="left")

        manual_row = ttk.Frame(content)
        manual_row.pack(fill="x", pady=6)
        ttk.Label(manual_row, text="Manual Client IPv4:").pack(side="left")
        ttk.Entry(manual_row, textvariable=self.manual_ip, width=20).pack(
            side="left", padx=8
        )
        ttk.Button(
            manual_row,
            text="ADD / CHECK IP",
            command=self._add_manual_ip,
        ).pack(side="left")

        ttk.Label(content, textvariable=self.status_text).pack(anchor="w", pady=(8, 0))

    def run(self) -> None:
        self.network.start()
        self.root.after(UI_POLL_INTERVAL_MS, self._process_network_events)
        self.root.after(500, self._refresh_table)
        self.root.mainloop()

    def _discover_now(self) -> None:
        self.status_text.set("Broadcasting discovery request...")
        self.network.discover_now()

    def _add_manual_ip(self) -> None:
        entered_ip = self.manual_ip.get().strip()
        try:
            parsed_ip = ipaddress.ip_address(entered_ip)
            if parsed_ip.version != 4:
                raise ValueError("Enter an IPv4 address.")
        except ValueError as error:
            messagebox.showerror("Invalid IP address", str(error), parent=self.root)
            return

        ip_address = str(parsed_ip)
        if ip_address not in self.clients:
            self.clients[ip_address] = AdminClientRecord(ip_address=ip_address)
        self.network.remember_target(ip_address, TCP_COMMAND_PORT)
        self.network.send_command(ip_address, TCP_COMMAND_PORT, "STATUS")
        self.status_text.set(f"Checking {ip_address}...")
        self._refresh_table()

    def _selected_client(self) -> AdminClientRecord | None:
        selection = self.client_table.selection()
        if not selection:
            messagebox.showinfo("Select a Client", "Select a Client PC first.")
            return None
        return self.clients.get(selection[0])

    def _send_selected_command(self, command: str) -> None:
        client = self._selected_client()
        if client is None:
            return
        self.status_text.set(f"Sending {command} to {client.ip_address}...")
        self.network.send_command(client.ip_address, client.tcp_port, command)

    def _process_network_events(self) -> None:
        if self.closing:
            return
        while True:
            try:
                event = self.ui_events.get_nowait()
            except queue.Empty:
                break

            event_kind = event.get("kind")
            if event_kind == "contact":
                self._record_contact(event)
            elif event_kind == "network_error":
                self.status_text.set(f"Discovery error: {event.get('message')}")
            elif event_kind == "request_failed":
                self.status_text.set(
                    f"{event.get('command')} failed for {event.get('ip')}: "
                    f"{event.get('message')}"
                )

        self.root.after(UI_POLL_INTERVAL_MS, self._process_network_events)

    def _record_contact(self, event: dict[str, object]) -> None:
        ip_address = str(event["ip"])
        tcp_port = int(event["port"])
        response = event["response"]
        if not isinstance(response, dict):
            return

        record = self.clients.get(ip_address)
        if record is None:
            record = AdminClientRecord(ip_address=ip_address)
            self.clients[ip_address] = record
        record.tcp_port = tcp_port

        computer_name = response.get("computer_name")
        if isinstance(computer_name, str) and computer_name:
            record.computer_name = computer_name
        state = response.get("state")
        if state in {STATE_LOCKED, STATE_UNLOCKED, STATE_UNKNOWN}:
            record.state = str(state)
        console_available = response.get("console_available")
        if isinstance(console_available, bool):
            record.console_available = console_available

        record.last_contact_monotonic = time.monotonic()
        record.last_contact_text = datetime.now().strftime("%H:%M:%S")
        self.network.remember_target(ip_address, tcp_port)

        ok = response.get("ok")
        message = response.get("message")
        if ok is False and isinstance(message, str):
            self.status_text.set(f"{record.computer_name}: {message}")
        else:
            self.status_text.set(
                f"Contacted {record.computer_name} at {record.ip_address}."
            )
        self._refresh_table()

    def _refresh_table(self) -> None:
        if self.closing:
            return
        now = time.monotonic()
        selected = self.client_table.selection()

        for ip_address, record in sorted(self.clients.items()):
            online = (
                record.last_contact_monotonic > 0
                and now - record.last_contact_monotonic <= OFFLINE_AFTER_SECONDS
            )
            values = (
                record.computer_name,
                record.ip_address,
                "ONLINE" if online else "OFFLINE",
                record.state,
                record.last_contact_text,
            )
            if self.client_table.exists(ip_address):
                self.client_table.item(ip_address, values=values)
            else:
                self.client_table.insert("", "end", iid=ip_address, values=values)

        if selected and self.client_table.exists(selected[0]):
            self.client_table.selection_set(selected[0])
        self.root.after(500, self._refresh_table)

    def _close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.status_text.set("Stopping network workers...")
        self.network.stop()
        self.root.destroy()


class ClientController:
    """Own Client networking and the separate CafeConsole child process."""

    def __init__(self) -> None:
        self.computer_name = get_computer_name()
        self.default_desktop: wintypes.HANDLE | None = None
        self.cafe_desktop: wintypes.HANDLE | None = None
        self.ready_event: wintypes.HANDLE | None = None
        self.shutdown_event: wintypes.HANDLE | None = None
        self.console_process: wintypes.HANDLE | None = None
        self.console_available = False
        self.network: ClientNetworkServer | None = None
        self.root: tk.Tk | None = tk.Tk()
        self.state_text = tk.StringVar(master=self.root, value=STATE_UNKNOWN)
        self.console_text = tk.StringVar(master=self.root, value="STARTING")
        self.network_text = tk.StringVar(master=self.root, value="STOPPED")
        self.shutting_down = False
        self.cleanup_complete = False
        self.desktop_lock = threading.Lock()

        event_suffix = f"{kernel32.GetCurrentProcessId()}_{uuid.uuid4().hex}"
        self.ready_event_name = rf"Local\RemoteConsoleReady_{event_suffix}"
        self.shutdown_event_name = rf"Local\RemoteConsoleShutdown_{event_suffix}"

    def run(self) -> None:
        """Initialize Client mode without initially leaving Default."""
        self._build_client_ui()
        try:
            self.default_desktop = open_desktop(DEFAULT_DESKTOP_NAME)
            self.cafe_desktop = create_cafe_desktop()
            self.ready_event = create_named_event(self.ready_event_name)
            self.shutdown_event = create_named_event(self.shutdown_event_name)
            self.console_process = launch_console_child(
                self.ready_event_name,
                self.shutdown_event_name,
            )
            wait_for_console_ready(self.ready_event, self.console_process)
            self.console_available = True
            self.console_text.set("AVAILABLE")

            # Client Mode deliberately stays on Default until a local or remote
            # LOCK request explicitly switches to CafeConsole.
            switch_desktop(self.default_desktop, DEFAULT_DESKTOP_NAME)

            self.network = ClientNetworkServer(self._handle_network_command)
            self.network.start()
            self.network_text.set("LISTENING")

            if self.root is None:
                raise RuntimeError("Client Tk window was not initialized.")
            self.root.after(UI_POLL_INTERVAL_MS, self._poll_client_state)
            self.root.mainloop()
        except Exception as error:
            print(f"Client Mode failed: {error}", file=sys.stderr)
            self._restore_default_best_effort()
            if self.root is not None and not self.shutting_down:
                messagebox.showerror("Client Mode failed", str(error), parent=self.root)
        finally:
            self._cleanup()

    def _build_client_ui(self) -> None:
        if self.root is None:
            raise RuntimeError("Client Tk root is unavailable.")
        self.root.title("Game Cafe Remote Test - Client")
        self.root.geometry("440x390")
        self.root.resizable(False, False)
        # The explicit SAFE EXIT button is the supported development exit path.
        self.root.protocol("WM_DELETE_WINDOW", self._ignore_close_request)

        content = ttk.Frame(self.root, padding=26)
        content.pack(fill="both", expand=True)
        ttk.Label(
            content,
            text="GAME CAFE REMOTE TEST - CLIENT",
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=(4, 18))
        ttk.Label(content, text=f"This PC: {self.computer_name}").pack(anchor="w")
        ttk.Label(content, text=f"Local IPv4: {get_local_ipv4()}").pack(anchor="w")

        status_frame = ttk.LabelFrame(content, text="Status", padding=12)
        status_frame.pack(fill="x", pady=16)
        ttk.Label(status_frame, text="Network:").grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.network_text).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        ttk.Label(status_frame, text="Current state:").grid(
            row=1, column=0, sticky="w"
        )
        ttk.Label(status_frame, textvariable=self.state_text).grid(
            row=1, column=1, sticky="w", padx=(12, 0)
        )
        ttk.Label(status_frame, text="CafeConsole:").grid(
            row=2, column=0, sticky="w"
        )
        ttk.Label(status_frame, textvariable=self.console_text).grid(
            row=2, column=1, sticky="w", padx=(12, 0)
        )

        ttk.Button(
            content,
            text="OPEN CONSOLE LOCALLY",
            command=self._open_console_locally,
            width=30,
        ).pack(pady=6)
        ttk.Button(
            content,
            text="SAFE EXIT CLIENT",
            command=self._safe_exit,
            width=30,
        ).pack(pady=6)

    def _ignore_close_request(self) -> None:
        """Ignore Alt+F4 and normal close requests in Client Mode."""
        pass

    def _open_console_locally(self) -> None:
        try:
            with self.desktop_lock:
                if not self.console_available or self.cafe_desktop is None:
                    raise RuntimeError("CafeConsole is unavailable.")
                switch_desktop(self.cafe_desktop, CAFE_DESKTOP_NAME)
        except Exception as error:
            messagebox.showerror("Client Mode", str(error), parent=self.root)

    def _handle_network_command(self, command: str) -> dict[str, object]:
        """Execute a remote intent locally and return the observed state."""
        response: dict[str, object] = {
            "ok": True,
            "message": "Status read.",
        }
        with self.desktop_lock:
            try:
                if self.shutting_down:
                    raise RuntimeError("Client is shutting down.")
                if command == "STATUS":
                    pass
                elif command == "LOCK":
                    if not self.console_available or self.cafe_desktop is None:
                        raise RuntimeError("CafeConsole is unavailable.")
                    switch_desktop(self.cafe_desktop, CAFE_DESKTOP_NAME)
                    response["message"] = "Switched to CafeConsole."
                elif command == "UNLOCK":
                    if self.default_desktop is None:
                        raise RuntimeError("Default desktop is unavailable.")
                    switch_desktop(self.default_desktop, DEFAULT_DESKTOP_NAME)
                    response["message"] = "Switched to Default."
                else:
                    raise ValueError(f"Unsupported command: {command}")
            except Exception as error:
                response["ok"] = False
                response["message"] = str(error)

            response["state"] = get_current_state()
            response["console_available"] = self.console_available
        return response

    def _poll_client_state(self) -> None:
        if self.shutting_down or self.root is None:
            return

        self.state_text.set(get_current_state())
        self._detect_console_exit()

        if self.shutdown_event is not None:
            try:
                if event_is_signaled(self.shutdown_event):
                    self._safe_exit()
                    return
            except Exception as error:
                print(f"Could not check shutdown request: {error}", file=sys.stderr)

        self.root.after(UI_POLL_INTERVAL_MS, self._poll_client_state)

    def _detect_console_exit(self) -> None:
        if self.console_process is None:
            return
        try:
            if not process_has_exited(self.console_process):
                return
            exit_code = get_process_exit_code(self.console_process)
            print(
                "CafeConsole child exited with code "
                f"{exit_code}; restoring Default and disabling LOCK.",
                file=sys.stderr,
            )
        except Exception as error:
            print(f"Could not monitor CafeConsole child: {error}", file=sys.stderr)
            return

        self._restore_default_best_effort()
        with self.desktop_lock:
            self.console_available = False
            close_kernel_handle(self.console_process)
            self.console_process = None
        self.console_text.set("UNAVAILABLE")
        self.state_text.set(get_current_state())

    def _safe_exit(self) -> None:
        """Restore Default first, then stop networking and both processes."""
        if self.shutting_down:
            return
        self.shutting_down = True
        self._restore_default_best_effort()

        if self.network is not None:
            self.network_text.set("STOPPING")
            self.network.stop()
            self.network = None
            self.network_text.set("STOPPED")

        if self.shutdown_event is not None:
            try:
                set_event(self.shutdown_event)
            except Exception as error:
                print(f"Could not signal CafeConsole shutdown: {error}", file=sys.stderr)
        self._wait_for_or_stop_console_child()

        if self.root is not None:
            self.root.destroy()

    def _restore_default_best_effort(self) -> None:
        if self.default_desktop is None:
            return
        try:
            with self.desktop_lock:
                switch_desktop(self.default_desktop, DEFAULT_DESKTOP_NAME)
        except Exception as error:
            print(f"Could not switch back to Default: {error}", file=sys.stderr)

    def _wait_for_or_stop_console_child(self) -> None:
        if self.console_process is None:
            return
        try:
            if process_has_exited(self.console_process):
                return
            wait_result = kernel32.WaitForSingleObject(
                self.console_process,
                CHILD_EXIT_TIMEOUT_MS,
            )
            if wait_result == WAIT_OBJECT_0:
                return
            if wait_result == WAIT_FAILED:
                raise win32_error("WaitForSingleObject")

            print(
                "CafeConsole did not exit after the safe shutdown request; "
                "terminating the child process.",
                file=sys.stderr,
            )
            if not kernel32.TerminateProcess(self.console_process, 1):
                raise win32_error("TerminateProcess")
            kernel32.WaitForSingleObject(self.console_process, CHILD_EXIT_TIMEOUT_MS)
        except Exception as error:
            print(f"Could not stop CafeConsole child: {error}", file=sys.stderr)

    def _cleanup(self) -> None:
        if self.cleanup_complete:
            return
        self.cleanup_complete = True
        self.shutting_down = True

        # Safe cleanup always restores Default before stopping the child or
        # releasing CafeConsole handles.
        self._restore_default_best_effort()
        if self.network is not None:
            self.network.stop()
            self.network = None
        if self.shutdown_event is not None:
            try:
                set_event(self.shutdown_event)
            except Exception as error:
                print(f"Could not signal CafeConsole shutdown: {error}", file=sys.stderr)
        self._wait_for_or_stop_console_child()

        if self.root is not None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            self.root = None

        with self.desktop_lock:
            close_kernel_handle(self.console_process)
            close_kernel_handle(self.ready_event)
            close_kernel_handle(self.shutdown_event)
            close_desktop_handle(self.cafe_desktop)
            close_desktop_handle(self.default_desktop)
            self.console_process = None
            self.ready_event = None
            self.shutdown_event = None
            self.cafe_desktop = None
            self.default_desktop = None


class ConsoleChildApplication:
    """Internal Tk mode created directly on WinSta0\\CafeConsole."""

    def __init__(self, ready_event_name: str, shutdown_event_name: str) -> None:
        self.ready_event_name = ready_event_name
        self.shutdown_event_name = shutdown_event_name
        self.default_desktop: wintypes.HANDLE | None = None
        self.shutdown_event: wintypes.HANDLE | None = None
        self.root: tk.Tk | None = None

    def run(self) -> None:
        ready_event: wintypes.HANDLE | None = None
        try:
            self.default_desktop = open_desktop(DEFAULT_DESKTOP_NAME)
            ready_event = open_named_event(self.ready_event_name, EVENT_MODIFY_STATE)
            self.shutdown_event = open_named_event(
                self.shutdown_event_name,
                EVENT_MODIFY_STATE | SYNCHRONIZE,
            )
            self._build_ui()

            if self.root is None:
                raise RuntimeError("CafeConsole Tk window was not initialized.")
            self.root.update_idletasks()
            self.root.update()
            set_event(ready_event)
            close_kernel_handle(ready_event)
            ready_event = None

            self.root.after(UI_POLL_INTERVAL_MS, self._poll_shutdown_request)
            self.root.mainloop()
        finally:
            self._restore_default_best_effort()
            if self.root is not None:
                try:
                    self.root.destroy()
                except tk.TclError:
                    pass
                self.root = None
            close_kernel_handle(ready_event)
            close_kernel_handle(self.shutdown_event)
            close_desktop_handle(self.default_desktop)

    def _build_ui(self) -> None:
        self.root = tk.Tk()
        self.root.title("Game Cafe Console - Remote Test")
        self.root.geometry("500x320")
        self.root.resizable(False, False)
        # Alt+F4 sends WM_DELETE_WINDOW, which this experiment intentionally ignores.
        self.root.protocol("WM_DELETE_WINDOW", self._ignore_close_request)

        content = ttk.Frame(self.root, padding=36)
        content.pack(fill="both", expand=True)
        ttk.Label(
            content,
            text="GAME CAFE CONSOLE",
            font=("Segoe UI", 22, "bold"),
        ).pack(pady=(6, 4))
        ttk.Label(
            content,
            text="REMOTE CONTROL TEST",
            font=("Segoe UI", 13),
        ).pack(pady=(0, 20))
        ttk.Label(content, text="Current mode:").pack()
        ttk.Label(
            content,
            text="LOCKED / CAFE CONSOLE",
            font=("Segoe UI", 12, "bold"),
        ).pack(pady=(2, 18))
        ttk.Button(
            content,
            text="GO TO DEFAULT DESKTOP",
            command=self._go_to_default,
            width=30,
        ).pack(pady=5)
        ttk.Button(
            content,
            text="SAFE EXIT CLIENT",
            command=self._request_safe_exit,
            width=30,
        ).pack(pady=5)

    def _ignore_close_request(self) -> None:
        """Ignore normal close requests on the CafeConsole UI."""
        pass

    def _go_to_default(self) -> None:
        try:
            if self.default_desktop is None:
                raise RuntimeError("Default desktop is unavailable.")
            switch_desktop(self.default_desktop, DEFAULT_DESKTOP_NAME)
        except Exception as error:
            messagebox.showerror("CafeConsole", str(error), parent=self.root)

    def _request_safe_exit(self) -> None:
        """Restore Default, then ask the Client controller to shut everything down."""
        try:
            if self.default_desktop is None or self.shutdown_event is None:
                raise RuntimeError("Safe shutdown handles are unavailable.")
            switch_desktop(self.default_desktop, DEFAULT_DESKTOP_NAME)
            set_event(self.shutdown_event)
        except Exception as error:
            print(f"Could not request safe Client shutdown: {error}", file=sys.stderr)

    def _poll_shutdown_request(self) -> None:
        if self.root is None or self.shutdown_event is None:
            return
        try:
            if event_is_signaled(self.shutdown_event):
                self._restore_default_best_effort()
                self.root.destroy()
                return
        except Exception as error:
            print(f"Could not check shutdown request: {error}", file=sys.stderr)
            self._restore_default_best_effort()
            self.root.destroy()
            return
        self.root.after(UI_POLL_INTERVAL_MS, self._poll_shutdown_request)

    def _restore_default_best_effort(self) -> None:
        if self.default_desktop is None:
            return
        try:
            switch_desktop(self.default_desktop, DEFAULT_DESKTOP_NAME)
        except Exception as error:
            print(f"Could not switch back to Default: {error}", file=sys.stderr)


def parse_arguments() -> argparse.Namespace:
    """Parse the private child-process mode arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ready-event", help=argparse.SUPPRESS)
    parser.add_argument("--shutdown-event", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    """Run internal child mode or the normal user mode selector."""
    arguments = parse_arguments()
    try:
        if arguments.console_child:
            if not arguments.ready_event or not arguments.shutdown_event:
                raise ValueError("Console child event names are missing.")
            ConsoleChildApplication(
                arguments.ready_event,
                arguments.shutdown_event,
            ).run()
            return 0

        selected_mode = ModeSelectionApplication().run()
        if selected_mode == "admin":
            AdminApplication().run()
        elif selected_mode == "client":
            ClientController().run()
        return 0
    except Exception as error:
        print(f"Remote Console Lab failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
