# Spec Delta

## ADDED Requirements

### Requirement: Write the root hash when replacing the content of a dimension
When an external cache is configured, replacing the content of a dimension with the content of another SHALL write the new root hash of the dimension to the external cache. It SHALL NOT write the nodes of the other dimension that the external cache already received from this process. The dimension to replace SHALL exist in memory or in the external cache.

#### Scenario: Requests sent to the external cache
- **WHEN** a dimension `main` holds 200 keys, a user creates `candidate` from `main`, changes one key in `candidate`, then replaces the content of `main` with the content of `candidate`
- **THEN** the only request that the replace sends to the backend is the write of the root hash of `main`, and that root hash is the root hash of `candidate`

#### Scenario: Another process reads the replaced dimension
- **WHEN** process A replaces the content of `main` with the content of `candidate`, and process B, which held `main` in memory, calls the reload operation for `main`
- **THEN** reads from `main` in process B return the values of `candidate`

#### Scenario: Replace a dimension held only in the external cache
- **WHEN** process A wrote `main`, and process B, which has never read `main`, replaces the content of `main` with the content of one of its own dimensions
- **THEN** the root hash of `main` in the external cache is the root hash of that dimension
