# Spec Delta

## ADDED Requirements

### Requirement: Write a change to several keys once
When an external cache is configured, `set_many()` SHALL write the new nodes of all its changes in one request, then write the new root hash of the dimension once. When the backend fails to write the nodes or the root hash, the dimension in memory SHALL keep its previous root, and the external cache SHALL NOT receive a root hash that refers to nodes it was not sent.

#### Scenario: Requests for 200 keys
- **WHEN** a user stores 200 new values in one dimension with one call to `set_many()`
- **THEN** the backend receives one request that writes nodes and one write of the root hash, in that order

#### Scenario: Another process reads the change
- **WHEN** process A changes several keys of `main` with `set_many()`, and process B, which shares the external cache, reads `main` for the first time or reloads it
- **THEN** process B reads every new value that process A stored and does not read any key that process A deleted

#### Scenario: Backend fails while writing nodes
- **WHEN** the backend raises an error while `set_many()` writes the new nodes
- **THEN** the library raises the error, no root hash is written, and the dimension in memory keeps its previous root

#### Scenario: Change that changes nothing
- **WHEN** a user calls `set_many()` with values equal to the stored ones
- **THEN** the library does not write the root hash
