# Spec Delta

## Purpose

Lets an application obtain one version of a dimension with one `await`, then read its keys and values from synchronous code, with every read returning data from that same version.

## ADDED Requirements

### Requirement: Take a snapshot of a dimension
The library SHALL provide an `async` operation that returns a read-only snapshot of one dimension. The snapshot SHALL hold the keys and values of the dimension as they were when the operation read the key map root of the dimension.

#### Scenario: Snapshot of a dimension in memory
- **WHEN** `main` holds `"a": 1` and `"b": 2`, and a user takes a snapshot of `main`
- **THEN** the snapshot returns `1` for `"a"` and `2` for `"b"`, and its keys are `"a"` and `"b"`

#### Scenario: Snapshot of an empty dimension
- **WHEN** a user takes a snapshot of a dimension that holds no key
- **THEN** the snapshot has no key and its root hash equals the root hash of the dimension

#### Scenario: Dimension does not exist
- **WHEN** a user takes a snapshot of a dimension that neither memory nor the external cache holds
- **THEN** the library raises a dimension-not-found error

### Requirement: Read a snapshot synchronously
A snapshot SHALL provide regular, not `async`, operations to get the value of a key, list its keys in no defined order, check whether it holds a key, and return its root hash. These operations SHALL NOT call the external cache or the loader.

#### Scenario: Get a value
- **WHEN** a snapshot holds `"a"`, and a user gets `"a"` from it
- **THEN** the snapshot returns the value without an `await`

#### Scenario: Get a key that the snapshot does not hold
- **WHEN** a user gets `"missing"` from a snapshot that does not hold it
- **THEN** the library raises a key-not-found error that names the dimension and the key

#### Scenario: Check whether a key is held
- **WHEN** a snapshot holds `"a"` and not `"missing"`
- **THEN** checking `"a"` returns true and checking `"missing"` returns false

#### Scenario: Root hash
- **WHEN** a user takes a snapshot of `main` and no change is made to `main`
- **THEN** the root hash of the snapshot equals the root hash of `main`

#### Scenario: Values are read-only
- **WHEN** a user gets a value that holds a list from a snapshot
- **THEN** the value holds the read-only form of the list, as a value read with the `async` read does

### Requirement: A snapshot does not follow later changes
Changes made to the dimension after the snapshot was taken, including set, delete, update, a change to several keys, a replacement of its content and a reload from the external cache, SHALL NOT change what the snapshot returns.

#### Scenario: Change after the snapshot
- **WHEN** a user takes a snapshot of `main` that holds `"a": 1`, then stores `"a": 2` and `"c": 3` in `main` and deletes `"b"` from `main`
- **THEN** the snapshot still returns `1` for `"a"`, still holds `"b"`, does not hold `"c"`, and keeps its root hash

#### Scenario: Reload after another process changed the dimension
- **WHEN** process B takes a snapshot of `main`, then process A changes `main` and process B reloads `main`
- **THEN** process B's snapshot returns the values from before the change, and process B's `async` reads return the new values

#### Scenario: Change during the snapshot
- **WHEN** a writer task changes `main` while another task is taking a snapshot of `main` and waits on the external cache
- **THEN** the snapshot holds either every value from before the change or every value from after it, never a mix of both

### Requirement: Taking a snapshot reads every value
Before it returns, the operation SHALL read every value of the dimension: from the in-memory cache, then from the external cache, then by calling the loader for each value that no cache holds. If any value cannot be read, the operation SHALL raise the error and SHALL NOT return a snapshot.

#### Scenario: Values evicted from memory
- **WHEN** an external cache holds the values of `main`, cleanup has evicted them from memory, and a user takes a snapshot of `main`
- **THEN** the snapshot returns every value of `main`, read from the external cache

#### Scenario: Values that no cache holds
- **WHEN** neither cache holds the value of `"a"` in `main`, a loader is registered, and a user takes a snapshot of `main`
- **THEN** the library calls the loader for `main` and `"a"`, and the snapshot returns the loaded value

#### Scenario: Loaded value with a different hash
- **WHEN** the loader returns a value for `"a"` whose hash differs from the hash in the key map
- **THEN** the snapshot returns the loaded value, and its root hash is the root hash of the key map with the loaded value under `"a"`

#### Scenario: Cache miss without a loader
- **WHEN** neither cache holds the value of `"a"` in `main`, no loader is registered, and a user takes a snapshot of `main`
- **THEN** the library raises a cache-miss error that names `main` and `"a"`, and returns no snapshot

#### Scenario: Loader error
- **WHEN** the loader raises an error while a user takes a snapshot
- **THEN** the error reaches the user and no snapshot is returned

### Requirement: Fetch the values of a snapshot together
When an external cache is configured, taking a snapshot SHALL fetch the missing values of the dimension together: it SHALL request the missing nodes of one tree level of all these values in one request, then the next level. The number of requests SHALL grow with the depth of the values, not with the number of values.

#### Scenario: Requests for 200 values
- **WHEN** a process that holds none of the 200 values of `main`, each a dictionary that holds a list, takes a snapshot of `main`
- **THEN** the backend receives no more node requests than the number of tree levels of the key map and of the values

### Requirement: A snapshot keeps its values in memory
A snapshot SHALL hold a reference to each of its values. While the application holds a snapshot, cleanup SHALL NOT make its values unreadable from the snapshot, whatever the idle time, eviction setting and memory limit.

#### Scenario: Cleanup with a memory limit
- **WHEN** a store has a memory limit lower than the size of the values of `main`, a user takes a snapshot of `main`, then cleanup runs
- **THEN** the snapshot returns every value of `main`

#### Scenario: Eviction by idle time
- **WHEN** a store without an external cache has eviction on and no loader, a user takes a snapshot of `main`, then cleanup evicts every value of `main` after the idle time
- **THEN** the snapshot returns every value of `main`, and an `async` read of `main` also finds the value in memory without calling the loader

#### Scenario: Snapshot dropped after eviction
- **WHEN** cleanup has evicted every value of `main` as in the previous scenario, and the application drops the snapshot
- **THEN** Python frees the values, and an `async` read of `main` raises a cache-miss error

### Requirement: Snapshot values have the declared type
For a store declared with the value type `V`, the snapshot operation SHALL return a snapshot whose get operation is declared to return `V`.

#### Scenario: Type of a snapshot read
- **WHEN** a store is declared as `Store[Device]` and a user gets a key from a snapshot of it
- **THEN** the type checker reports the result as `Device`
