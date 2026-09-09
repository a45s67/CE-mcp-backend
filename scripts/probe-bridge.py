"""Bounded service/pipe acceptance against the isolated probe runner's CE."""
import argparse
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import time

from ce_mcp.service import BackendService
from ce_mcp.transport import WindowsNamedPipeBridgeClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--deadline-ms", type=int, default=5000)
    args = parser.parse_args()
    if not 1 <= args.deadline_ms <= 30000:
        parser.error("diagnostic deadline must be 1..30000 milliseconds")
    manifest = json.loads(args.manifest.read_text())
    output = args.manifest.parent
    target = manifest["target"]
    if manifest["mode"] == "bridge-data" and not (target.get("owned") and target.get("fixture")):
        parser.error("bridge-data requires the isolated runner's owned fixture target")
    if manifest["mode"] == "bridge-data":
        fixture = target["fixture"]
        if fixture["size"] != 4096 or fixture["base"] <= 0 or fixture["base"] % 4096:
            parser.error("bridge-data requires exactly one aligned 4096-byte fixture page")
    root = Path(__file__).resolve().parents[1]
    generation = None
    breakpoint = None
    debugging = False
    failures = []
    cleanup_errors = []
    sequence_broken = False
    sequence_error = None
    with (output / "bridge.jsonl").open("w", encoding="utf-8") as stream:
        def log(record):
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()

        class TracedBridge(WindowsNamedPipeBridgeClient):
            def call(self, request):
                nonlocal sequence_broken
                try:
                    return super().call(request)
                except Exception as exc:
                    sequence_broken = True
                    log({"transportError": type(exc).__name__, "message": str(exc), "method": request.method,
                         "cause": repr(exc.__cause__)})
                    raise

        bridge = TracedBridge(ce_pid=manifest["cePid"])

        def call(tool, params, expected_error=None, reconcile=False):
            nonlocal sequence_broken
            if sequence_broken and not reconcile:
                raise RuntimeError("probe sequence stopped after transport/session failure")
            log({"stage": "before", "tool": tool, "arguments": params})
            start = time.monotonic()
            try:
                result = service.call_tool(tool, params)
            except Exception:
                sequence_broken = True
                if not reconcile:
                    try:
                        call("ce.status", {}, reconcile=True)
                    except Exception as exc:
                        log({"reconcileError": type(exc).__name__, "message": str(exc)})
                raise
            log({"stage": "after", "tool": tool, "elapsedMs": round((time.monotonic()-start)*1000, 3),
                 "outcome": result.to_dict()})
            if result.error and result.error.code in {
                "BRIDGE_UNAVAILABLE", "OUTCOME_UNKNOWN", "TIMEOUT", "STALE_SESSION", "NO_TARGET", "TARGET_EXITED",
            }:
                sequence_broken = True
            if sequence_broken and not reconcile:
                # Never retry a failed mutation or reuse generation guards after reconnect.
                try:
                    call("ce.status", {}, reconcile=True)
                except Exception as exc:
                    log({"reconcileError": type(exc).__name__, "message": str(exc)})
                raise RuntimeError(f"{tool}: transport/session failure: {result.to_dict()}")
            if expected_error:
                assert result.error and result.error.code == expected_error, result.to_dict()
                return result.to_dict()
            if result.error:
                raise AssertionError(f"{tool}: {result.error.code}: {result.to_dict()}")
            return result.result

        @contextmanager
        def checks(name):
            log({"case": name, "stage": "before"})
            before = (len(failures), len(cleanup_errors))
            try:
                yield
            except AssertionError as exc:
                failures.append(name)
                log({"case": name, "result": "FAIL", "message": str(exc)})
            except Exception as exc:
                log({"case": name, "result": "ABORTED", "message": str(exc)})
                raise
            else:
                if before != (len(failures), len(cleanup_errors)):
                    failures.append(name)
                    log({"case": name, "result": "FAIL", "message": "nested check or cleanup failed"})
                else:
                    log({"case": name, "result": "PASS"})

        def cleanup(tool, params):
            try:
                if sequence_broken:
                    raise RuntimeError("generation invalid after transport/session loss; owned CE exit must release resource")
                result = call(tool, params)
                if params["action"] == "close":
                    assert result.get("closed") is True, result
                if tool == "ce.debug_control":
                    assert not result["debugger"]["active"], result
                log({"cleanup": tool, "arguments": params, "result": "PASS"})
            except Exception as exc:
                cleanup_errors.append({"tool": tool, "arguments": params,
                                       "type": type(exc).__name__, "message": str(exc)})
                log({"cleanupError": cleanup_errors[-1]})

        try:
            service = BackendService(bridge, root / "ce_mcp/contracts/v1/tools", request_deadline_ms=args.deadline_ms)
            call("ce.status", {})
            call("ce.process", {"action": "list", "limit": 200})
            attached = call("ce.process", {"action": "attach", "pid": target["pid"]})
            generation = attached["session"]["generation"]
            if manifest["mode"] == "bridge-data":
                fixture = target["fixture"]
                base = fixture["base"]
                guard = {"expectedGeneration": generation}

                def address(offset):
                    return f"0x{base + offset:016X}"

                def typed(data_type, offset, **extra):
                    return call("ce.memory_read", {"mode": "typed", "address": address(offset),
                                "dataType": data_type, **guard, **extra})

                def wait_completed(operation_id):
                    deadline = time.monotonic() + 8
                    while True:
                        operation = call("ce.operations", {"action": "get", "operationId": operation_id,
                                                          **guard})["operation"]
                        if operation["state"] == "completed":
                            return operation
                        assert operation["state"] in {"queued", "running"}, operation
                        assert time.monotonic() < deadline, f"operation did not complete: {operation}"
                        time.sleep(0.05)

                for data_type, item in fixture["numeric"].items():
                    for count in (1, 2):
                        with checks(f"typed.{data_type}.count{count}"):
                            result = typed(data_type, item["offset"], count=count)
                            expected = item["values"][0] if count == 1 else item["values"]
                            assert result["value"] == expected, {"expected": expected, "actual": result}
                            assert result["complete"] is True, result
                            assert int(result["resolvedAddress"]["address"], 16) == base + item["offset"], result

                for data_type, width in (("string", 1), ("wstring", 2)):
                    item = fixture[data_type]
                    # Include odd UTF-16 bounds and the exact terminator boundary.
                    for limit in (1, 5, 6, item["size"] - width, item["size"]):
                        with checks(f"typed.{data_type}.maxStringBytes{limit}"):
                            result = typed(data_type, item["offset"], maxStringBytes=limit)
                            expected = item["value"][:limit // width]
                            assert result["value"] == expected, {"expected": expected, "actual": result}
                            assert len(result["value"].encode("ascii" if width == 1 else "utf-16le")) <= limit, result
                    with checks(f"typed.{data_type}.normal"):
                        result = typed(data_type, item["offset"])
                        assert result["value"] == item["value"] and result["complete"] is True, result

                raw = fixture["raw"]
                expected_bytes = bytes.fromhex(raw["hex"])
                reads = {}
                for encoding in ("hex", "base64"):
                    with checks(f"raw.{encoding}"):
                        result = call("ce.memory_read", {"mode": "raw", "address": address(raw["offset"]),
                                      "size": len(expected_bytes), "encoding": encoding, **guard})
                        reads[encoding] = result
                        assert result["encoding"] == encoding, result
                        assert result["complete"] is True, result
                with checks("raw.decoded-equality"):
                    assert len(reads) == 2, "both raw responses required"
                    decoded = []
                    for result in reads.values():
                        try:
                            decoded.append(bytes.fromhex(result["bytes"]) if result["encoding"] == "hex" else
                                           base64.b64decode(result["bytes"], validate=True))
                        except ValueError as exc:
                            raise AssertionError(f"invalid raw encoding: {result}") from exc
                    assert decoded[0] == decoded[1] == expected_bytes, [value.hex() for value in decoded]

                with checks("typed.followPointers"):
                    chain = fixture["chain"]
                    result = typed("i32", chain["offset"], followPointers=chain["offsets"])
                    assert result["value"] == chain["value"], result
                    assert int(result["resolvedAddress"]["address"], 16) == chain["finalAddress"], result
                    path = [int(item["address"], 16) for item in result["pointerPath"]]
                    assert path == [base + chain["offset"], base + 0x280, chain["finalAddress"]], result

                with checks("memory.compare.unequal"):
                    result = call("ce.memory_analysis", {"action": "compare", "leftAddress": address(raw["offset"]),
                                  "rightAddress": address(raw["unequalOffset"]), "size": len(expected_bytes), **guard})
                    assert result["equal"] is False and result["firstDifference"] == raw["firstDifference"], result

                with checks("threads.pagination"):
                    first = call("ce.threads", {"action": "list", "limit": 1, **guard})
                    assert first["items"] and all(item["threadId"] > 0 for item in first["items"]), first
                    if first.get("nextCursor"):
                        second = call("ce.threads", {"action": "list", "limit": 1,
                                      "cursor": first["nextCursor"], **guard})
                        assert second["items"] and all(item["threadId"] > 0 for item in second["items"]), second
                        assert {item["threadId"] for item in first["items"]}.isdisjoint(
                            item["threadId"] for item in second["items"]), second
                    else:
                        log({"case": "threads.page2", "result": "NOT_CHECKED", "reason": "no nextCursor"})

                with checks("modules.observation"):
                    modules = call("ce.symbols", {"action": "modules", "limit": 200, **guard})
                    items = list(modules["items"])
                    if modules.get("nextCursor"):
                        second = call("ce.symbols", {"action": "modules", "limit": 200,
                                      "cursor": modules["nextCursor"], **guard})
                        items.extend(second["items"])
                    keys = [(item["name"].casefold(), int(item["base"]["address"], 16)) for item in items]
                    log({"observation": "modules.dedup", "items": len(keys), "uniqueNameBasePairs": len(set(keys)),
                         "uniqueBases": len({base for _, base in keys}),
                         "duplicates": len(keys) - len(set(keys)), "asserted": False})

                scan_range = {"rangeStart": address(0), "rangeEnd": address(fixture["size"] - 1),
                              "protection": "+W*X*C", "alignment": 4}
                sentinel = fixture["sentinel"]

                def scan_results(operation_id, unique=True):
                    result = call("ce.scan", {"action": "results", "operationId": operation_id, "limit": 200, **guard})
                    assert any(int(item["address"]["address"], 16) == base + sentinel["offset"]
                               for item in result["items"]), result
                    if unique:
                        assert result["total"] == 1 and len(result["items"]) == 1 and not result["truncated"], result
                        assert result["items"][0]["value"] == str(sentinel["value"]), result

                for initial in ("exact", "unknown"):
                    operation_id = None
                    with checks(f"scan.{initial}.lifecycle"):
                        try:
                            params = {"action": "start", "scanType": initial, "valueType": "i32", **scan_range, **guard}
                            if initial == "exact":
                                params["value"] = str(sentinel["value"])
                            started = call("ce.scan", params)
                            operation_id = started["operation"]["operationId"]
                            wait_completed(operation_id)
                            if initial == "exact":
                                with checks("scan.exact.results"):
                                    scan_results(operation_id)
                            for refinement in (("exact", "unchanged") if initial == "exact" else ("unchanged",)):
                                with checks(f"scan.{initial}.refine.{refinement}"):
                                    wait_completed(operation_id)
                                    params = {"action": "refine", "operationId": operation_id,
                                              "scanType": refinement, **guard}
                                    if refinement == "exact":
                                        params["value"] = str(sentinel["value"])
                                    call("ce.scan", params)
                                    wait_completed(operation_id)
                                    scan_results(operation_id, unique=initial == "exact")
                        finally:
                            if operation_id is not None:
                                cleanup("ce.scan", {"action": "close", "operationId": operation_id, **guard})

                operation_id = None
                with checks("signature.queued-cancel.lifecycle"):
                    try:
                        started = call("ce.signature", {"action": "start", "address": address(sentinel["offset"]),
                                       "rangeStart": address(0), "rangeEnd": address(fixture["size"] - 1),
                                       "minBytes": 4, "maxBytes": 16, "protection": "+W*X*C", **guard})
                        operation_id = started["operation"]["operationId"]
                        cancelled = call("ce.operations", {"action": "cancel", "operationId": operation_id, **guard})
                        with checks("signature.queued-cancel.immediate"):
                            assert started["operation"]["state"] == "queued", started
                            assert cancelled["operation"]["state"] == "cancelled", cancelled
                        time.sleep(1.2)
                        with checks("signature.queued-cancel.after-timer"):
                            result = call("ce.operations", {"action": "get", "operationId": operation_id, **guard})
                            assert result["operation"]["state"] == "cancelled", result
                    finally:
                        if operation_id is not None:
                            cleanup("ce.signature", {"action": "close", "operationId": operation_id, **guard})
            else:
                modules = call("ce.symbols", {"action": "modules", "limit": 1, "expectedGeneration": generation})
                module = modules["items"][0]
                call("ce.memory_read", {"mode": "raw", "address": module["base"]["address"], "size": 2,
                                        "expectedGeneration": generation})
                mapped = call("ce.memory_map", {"moduleFilter": module["name"], "limit": 5,
                                               "expectedGeneration": generation})
                assert mapped["items"], "module map is empty"
                if target["code"] != 0x400000:
                    decoded = call("ce.disassembly", {"action": "function", "address": f'{target["code"]:X}',
                                                      "detail": "full", "expectedGeneration": generation})
                    assert decoded["function"]["instructionCount"] == 2
                    assert [item["opcode"].strip() for item in decoded["items"]] == ["nop", "ret"]
                    assert not decoded["truncated"]
            if manifest["mode"] == "bridge-debug":
                def debug(action, **extra):
                    return call("ce.debug_control", {"action": action, "expectedGeneration": generation, **extra})

                def counter():
                    return call("ce.memory_read", {"mode": "typed", "address": f'{target["counter"]:X}',
                        "dataType": "u32", "expectedGeneration": generation})["value"]

                def stopped(after=0):
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        state = debug("status")["debugger"]
                        if state["stopped"] and state["stopGeneration"] > after:
                            return state["stopGeneration"]
                        time.sleep(0.05)
                    raise RuntimeError("native stop not observed")

                debug("start", interface="windows")
                debugging = True
                pause = debug("pause")["debugger"]
                assert pause["stopped"] and pause["stopKind"] == "suspend"
                before = counter()
                time.sleep(0.25)
                assert counter() == before
                call("ce.registers", {"action": "read", "expectedGeneration": generation,
                    "expectedStopGeneration": pause["stopGeneration"]}, "CONTEXT_UNAVAILABLE")
                debug("continue", mode="run", expectedStopGeneration=pause["stopGeneration"])
                time.sleep(0.1)
                assert counter() > before
                breakpoint = call("ce.breakpoints", {"action": "set", "trigger": "write", "size": 4,
                    "address": f'{target["counter"]:X}', "expectedGeneration": generation})["breakpoint"]["breakpointId"]
                stop = stopped(pause["stopGeneration"])
                before = counter()
                time.sleep(0.3)
                assert counter() == before, "false-paused regression"
                call("ce.registers", {"action": "read", "includeVectors": True,
                    "expectedGeneration": generation, "expectedStopGeneration": stop})
                call("ce.breakpoints", {"action": "remove", "breakpointId": breakpoint, "expectedGeneration": generation})
                breakpoint = None
                for mode in ("step_into", "step_over"):
                    debug("continue", mode=mode, expectedStopGeneration=stop)
                    previous = stop
                    stop = stopped(previous)
                    call("ce.registers", {"action": "read", "expectedGeneration": generation,
                        "expectedStopGeneration": previous}, "STALE_STOP")
                    call("ce.registers", {"action": "read", "expectedGeneration": generation,
                        "expectedStopGeneration": stop})
                call("ce.debug_events", {"action": "list", "limit": 10, "expectedGeneration": generation})
                debug("continue", mode="run", expectedStopGeneration=stop)
                time.sleep(0.2)
                assert counter() > before
                assert not debug("status")["debugger"]["stopped"]
        except Exception as exc:
            sequence_error = {"type": type(exc).__name__, "message": str(exc)}
            log({"sequenceError": sequence_error})
            raise
        finally:
            try:
                if generation is not None:
                    if breakpoint:
                        cleanup("ce.breakpoints", {"action": "remove", "breakpointId": breakpoint, "expectedGeneration": generation})
                    if debugging:
                        cleanup("ce.debug_control", {"action": "detach", "expectedGeneration": generation})
                    cleanup("ce.process", {"action": "detach", "expectedGeneration": generation})
            finally:
                try:
                    bridge.close()
                except Exception as exc:
                    cleanup_errors.append({"tool": "bridge.close", "type": type(exc).__name__, "message": str(exc)})
                    log({"cleanupError": cleanup_errors[-1]})
                log({"summary": {"failedCases": failures, "cleanupErrors": cleanup_errors,
                                 "sequenceBroken": sequence_broken, "sequenceError": sequence_error,
                                 "success": not (failures or cleanup_errors or sequence_broken or sequence_error)}})
        if failures or cleanup_errors:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
