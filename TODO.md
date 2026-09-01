# Deferred work

## DBK / DBVM positive lifecycle

Status: deferred and not a release blocker. Contracts remain disabled by normal
profiles. Do not initialize DBK or DBVM merely to satisfy tests.

Resume only on an explicitly authorized host where Cheat Engine reports:

```lua
dbk_initialized() == true
dbvm_initialized() == true
```

Required before enabling:

1. `ce.status` reports `dbvmReadiness = "ready"` under matching hypervisor
   policies.
2. Watch lifecycle: `start -> events -> stop`, including disconnect/detach and
   generation cleanup.
3. Trace lifecycle: `start -> results -> archive_results -> stop -> remove`.
4. No tokens or trace contents enter audit metadata.
5. Repeat the full suite, real-CE gates, wheel build, and clean-install gate.
