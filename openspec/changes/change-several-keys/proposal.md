# Proposal

## Why

Infrahub plans to store its schema registry in Permafrost, with one dimension for each branch and one key for each schema kind. A schema update or a full schema processing run changes many kinds of one branch at once, and readers must view either the whole old schema of a branch or the whole new schema, never a schema that is partly updated ([issue #11](https://github.com/opsmill/permafrost/issues/11)).

Today `Store.set()`, `Store.delete()` and `Store.update()` each change one key and commit one new root. A sequence of changes therefore shows each intermediate state to readers, sends one node request and one root hash write per change to the external cache, and keeps the changes made before a change that fails. Infrahub needs this operation before it can use Permafrost.

## What Changes

- Add `Store.set_many(dimension, values, *, delete=())`. It stores every value in `values` (a mapping of keys to values) and deletes every key in `delete`, in one step, and returns a new `dict` that maps each key in `values` to the read-only form of its value.
- The operation builds one new key map root from all the changes and commits it once. Readers of the dimension view either the previous root or the new root, never a root with only some of the changes.
- With an external cache, the operation writes all new nodes of all changes in one request, then writes the root hash once.
- If any change fails, the dimension keeps its previous root, in memory and in the external cache. Failures include:
  - a value that `freeze()` cannot convert, which raises `UnsupportedTypeError`
  - a key in `delete` that the dimension does not hold, which raises `KeyNotFoundError`, as `delete()` does
  - a key that is in both `values` and `delete`, which raises `ValueError`
  - an error from the backend while writing the nodes or the root hash
- When the changes do not change the root, for example when every value equals the stored one, the operation writes no root hash.
- `SPEC.md` goals 1 and 9 describe the operation and the order of writes for a change to several keys.

Assumptions, because the issue leaves the API shape open and no answer was given when planning:

- The API is one call that takes a mapping and a list of keys to delete, not a context manager that collects changes. A context manager can be added later on top of this call.
- Deleting a key that the dimension does not hold fails the whole operation, as `delete()` does, instead of being ignored.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dimensions`: adds the requirement to change several keys of a dimension in one commit, with all changes or none applied.
- `dimension-sharing`: adds the requirement that a change to several keys writes all new nodes in one request and the root hash once, after the nodes.

## Impact

- `src/permafrost/store.py`: new public method `Store.set_many()`. `set()`, `delete()`, `update()` and `_commit()` keep their behavior.
- `SPEC.md`: goals 1 and 9.
- `README.md`: if it lists the write operations of a store, add `set_many()`.
- Tests: unit tests with `MemoryBackend` and without a backend, and a real Redis integration test in `tests/integration/test_redis.py`.
- No new dependency. No change to the backend interface or to the Redis backend.
