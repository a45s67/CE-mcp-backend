from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import subprocess
import time


_HOST_NAME = re.compile(r"^(?:Cheat Engine|cheatengine-.+)\.exe$", re.IGNORECASE)


def is_host_filename(name: str) -> bool:
    return bool(_HOST_NAME.fullmatch(name)) and not any(ord(character) < 32 for character in name)


def same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


@dataclass(frozen=True)
class HostProcess:
    pid: int
    path: Path


@dataclass(frozen=True)
class StopResult:
    exited: bool
    forced: bool
    window_count: int


class PlatformError(RuntimeError):
    pass


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
    ]


class WindowsPlatform:
    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_TERMINATE = 0x0001
    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    WM_CLOSE = 0x0010

    def __init__(self) -> None:
        if os.name != "nt":
            raise PlatformError("CE host controller requires Windows")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        self.kernel32.Process32FirstW.restype = wintypes.BOOL
        self.kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        self.kernel32.Process32NextW.restype = wintypes.BOOL
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel32.TerminateProcess.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.PostMessageW.restype = wintypes.BOOL

    def _query_path(self, handle: int) -> Path:
        capacity = 32768
        buffer = ctypes.create_unicode_buffer(capacity)
        size = wintypes.DWORD(capacity)
        if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise PlatformError("cannot query a recognized CE process path")
        return Path(buffer.value)

    def _open_verified(self, process: HostProcess, terminate: bool = False) -> int:
        access = self.PROCESS_QUERY_LIMITED_INFORMATION | self.SYNCHRONIZE
        if terminate:
            access |= self.PROCESS_TERMINATE
        handle = self.kernel32.OpenProcess(access, False, process.pid)
        if not handle:
            raise PlatformError("cannot open the selected CE process")
        try:
            if not same_path(self._query_path(handle), process.path):
                raise PlatformError("selected CE process identity changed")
            return handle
        except Exception:
            self.kernel32.CloseHandle(handle)
            raise

    def list_hosts(self, root: Path) -> list[HostProcess]:
        snapshot = self.kernel32.CreateToolhelp32Snapshot(self.TH32CS_SNAPPROCESS, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            raise PlatformError("cannot enumerate Windows processes")
        result: list[HostProcess] = []
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            present = self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while present:
                name = entry.szExeFile
                if is_host_filename(name):
                    handle = self.kernel32.OpenProcess(
                        self.PROCESS_QUERY_LIMITED_INFORMATION, False, entry.th32ProcessID
                    )
                    if not handle:
                        raise PlatformError("cannot inspect a recognized CE process")
                    try:
                        path = self._query_path(handle)
                    finally:
                        self.kernel32.CloseHandle(handle)
                    if same_path(path.parent, root):
                        result.append(HostProcess(int(entry.th32ProcessID), path))
                present = self.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.kernel32.CloseHandle(snapshot)
        return sorted(result, key=lambda item: item.pid)

    def start(self, executable: Path, root: Path) -> None:
        subprocess.Popen(
            [str(executable)], cwd=str(root), shell=False, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def stop(self, process: HostProcess, timeout_ms: int, force: bool) -> StopResult:
        deadline = time.monotonic() + timeout_ms / 1000.0
        handle = self._open_verified(process, terminate=force)
        window_count = 0
        try:
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            @callback_type
            def close_window(window, _parameter):
                nonlocal window_count
                pid = wintypes.DWORD()
                self.user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
                if pid.value == process.pid:
                    if self.user32.PostMessageW(window, self.WM_CLOSE, 0, 0):
                        window_count += 1
                return True

            if not self.user32.EnumWindows(close_window, 0):
                raise PlatformError("cannot enumerate CE windows")
            # Reserve half of an explicit force operation's budget for the
            # terminate-and-confirm phase. A normal stop uses the whole budget.
            graceful_ms = timeout_ms if not force else max(1, timeout_ms // 2)
            wait = self.kernel32.WaitForSingleObject(handle, graceful_ms)
            if wait == self.WAIT_OBJECT_0:
                return StopResult(True, False, window_count)
            if wait != self.WAIT_TIMEOUT:
                raise PlatformError("cannot wait for CE process exit")
            if not force:
                return StopResult(False, False, window_count)
            if not self.kernel32.TerminateProcess(handle, 1):
                return StopResult(False, True, window_count)
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            final_wait = self.kernel32.WaitForSingleObject(handle, remaining_ms)
            return StopResult(final_wait == self.WAIT_OBJECT_0, True, window_count)
        finally:
            self.kernel32.CloseHandle(handle)
