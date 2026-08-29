# Artifact store design

## Security boundary

The sidecar owns a single configured artifact root. Public requests never
contain a host path. Artifact lookup accepts only `art-` plus 32 lowercase hex
digits and resolves both data and metadata directly beneath that root.

Creation writes data and metadata to exclusive temporary files, flushes them,
then atomically renames them. Failure removes every partial file. Metadata
records size, SHA-256, media type, session, generation, creation time, and a
bounded source description. Reads recompute SHA-256 so external corruption is
reported instead of silently served.

Current actions are `memory_dump`, `list`, `get_metadata`, `preview`, and
`delete`. Preview is capped at 4096 bytes. Memory dumps are capped at 16 MiB
and use 256 KiB bridge reads. Larger dumps remain pending until the sidecar has
a cancellable long-operation manager.

## Verification record (2026-08-29)

A real CE 7.5 x64 MCP vertical slice attached to the smoke process and dumped a
266,240-byte controlled buffer, forcing two bridge reads. The artifact SHA-256
matched the source buffer, a 32-byte preview crossing the 256 KiB read boundary
matched, metadata and list returned the created ID, and delete removed it.

Contract/security tests additionally prove rejection of client host paths and
path traversal, rollback of oversized creation, integrity detection after data
tampering, lazy root creation, and MCP stdio startup without filesystem writes.
