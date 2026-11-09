# Design

## Context

`Store.set()`, `Store.delete()` and `Store.update()` in `src/permafrost/store.py` each take the lock of the dimension, read its key map root with `_root()`, build one new root with `keymap.set()` or `keymap.delete()`, and call `_commit()`. `_commit()` already does what one commit of several keys needs:

- `_send()` walks the new root and the new values and writes, in one `Backend.set_many()` request, only the nodes that `_pending` still lists.
- It skips the root hash write when the new root is the previous root.
- It writes the root hash to the backend before it switches `self._dims[dimension]`, so an error from the backend leaves the dimension in memory on its previous root.

Readers (`get()`, `keys()`, `diff_keys()`) do not take the lock. They read `self._dims[dimension]` once. Because a store runs in one event loop, a reader can only interleave with a writer at an `await`.

`keymap.set()` returns the same node when the key already has that value hash, and `keymap.delete()` returns the same root when the key is absent.

See `proposal.md` for the motivation and the specs in `specs/dimensions/spec.md` and `specs/dimension-sharing/spec.md` for the requirements.

## Goals / Non-Goals

**Goals:**

- One public method that applies several sets and deletes to one dimension and commits one root.
- Reuse `_commit()` and `_send()` without changing their behavior for the existing operations.

**Non-Goals:**

- A context manager that collects changes. It can be built later on top of `set_many()`.
- Changes to several dimensions in one commit. Each dimension has its own root hash entry in the external cache, and the backend interface has no operation that writes several root hashes at once.
- A per-key `update()` callable inside the batch. The caller reads the current values with `get()`, computes the new values, and passes them in `values`.
- A bulk key map builder that shares path nodes between the keys of one batch. See Risks.
- Preventing concurrent changes from several processes. The last write of the root hash still wins, as the `dimension-sharing` spec states.

## Decisions

### Method name and signature

`async def set_many(self, dimension: str, values: Mapping[str, V], *, delete: Iterable[str] = ()) -> dict[str, V]`.

- The name follows `set()` and the existing `Backend.set_many()`. The keyword `delete` matches the existing `delete()` method and keeps deletes optional.
- A mapping cannot hold the same key twice, so sets cannot conflict with each other.
- The return value matches `set()`, which returns the read-only form of the stored value.
- Alternative considered: a context manager such as `async with store.batch(dimension) as b`. It needs rules for holding the lock while the caller awaits other work and for reads of staged changes inside the block. The issue leaves the shape open; the mapping form is the smaller contract and a context manager can call it.
- Alternative considered: separate `values` and `delete` positional arguments. A keyword-only `delete` makes calls that only set keys shorter.

### Order of steps

1. Copy `values` into a `dict` and `delete` into a set of keys, so the caller cannot change them while the method awaits.
2. Raise `ValueError` when a key is in both. Nothing is locked or written yet.
3. Freeze every value and compute its hash, before taking the lock. An `UnsupportedTypeError` then leaves the dimension untouched, as `set()` does today.
4. Take the lock of the dimension, then read the root with `_root()`, which raises `DimensionNotFoundError` for an unknown dimension.
5. Check that every key in `delete` is in the root, and raise `KeyNotFoundError` for the first one that is not, before building any root.
6. Build the new root by applying `keymap.set()` for each value hash and `keymap.delete()` for each deleted key to a local variable. No `await` runs during this step.
7. Call `_commit(dimension, new_root, frozen_values, previous=root)`. It writes all new nodes in one request, then the root hash once, then switches the dimension in memory. When `new_root is root`, it writes nothing.
8. After the commit, add each frozen value to the value cache with `_cache()`, as `set()` does, and return the dict of frozen values.

Readers cannot view a partial change because the only change to `self._dims[dimension]` is the single assignment at the end of `_commit()`. The intermediate roots of step 6 never reach `self._dims`.

### No change to `_commit()` or `_send()`

`_send()` walks from the final root only, so key map nodes created for intermediate roots in step 6 and not reachable from the final root are not written. Python frees them because nothing refers to them, and `_pending` is a `WeakValueDictionary`, so they leave it too.

### Specification updates

- `SPEC.md` goal 1 gets a bullet for changing several keys of one dimension in one step.
- `SPEC.md` goal 9 gets a bullet that describes the order of writes for such a change: all new nodes in one request, then the root hash once.
- `README.md` lists `set_many()` with the other value operations.

### Tests

- Unit tests in `tests/unit/test_store.py` without a backend cover the scenarios of `specs/dimensions/spec.md`, including a reader task that reads three keys after each `await asyncio.sleep(0)` while a writer calls `set_many()`.
- Unit tests in `tests/unit/test_sharing.py` with `MemoryBackend` cover request counts (`set_requests == 1` and one `set_root` entry in `log` for 200 keys), the order of `set_many` before `set_root` in `log`, no root write for a change that changes nothing, and a backend whose `set_many()` raises.
- A real Redis integration test in `tests/integration/test_redis.py` uses the `redis_backend` and `redis_peer` fixtures with two `Process` contexts. Process A calls `set_many()` with sets and a delete on a dimension. Process B, with its own store and registry, reads the dimension and gets every new value and no deleted key. The test also checks that the Redis root entry equals process A's `root_hash()`. A second case calls `set_many()` with a key to delete that is missing and checks that the Redis root entry has not changed.

## Risks / Trade-offs

- [Applying the keys one at a time in step 6 creates key map nodes for each intermediate root, about 4 per key for large dimensions, that the final root does not use.] → They are freed right away and never written to the backend. A bulk builder that groups keys by key map path can replace step 6 later without changing the specs.
- [When the root hash write fails after the nodes were written, the external cache holds nodes that no root refers to.] → They expire with the normal node expiry time, and the dimension in memory and in the external cache keeps its previous root.
- [A caller that awaits database queries between building parts of the `values` mapping still holds no lock, so another task can change the dimension in between.] → Same as today for `set()`. The calling application prevents concurrent changes to the same dimension, as the `dimension-sharing` spec states.
- [A large batch sends one large `set_many()` request to Redis.] → This is the behavior that the issue requests. Splitting a request is not in scope.
