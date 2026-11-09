# Spec Delta

## ADDED Requirements

### Requirement: Change several keys of a dimension in one commit
`Store.set_many(dimension, values, *, delete=())` SHALL store every value of the mapping `values` under its key and delete every key in `delete`, then switch the dimension to one new root. It SHALL return a new `dict` that maps each key of `values` to the read-only form of its value. Readers SHALL view either the previous root or the new root, never only some of the changes.

#### Scenario: Set and delete several keys
- **WHEN** `main` holds `"a": 0`, `"b": 0` and `"c": 0`, and a user calls `set_many("main", {"a": 1, "b": 1}, delete=["c"])`
- **THEN** reading `main` returns `"a": 1` and `"b": 1`, the keys of `main` are `"a"` and `"b"`, and the result is `{"a": 1, "b": 1}`

#### Scenario: Reader during a change
- **WHEN** a reader task in the same event loop reads `"a"`, `"b"` and `"c"` of `main` while a writer task applies `set_many()` to these three keys
- **THEN** the reader views either the three previous values or the three new values

#### Scenario: Same root hash as single changes
- **WHEN** a user applies a set of changes to one dimension with `set_many()`, and the same changes to a copy of that dimension with `set()` and `delete()`
- **THEN** both dimensions have the same root hash

#### Scenario: Values are read-only
- **WHEN** a user calls `set_many()` with a value that holds a `list`
- **THEN** the value in the result and the value read back hold the read-only form of that list

#### Scenario: No change
- **WHEN** a user calls `set_many()` with an empty mapping and no key to delete, or with values equal to the stored ones
- **THEN** the root hash of the dimension stays the same

### Requirement: A failed change to several keys applies no change
When any change of `set_many()` fails, the dimension SHALL keep its previous root and the operation SHALL raise the error. A key in `delete` that the dimension does not hold SHALL raise a key-not-found error. A key that is in both `values` and `delete` SHALL raise a `ValueError`.

#### Scenario: A value cannot be frozen
- **WHEN** a user calls `set_many()` on `main` with one value that the library cannot convert to a read-only form
- **THEN** the library raises an unsupported-type error, and every key of `main` keeps its previous value

#### Scenario: Delete a key that the dimension does not hold
- **WHEN** a user calls `set_many("main", {"a": 1}, delete=["missing"])` and `main` does not hold `"missing"`
- **THEN** the library raises a key-not-found error and `"a"` keeps its previous value

#### Scenario: Set and delete the same key
- **WHEN** a user calls `set_many("main", {"a": 1}, delete=["a"])`
- **THEN** the library raises a `ValueError` and `main` keeps its previous root hash

#### Scenario: Dimension does not exist
- **WHEN** a user calls `set_many()` on a dimension that neither memory nor the external cache holds
- **THEN** the library raises a dimension-not-found error
