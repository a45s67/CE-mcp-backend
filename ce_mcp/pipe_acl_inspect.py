"""Read-only diagnostic for the security descriptor of a local named pipe."""

from __future__ import annotations

import argparse
import ctypes
import json
import re


PIPE_NAME = re.compile(r"^\\\\\.\\pipe\\[A-Za-z0-9_.-]{1,128}$")


def inspect_pipe(pipe_name: str) -> dict[str, object]:
    if not PIPE_NAME.fullmatch(pipe_name):
        raise ValueError("pipe must be a local \\\\.\\pipe\\ name")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.GetNamedPipeServerProcessId.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.GetSecurityInfo.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetSecurityInfo.restype = ctypes.c_uint32
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(ctypes.c_uint32),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = ctypes.c_int

    # Data access connects to the waiting server; READ_CONTROL permits querying
    # the descriptor. No request frame is sent and the handle is always closed.
    desired_access = 0x80000000 | 0x40000000 | 0x00020000
    handle = kernel32.CreateFileW(pipe_name, desired_access, 0, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed", pipe_name)
    descriptor = ctypes.c_void_p()
    string_descriptor = ctypes.c_wchar_p()
    try:
        server_pid = ctypes.c_uint32()
        if not kernel32.GetNamedPipeServerProcessId(handle, ctypes.byref(server_pid)):
            raise OSError(ctypes.get_last_error(), "GetNamedPipeServerProcessId failed")
        status = advapi32.GetSecurityInfo(
            handle, 6, 0x00000001 | 0x00000002 | 0x00000004,
            None, None, None, None, ctypes.byref(descriptor),
        )
        if status:
            raise OSError(status, "GetSecurityInfo failed")
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, 0x00000001 | 0x00000002 | 0x00000004,
            ctypes.byref(string_descriptor), None,
        ):
            raise OSError(ctypes.get_last_error(), "security descriptor conversion failed")
        return {"pipe": pipe_name, "serverPid": server_pid.value, "sddl": string_descriptor.value}
    finally:
        if string_descriptor:
            kernel32.LocalFree(string_descriptor)
        if descriptor:
            kernel32.LocalFree(descriptor)
        kernel32.CloseHandle(handle)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pipe")
    print(json.dumps(inspect_pipe(parser.parse_args().pipe), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
