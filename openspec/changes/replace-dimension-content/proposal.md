# Proposal

## Why

Infrahub plans to store its in-memory schema registry in Permafrost, with one dimension for each branch ([issue #14](https://github.com/opsmill/permafrost/issues/14)). It changes the schema of a branch in three steps:

- It validates the change on a copy of the branch schema.
- It replaces the branch schema with the copy.
- If a later step fails, it puts the previous schema back.

Permafrost can copy a dimension into a new name with `create_dimension(name, source)`, but it cannot point an existing dimension at the content of another one: `create_dimension()` raises `DimensionExistsError`. With the public operations, an application has to call `set()` or `delete()` for each key that differs, and each call commits its own root, so readers can view the dimension partly updated ([issue #11](https://github.com/opsmill/permafrost/issues/11)). Infrahub cannot use Permafrost until this is fixed.

## What Changes

- New `Store.replace_dimension(name, source)`. It sets the root of the existing dimension `name` to the current root of the dimension `source`, in one commit.
- It copies one hash and creates no node, like `create_dimension(name, source)`.
- With an external cache, it writes the new root hash of `name`. It writes no node, except the nodes of `source` that this process created and has not written yet.
- It raises `DimensionNotFoundError` when `name` is neither in memory nor in the external cache, or when `source` does not exist. It does not create `name`.
- It takes the lock of `name`, so a change to `name` by another task of the same process runs before or after it, not in the middle.
- It does not check that `name` still has the root it had when the application created the candidate. The issue lists this as not decided, and `SPEC.md` lists preventing concurrent changes from several processes as out of scope.
- Not part of this change: a snapshot as the source ([issue #10](https://github.com/opsmill/permafrost/issues/10)), which does not exist yet, and deleting the candidate dimension after the switch ([issue #12](https://github.com/opsmill/permafrost/issues/12)).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dimensions`: a new requirement to replace the content of a dimension with the content of another.
- `dimension-sharing`: a new requirement that replacing the content of a dimension writes its new root hash to the external cache, and no node that the external cache already holds.

## Impact

- `src/permafrost/store.py`: the new `replace_dimension()` method.
- `SPEC.md`: goal 1 ("Store data across many dimensions"), goal 9 ("Share dimensions between processes") and "Open questions".
- `README.md`: the "Dimensions" section, a new "Replacing the content of a dimension" section, and "Errors".
- Tests in `tests/unit/test_store.py` and `tests/unit/test_sharing.py`.
- No new dependency, no new error class and no change to the `Backend` interface.
