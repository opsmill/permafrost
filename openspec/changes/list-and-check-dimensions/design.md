# Design

## Context

See `proposal.md` for motivation and `specs/dimensions/spec.md` for the contract. The user chose memory-only listing and an existence check covering memory and the external cache.

`Store._dims` maps names to key map roots. Creation commits a root there; `_root()` loads a nonlocal dimension's key map there; `reload()` also installs a fetched root. `root_hash()` already inspects local memory synchronously. `create_dimension()` checks local membership and then `Backend.get_root()` to reject duplicate names. The backend protocol already exposes every operation needed here.

`MemoryBackend` supports sharing between stores, dropping nodes, and clearing its entries. Its `get_root()` does not record requests in `log`, so verifying root lookup counts requires a test subclass or spy. `tests/process.py` supplies separate registries for tests that simulate processes.

## Goals / Non-Goals

**Goals:**

- Inspect dimension metadata without fetching key maps or values.
- Use the same definition of existence as the duplicate-name check in `create_dimension()`.
- Return exact types independent of the store's value type.

**Non-Goals:**

- Validate the completeness of a dimension's cached data or refresh a locally held root.
- Reserve a name or guarantee that a later operation will succeed.
- Introduce backend operations, scanning, locks, or another registry of dimension names.

## Decisions

### 1. Synchronous `dimensions()` returns a detached list

Return `list(self._dims)` with a docstring specifying local scope and undefined order. This follows the collection return style of `keys()`. A dictionary view would expose live membership changes and a set would add an unnecessary return convention. The snapshot contains names only, takes O(number of local dimensions) time and space, and does no I/O. No lock is needed: the method does not yield, and stores are used in one thread and event loop.

### 2. Asynchronous `has_dimension()` checks memory before one root lookup

Use membership in `_dims` as the fast path. If the name is absent and there is no backend, return `False`. Otherwise return whether `await backend.get_root(dimension)` is not `None`. Keep the method async even without a backend, so its call shape does not depend on store configuration.

Calling `_root()` was considered but would fetch the entire key map, mutate the local dimension listing, and fail when nodes are lost. Catching `DimensionNotFoundError` from another read has the same drawbacks. The root-entry check matches `create_dimension()` and needs no backend protocol or Redis implementation changes.

Keep the existing checks in `create_dimension()` and `replace_dimension()` in place; a refactor is unnecessary for these two small additions. No new package-level exports are needed because both methods are on the exported `Store` class.

### 3. Existence reports membership rather than readability

A local dimension remains present when an external cache loses entries. A remote root entry counts as present even when its nodes are missing. The existence check neither inserts a name into `_dims` nor calls the loader. Backend errors propagate, following other store operations; treating an outage as absence could lead a caller to attempt to recreate an existing dimension.

Document these semantics in both method docstrings, `SPEC.md` goals 1 and 9, and README dimensions and sharing guidance. The README example should show a second store finding a dimension through `has_dimension()` while its `dimensions()` stays empty, then including it after a successful read or reload.

## Risks / Trade-offs

- [Listing and existence have different scopes.] → State each scope in the API documentation and demonstrate the difference with two stores sharing a backend.
- [A positive check does not guarantee a successful subsequent read or create.] → Explain missing nodes and concurrent changes; preserve the existing errors and atomicity of the operation that follows.
- [Names present in local memory can outlive external root entries.] → Keep memory authoritative for this query, as it is for ordinary reads; do not turn an existence check into reload or deletion.

## Migration Plan

No migration is needed. Both methods are additive and change no serialized content, hashes, backend keys, or existing method behavior.
