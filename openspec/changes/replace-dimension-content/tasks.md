# Tasks

## 1. Replace the content of a dimension

- [x] 1.1 In `src/permafrost/store.py`, add `replace_dimension(name, source)` with a docstring (design decisions 1 to 3). Verify with tests in `tests/unit/test_store.py` for the spec scenarios "Switch a dimension to a candidate", "Put the previous content back", "Changes after a replace stay in one dimension", "Replace a dimension that does not exist", "Replace with a source that does not exist", "Cost of replacing a dimension", and replacing a dimension with itself.
- [x] 1.2 Verify with tests in `tests/unit/test_sharing.py` that use `tests/process.py` the spec scenarios "Requests sent to the external cache", "Another process reads the replaced dimension" and "Replace a dimension held only in the external cache", and that replacing a dimension that no process wrote raises `DimensionNotFoundError` and writes no root hash.

## 2. Document the operation

- [x] 2.1 Update `SPEC.md` goal 1 and goal 9, and add the expected-root question to "Open questions". Verify by reading the sections against the files in `specs/` of this change.
- [x] 2.2 In `README.md`, list `replace_dimension()` in "Dimensions", add a "Replacing the content of a dimension" section with an example that builds a candidate, switches to it and puts the previous content back, and state in "Errors" when it raises `DimensionNotFoundError`. Verify with `uv run pytest tests/unit/test_readme.py`.

## 3. Integration checks

- [x] 3.1 Run `uv run pytest`, `uv run ruff check` and `uv run ruff format --check`, and verify that all pass, including the type check. Check by reading the diff that the new method has a docstring and that no `typing.Any` was added.

## Workflow follow-up

- Decide whether the operation checks an expected root (design decision 4).
- Accept a snapshot as the source once issue #10 is done.
- Archive the change after review.
