# Proposal

## Why

Infrahub needs to compare branches with the dimensions whose schemas it holds in memory, and check whether a dimension exists before using it. [Issue #13](https://github.com/opsmill/permafrost/issues/13) currently requires callers to maintain a separate name registry or use exceptions from unrelated operations.

## What Changes

- Add synchronous `Store.dimensions() -> list[str]`, returning a detached snapshot of the dimension names held in this store's memory, in no defined order.
- Add asynchronous `Store.has_dimension(dimension: str) -> bool`, checking memory first and then the configured external cache's root entry, without fetching nodes or loading the dimension into memory.
- Document that existence means a local dimension or an external root entry; it does not guarantee that the external cache still holds every node.
- Update the reference specification, usage documentation, and tests during implementation.
- External-cache listing and dimension deletion (issue #12) are out of scope.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dimensions`: Add listing of dimensions held in memory and checking existence in memory or the external cache.

## Impact

- Two new methods in `src/permafrost/store.py`; no backend protocol, encoding, dependency, or existing API change.
- Tests in `tests/unit/test_store.py`, `tests/unit/test_sharing.py`, and `tests/unit/test_typing.py`, using the existing backend and process fixtures.
- Documentation in `SPEC.md` and the dimensions and sharing sections of `README.md`, whose Python examples are executed by `tests/unit/test_readme.py`.
