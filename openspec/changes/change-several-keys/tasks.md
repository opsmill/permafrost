# Tasks

## 1. Change several keys in one commit, in one process

- [x] 1.1 Add `Store.set_many(dimension, values, *, delete=())` to `src/permafrost/store.py`, following the order of steps in `design.md`, with a docstring that states the commit-once and all-or-nothing behavior. Verify with `uv run ruff check`, `uv run ruff format --check` and `uv run ty check`.
- [x] 1.2 Add unit tests without a backend to `tests/unit/test_store.py` for every scenario of `specs/dimensions/spec.md`: set and delete several keys, reader during a change (a reader task that reads `"a"`, `"b"`, `"c"` after each `await asyncio.sleep(0)` and only views `(0, 0, 0)` or `(1, 1, 1)`), same root hash as single changes, read-only values, no change, unfreezable value, missing key to delete, same key in both arguments, unknown dimension. Verify with `uv run pytest tests/unit/test_store.py`.
- [x] 1.3 Add a bullet to `SPEC.md` goal 1 for changing several keys of one dimension in one step, and list `set_many()` with the value operations in `README.md`. Verify with `uv run pytest tests/unit/test_readme.py tests/unit/test_examples.py`.

## 2. Writes to the external cache

- [x] 2.1 Add unit tests with `MemoryBackend` to `tests/unit/test_sharing.py` for `specs/dimension-sharing/spec.md`: 200 new values give `set_requests == 1` and one `set_root` in `log`, after the `set_many` entry; a change that changes nothing adds no `set_root`; a backend whose `set_many()` raises leaves `root_hash()` and the backend root unchanged; a second `Process` reads the new values and not the deleted key. Verify with `uv run pytest tests/unit/test_sharing.py`.
- [x] 2.2 Add a real Redis integration test to `tests/integration/test_redis.py` that uses the `redis_backend` and `redis_peer` fixtures with two `Process` contexts: process A calls `set_many()` with sets and a delete; process B reads every new value and gets `KeyNotFoundError` for the deleted key; the Redis root entry equals process A's `root_hash()`. Add a second test where `set_many()` deletes a missing key and the Redis root entry stays unchanged. Verify with `uv run pytest tests/integration` with Docker running, and report explicitly if Docker is not available.
- [x] 2.3 Add a bullet to `SPEC.md` goal 9 for the order of writes of a change to several keys: all new nodes in one request, then the root hash once. Verify by reading the updated goal against `specs/dimension-sharing/spec.md`.

## 3. Full check

- [x] 3.1 Run `uv run pytest`, `uv run ruff check` and `uv run ruff format --check`, and confirm that every new function and method has a docstring. Verify that all pass.
