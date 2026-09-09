"""Run a sole-autorun CE probe in a copied runtime against an owned target.

All runtime copies/evidence stay in a fresh directory beneath the caller's var/.
Neither the installed CE files nor any already-running process are modified.
"""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def target(data=False):
    counter = ctypes.c_uint32()
    code = ctypes.create_string_buffer(b"\x90\xc3")
    info = {"pid": os.getpid(), "counter": ctypes.addressof(counter),
            "code": ctypes.addressof(code)}
    retained = [counter, code]
    if data:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
        kernel32.VirtualAlloc.restype = ctypes.c_void_p
        base = kernel32.VirtualAlloc(None, 4096, 0x3000, 0x04)
        if not base:
            raise ctypes.WinError(ctypes.get_last_error())
        # This page is immutable after publication and is released only at process exit.
        page = (ctypes.c_ubyte * 4096).from_address(base)
        retained.append(page)
        fixture = {"base": base, "size": 4096, "numeric": {}}
        sentinel = ctypes.c_int32.from_address(base + 0x40)
        sentinel.value = 0x5A17C3E9
        retained.append(sentinel)
        fixture["sentinel"] = {"offset": 0x40, "value": sentinel.value}
        specs = [
            ("u8", ctypes.c_uint8, [0xE7, 0x19]),
            ("i8", ctypes.c_int8, [-101, 99]),
            ("u16", ctypes.c_uint16, [0xE731, 0x1234]),
            ("i16", ctypes.c_int16, [-23456, 12345]),
            ("u32", ctypes.c_uint32, [0xE731A529, 0x12345678]),
            ("i32", ctypes.c_int32, [-123456789, 987654321]),
            ("u64", ctypes.c_uint64, [0xFEDCBA9876543210, 0x0123456789ABCDEF]),
            ("i64", ctypes.c_int64, [-9223372036854775808, 9223372036854775807]),
            ("f32", ctypes.c_float, [1.25, -2.5]),
            ("f64", ctypes.c_double, [-1234.5, 0.125]),
        ]
        for index, (name, ctype, values) in enumerate(specs):
            offset = 0x100 + index * 0x20
            array = (ctype * 2).from_address(base + offset)
            array[:] = values
            retained.append(array)
            expected = ([f"0x{value:016X}" for value in values] if name == "u64" else
                        [str(value) for value in values] if name == "i64" else values)
            fixture["numeric"][name] = {"offset": offset, "width": ctypes.sizeof(ctype), "values": expected}
        array_address = base + fixture["numeric"]["i32"]["offset"]
        pointers = (ctypes.c_void_p * 2).from_address(base + 0x280)
        pointers[:] = [array_address, array_address + 4]
        root = ctypes.c_void_p.from_address(base + 0x2C0)
        root.value = base + 0x270
        retained.extend([pointers, root])
        fixture["numeric"]["pointer"] = {
            "offset": 0x280, "width": ctypes.sizeof(ctypes.c_void_p),
            "values": [f"0x{value:016X}" for value in pointers],
        }
        fixture["chain"] = {"offset": 0x2C0, "offsets": ["0x10", "0x04"],
                            "finalAddress": array_address + 4, "value": specs[5][2][1]}
        text = "BoundaryASCII"
        for name, offset, encoding in [("string", 0x300, "ascii"), ("wstring", 0x340, "utf-16le")]:
            encoded = (text + "\0").encode(encoding)
            buffer = (ctypes.c_ubyte * len(encoded)).from_address(base + offset)
            buffer[:] = encoded
            retained.append(buffer)
            fixture[name] = {"offset": offset, "value": text, "size": len(encoded)}
        raw = bytes.fromhex("00 7F 80 FF 11 22 33 44 55 66 77 88 99 AA BB CC")
        for offset, value in [(0x380, raw), (0x3C0, raw[:7] + b"\x45" + raw[8:])]:
            buffer = (ctypes.c_ubyte * len(value)).from_address(base + offset)
            buffer[:] = value
            retained.append(buffer)
        fixture["raw"] = {"offset": 0x380, "hex": raw.hex(), "unequalOffset": 0x3C0, "firstDifference": 7}
        assert bytes(page).count(bytes(sentinel)) == 1, "scan sentinel must be unique in the page"
        # Keep a second stable thread available for pagination, without mutating the page.
        import threading
        waiter = threading.Thread(target=lambda: time.sleep(180), daemon=True)
        waiter.start()
        retained.append(waiter)
        info.update(owned=True, fixture=fixture)
    print(json.dumps(info), flush=True)
    end = time.monotonic() + (180 if data else 60)
    while time.monotonic() < end:
        counter.value += 1
        time.sleep(0.02)


def stop_owned(process):
    if process is not None and process.poll() is None:
        process.kill()
        process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ce-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=["bridge", "bridge-data", "bridge-debug", "disassembly", "memory-map", "breakpoint-0",
                                         "breakpoint-1", "breakpoint-none"])
    parser.add_argument("--step", choices=["into", "over"])
    parser.add_argument("--target-pid", type=int, help="Read-only memory-map probe of an authorized existing target")
    parser.add_argument("--thread-check", action="store_true", help="Match the installed threadsafegui autorun setting")
    args = parser.parse_args()
    if args.target:
        target(data=args.mode == "bridge-data")
        return
    if os.name != "nt" or not args.ce_dir or not args.output or not args.mode:
        parser.error("Windows, --ce-dir, --output and --mode are required")
    if args.target_pid is not None and args.mode not in {"memory-map", "bridge"}:
        parser.error("existing targets are permitted only for read-only memory-map")
    if args.step and args.mode != "breakpoint-1":
        parser.error("step requires the verified callback-1 stop")
    output = args.output.resolve()
    if "var" not in output.parts or not output.parent.is_dir() or output.exists():
        parser.error("output must be new, beneath var/, with an existing parent")
    source = args.ce_dir.resolve()
    root = Path(__file__).resolve().parents[1]
    bridge_mode = args.mode in {"bridge", "bridge-data", "bridge-debug"}
    probe = root / ("bridge/ce_mcp_bridge.lua" if bridge_mode else "bridge/probes/reliability_probe.lua")
    files = ["cheatengine-x86_64-SSE4-AVX2.exe", "lua53-64.dll", "defines.lua", "main.lua"]
    for name in files:
        if not (source / name).is_file():
            parser.error(f"missing CE runtime file: {name}")
    output.mkdir()
    runtime = output / "runtime"
    runtime.mkdir()
    manifest = {"mode": args.mode, "files": {}}
    for name in files:
        shutil.copy2(source / name, runtime / name)
        manifest["files"][name] = hashlib.sha256((runtime / name).read_bytes()).hexdigest()
    signature = source / (files[0] + ".sig")
    if signature.is_file():
        shutil.copy2(signature, runtime / signature.name)
    (runtime / "autorun").mkdir()
    if args.thread_check:
        (runtime / "autorun/00_check.lua").write_text("setThreadSafetyCheck(true)\n", encoding="ascii")
        manifest["threadSafetyCheck"] = True
    shutil.copy2(probe, runtime / "autorun/00_reliability.lua")
    manifest["probeSha256"] = hashlib.sha256(probe.read_bytes()).hexdigest()
    ce = child = None
    report = {"success": False, "mode": args.mode}
    try:
        if args.target_pid:
            info = {"pid": args.target_pid, "code": 0x400000, "counter": 0x400000}
        else:
            child = subprocess.Popen([sys._base_executable, str(Path(__file__).resolve()), "--target", "--mode", args.mode],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # The target's first line is bounded local metadata, never credentials.
            import threading
            line = []
            reader = threading.Thread(target=lambda: line.append(child.stdout.readline()), daemon=True)
            reader.start()
            reader.join(5)
            if reader.is_alive() or not line or not line[0]:
                raise RuntimeError("owned target metadata timed out")
            info = json.loads(line[0])
            if info["pid"] != child.pid:
                raise RuntimeError("target PID does not match owned process handle")
        manifest["target"] = info
        env = dict(os.environ)
        env.update(CE_RELIABILITY_MODE=args.mode, CE_RELIABILITY_OUTPUT=str(output / "native.jsonl"),
                   CE_RELIABILITY_PID=str(info["pid"]), CE_RELIABILITY_COUNTER=f'{info["counter"]:X}',
                   CE_RELIABILITY_CODE=f'{info["code"]:X}')
        env["CE_RELIABILITY_STEP"] = args.step or ""
        with (output / "ce-output.txt").open("w", encoding="utf-8") as log:
            ce = subprocess.Popen([str(runtime / files[0])], cwd=runtime, env=env,
                                  stdout=log, stderr=subprocess.STDOUT)
            manifest["cePid"] = ce.pid
            (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            if bridge_mode:
                time.sleep(2)
                checked = subprocess.run([sys.executable, str(root / "scripts/probe-bridge.py"),
                    "--manifest", str(output / "manifest.json")], timeout=150 if args.mode == "bridge-data" else 30,
                    capture_output=True, text=True)
                (output / "bridge-output.txt").write_text(checked.stdout + checked.stderr, encoding="utf-8")
                report["success"] = checked.returncode == 0
            else:
                try:
                    report["ceExitCode"] = ce.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    report["timeout"] = True
        evidence = output / "native.jsonl"
        records = [json.loads(row) for row in evidence.read_text(encoding="utf-8").splitlines()] if evidence.exists() else []
        results = [row for row in records if row.get("event") == "result"]
        report["lastEvent"] = records[-1] if records else None
        report["result"] = results[-1] if results else None
        if not bridge_mode:
            report["success"] = not report.get("timeout") and bool(results) and results[-1]["result"] == "PASS"
    except Exception as exc:
        report["success"] = False
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        for label, process in [("ce", ce), ("target", child)]:
            try:
                stop_owned(process)
            except Exception as exc:
                report.setdefault("cleanupErrors", []).append({"resource": label, "message": str(exc)})
        if child is not None:
            for stream in (child.stdout, child.stderr):
                try:
                    stream.close()
                except Exception as exc:
                    report.setdefault("cleanupErrors", []).append({"resource": "target pipe", "message": str(exc)})
        if report.get("cleanupErrors"):
            report["success"] = False
        report["ceExited"] = ce is None or ce.poll() is not None
        report["targetExited"] = child.poll() is not None if child is not None else None
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
