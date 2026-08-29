"""Concrete framed bridge transports independent of MCP and CE APIs."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
import re
import sys
from threading import Lock, Timer
from time import perf_counter
from typing import BinaryIO, Protocol

from .framing import read_frame, write_frame
from .models import ContractViolation
from .protocol import BridgeRequest, BridgeResponse, BridgeTransportError


class FramedStreamBridgeClient:
    """Serialize calls over a fresh bounded binary stream per request."""

    def __init__(self, stream_factory: Callable[[], BinaryIO]) -> None:
        self._stream_factory = stream_factory
        self._lock = Lock()

    def call(self, request: BridgeRequest) -> BridgeResponse:
        with self._lock:
            try:
                stream = self._stream_factory()
                with closing(stream):
                    write_frame(stream, request.to_dict())
                    value = read_frame(stream)
                response = BridgeResponse.from_dict(value)
                if response.request_id != request.request_id:
                    raise ContractViolation("bridge response request ID mismatch")
                return response
            except ContractViolation:
                raise
            except (EOFError, OSError, ConnectionError) as exc:
                raise BridgeTransportError("framed bridge call failed") from exc


_PIPE_NAME = re.compile(r"^\\\\\.\\pipe\\[A-Za-z0-9_.-]{1,128}$")
DEFAULT_PIPE_NAME = "auto"
_CE_EXECUTABLES = {
    "cheat engine.exe", "cheatengine-i386.exe",
    "cheatengine-x86_64.exe", "cheatengine-x86_64-sse4-avx2.exe",
}


def cheat_engine_pipe_name(pid: int) -> str:
    if not isinstance(pid, int) or isinstance(pid, bool) or not 1 <= pid <= 0xFFFFFFFF:
        raise ValueError("CE PID must be a positive 32-bit integer")
    return rf"\\.\pipe\CE_MCP_Backend_v1_{pid}"


def enumerate_cheat_engine_pids() -> list[int]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    result: list[int] = []
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        success = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while success:
            if entry.szExeFile.casefold() in _CE_EXECUTABLES:
                result.append(int(entry.th32ProcessID))
            success = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return sorted(set(result))


class WindowsPipeApi(Protocol):
    def wait(self, pipe_name: str, timeout_ms: int) -> None: ...

    def cancel(self, stream: BinaryIO) -> None: ...

    def server_pid(self, stream: BinaryIO) -> int: ...


class CtypesWindowsPipeApi:
    """Narrow Win32 API surface used for bounded named-pipe calls."""

    ERROR_SEM_TIMEOUT = 121

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Windows named pipes are only available on Windows")
        import ctypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        self._kernel32.WaitNamedPipeW.restype = ctypes.c_int
        self._kernel32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._kernel32.CancelIoEx.restype = ctypes.c_int
        self._kernel32.GetNamedPipeServerProcessId.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
        ]
        self._kernel32.GetNamedPipeServerProcessId.restype = ctypes.c_int

    def wait(self, pipe_name: str, timeout_ms: int) -> None:
        if self._kernel32.WaitNamedPipeW(pipe_name, timeout_ms):
            return
        error = self._ctypes.get_last_error()
        if error == self.ERROR_SEM_TIMEOUT:
            raise TimeoutError(f"named pipe was unavailable after {timeout_ms} ms")
        raise OSError(error, "WaitNamedPipeW failed", pipe_name)

    def cancel(self, stream: BinaryIO) -> None:
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        if not self._kernel32.CancelIoEx(handle, None):
            error = self._ctypes.get_last_error()
            if error != 1168:
                raise OSError(error, "CancelIoEx failed")

    def server_pid(self, stream: BinaryIO) -> int:
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        value = self._ctypes.c_uint32()
        if not self._kernel32.GetNamedPipeServerProcessId(handle, self._ctypes.byref(value)):
            raise OSError(self._ctypes.get_last_error(), "GetNamedPipeServerProcessId failed")
        if value.value < 1:
            raise OSError("named-pipe server returned an invalid process ID")
        return int(value.value)


class WindowsNamedPipeBridgeClient:
    """Deadline-bounded, serialized bridge client for Cheat Engine on Windows."""

    def __init__(
        self,
        pipe_name: str = DEFAULT_PIPE_NAME,
        *,
        ce_pid: int | None = None,
        process_enumerator=None,
        api: WindowsPipeApi | None = None,
        opener=None,
    ) -> None:
        if pipe_name != DEFAULT_PIPE_NAME and not _PIPE_NAME.fullmatch(pipe_name):
            raise ValueError("pipe_name must be a local \\\\.\\pipe\\ name")
        if ce_pid is not None and pipe_name != DEFAULT_PIPE_NAME:
            raise ValueError("ce_pid and an explicit pipe_name are mutually exclusive")
        self._configured_pipe_name = pipe_name
        self._ce_pid = ce_pid
        self._process_enumerator = process_enumerator or enumerate_cheat_engine_pids
        self._pipe_name: str | None = None if pipe_name == DEFAULT_PIPE_NAME else pipe_name
        suffix = re.search(r"CE_MCP_Backend_v1_([0-9]+)$", pipe_name)
        self._selected_ce_pid: int | None = ce_pid or (int(suffix.group(1)) if suffix else None)
        self._api = api or CtypesWindowsPipeApi()
        self._opener = opener or open
        self._lock = Lock()
        self._stream: BinaryIO | None = None

    def call(self, request: BridgeRequest) -> BridgeResponse:
        with self._lock:
            timer: Timer | None = None
            try:
                started = perf_counter()
                if self._pipe_name is None:
                    self._pipe_name = self._discover_pipe()
                if self._stream is None:
                    self._api.wait(self._pipe_name, request.deadline_ms)
                    self._stream = self._opener(self._pipe_name, "r+b", buffering=0)
                    self._verify_server_identity(self._stream)
                elapsed_ms = int((perf_counter() - started) * 1000)
                remaining_ms = request.deadline_ms - elapsed_ms
                if remaining_ms < 1:
                    raise TimeoutError("named-pipe connection exhausted the request deadline")
                stream = self._stream
                timer = Timer(
                    remaining_ms / 1000.0,
                    self._cancel_quietly,
                    args=(stream,),
                )
                timer.daemon = True
                timer.start()
                write_frame(stream, request.to_dict())
                response = BridgeResponse.from_dict(read_frame(stream))
                if response.request_id != request.request_id:
                    raise ContractViolation("bridge response request ID mismatch")
                return response
            except ContractViolation:
                self._discard_stream()
                raise
            except BridgeTransportError:
                self._discard_stream()
                raise
            except (EOFError, OSError, TimeoutError, ConnectionError) as exc:
                self._discard_stream()
                raise BridgeTransportError("Windows named-pipe bridge call failed") from exc
            finally:
                if timer is not None:
                    timer.cancel()

    def close(self) -> None:
        with self._lock:
            self._discard_stream()

    def _discard_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if self._configured_pipe_name == DEFAULT_PIPE_NAME:
            self._pipe_name = None

    def _discover_pipe(self) -> str:
        if self._ce_pid is not None:
            return cheat_engine_pipe_name(self._ce_pid)
        pids = self._process_enumerator()
        if not pids:
            raise BridgeTransportError("No Cheat Engine process is available for bridge discovery")
        if len(pids) > 1:
            joined = ", ".join(str(pid) for pid in pids)
            raise BridgeTransportError(
                f"Multiple Cheat Engine instances are running ({joined}); select one with --ce-pid"
            )
        self._selected_ce_pid = pids[0]
        return cheat_engine_pipe_name(pids[0])

    def _verify_server_identity(self, stream: BinaryIO) -> None:
        server_pid = self._api.server_pid(stream)
        if self._selected_ce_pid is not None:
            if server_pid != self._selected_ce_pid:
                raise PermissionError(
                    f"named-pipe server PID {server_pid} does not match selected CE PID {self._selected_ce_pid}"
                )
            return
        if server_pid not in self._process_enumerator():
            raise PermissionError(
                f"named-pipe server PID {server_pid} is not a recognized Cheat Engine process"
            )

    def _cancel_quietly(self, stream: BinaryIO) -> None:
        try:
            self._api.cancel(stream)
        except OSError:
            pass
