# Spec Delta

## MODIFIED Requirements

### Requirement: Reads that can reach the loader are async
Operations that read a value, a dimension or the keys of a dimension SHALL be `async`. Operations that use only data already in memory, such as comparing the hashes of two values or the root hashes of two dimensions held in memory, SHALL be regular functions. Reads from a snapshot of a dimension SHALL be regular functions, because taking the snapshot reads every value before it returns.

#### Scenario: Compare two dimensions held in memory
- **WHEN** a user compares the root hashes of two dimensions held in memory
- **THEN** the comparison runs without `await`

#### Scenario: Read a snapshot
- **WHEN** a user has taken a snapshot of a dimension with one `await`, and reads values and keys from it
- **THEN** each read runs without `await` and does not call the loader
