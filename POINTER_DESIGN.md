# Pointer workflow design

## Evidence and boundary

Cheat Engine 7.5 documents `readPointer(address)` as a target-width pointer
read. It does not document a result-bearing pointer-scanner Lua object. The
surveyed CE bridge calls an undocumented `pointerRescan`, returns
`result_count = -1`, and tells the caller to inspect the Pointer Scanner GUI.
Its later pointer-tools design explicitly excludes a full programmatic pointer
scanner for the same reason.

The MCP backend therefore does not expose that GUI side effect as a successful
scan. The verified public boundary is:

- `ce.pointer(action="resolve")`: resolve one chain using CE convention
  (dereference, then add each signed offset);
- `ce.pointer(action="validate")`: resolve at most 500 candidate chains and
  return matching chains, with misses omitted by default;
- one-level walk-back discovery: use the existing bounded `ce.scan` exact scan
  for the target architecture's pointer-sized integer, then turn result
  addresses into candidate chain bases;
- stability: rerun `validate` after target restart with a fresh generation and
  newly resolved target address. No chain is called stable from one session.

Full native pointer scan/rescan remains capability-unavailable until an API is
found that provides bounded results, progress, cancellation, and deterministic
cleanup without depending on CE GUI state or arbitrary host paths.

## Verification record (2026-08-29)

A real CE 7.5 x64 MCP vertical slice attached to the smoke process itself. It
constructed a controlled two-dereference chain, resolved the exact leaf
address, then validated one match, one readable miss, and one unreadable chain.
Observed result: three resolution steps, `matched=1`, `unreadable=1`, and two
reported misses. Resolve and validate are MCP-verified on x64; x86 coverage is
still pending.

## Limits and safety

- 16 dereferences per chain;
- 500 chains per validation request;
- every request is target-session and generation bound;
- pointer-sized values are serialized as canonical address strings, never JSON
  floating-point numbers;
- invalid reads are classified as unreadable candidates rather than guessed;
- no arbitrary pointer-map filename is accepted.
