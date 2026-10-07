"""Development experiment for switching between two Win32 desktops."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox
import uuid


if sys.platform != "win32":
    raise SystemExit("Desktop Lab can only run on Windows.")


DEFAULT_DESKTOP_NAME = "Default"
CAFE_DESKTOP_NAME = "CafeConsole"
CAFE_DESKTOP_PATH = rf"WinSta0\{CAFE_DESKTOP_NAME}"

DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_SWITCHDESKTOP = 0x0100
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF

STARTUP_READY_TIMEOUT_MS = 15_000
ORDERLY_CHILD_EXIT_TIMEOUT_MS = 3_000
PROCESS_POLL_INTERVAL_MS = 250


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
    """ctypes representation of the Win32 PROCESS_INFORMATION structure."""

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
    """Build an OSError containing the most recent Win32 error details."""
    error_code = ctypes.get_last_error()
    return OSError(
        error_code,
        f"{operation} failed: {ctypes.FormatError(error_code).strip()}",
    )


def open_desktop(name: str) -> wintypes.HANDLE:
    """Open a desktop handle that is allowed to become the visible desktop."""
    desktop_handle = user32.OpenDesktopW(
        name,
        0,
        False,
        DESKTOP_SWITCHDESKTOP,
    )
    if not desktop_handle:
        raise win32_error(f'OpenDesktopW("{name}")')
    return desktop_handle


def create_cafe_desktop() -> wintypes.HANDLE:
    """Create or open the CafeConsole desktop in the current window station."""
    # CreateDesktop returns a desktop handle; Windows keeps the desktop object
    # alive while this handle or a process connected to the desktop still exists.
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
    """Close a desktop handle, reporting cleanup failures to stderr."""
    if desktop_handle and not user32.CloseDesktop(desktop_handle):
        print(win32_error("CloseDesktop"), file=sys.stderr)


def close_kernel_handle(handle: wintypes.HANDLE | None) -> None:
    """Close a kernel handle, reporting cleanup failures to stderr."""
    if handle and not kernel32.CloseHandle(handle):
        print(win32_error("CloseHandle"), file=sys.stderr)


def create_named_event(name: str) -> wintypes.HANDLE:
    """Create a manual-reset, initially non-signaled local Win32 event."""
    event_handle = kernel32.CreateEventW(None, True, False, name)
    if not event_handle:
        raise win32_error(f'CreateEventW("{name}")')
    return event_handle


def open_named_event(name: str, desired_access: int) -> wintypes.HANDLE:
    """Open a named event created by the controller process."""
    event_handle = kernel32.OpenEventW(desired_access, False, name)
    if not event_handle:
        raise win32_error(f'OpenEventW("{name}")')
    return event_handle


def set_event(event_handle: wintypes.HANDLE) -> None:
    """Signal a Win32 event."""
    if not kernel32.SetEvent(event_handle):
        raise win32_error("SetEvent")


def process_has_exited(process_handle: wintypes.HANDLE) -> bool:
    """Return whether the child process has exited without blocking."""
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


def launch_cafe_process(
    ready_event_name: str,
    shutdown_event_name: str,
) -> wintypes.HANDLE:
    """Start a second Python process whose first GUI belongs to CafeConsole."""
    script_path = Path(__file__).resolve()
    executable_path = Path(sys.executable).resolve()
    arguments = [
        str(executable_path),
        str(script_path),
        "--cafe-child",
        "--ready-event",
        ready_event_name,
        "--shutdown-event",
        shutdown_event_name,
    ]

    # CreateProcessW may modify its command-line buffer. list2cmdline applies
    # Windows quoting rules, including for this project's paths with spaces.
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
    startup_info = STARTUPINFOW()
    startup_info.cb = ctypes.sizeof(startup_info)
    # This assigns the child to CafeConsole before Tk creates any windows.
    startup_info.lpDesktop = CAFE_DESKTOP_PATH
    process_info = PROCESS_INFORMATION()

    created = kernel32.CreateProcessW(
        str(executable_path),
        command_line,
        None,
        None,
        False,
        0,
        None,
        str(script_path.parent.parent),
        ctypes.byref(startup_info),
        ctypes.byref(process_info),
    )
    if not created:
        raise win32_error("CreateProcessW")

    # The controller monitors the process handle; it does not need the initial
    # thread handle returned by CreateProcessW.
    close_kernel_handle(process_info.hThread)
    return process_info.hProcess


class DefaultDesktopController:
    """Own the Default helper, child process, synchronization, and cleanup."""

    def __init__(self) -> None:
        self.default_desktop: wintypes.HANDLE | None = None
        self.cafe_desktop: wintypes.HANDLE | None = None
        self.ready_event: wintypes.HANDLE | None = None
        self.shutdown_event: wintypes.HANDLE | None = None
        self.cafe_process: wintypes.HANDLE | None = None
        self.root: tk.Tk | None = None
        self.navigation_helper: tk.Toplevel | None = None
        self.shutting_down = False

        event_suffix = f"{kernel32.GetCurrentProcessId()}_{uuid.uuid4().hex}"
        self.ready_event_name = rf"Local\CafeConsoleReady_{event_suffix}"
        self.shutdown_event_name = rf"Local\CafeConsoleShutdown_{event_suffix}"

    def run(self) -> None:
        """Start the experiment and run the Default-side Tk event loop."""
        try:
            self.default_desktop = open_desktop(DEFAULT_DESKTOP_NAME)
            self.cafe_desktop = create_cafe_desktop()
            self._build_default_ui()
            self.ready_event = create_named_event(self.ready_event_name)
            self.shutdown_event = create_named_event(self.shutdown_event_name)
            self.cafe_process = launch_cafe_process(
                self.ready_event_name,
                self.shutdown_event_name,
            )

            self._wait_for_cafe_ui()
            switch_desktop(self.cafe_desktop, CAFE_DESKTOP_NAME)

            if self.root is None:
                raise RuntimeError("Default Tk controller was not initialized.")
            self.root.after(PROCESS_POLL_INTERVAL_MS, self._poll_cafe_process)
            self.root.mainloop()
        except Exception as error:
            print(f"Desktop Lab failed: {error}", file=sys.stderr)
            self._show_startup_error_if_possible(error)
        finally:
            self._cleanup()

    def _build_default_ui(self) -> None:
        """Create a hidden controller root and its recreatable helper window."""
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self._ignore_close_request)
        self._create_navigation_helper()
        self.root.update_idletasks()

    def _create_navigation_helper(self) -> None:
        """Create the small Default-desktop navigation window if needed."""
        if self.shutting_down or self.root is None:
            return
        if (
            self.navigation_helper is not None
            and self.navigation_helper.winfo_exists()
        ):
            return

        helper = tk.Toplevel(self.root)
        self.navigation_helper = helper
        helper.title("Desktop Lab - Default")
        helper.geometry("300x125")
        helper.resizable(False, False)

        # Alt+F4 sends WM_DELETE_WINDOW; ignoring it keeps navigation available.
        helper.protocol("WM_DELETE_WINDOW", self._ignore_close_request)
        helper.bind("<Destroy>", self._navigation_helper_was_destroyed)

        tk.Label(
            helper,
            text="DEFAULT DESKTOP",
            font=("Segoe UI", 14, "bold"),
        ).pack(pady=(16, 10))
        tk.Button(
            helper,
            text="OPEN CAFE CONSOLE",
            command=self._open_cafe_console,
            width=24,
        ).pack()

    def _ignore_close_request(self) -> None:
        """Ignore normal close requests for the development helper."""
        pass

    def _navigation_helper_was_destroyed(self, event: tk.Event) -> None:
        """Recreate the helper if it is destroyed while the controller lives."""
        if event.widget is not self.navigation_helper:
            return

        self.navigation_helper = None
        if not self.shutting_down and self.root is not None:
            self.root.after_idle(self._create_navigation_helper)

    def _open_cafe_console(self) -> None:
        """Switch from Default to the existing CafeConsole UI."""
        if self.cafe_process is None or self.cafe_desktop is None:
            return

        try:
            if process_has_exited(self.cafe_process):
                self._handle_cafe_process_exit()
                return
            switch_desktop(self.cafe_desktop, CAFE_DESKTOP_NAME)
        except Exception as error:
            messagebox.showerror("Desktop Lab", str(error), parent=self.navigation_helper)

    def _wait_for_cafe_ui(self) -> None:
        """Wait for either the child's ready signal or its early exit."""
        if self.ready_event is None or self.cafe_process is None:
            raise RuntimeError("CafeConsole startup handles are missing.")

        handles = (wintypes.HANDLE * 2)(self.ready_event, self.cafe_process)
        wait_result = kernel32.WaitForMultipleObjects(
            len(handles),
            handles,
            False,
            STARTUP_READY_TIMEOUT_MS,
        )

        if wait_result == WAIT_OBJECT_0:
            if process_has_exited(self.cafe_process):
                exit_code = get_process_exit_code(self.cafe_process)
                raise RuntimeError(
                    "CafeConsole exited immediately after initialization "
                    f"with code {exit_code}."
                )
            return
        if wait_result == WAIT_OBJECT_0 + 1:
            exit_code = get_process_exit_code(self.cafe_process)
            raise RuntimeError(
                f"CafeConsole exited before its UI was ready (code {exit_code})."
            )
        if wait_result == WAIT_TIMEOUT:
            raise TimeoutError(
                "CafeConsole did not report a ready UI within "
                f"{STARTUP_READY_TIMEOUT_MS // 1000} seconds."
            )
        if wait_result == WAIT_FAILED:
            raise win32_error("WaitForMultipleObjects")
        raise RuntimeError(f"Unexpected startup wait result: 0x{wait_result:08X}")

    def _poll_cafe_process(self) -> None:
        """Detect child exit without blocking the Default-side Tk loop."""
        if self.shutting_down or self.root is None or self.cafe_process is None:
            return

        try:
            if process_has_exited(self.cafe_process):
                self._handle_cafe_process_exit()
                return
        except Exception as error:
            print(f"Could not monitor CafeConsole: {error}", file=sys.stderr)
            self._finish_controller()
            return

        self.root.after(PROCESS_POLL_INTERVAL_MS, self._poll_cafe_process)

    def _handle_cafe_process_exit(self) -> None:
        """Return to Default and stop after the child exits for any reason."""
        if self.cafe_process is not None:
            try:
                exit_code = get_process_exit_code(self.cafe_process)
                if exit_code == 0:
                    print("CafeConsole exited; cleaning up Desktop Lab.")
                else:
                    print(
                        "CafeConsole exited unexpectedly with code "
                        f"{exit_code}; returning to Default.",
                        file=sys.stderr,
                    )
            except Exception as error:
                print(f"Could not read CafeConsole exit code: {error}", file=sys.stderr)

        self._finish_controller()

    def _finish_controller(self) -> None:
        """Attempt the safe return to Default, then end the controller loop."""
        if self.shutting_down:
            return
        self.shutting_down = True

        self._switch_to_default_best_effort()

        if self.navigation_helper is not None:
            self.navigation_helper.destroy()
            self.navigation_helper = None
        if self.root is not None:
            self.root.quit()

    def _show_startup_error_if_possible(self, error: Exception) -> None:
        """Show startup errors on Default when the Tk controller is available."""
        self._switch_to_default_best_effort()
        if self.root is None:
            return
        try:
            messagebox.showerror("Desktop Lab startup failed", str(error))
        except tk.TclError:
            pass

    def _switch_to_default_best_effort(self) -> None:
        """Try to restore Default without hiding an earlier cleanup error."""
        if self.default_desktop is None:
            return
        try:
            switch_desktop(self.default_desktop, DEFAULT_DESKTOP_NAME)
        except Exception as error:
            print(f"Could not switch back to Default: {error}", file=sys.stderr)

    def _cleanup(self) -> None:
        """Restore Default, stop the child if needed, and release all handles."""
        self.shutting_down = True

        # Returning to Default always comes before asking the CafeConsole child
        # to exit, so cleanup never intentionally strands the visible desktop.
        self._switch_to_default_best_effort()
        self._stop_cafe_process_if_running()

        if self.navigation_helper is not None:
            try:
                self.navigation_helper.destroy()
            except tk.TclError:
                pass
            self.navigation_helper = None
        if self.root is not None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass
            self.root = None

        close_kernel_handle(self.cafe_process)
        close_kernel_handle(self.ready_event)
        close_kernel_handle(self.shutdown_event)
        close_desktop_handle(self.cafe_desktop)
        close_desktop_handle(self.default_desktop)

        self.cafe_process = None
        self.ready_event = None
        self.shutdown_event = None
        self.cafe_desktop = None
        self.default_desktop = None

    def _stop_cafe_process_if_running(self) -> None:
        """Request orderly child shutdown, with termination only as a fallback."""
        if self.cafe_process is None:
            return
        try:
            if process_has_exited(self.cafe_process):
                return

            if self.shutdown_event is not None:
                set_event(self.shutdown_event)
                wait_result = kernel32.WaitForSingleObject(
                    self.cafe_process,
                    ORDERLY_CHILD_EXIT_TIMEOUT_MS,
                )
                if wait_result == WAIT_OBJECT_0:
                    return
                if wait_result == WAIT_FAILED:
                    raise win32_error("WaitForSingleObject")

            print(
                "CafeConsole did not exit after the shutdown request; "
                "terminating the child process.",
                file=sys.stderr,
            )
            if not kernel32.TerminateProcess(self.cafe_process, 1):
                raise win32_error("TerminateProcess")
            wait_result = kernel32.WaitForSingleObject(
                self.cafe_process,
                ORDERLY_CHILD_EXIT_TIMEOUT_MS,
            )
            if wait_result == WAIT_FAILED:
                raise win32_error("WaitForSingleObject")
            if wait_result == WAIT_TIMEOUT:
                print("Timed out waiting for process termination.", file=sys.stderr)
        except Exception as error:
            print(f"Could not stop CafeConsole cleanly: {error}", file=sys.stderr)


class CafeConsoleApplication:
    """Tk application that is created inside the CafeConsole desktop process."""

    def __init__(self, ready_event_name: str, shutdown_event_name: str) -> None:
        self.ready_event_name = ready_event_name
        self.shutdown_event_name = shutdown_event_name
        self.default_desktop: wintypes.HANDLE | None = None
        self.shutdown_event: wintypes.HANDLE | None = None
        self.root: tk.Tk | None = None

    def run(self) -> None:
        """Build the CafeConsole UI, signal readiness, and process UI events."""
        ready_event: wintypes.HANDLE | None = None
        try:
            self.default_desktop = open_desktop(DEFAULT_DESKTOP_NAME)
            ready_event = open_named_event(
                self.ready_event_name,
                EVENT_MODIFY_STATE,
            )
            self.shutdown_event = open_named_event(
                self.shutdown_event_name,
                SYNCHRONIZE,
            )
            self._build_cafe_ui()

            if self.root is None:
                raise RuntimeError("CafeConsole Tk window was not initialized.")
            self.root.update_idletasks()
            self.root.update()
            set_event(ready_event)
            close_kernel_handle(ready_event)
            ready_event = None

            self.root.after(PROCESS_POLL_INTERVAL_MS, self._poll_shutdown_request)
            self.root.mainloop()
        finally:
            # Any normal child shutdown also makes a best effort to restore the
            # user's Default desktop before its CafeConsole UI disappears.
            self._switch_to_default_best_effort()
            if self.root is not None:
                try:
                    self.root.destroy()
                except tk.TclError:
                    pass
                self.root = None
            close_kernel_handle(ready_event)
            close_kernel_handle(self.shutdown_event)
            close_desktop_handle(self.default_desktop)

    def _build_cafe_ui(self) -> None:
        """Create the development UI on the process-assigned desktop."""
        self.root = tk.Tk()
        self.root.title("Game Cafe Console - Desktop Lab")
        self.root.geometry("480x290")
        self.root.resizable(False, False)

        # Ignore WM_DELETE_WINDOW so Alt+F4 cannot remove the CafeConsole UI.
        self.root.protocol("WM_DELETE_WINDOW", self._ignore_close_request)

        content = tk.Frame(self.root, padx=36, pady=32)
        content.pack(fill="both", expand=True)

        tk.Label(
            content,
            text="GAME CAFE CONSOLE",
            font=("Segoe UI", 22, "bold"),
        ).pack(pady=(8, 6))
        tk.Label(
            content,
            text="Win32 Desktop Experiment",
            font=("Segoe UI", 13),
        ).pack(pady=(0, 24))
        tk.Button(
            content,
            text="GO TO DEFAULT DESKTOP",
            command=self._go_to_default,
            width=28,
        ).pack(pady=6)
        tk.Button(
            content,
            text="SAFE EXIT EXPERIMENT",
            command=self._safe_exit,
            width=28,
        ).pack(pady=6)

    def _ignore_close_request(self) -> None:
        """Ignore normal close requests for the CafeConsole UI."""
        pass

    def _go_to_default(self) -> None:
        """Show Default while keeping this process and its UI alive."""
        try:
            if self.default_desktop is None:
                raise RuntimeError("The Default desktop handle is unavailable.")
            switch_desktop(self.default_desktop, DEFAULT_DESKTOP_NAME)
        except Exception as error:
            messagebox.showerror("Desktop Lab", str(error), parent=self.root)

    def _safe_exit(self) -> None:
        """Return to Default first, then close this child process normally."""
        try:
            if self.default_desktop is None:
                raise RuntimeError("The Default desktop handle is unavailable.")
            switch_desktop(self.default_desktop, DEFAULT_DESKTOP_NAME)
        except Exception as error:
            messagebox.showerror("Desktop Lab", str(error), parent=self.root)
            return

        if self.root is not None:
            self.root.destroy()

    def _poll_shutdown_request(self) -> None:
        """Honor controller cleanup requests without blocking Tkinter."""
        if self.root is None or self.shutdown_event is None:
            return

        wait_result = kernel32.WaitForSingleObject(self.shutdown_event, 0)
        if wait_result == WAIT_OBJECT_0:
            self._safe_exit()
            return
        if wait_result == WAIT_FAILED:
            error = win32_error("WaitForSingleObject")
            print(f"Could not check shutdown event: {error}", file=sys.stderr)
            self._safe_exit()
            return
        if wait_result != WAIT_TIMEOUT:
            print(
                f"Unexpected shutdown wait result: 0x{wait_result:08X}",
                file=sys.stderr,
            )
            self._safe_exit()
            return

        self.root.after(PROCESS_POLL_INTERVAL_MS, self._poll_shutdown_request)

    def _switch_to_default_best_effort(self) -> None:
        """Attempt to restore Default during exception cleanup."""
        if self.default_desktop is None:
            return
        try:
            switch_desktop(self.default_desktop, DEFAULT_DESKTOP_NAME)
        except Exception as error:
            print(f"Could not switch back to Default: {error}", file=sys.stderr)


def parse_arguments() -> argparse.Namespace:
    """Parse the internal child-process arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cafe-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ready-event", help=argparse.SUPPRESS)
    parser.add_argument("--shutdown-event", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    """Run either the Default controller or its CafeConsole child component."""
    arguments = parse_arguments()

    try:
        if arguments.cafe_child:
            if not arguments.ready_event or not arguments.shutdown_event:
                raise ValueError("CafeConsole child event names are missing.")
            CafeConsoleApplication(
                arguments.ready_event,
                arguments.shutdown_event,
            ).run()
        else:
            DefaultDesktopController().run()
    except Exception as error:
        print(f"Desktop Lab failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
