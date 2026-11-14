# Proposal

## Why

Infrahub plans to store its schema registry in Permafrost, with one dimension for each branch, and reads the schema of a branch from synchronous code in about 110 functions ([issue #10](https://github.com/opsmill/permafrost/issues/10)). Infrahub plans to obtain the schema of a branch once per request or task, with one `await`, and read from it synchronously afterwards. Every read in that request or task must return values from the same version of the branch schema.

Permafrost cannot support this today, for three reasons:

- `Store.get()` and `Store.keys()` are `async`, even when no I/O is needed.
- Each read uses the current root of the dimension, so two reads separated by an `await` can return values from two versions of the dimension.
- Keeping a copy of a root does not keep the values under it in memory, because cleanup can evict them from the value cache.

Infrahub needs this operation before it can use Permafrost.

## What Changes

- Add `Store.snapshot(dimension)`, an `async` operation that returns a `Snapshot[V]`, a read-only snapshot of one dimension.
- Taking a snapshot reads the key map root of the dimension once and reads every value under it before it returns:
  - values in the value cache or the registry are used without I/O
  - with an external cache, the missing values are fetched together, one tree level at a time, so the number of requests grows with the depth of the values, not with their number
  - values that no cache holds are loaded with the loader, one key at a time, as `get()` does
  - when a value cannot be read, for example a cache miss without a loader, taking the snapshot raises the error and returns no snapshot
- `Snapshot` has synchronous read operations. None of them waits on I/O or calls the loader:
  - `get(key)` returns the value, or raises `KeyNotFoundError` when the snapshot does not hold the key
  - `keys()` returns the keys, in no defined order
  - `key in snapshot` checks whether the snapshot holds a key
  - `root_hash` returns the root hash of the snapshot's key map
  - `dimension` returns the name of the dimension the snapshot was taken from
- Changes made to the dimension after the snapshot was taken do not change the snapshot.
- A snapshot holds a reference to each of its values, so cleanup does not free them while the application holds the snapshot.
- `SPEC.md` changes:
  - goal 10 allows synchronous reads of values from a snapshot, whose values are already in memory
  - "What keeps nodes in memory" lists snapshots as a third holder of strong references
  - goal 1 describes snapshots, and goal 11 states that cleanup does not free the values of a snapshot

Assumptions, because the issue leaves these points open and no answer was given when planning:

- The operation is named `Store.snapshot()` and the class `Snapshot`.
- A snapshot reads every value of the dimension. Taking a snapshot of only a list of keys is not part of this change.
- Because a snapshot holds every value, a synchronous read of a key that the snapshot holds always returns its value. The third open question of the issue, what a read returns when the value is not in memory, therefore does not apply.

## Capabilities

### New Capabilities

- `dimension-snapshots`: taking a read-only snapshot of one dimension and reading it synchronously, with values from one version of the dimension that stay in memory while the snapshot is held.

### Modified Capabilities

- `value-loader`: the requirement "Reads that can reach the loader are async" changes so that reads from a snapshot, which cannot reach the loader or the external cache, are regular functions.

## Impact

- `src/permafrost/store.py`: new public method `Store.snapshot()` and new class `Snapshot`. The fetch from the external cache gets a form that fetches several values together. `get()`, `keys()` and the write operations keep their behavior.
- `src/permafrost/__init__.py`: exports `Snapshot`.
- `SPEC.md`: goals 1, 10 and 11, and "What keeps nodes in memory".
- `README.md`: a section on snapshots under "Dimensions".
- Tests: unit tests without a backend and with `MemoryBackend`, a static typing test, and real Redis integration tests in `tests/integration/test_redis.py`.
- No new dependency. No change to the backend interface or to the Redis backend.
