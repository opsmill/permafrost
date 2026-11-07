# Tasks

## 1. List dimensions held in memory

- [x] 1.1 Add synchronous `Store.dimensions() -> list[str]` in `src/permafrost/store.py` with a docstring defining local scope, detached snapshot behavior, and undefined order. Add tests in `tests/unit/test_store.py` for a new store, empty and copied dimensions, unique names, and snapshot isolation in both directions. Verify with `uv run pytest tests/unit/test_store.py`.
- [x] 1.2 Add sharing tests in `tests/unit/test_sharing.py` using `MemoryBackend` and the existing `Process` helper: an external-only dimension is excluded, and a successful read or reload makes it appear. Use a test backend subclass or spy to prove listing makes no backend call, including root lookups that `MemoryBackend.log` does not record. Verify with `uv run pytest tests/unit/test_sharing.py`.
- [x] 1.3 Document memory-only listing in `SPEC.md` goal 1 and the README dimensions section, including an executable example. Verify the prose against the listing requirement and run `uv run pytest tests/unit/test_readme.py`.

## 2. Check existence in memory and the external cache

- [x] 2.1 Add async `Store.has_dimension(dimension: str) -> bool` with a docstring covering memory precedence, root-entry semantics, and absence of loading. Add tests in `tests/unit/test_store.py` for existing empty and populated dimensions and unknown names without a backend; verify a missing name returns `False` without calling a supplied loader. Run `uv run pytest tests/unit/test_store.py`.
- [x] 2.2 Add tests in `tests/unit/test_sharing.py` for every external-cache existence scenario in the delta spec: local membership short-circuits even after backend loss, an external-only name returns `True`, an unknown name returns `False`, a surviving root with missing key map nodes returns `True`, and backend errors propagate. Use a test backend subclass or spy to verify exactly one root lookup for each nonlocal check, no node fetches, writes, touches, or loader calls, and unchanged local listings. Verify with `uv run pytest tests/unit/test_sharing.py`.
- [x] 2.3 Add `assert_type` checks in `tests/unit/test_typing.py` proving `dimensions()` returns `list[str]` synchronously and awaited `has_dimension()` returns `bool` on a typed store. Verify with `uv run pytest tests/unit/test_typing.py tests/unit/test_type_check.py`.
- [x] 2.4 Update `SPEC.md` goals 1 and 9 and the README dimensions and sharing sections for existence semantics, error propagation, and the distinction between existence and readability. Add an executable two-store example showing an external-only dimension passes the existence check while remaining absent from the local listing until a read or reload. Verify against the delta spec and run `uv run pytest tests/unit/test_readme.py`.

## 3. Integration checks

- [x] 3.1 Run `uv run pytest` (including the type check and the existing Redis integration suite, which requires Docker), `uv run ruff check`, and `uv run ruff format --check`. Verify all pass; inspect the diff for exact annotations, docstrings on all added source methods, and no `typing.Any` or backend protocol changes. Report any environment limitation separately from a passing result.
