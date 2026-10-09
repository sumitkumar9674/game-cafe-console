"""Win32 desktop and child-process operations for the Windows UI."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import uuid


if sys.platform != "win32":
    raise RuntimeError("Win32 desktops require Windows.")


DESKTOP_READOBJECTS = 0x0001
DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_SWITCHDESKTOP = 0x0100
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
UOI_NAME = 2
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
CREATE_NO_WINDOW = 0x08000000


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                wintypes.BOOL, wintypes.DWORD]
user32.OpenDesktopW.restype = wintypes.HANDLE
user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                  ctypes.c_void_p, wintypes.DWORD,
                                  wintypes.DWORD, ctypes.c_void_p]
user32.CreateDesktopW.restype = wintypes.HANDLE
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                    wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                              ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD)]
user32.GetUserObjectInformationW.restype = wintypes.BOOL
user32.SwitchDesktop.argtypes = [wintypes.HANDLE]
user32.SwitchDesktop.restype = wintypes.BOOL
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.CloseDesktop.restype = wintypes.BOOL

kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                  wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForMultipleObjects.argtypes = [wintypes.DWORD,
                                            ctypes.POINTER(wintypes.HANDLE),
                                            wintypes.BOOL, wintypes.DWORD]
kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                        ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR,
                                    ctypes.c_void_p, ctypes.c_void_p,
                                    wintypes.BOOL, wintypes.DWORD,
                                    ctypes.c_void_p, wintypes.LPCWSTR,
                                    ctypes.POINTER(STARTUPINFOW),
                                    ctypes.POINTER(PROCESS_INFORMATION)]
kernel32.CreateProcessW.restype = wintypes.BOOL


def error(operation: str) -> OSError:
    code = ctypes.get_last_error()
    return OSError(code, f"{operation}: {ctypes.FormatError(code).strip()}")


def open_default() -> int:
    handle = user32.OpenDesktopW("Default", 0, False, DESKTOP_SWITCHDESKTOP)
    if not handle:
        raise error("OpenDesktopW(Default)")
    return handle


def create_console() -> int:
    # The desktop object lives while its handles or attached processes live.
    handle = user32.CreateDesktopW("CafeConsole", None, None, 0,
                                    DESKTOP_CREATEWINDOW | DESKTOP_SWITCHDESKTOP,
                                    None)
    if not handle:
        raise error("CreateDesktopW(CafeConsole)")
    return handle


def switch(handle: int) -> None:
    if not user32.SwitchDesktop(handle):
        raise error("SwitchDesktop")


def active_desktop_name() -> str | None:
    handle = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not handle:
        return None
    try:
        needed = wintypes.DWORD()
        user32.GetUserObjectInformationW(handle, UOI_NAME, None, 0,
                                          ctypes.byref(needed))
        if not needed.value:
            return None
        count = (needed.value + ctypes.sizeof(ctypes.c_wchar) - 1) // ctypes.sizeof(ctypes.c_wchar)
        buffer = ctypes.create_unicode_buffer(count)
        if not user32.GetUserObjectInformationW(handle, UOI_NAME, buffer,
                                                needed.value, ctypes.byref(needed)):
            return None
        return buffer.value
    finally:
        close_desktop(handle)


def close_desktop(handle: int | None) -> None:
    if handle and not user32.CloseDesktop(handle):
        print(error("CloseDesktop"), file=sys.stderr)


def close_handle(handle: int | None) -> None:
    if handle and not kernel32.CloseHandle(handle):
        print(error("CloseHandle"), file=sys.stderr)


def create_event(prefix: str) -> tuple[str, int]:
    name = rf"Local\{prefix}_{uuid.uuid4().hex}"
    handle = kernel32.CreateEventW(None, True, False, name)
    if not handle:
        raise error("CreateEventW")
    return name, handle


def open_event(name: str, access: int) -> int:
    handle = kernel32.OpenEventW(access, False, name)
    if not handle:
        raise error("OpenEventW")
    return handle


def signal(handle: int) -> None:
    if not kernel32.SetEvent(handle):
        raise error("SetEvent")


def signaled(handle: int) -> bool:
    result = kernel32.WaitForSingleObject(handle, 0)
    if result == WAIT_OBJECT_0:
        return True
    if result == WAIT_TIMEOUT:
        return False
    raise error("WaitForSingleObject")


def exited(process: int) -> bool:
    return signaled(process)


def exit_code(process: int) -> int:
    code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(process, ctypes.byref(code)):
        raise error("GetExitCodeProcess")
    return code.value


def launch_child(ready_name: str, stop_name: str) -> int:
    executable = str(Path(sys.executable).resolve())
    if getattr(sys, "frozen", False):
        arguments = [executable, "--console-child", ready_name, stop_name,
                     str(os.getpid())]
    else:
        entry = str((Path(__file__).resolve().parent.parent / "run_game_cafe.py"))
        arguments = [executable, entry, "--console-child", ready_name, stop_name,
                     str(os.getpid())]
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    # The child belongs to CafeConsole from process startup, before Qt opens.
    startup.lpDesktop = r"WinSta0\CafeConsole"
    info = PROCESS_INFORMATION()
    # A console build must not leave a closable console window on CafeConsole.
    if not kernel32.CreateProcessW(executable, command, None, None, False,
                                   CREATE_NO_WINDOW,
                                   None, str(Path(executable).parent),
                                   ctypes.byref(startup), ctypes.byref(info)):
        raise error("CreateProcessW")
    close_handle(info.hThread)
    return info.hProcess


def wait_child_ready(ready: int, process: int, milliseconds: int = 15000) -> None:
    handles = (wintypes.HANDLE * 2)(ready, process)
    result = kernel32.WaitForMultipleObjects(2, handles, False, milliseconds)
    if result == WAIT_OBJECT_0 and not exited(process):
        return
    if result == WAIT_OBJECT_0 + 1 or exited(process):
        raise RuntimeError(f"CafeConsole child exited (code {exit_code(process)}).")
    if result == WAIT_TIMEOUT:
        raise TimeoutError("CafeConsole child did not become ready.")
    raise error("WaitForMultipleObjects")


def stop_child(process: int | None, stop_event: int | None) -> None:
    if not process or exited(process):
        return
    if stop_event:
        signal(stop_event)
    result = kernel32.WaitForSingleObject(process, 8000)
    if result == WAIT_OBJECT_0:
        return
    if result != WAIT_TIMEOUT:
        raise error("WaitForSingleObject")
    raise TimeoutError("CafeConsole child did not stop; shutdown can be retried.")


def open_parent_process(pid: int) -> int:
    """Let the child detect an unexpectedly terminated Default controller."""
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        raise error("OpenProcess(parent)")
    return handle
