# Spec Delta

## ADDED Requirements

### Requirement: Replace the content of a dimension with the content of another
The library SHALL let a user replace the keys and values of an existing dimension with those of another dimension, in one commit. The dimension SHALL then have the root hash of the other dimension, and later changes to either dimension SHALL NOT affect the other. Replacing the content SHALL NOT create any key map node or value node. Readers of the dimension SHALL get either the previous content or the new content, never part of each. The library SHALL raise a dimension-not-found error, and change nothing, when either dimension does not exist.

#### Scenario: Switch a dimension to a candidate
- **WHEN** a user creates `candidate` from `main`, changes one key, deletes one key and adds one key in `candidate`, then replaces the content of `main` with the content of `candidate`
- **THEN** `main` has the root hash of `candidate`, the same keys and the same values

#### Scenario: Put the previous content back
- **WHEN** a user creates `backup` from `main`, changes `main`, then replaces the content of `main` with the content of `backup`
- **THEN** `main` has the root hash it had before the change

#### Scenario: Changes after a replace stay in one dimension
- **WHEN** a user replaces the content of `main` with the content of `candidate`, then stores a new value in `candidate`
- **THEN** reading the key from `main` returns the previous value

#### Scenario: Replace a dimension that does not exist
- **WHEN** a user replaces the content of a dimension name that no process has written
- **THEN** the library raises a dimension-not-found error and does not create the dimension

#### Scenario: Replace with a source that does not exist
- **WHEN** a user replaces the content of `main` with the content of a dimension name that no process has written
- **THEN** the library raises a dimension-not-found error and `main` keeps its root hash

#### Scenario: Cost of replacing a dimension
- **WHEN** a user replaces the content of a dimension that holds 1,000 keys with the content of a candidate that differs by one key
- **THEN** no new key map node and no new value node is created
