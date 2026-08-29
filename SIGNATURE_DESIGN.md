# Signature workflow design

`getUniqueAOB` is not exposed. CE 7.5 implements it as a potentially long,
whole-memory synchronous search with no result-bearing cancellation handle.
That behavior is incompatible with the bridge request deadline and disconnect
cleanup guarantees.

`ce.signature` instead searches for an exact AOB unique inside a caller-supplied
range. The range is mandatory and limited to 64 MiB. Candidate bytes are read
once from the requested target address, then CE `MemScan` tests increasing
lengths from 4–64 bytes. The first candidate whose only match is the requested
address is returned with offset zero.

The search is a generation-bound operation. `start` returns a queued handle
before any MemScan begins; a proved one-second main-thread timer boundary lets
the pipe worker flush that response. `ce.operations` provides status and
cancellation, while `ce.signature(result|close)` snapshots or releases the
result. Target replacement, pipe disconnect, cancel, and bridge shutdown share
the normal operation cleanup path.

This first version intentionally produces exact signatures. Relocation-aware
wildcard inference requires separate disassembler evidence and uniqueness gates;
it must not guess which immediate or displacement bytes are stable.
