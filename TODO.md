# Deferred work

## DBK / DBVM positive lifecycle

Status: deferred by user on 2026-08-29. This is not a current release blocker.

The bounded implementation, dual authorization, cleanup logic, contracts, and
negative/no-load probes remain in the repository as experimental functionality.
Do not initialize DBK or DBVM merely to satisfy tests.

Resume only on an explicitly authorized host where Cheat Engine reports:

```lua
dbk_initialized() == true
dbvm_initialized() == true
```

Then prove, in order:

1. `ce.status` reports `dbvmReadiness = "ready"` under matching hypervisor
   policies.
2. Watch lifecycle: `start -> events -> stop`, including disconnect/detach and
   generation cleanup.
3. Trace lifecycle: `start -> results -> archive_results -> stop -> remove`.
4. No tokens or trace contents enter audit metadata.
5. Repeat the full suite, real-CE gates, wheel build, and clean-install gate.

Current-host evidence:

- CE 7.7 documents and exposes both readiness queries.
- With the debugger method selected but DBVM unavailable due the host reporting
  that it may already be inside a VM, both queries returned `false`.
- Trace status safely returned `(0,0,0)` and physical translation returned nil;
  no watch/trace resource was created.
