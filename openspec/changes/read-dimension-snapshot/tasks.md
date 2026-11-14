# Tasks

## 1. Snapshot of a dimension in one process

- [x] 1.1 Add the `Snapshot[V]` class to `src/permafrost/store.py` with `__slots__`, the `dimension` and `root_hash` properties, `get()`, `keys()` and `__contains__`, as described in `design.md` "API" and "What a snapshot holds", and export it from `src/permafrost/__init__.py`. Give every method a docstring. Verify with `uv run ruff check`, `uv run ruff format --check` and `uv run ty check`.
- [x] 1.2 Move the loader part of `Store._value()` into `Store._load(dimension, key, vh, *, locked)` without changing the behavior of `_value()`. Verify that `uv run pytest tests/unit` passes unchanged.
- [x] 1.3 Add `Store.snapshot(dimension)` following steps 1, 2, 4, 5, 6 and 7 of `design.md` "Order of steps", without the external cache fetch yet. Verify with `uv run ty check`.
- [x] 1.4 Add unit tests without a backend to `tests/unit/test_store.py` for the scenarios of the requirements "Take a snapshot of a dimension", "Read a snapshot synchronously" and "A snapshot does not follow later changes" in `specs/dimension-snapshots/spec.md`, and for a cache miss without a loader, a loader error and a loaded value with another hash. Verify with `uv run pytest tests/unit/test_store.py`.
- [x] 1.5 Add tests to `tests/unit/test_cleanup.py` for the scenarios of "A snapshot keeps its values in memory": a memory limit below the size of the values, and eviction by idle time with a fake clock, where `Store.get()` raises `CacheMissError` and the snapshot still returns every value. Verify with `uv run pytest tests/unit/test_cleanup.py`.
- [x] 1.6 Add `assert_type(snapshot.get(...), <model>)` for a store declared with a model to `tests/unit/test_typing.py`. Verify with `uv run pytest tests/unit/test_typing.py tests/unit/test_type_check.py`.
- [x] 1.7 Update `SPEC.md`: goal 1 (snapshots), goal 10 (synchronous reads from a snapshot), "What keeps nodes in memory" (snapshots hold strong references) and goal 11 (cleanup does not free the values of a held snapshot). Add a "Snapshots" section under "Dimensions" in `README.md` with a runnable example and the advice to drop a snapshot at the end of the request or task. Verify with `uv run pytest tests/unit/test_readme.py tests/unit/test_examples.py`.

## 2. Snapshot with an external cache

- [x] 2.1 Add `Store._fetch_many(hashes)` as described in `design.md` "Fetching several values together", and make `_fetch(h)` call it. Verify that `uv run pytest tests/unit/test_external_cache.py tests/unit/test_sharing.py` passes unchanged, including the existing request counts.
- [x] 2.2 Use `_fetch_many()` in step 3 of `Store.snapshot()`, and call `_load()` only for the keys it does not return. Verify with `uv run ty check`.
- [x] 2.3 Add unit tests with `MemoryBackend` to `tests/unit/test_external_cache.py` and `tests/unit/test_sharing.py`: 200 nested values give no more `get_requests` than the number of tree levels; one value with a dropped node is loaded with the loader while the other values come from the backend; a writer task that changes `main` while the snapshot awaits `get_many()` does not change the snapshot; a second `Process` takes a snapshot, then reloads after a change in the first process, and its old snapshot keeps the old values. Verify with `uv run pytest tests/unit/test_external_cache.py tests/unit/test_sharing.py`.
- [x] 2.4 Add real Redis integration tests to `tests/integration/test_redis.py` that use the `redis_backend` and `redis_peer` fixtures with two `Process` contexts: process B takes a snapshot of nested values that process A stored, reads every value without `await`, and its root hash equals the Redis root entry; process A changes `main`, process B reloads, and the old snapshot keeps the old values while `Store.get()` returns the new ones; after deleting some value nodes from Redis, a snapshot with a loader returns the loaded values and a snapshot without a loader raises `CacheMissError`. Verify with `uv run pytest tests/integration` with Docker running, and report explicitly if Docker is not available.

## 3. Full check

- [x] 3.1 Run `uv run pytest`, `uv run ruff check` and `uv run ruff format --check`, and confirm that every new function, method and class in `src/permafrost/`, including `_fetch_many()` and `_load()`, has a docstring. Verify that all pass.
- [x] 3.2 Run `openspec validate read-dimension-snapshot --strict`. Verify that it reports no error.
