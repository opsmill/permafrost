# Spec Delta

## ADDED Requirements

### Requirement: List dimensions held in memory
`Store.dimensions()` SHALL synchronously return a new `list[str]` containing every dimension name held in that store's memory, in no defined order. It SHALL NOT query the external cache or call the loader.

#### Scenario: No dimensions held in memory
- **WHEN** a user lists dimensions on a new store
- **THEN** the result is an empty list, even if the external cache holds dimensions

#### Scenario: List created dimensions including empty dimensions
- **WHEN** a user creates an empty dimension `main` and a dimension `branch-a` from `main`
- **THEN** listing dimensions returns exactly `main` and `branch-a`, each once

#### Scenario: Returned list is a detached snapshot
- **WHEN** a user keeps the list returned by `dimensions()`, mutates that list, and creates another dimension
- **THEN** mutating the list does not change the store's dimensions, and creating another dimension does not change the saved list

#### Scenario: Reading or reloading adds a dimension to the local listing
- **WHEN** a store successfully loads a dimension held only in the external cache through a read or reload operation
- **THEN** subsequent listings include that dimension

### Requirement: Check whether a dimension exists
`Store.has_dimension(dimension: str)` SHALL asynchronously return `True` if the name is held in that store's memory or has a root entry in its configured external cache, and `False` otherwise. A local match SHALL require no backend call. A nonlocal check SHALL request only the root entry, without fetching nodes, calling the loader, or adding the dimension to memory. Backend errors SHALL propagate.

#### Scenario: Dimension held in memory
- **WHEN** a user checks an empty or populated dimension held in the store's memory
- **THEN** the result is `True` without querying the backend, even if the external cache has lost the dimension

#### Scenario: Unknown name without an external cache
- **WHEN** a user checks a name absent from memory on a store without a backend
- **THEN** the result is `False` without calling the loader or raising a dimension-not-found error

#### Scenario: Dimension held only in the external cache
- **WHEN** a user checks a dimension absent from this store's memory but with a root entry in the external cache
- **THEN** the result is `True` after one root lookup, no nodes are fetched, and the local dimension listing is unchanged

#### Scenario: Unknown name with an external cache
- **WHEN** a user checks a name absent from memory and with no root entry in the external cache
- **THEN** the result is `False` after one root lookup, without calling the loader or changing the local dimension listing

#### Scenario: External root survives loss of key map nodes
- **WHEN** the external cache holds the root entry of a dimension absent from local memory but has lost some of its key map nodes
- **THEN** the existence check returns `True` without attempting to load or validate the key map

#### Scenario: External cache lookup fails
- **WHEN** the dimension is absent from local memory and the backend raises an error while reading its root entry
- **THEN** the existence check propagates that error and does not change the local dimension listing
