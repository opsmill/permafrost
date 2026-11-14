# Design

## Context

See `proposal.md` for the motivation and `specs/dimension-snapshots/spec.md` and `specs/value-loader/spec.md` for the requirements.

Current state in `src/permafrost/store.py`:

- `Store.get()` calls `_root()`, then `keymap.get()`, then `_value()`. `_root()` returns `self._dims[dimension]`, or reads the root hash from the backend and fetches the key map when the dimension is not in memory.
- `_value()` looks for one value in `self._values` (the value cache), then in the registry, then calls `_fetch()` for one tree, then calls the loader. When the loaded value has another hash and the caller does not hold the lock, it takes the lock and commits the loaded value to the dimension, if the key still has the old hash.
- `_fetch(root_hash)` fetches one tree, one level at a time, with one `Backend.get_many()` request for each level, then calls `touch()` on the fetched hashes and rebuilds the tree. It returns `_MISSING` as soon as one node is missing or cannot be decoded.
- `_cache()` puts a value in the value cache with the current time, adds its estimated size when a memory limit is set, and records it as read since the previous cleanup when a backend is set.
- `cleanup()` evicts from `self._values` by idle time and by memory limit. The registry holds weak references, so a value that only an evicted cache entry held is freed.
- Readers do not take the dimension lock. A store runs in one event loop, so a reader and a writer can only interleave at an `await`.
- `keymap.items(root)` yields every `(key, value_hash)` entry of a key map.

## Goals / Non-Goals

**Goals:**

- One `async` call that returns an object holding one key map root and every value under it, readable without `await`.
- Fetch the missing values of a dimension from the external cache in as many requests as the trees have levels, not one series of requests for each value.
- Keep `get()`, `keys()`, the loader behavior and all write operations unchanged.

**Non-Goals:**

- A snapshot of only some keys. See the assumptions in `proposal.md`.
- Updating a snapshot in place, or a snapshot that follows the dimension. The application takes a new snapshot.
- Calling the loader for several keys at once or concurrently. The loader is still called once for each key, one key at a time, as `SPEC.md` "Out of scope" states.
- Counting the memory of values held only by snapshots in `cached_size` or in the memory limit.

## Decisions

### API

```python
class Store[V]:
    async def snapshot(self, dimension: str) -> Snapshot[V]: ...


@final
class Snapshot[V]:
    dimension: str  # read-only property
    root_hash: bytes  # read-only property

    def get(self, key: str) -> V: ...  # raises KeyNotFoundError(dimension, key)
    def keys(self) -> list[str]: ...  # new list, no defined order
    def __contains__(self, key: object) -> bool: ...
```

- `Snapshot` lives in `store.py` next to `Store` and is exported from `permafrost`. Its constructor is internal: only `Store.snapshot()` creates one.
- `Snapshot` does not subclass `collections.abc.Mapping`. `Mapping.get()` returns `None` for a missing key, while `Store.get()` raises `KeyNotFoundError`. Keeping the same name with the same behavior as `Store.get()` avoids two meanings of `get`. Alternative considered: a `Mapping` subclass with `__getitem__`. It adds `values()`, `items()`, `==` and `get(key, default)` to the contract, which the issue does not ask for.
- `root_hash` is a property, because a snapshot has one root. `Store.root_hash(dimension)` stays a method.
- `keys()` returns a new list, like `Store.keys()`, so the caller can sort or change it.
- Name and class name are the recommended answers to the open question of the issue. See the assumptions in `proposal.md`.

### What a snapshot holds

- `_dimension: str`, `_root_hash: bytes`, and `_values: dict[str, object]` that maps each key to its value root.
- The dict holds strong references, so cleanup cannot free the values while the snapshot exists. The values are the same objects as in the value cache and the registry, so the snapshot adds one dict entry for each key, not a copy of each value.
- The snapshot holds the root hash, not the key map root. `get()`, `keys()` and `in` use the dict, so the key map nodes are not needed after the snapshot is built.
- `__slots__` keeps the object small. A `Snapshot` has no reference to the `Store`.

### Order of steps in `Store.snapshot()`

1. Call `_root(dimension)` once and keep the returned root in a local variable. This is the only read of `self._dims`, so later commits to the dimension do not change what the snapshot holds, even while step 3 awaits the backend.
2. List the entries with `keymap.items(root)`. For each distinct value hash, look in `self._values`, then in the registry. Collect the hashes that neither holds.
3. With a backend, fetch the collected hashes together with a new `_fetch_many(hashes)` (see below).
4. For each key whose value is still missing, call the loader through `_load()` (see below), one key at a time. Without a loader, raise `CacheMissError(dimension, key)` for the first missing key, in sorted key order, so the error is deterministic.
5. When a loaded value has another hash, apply `keymap.set()` to the local root for that key, so the snapshot's root hash matches its values. `_load()` also commits the loaded value to the dimension, as `get()` does today.
6. Call `_cache()` for every value, so taking a snapshot counts as a read of each value for eviction and for extending expiry in the external cache.
7. Return `Snapshot(dimension, local_root._pf_hash, values_by_key)`.

The operation does not take the dimension lock. Step 1 reads one root, and the values are found by the hashes in that root, so a concurrent writer cannot mix two versions into one snapshot. Taking the lock would make the snapshot wait for writers without making it more consistent.

### Fetching several values together: `_fetch_many()`

`_fetch_many(hashes: Iterable[bytes]) -> dict[bytes, object]` returns the rebuilt tree for each root hash it could fetch completely, and leaves out the others.

- It walks all trees together: each round sends one `get_many()` request with the missing nodes of the current level of every tree, then continues with their children. Nodes already in the registry are held in a local list and not requested, as in `_fetch()`.
- A missing or undecodable node does not stop the other trees. The function records it, and after the walk a root whose tree reaches a missing node is left out of the result.
- It calls `touch()` once on all fetched hashes, then rebuilds each complete tree with the same `build()` and `resolve()` logic as `_fetch()`.
- `_fetch(h)` becomes a call to `_fetch_many([h])`, so both paths share one implementation and the current behavior of `get()` stays the same: one tree, one request per level.

Alternative considered: call `_value()` for each missing key. For a branch schema of a few hundred kinds with a depth of about five, that is about a thousand requests instead of about five.

### Loading a value: `_load()`

The loader part of `_value()` moves to `_load(dimension, key, vh, *, locked)`: call the loader, freeze the result, write every node of it to the backend, commit it to the dimension when its hash differs and the key still has the old hash, cache it and return it. `_value()` calls `_load()` after its cache lookups, so its behavior does not change. `snapshot()` calls `_load()` directly for the keys that `_fetch_many()` did not return, which avoids a second fetch request for each of these keys.

### Reads from a snapshot do not touch the value cache

`Snapshot.get()` does not update the read time of the value in the store and does not record it as read since the previous cleanup. It has no reference to the store, and updating the cache from a synchronous read would make each read cost more than a dict lookup. The values stay readable from the snapshot whatever cleanup does. The consequence is described under Risks.

### Specification updates

- `SPEC.md` goal 1 gets a bullet for taking a snapshot of a dimension and reading it synchronously.
- `SPEC.md` goal 10: the bullet "operations that read a value are `async`" gets the exception for reads from a snapshot, whose values are all in memory.
- `SPEC.md` "What keeps nodes in memory": a third holder of strong references, snapshots, which hold the root of each of their values.
- `SPEC.md` goal 11: cleanup does not free the values of a snapshot that the application still holds.
- `README.md` gets a "Snapshots" section under "Dimensions" with a runnable example, which `tests/unit/test_readme.py` runs.

### Tests

- `tests/unit/test_store.py`, without a backend: the scenarios of the requirements "Take a snapshot of a dimension", "Read a snapshot synchronously" and "A snapshot does not follow later changes", including a writer task that changes `main` between two reads from a snapshot.
- `tests/unit/test_cleanup.py`: the scenarios of "A snapshot keeps its values in memory", with a fake clock and with a memory limit.
- `tests/unit/test_external_cache.py` and `tests/unit/test_sharing.py`, with `MemoryBackend`: request count for 200 values against the number of tree levels, values dropped from the backend and loaded with the loader, a loaded value with another hash, a cache miss without a loader, a loader error, one tree with a missing node while the others are returned, the change-during-snapshot scenario with a writer task that runs while the snapshot awaits `get_many()`, and a second `Process` that takes a snapshot, then reloads after a change.
- `tests/unit/test_typing.py`: `assert_type(snapshot.get("k"), Device)` for a `Store[Device]`.
- `tests/integration/test_redis.py`, with the `redis_backend` and `redis_peer` fixtures and two `Process` contexts:
  - process A stores nested values in `main`; process B, with its own store and registry, takes a snapshot and reads every value synchronously, and the snapshot's root hash equals the Redis root entry
  - process A then changes `main`; process B reloads `main`, its old snapshot still returns the old values and its `async` reads return the new ones
  - some value nodes are deleted from Redis; process B takes a snapshot with a loader and gets the loaded values, and without a loader gets `CacheMissError`

## Risks / Trade-offs

- [A snapshot that the application keeps for a long time keeps its values in memory after cleanup evicts them from the value cache, and that memory is not counted in `cached_size` or the memory limit.] → This is the requested behavior. The README states that an application should drop a snapshot at the end of the request or task that took it.
- [Reads from a snapshot do not update read times, so cleanup can evict from the value cache the values that a long-held snapshot still reads.] → The snapshot still returns them. The next snapshot reads them again from the external cache or the loader.
- [After a restart of the external cache, taking a snapshot of a large dimension calls the loader once for each key, one at a time.] → Same per-key behavior as `get()`. A loader for several keys is out of scope in `SPEC.md`.
- [Taking a snapshot reads every value, even the ones the application will not read.] → The issue expects this to be acceptable for a branch schema of a few hundred kinds. It has not been measured.
- [The loader can commit a loaded value with another hash to the dimension while the snapshot is being taken.] → The snapshot builds its own root with the loaded value, so its root hash matches its content. This is the same commit that `get()` makes today.

## Open Questions

- The time and memory cost of a snapshot of a real Infrahub branch schema has not been measured. It does not change the API or the tasks.
