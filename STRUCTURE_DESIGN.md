# Structure workspace design

Structure definitions are sidecar-owned analysis data, not CE global GUI state.
The reference bridge calls `addToGlobalStructureList`, which mutates the user's
visible structures and persists them into a cheat table. This backend does not
do that implicitly.

Each workspace definition has an opaque ID, revision, name, bounded layout size,
and at most 256 validated fields. Updates require the current revision so two
clients cannot silently overwrite one another. Field offsets and byte spans must
fit inside a 64 KiB layout. Names are bounded and field types come from a closed
portable set.

CRUD is target-independent. Reading a definition at a target base is
generation-bound: the sidecar sends a bounded immutable field plan to the CE
bridge and the bridge returns copied values. No Structure userdata, CE-local
pointer, Lua callback, host path, or global structure is exposed.

Inline export can later be stored through the artifact store. Client-selected
host paths are forbidden. CE `Structure.autoGuess` remains a separate capability
until a temporary-object lifecycle probe proves output and cleanup semantics;
heuristic guesses must never overwrite an explicit workspace definition.
