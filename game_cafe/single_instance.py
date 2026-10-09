"""Windows lifetime guard for the one main Game Cafe Console controller."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys


MAIN_INSTANCE_MUTEX = r"Global\StickForYou.GameCafeConsole.MainController.v1"
ERROR_ALREADY_EXISTS = 183


class WindowsSingleInstance:
    """Keep a named mutex handle open for the controller process lifetime."""

    def __init__(self, name: str = MAIN_INSTANCE_MUTEX):
        self.name = name
        self.handle: int | None = None

    def acquire(self) -> bool:
        if self.handle:
            return True
        if sys.platform != "win32":
            raise OSError("Windows single-instance protection requires Windows.")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                          wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self.handle = handle
        return True

    def close(self) -> None:
        if not self.handle:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle, self.handle = self.handle, None
        if not kernel32.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
