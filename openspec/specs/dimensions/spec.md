# dimensions Specification

## Purpose

Lets a user store values under keys in many independent dimensions, such as branches, create a dimension from another at almost no cost, and find the keys that differ between two dimensions.

## Requirements

### Requirement: Store and read a value by key in a dimension
The library SHALL let a user store a value under a key in a named dimension and read it back from that dimension. A key SHALL be a string. Reading a value SHALL be `async`.

#### Scenario: Read back a stored value
- **WHEN** a user stores `{"a": 1}` under the key `"device-1"` in the dimension `main` and reads `"device-1"` from `main`
- **THEN** the library returns `{"a": 1}`

#### Scenario: Read a key that the dimension does not have
- **WHEN** a user reads a key that was never stored in the dimension
- **THEN** the library raises a key-not-found error and does not call the loader

### Requirement: Delete a key from a dimension
The library SHALL let a user delete a key from a dimension. Other dimensions SHALL keep the key.

#### Scenario: Delete a key on a branch
- **WHEN** `branch-a` was created from `main` and a user deletes `"device-1"` from `branch-a`
- **THEN** reading `"device-1"` from `main` still returns the value

### Requirement: Update a value in a dimension
The library SHALL let a user apply the set, delete and merge operations of the value-updates capability to the value under a key in a dimension, and store the result under the same key.

#### Scenario: Merge into the value under a key
- **WHEN** a user merges `{"b": 2}` into the value `{"a": 1}` under `"k"` in `main`
- **THEN** reading `"k"` from `main` returns `{"a": 1, "b": 2}`

### Requirement: Any name can be a dimension
Dimension names SHALL be strings with no meaning for the library. A dimension SHALL exist once a user creates it, and creating a dimension with a name that already exists SHALL raise an error.

#### Scenario: Create an empty dimension
- **WHEN** a user creates the dimension `snapshot-2026-10-24` with no source
- **THEN** the dimension exists and has no keys

### Requirement: Create a dimension from another
The library SHALL let a user create a dimension from an existing one. The new dimension SHALL hold the same keys and values, and later changes to either dimension SHALL NOT affect the other. Creating it SHALL NOT copy any key or value.

#### Scenario: Change the copy
- **WHEN** a user creates `branch-a` from `main` and stores a new value under `"device-1"` in `branch-a`
- **THEN** reading `"device-1"` from `main` returns the previous value

#### Scenario: Cost of creating a dimension
- **WHEN** a user creates `branch-a` from a dimension that holds 10,000 keys
- **THEN** no new key map node and no new value node is created

### Requirement: Dimension equality by hash
Each dimension SHALL have a root hash that depends only on its set of keys and the hashes of their values, whatever the order in which keys were added. Two dimensions with the same keys and values SHALL have the same root hash.

#### Scenario: Same keys added in different orders
- **WHEN** a user adds keys `"a"`, `"b"`, `"c"` to one dimension and `"c"`, `"a"`, `"b"` with the same values to another
- **THEN** both dimensions have the same root hash

#### Scenario: Revert a change
- **WHEN** a user changes a value in `branch-a` and then stores the original value again
- **THEN** `branch-a` has the same root hash as before the change

### Requirement: Changing one key creates few key map nodes
Storing or deleting one key SHALL create new key map nodes only on the path from that key to the root of the key map. All other key map nodes SHALL be shared with the previous version and with other dimensions.

#### Scenario: Change one key among 10,000
- **WHEN** a user stores a new value under each of many keys in a dimension of 10,000 keys
- **THEN** the library creates on average at most 4 new key map nodes for each key

### Requirement: List keys of a dimension
The library SHALL let a user list the keys of a dimension, in no defined order, without reading any value.

#### Scenario: List keys
- **WHEN** a dimension holds the keys `"a"` and `"b"`
- **THEN** listing its keys returns `"a"` and `"b"` and no value is fetched or loaded

### Requirement: Keys that differ between two dimensions
The library SHALL return the keys whose values differ between two dimensions, with for each key whether it was added, removed or changed. The library SHALL NOT read the parts of the two key maps that have the same hash.

#### Scenario: One key changed on a branch
- **WHEN** a user changes the value under `"device-1"` in `branch-a`, created from `main` with 10,000 keys
- **THEN** the result holds only `"device-1"` as changed, and the library reads only the key map nodes on the path to `"device-1"`

#### Scenario: Two dimensions with the same root hash
- **WHEN** a user compares two dimensions with the same root hash
- **THEN** the result is empty

### Requirement: Many dimensions in one process
Memory use SHALL grow with the number of nodes that differ between dimensions, not with the number of dimensions.

#### Scenario: One thousand copies of a dimension
- **WHEN** a user creates 1,000 dimensions from one dimension of 10,000 keys
- **THEN** the number of nodes in the registry does not increase

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
