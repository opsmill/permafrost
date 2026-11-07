# Design

## Context

See `proposal.md` for the reason for this change and the files in `specs/` for the required behavior.

How the root of a dimension changes today, at commit `46f6db2`:

- `Store._dims` maps each dimension held in memory to its key map root, a `KMBranch`.
- `Store._commit(dimension, root, values, previous=...)` writes the nodes that the backend may not hold yet, then the root hash with `backend.set_root()`, then sets `_dims[dimension]`. It returns early when `root is previous`. The caller must hold the lock of the dimension.
- `Store._send()` writes only the nodes recorded in `_pending`, which holds the nodes created in this process that were not written yet.
- `Store._root(dimension)` returns the root from memory, or reads the root hash from the backend and fetches the whole key map.
- `create_dimension(name, source)` checks that `name` exists neither in memory nor in the backend, then commits the root of `source` under `name`.

## Goals / Non-Goals

**Goals:**

- Switch an existing dimension to the content of another dimension in one commit.
- Write no node and create no node when the content of the source is already in the external cache and in memory.

**Non-Goals:**

- A snapshot as the source (issue #10).
- Deleting the candidate dimension (issue #12).
- Checking that the dimension still has an expected root before replacing it.
- Several changes to keys in one commit (issue #11).

## Decisions

### 1. A new method rather than an option of `create_dimension()`

`replace_dimension(name, source)` is a method of its own. `create_dimension()` keeps raising `DimensionExistsError` for an existing name, which protects an application from overwriting a dimension by mistake. `source` is required, because replacing a dimension with an empty one is the same as deleting each key, which is not what the issue asks for.

Alternative considered: `create_dimension(name, source, replace=True)`. It mixes two operations with opposite checks on whether `name` exists.

### 2. Check that `name` exists without fetching its key map

`replace_dimension()` looks for `name` in `_dims`, then asks the backend for its root hash with `get_root()`. It does not call `_root(name)`, because that would fetch the whole key map of `name` only to drop it. When `name` is in neither, it raises `DimensionNotFoundError(name)` and writes nothing.

### 3. Commit through `_commit()`

The method holds the lock of `name`, reads the root of `source` with `_root(source)`, and calls `_commit(name, root, previous=current)`, where `current` is the root of `name` held in memory or `None`. This gives the same order of writes as every other change: nodes first, then the root hash. Because the nodes of `source` were written when `source` was committed, `_send()` normally finds no pending node, and the only request is `set_root`. When `name` already has the same root in memory, `_commit()` returns early and writes nothing.

The lock of `source` is not taken. `_commit()` sets `_dims[source]` in one step after its writes, so `_root(source)` returns either the root before or after a concurrent change to `source`. `create_dimension()` reads `source` in the same way.

### 4. No check of an expected root

The issue leaves open whether the operation checks that `name` still has an expected root. Within one process, the lock already orders changes to `name`. Across processes, a check would need an atomic compare-and-set in the backend, which the `Backend` interface does not offer and `SPEC.md` lists as out of scope. The method replaces the root without a check, and `SPEC.md` lists the question in "Open questions".

## Risks / Trade-offs

- [A change made to `name` after the application created the candidate is lost when it replaces `name`.] → The README states it. The application decides whether to check `root_hash(name)` before the switch.
- [The external cache lost some nodes of `source` that this process holds in memory and already wrote.] → `_send()` does not write them again, so another process that reads `name` raises `DimensionNotFoundError`. `create_dimension(name, source)` has the same behavior today, and goal 9 of `SPEC.md` already describes it.
- [Candidate and backup dimensions stay in memory and in the external cache.] → Deleting a dimension is issue #12.

## Migration Plan

No migration. The method is new and changes no encoding, hash or backend entry.

## Open Questions

- Whether the operation can check that the dimension still has an expected root before replacing it.
