# Evidence-first development workflow

This workflow is mandatory for CE-facing functionality. A passing schema or
mock test does not prove that a Cheat Engine API lifecycle is usable.

## 1. Evidence hierarchy

Use evidence in this order:

1. Documentation for the installed production CE version (currently CE 7.7 at
   `C:\tools\Cheat Engine\celua.txt`; retain CE 7.5 evidence only when the
   supported-version comparison is relevant).
2. Official Cheat Engine source for the installed API binding and underlying
   object lifecycle.
3. The surveyed reference implementations, read as complete call sequences;
   ordering and cleanup lines are part of the behavior.
4. A standalone minimal CE probe against a disposable target.
5. Bridge integration tests against a disposable target.
6. Sidecar/MCP vertical-slice tests.

Lower levels may reveal a problem, but they must not override contradictory
higher-level evidence without a written explanation and a reproducer.

## 2. Required sequence for a new CE capability

### A. Derive the lifecycle before designing the MCP wrapper

Record:

- creating thread and required execution thread;
- object ownership and object dependencies;
- legal call order;
- completion signal and whether it requires the GUI thread;
- cancellation mechanism;
- cleanup order for success, error, timeout, detach, target exit, and CE exit;
- which calls mutate CE or target state;
- limits and potentially blocking calls.

Do not choose synchronous versus operation-backed MCP semantics until these
facts are evidenced.

### B. Build a standalone probe

The probe must:

- contain no MCP, named-pipe, or sidecar code;
- use a disposable process and isolated CE instance;
- write/flush a stage marker before every potentially blocking transition;
- have an external timeout and exact-PID cleanup;
- test the full native lifecycle, including cleanup;
- preserve a failing stage as evidence.

Probe code belongs under `bridge/probes/` and is never a public handler.

### C. Promote one boundary at a time

Promotion order:

1. native synchronous lifecycle probe;
2. bridge handler with structured errors and cleanup;
3. session/generation and disconnect behavior;
4. operation/cancel/pagination behavior if needed;
5. MCP SDK exposure;
6. real vertical-slice smoke.

Each boundary gets its own test. Do not debug two new boundaries at once.

## 3. Promotion gate

A capability may be listed in `capabilities.enabled` only when all applicable
evidence exists:

- contract fixtures pass;
- Lua syntax and handler coverage pass;
- standalone lifecycle probe passes on the supported CE 7.7 baseline;
- bridge integration passes on the supported CE 7.7 baseline;
- timeout/cancel/cleanup behavior has a deterministic result;
- the production vertical slice passes;
- remaining platform coverage is recorded in `TODO.md` or probe results.

Until then, the handler must return `CAPABILITY_UNAVAILABLE`, status must state
the reason, and documentation must call the capability unverified. Checked-in
but unreachable experimental code is not allowed in a production handler.

## 4. Failure and rollback rule

After the first unexplained hang, crash, or unknown mutation outcome:

1. stop changing the production handler;
2. capture the last emitted stage and exact process state;
3. restore the last real-CE-verified behavior;
4. move investigation to a minimal probe;
5. inspect the complete official/reference lifecycle again;
6. promote only after the probe proves the corrected hypothesis.

Do not cycle through callback, timer, or thread models inside the production
bridge based only on inference.

When a probe fails before the behavior under test, report it as a probe or
fixture failure. For memory scans, make the test range cover a complete
committed region unless the purpose of the probe is specifically to test range
boundaries; a tiny candidate-sized range can be rejected during CE region
enumeration and create a false negative.

## 5. Test reporting vocabulary

Use these terms precisely:

- **Contract-tested**: schema/service behavior only.
- **Lua-loaded**: CE accepted the script; behavior is not yet proven.
- **Probe-verified**: isolated native lifecycle passed.
- **Bridge-verified**: real CE request/response lifecycle passed.
- **MCP-verified**: official MCP client vertical slice passed.
- **Fail-closed**: intentionally exposed as unavailable and not enabled.

Never describe contract-tested or Lua-loaded behavior as implemented or
working on CE.

## 6. Review checklist

Before merging a CE-facing change, verify:

- [ ] Documentation and official binding source were read for the exact API.
- [ ] Reference call order and cleanup order were recorded with the probe or
      implementation.
- [ ] Object/thread ownership is explicit.
- [ ] Standalone probe exists and passed, or capability remains fail-closed.
- [ ] No old handle survives generation change, detach, or target replacement.
- [ ] Blocking calls have an external timeout and recovery procedure.
- [ ] Tests distinguish mocks from real CE evidence.
- [ ] Temporary autorun files and exact test PIDs were cleaned up.
- [ ] Real-CE tests select the intended PID-specific pipe through automatic
      single-instance discovery or explicit `--ce-pid`; no environment variable
      is required. Server-PID verification proves the pipe belongs to that CE.

## 7. Python test environment gate

On Windows, `python`, `py -3.10`, and `.venv\Scripts\python.exe` may resolve to
different installations. The project environment is managed by uv and the
canonical locked test runner is:

```powershell
uv run --locked python -m unittest discover -s tests -v
```

`uv.lock` is checked in and `.python-version` selects Python 3.10. Before
interpreting dependency-related skips, use `uv run --locked`, print
`sys.executable`, and test the import in that same interpreter. Do not report an
SDK as missing merely because a global launcher lacks it. Falling back from an
unusable WindowsApps alias to `py` is not an environment diagnosis.
