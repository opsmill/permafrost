# Permafrost specification

Status: draft, not yet reviewed.

## Summary

Applications that keep many versions of the same data at once (for example, one version per branch) need to hold hundreds or thousands of these versions in memory without memory use growing with each version. Permafrost is a Python library that stores deeply nested data across many dimensions, stores each identical piece of data only once, prevents accidental changes to stored data, and compares and diffs stored data. Every structure, including the list of keys in a dimension, is a tree of read-only nodes stored once under its hash, so creating a dimension copies one hash. Permafrost keeps data in an in-memory cache and can fetch data that is not in memory from an optional external cache, starting with Redis. Both are caches: Permafrost does not persist data, and when neither cache holds a value, Permafrost calls a loader function that the calling application provides to reload it from the application's own storage. Python frees data that nothing refers to any more, and a cleanup operation that the application runs evicts values that nobody has read for a configurable time.

Not decided yet:

- Whether the merge rules carried over from the earlier project notes are the right ones.
- The expected depth of values, number of dimensions and number of keys, which set the memory and performance targets.

The full list is in [Open questions](#open-questions).

## Terms

- Value: a nested Python data structure that a user stores, such as a dictionary of lists of Pydantic models.
- Node: one read-only element inside a tree that has children or fields: a dictionary, a list, a tuple, a set, a model or a part of a key map. Scalars such as strings and integers are not nodes: they are stored inside their parent node.
- Hash: an identifier computed from the content of a node and of all the nodes it contains. Two nodes with the same content have the same hash.
- Registry: the shared store that holds each node once, under its hash.
- Key map: the tree of nodes that maps each key of a dimension to the root node of its value.
- Dimension: one independent view of the stored data. A branch is the main example. A dimension is a name that points to the root hash of a key map.
- In-memory cache: the nodes held in the memory of the current Python process.
- External cache: an optional cache service outside the process, such as Redis, that holds nodes the in-memory cache does not hold. It can lose any data at any time.
- Edge: the point where data enters Permafrost from the calling application.
- Eviction: removing a value from the in-memory cache because nobody has read it recently.
- Cache miss: a read of a value that neither the in-memory cache nor the external cache holds.
- Garbage collection: removing a node that no dimension refers to any more, from every cache.

## Architecture

```
  Calling application
        │   (data is validated at the edge, at most once)
        ▼
┌───────────────────────────────────────────────────┐
│ Dimensions: name → root hash of a key map         │
│   (in memory, and in the external cache)          │
│   main     → a1f3…                                │
│   branch-a → a1f3…   (same data as main)          │
│   branch-b → 7c09…                                │
└─────────────────────────┬─────────────────────────┘
                          ▼
┌───────────────────────────────────────────────────┐
│ Registry: hash → node, each node stored once      │
│                                                   │
│   Key map trees   (key → hash of value root)      │
│        │  refers to values by hash                │
│        ▼                                          │
│   Value trees     (direct Python references       │
│                    between parent and children)   │
│                                                   │
│   held in the in-memory cache of this process     │
└─────────────────────────┬─────────────────────────┘
                          │ hash not in memory → fetch
                          ▼
┌───────────────────────────────────────────────────┐
│ External cache (optional, Redis first)            │
│   hash → serialized node                          │
│   dimension name → root hash of its key map       │
└───────────────────────────────────────────────────┘

  Cleanup, separate from reads and writes:
    eviction           → drop values nobody has read recently
    garbage collection → delete nodes no dimension refers to
```

### One mechanism for all stored data

Values and key maps are both trees of read-only nodes, stored once in the registry under their hash. A dimension is only a name and the root hash of its key map. Because of this:

- Creating a dimension from an existing one copies one hash. It copies no keys and no data.
- Two dimensions that differ by one key share almost all of their key map. Only the key map nodes on the path to the changed key differ.
- Two dimensions with the same data have the same root hash, so comparing two dimensions compares two hashes.
- Listing the keys that differ between two dimensions walks only the parts of the two key maps whose hashes differ.

### Shape of the key map

- The key map has a canonical shape: the same set of keys and value hashes always produces the same tree and the same root hash, whatever the order in which the keys were added. Without this, two dimensions with the same data could have different root hashes.
- Changing one key creates new nodes only on the path from that key to the root of the key map. The number of new nodes grows with the logarithm of the number of keys.
- The key map is a hash array mapped trie. Each key is placed in the tree by the hash of the key, and each node has up to 32 children. This gives a canonical shape, and changing one key creates about log32(n) new nodes: 3 nodes for 10,000 keys, 4 for 1 million keys.
- Keys are not stored in sorted order. Listing the keys of a dimension in order requires a sort.

### Hashing and serialization

- Permafrost serializes each node with msgpack, in a deterministic form: dictionary entries and set items are sorted, and each child is written as its hash.
- The hash of a node is the BLAKE2b hash of these bytes, with a 16-byte digest, from the Python standard library.
- The same bytes are what Permafrost stores in the external cache, so a node is serialized once for both purposes.

### References between nodes

- Inside a value, each node refers to its children by direct Python reference. Reading down a value does not need a lookup in the registry.
- A key map refers to the root of each value by hash, not by direct reference. Permafrost looks up the value in the registry when a user reads the key, and fetches it from the external cache when it is not in memory.
- Because of the direct references, Python keeps every node of a value in memory as long as the root of the value is in memory. The smallest unit that cleanup can evict is therefore a whole value. The hash boundary between the key map and the values is what lets cleanup evict one value without unloading the whole dimension.

### What keeps nodes in memory

- The registry holds weak references to nodes. It finds an existing node by hash, so identical nodes are stored once, but it does not keep a node in memory on its own.
- Two places hold strong references, which keep nodes in memory:
  - Each dimension holds its key map root, which holds the rest of its key map.
  - The value cache holds the root of each value that was read or written recently, with the time of the last read. A value root holds all the nodes of the value.
- Because nodes are read-only and never refer to their parents, the trees have no reference cycles. Python frees a node as soon as the last strong reference to it is gone, and the registry entry disappears with it. Removing nodes that nothing refers to does not need a separate garbage collection step.
- Eviction removes a value root from the value cache. Python then frees every node of the value that no other value or key map refers to.

### Requirements that weak references put on nodes

- Python cannot create a weak reference to its built-in `dict`, `list`, `tuple`, `set`, `str` or `int` objects, or to classes with `__slots__` that do not include `__weakref__`. Measured on 2026-10-19.
- Permafrost therefore stores dictionaries, lists, tuples and sets as its own read-only classes, which support weak references, and returns those classes to users.
- Scalars are stored inside their parent node and are not registry entries.
- Pydantic dataclasses do not accept the `weakref_slot` option of standard dataclasses. Users get weak reference support by inheriting from a Permafrost base class that declares a `__weakref__` slot. A Pydantic dataclass that inherits from this base class still validates normally when the application calls it.
- Pydantic models (`BaseModel`) support weak references without a base class.
- Weak references cost memory. For a small model, a node and its registry entry use about 280 bytes with weak references and about 175 bytes with strong references (see [Measured memory and creation time](#measured-memory-and-creation-time)). The registry can switch to strong references later if this cost is too high. Garbage collection would then be a step of the cleanup operation.

## Goals

### 1. Store data across many dimensions

- A user stores a value under a key in a given dimension, and reads it back from that dimension.
- A user reads one dimension at a time. Reading a dimension does not require loading the other dimensions.
- A user creates a new dimension from an existing one. The new dimension is an independent copy from the start: later changes to either dimension do not affect the other.
- A user replaces the keys and values of an existing dimension with those of another dimension, in one step. Like creating a dimension from another, this copies the root hash of the key map. Readers of the dimension get either the previous content or the new content, never part of each. Later changes to either dimension do not affect the other. This lets an application build and validate a change in a candidate dimension, then switch the original dimension to it.
- The library supports hundreds to thousands of dimensions in the same process.
- Branches are the main use case, but the library does not depend on the meaning of a dimension. Any name or identifier can be used as a dimension.

### 2. Store each identical piece of data only once

- Permafrost stores each node once, under its hash, in the registry.
- When two dimensions, two keys or two parts of the same value contain identical nodes, the registry holds one copy of each.
- Creating a dimension from an existing one copies the root hash of the key map (see [Architecture](#architecture)). Memory use grows with the number of nodes that differ between dimensions, not with the number of dimensions.
- When a user changes one node deep inside a value, Permafrost creates new nodes only for the changed node and its parents up to the root of the value, then for the path to that key in the key map. All other nodes are shared with the previous version and with other dimensions.

### 3. Support deeply nested data

The library stores values made of any combination of:

- scalars: strings, integers, floats, booleans, `None`, bytes, dates, and members of enum classes that subclass `str` or `int`
- dictionaries, lists, tuples and sets
- Pydantic dataclasses and Pydantic models
- other Python objects that a user registers with a type handler, which tells Permafrost how to hash, freeze and store that type

### 4. Validate at the edge, not inside the library

- Users define their data types as Pydantic dataclasses with `frozen=True` and `slots=True` (`pydantic.dataclasses.dataclass`), inheriting from the Permafrost base class (see [Requirements that weak references put on nodes](#requirements-that-weak-references-put-on-nodes)). This is the preferred way. Type checkers check the code that uses them, Permafrost returns instances of the user's own class, and each instance uses about as little memory as a standard dataclass with `__slots__`.
- At the edge, the application validates data by calling the class or with a Pydantic `TypeAdapter`.
- Inside the library, Permafrost creates instances without calling the class, by creating the object and setting each field directly. This skips validation and any `__post_init__` method.
- Permafrost also supports Pydantic models (`BaseModel`) with `frozen=True`, for types that cannot be written as dataclasses. Each instance uses several times more memory (see [Measured memory and creation time](#measured-memory-and-creation-time)).
- Permafrost validates data at most once, at the edge. In many cases the calling application has already validated the data, so validation at the edge is optional.
- Inside the library, Permafrost creates, copies and updates nodes without validating them again.
- Creating and copying nodes inside the library is as fast as possible, and each node uses as little memory as possible.

A measurement of the cost of Pydantic models compared with other Python classes is in [Measured memory and creation time](#measured-memory-and-creation-time).

### 5. Prevent accidental changes to stored data

- Every value that Permafrost returns is read-only. Assigning an attribute, setting a dictionary key or appending to a list raises an error.
- The only way to change stored data is through the Permafrost update operations. Each update returns a new version of the value and leaves the previous version unchanged.
- Update operations include at least:
  - set or replace the value at a path inside a value
  - delete the value at a path
  - merge a partial value into an existing value (see [Merge](#7-merge))
- When a user passes a mutable object to Permafrost, Permafrost converts it into a read-only form. Later changes to the original object have no effect on the stored data.

### 6. Compare and diff data

- Comparing two values for equality compares their hashes. It does not walk the two trees.
- A user requests the diff between two values, or between the same key in two dimensions. The diff lists each path that was added, removed or changed, with the old and new content.
- Computing a diff skips every subtree whose hash is the same on both sides, so the time it takes grows with the size of the change, not with the size of the value.
- A user requests the list of keys whose values differ between two dimensions. Permafrost computes it by comparing the two key maps, skipping every part whose hash is the same on both sides.

### 7. Merge

These rules come from the existing project notes in `AGENTS.md` and need confirmation.

- By default, merging a partial value into an existing value merges dictionaries and models key by key, at every level.
- For lists, the user selects one of these behaviors:
  - replace the existing list with the new list. This is the default.
  - append: the result is the items of the existing list followed by the items of the new list
  - apply an incremental update to the list (how this works is not decided, and the first version does not provide it)

### 8. Cache data in memory and in an external cache

- Permafrost keeps nodes in an in-memory cache.
- When a node is not in the in-memory cache, Permafrost fetches it from the external cache and adds it to the in-memory cache.
- The external cache stores each node as its own entry, under its hash. A node shared by several values or dimensions is stored once in the external cache too.
- In the external cache, a node refers to its children by their hashes. When Permafrost fetches a value, it rebuilds the direct Python references between the nodes in memory.
- To fetch a value, Permafrost requests all the missing nodes of one level of the tree in a single request (for Redis, one `MGET`), then the next level. The number of requests grows with the depth of the value, not with the number of nodes.
- When Permafrost writes a value, it sends the new nodes in a single request (for Redis, one pipeline).
- The external cache is pluggable. A backend implements a small interface of `async` methods: get several hashes, set several nodes, and extend the expiry time of several hashes. The Redis backend uses the `asyncio` client of the `redis` package.
- Redis is the first backend.
- The external cache is optional. Without it, Permafrost keeps all nodes in memory, which is the setup for development and tests.
- Because nodes are identified by their hash and never change, a node fetched from the external cache never needs to be checked for staleness.

### 9. Share dimensions between processes

- Permafrost keeps the map from dimension name to the root hash of its key map in memory and also writes it to the external cache, so other processes that use the same external cache can read the dimension.
- A process that reads a dimension it does not hold in memory reads the root hash from the external cache, then fetches the key map nodes and value nodes it needs by hash.
- When a process changes a dimension, it writes the new nodes to the external cache first, then writes the new root hash. A process that reads the new root hash can then fetch every node it refers to, unless the external cache has removed some of them since.
- Replacing the content of a dimension with the content of another writes only the new root hash, because the external cache already holds the nodes of the other dimension, unless it has removed some of them since.
- The root hash of a dimension is the only stored data that changes. A process that holds a dimension in memory can hold a root hash that another process has replaced since. Nodes never change, so only the root hash needs to be checked to find out whether a dimension is up to date.
- The calling application decides when a process checks for a newer root hash. Permafrost provides an operation that reads the current root hash of a dimension from the external cache and switches the dimension in memory to it. Permafrost does not check on its own.
- Permafrost writes the root hash of a dimension without checking whether another process changed it since. When two processes change the same dimension at the same time, the last write of the root hash replaces the other, and the other change is lost. The calling application is responsible for preventing concurrent changes to the same dimension.
- Entries for dimension names do not expire by idle time like nodes do, because losing one makes the whole dimension unreachable for other processes. The external cache can still lose them, for example on a restart.
- When a process reads a dimension that is neither in its memory nor in the external cache, Permafrost raises an error. The calling application rebuilds the dimension from its own storage with the regular write operations.

### 10. Leave persistence to the calling application

- The in-memory cache and the external cache are both caches. Permafrost does not write data to durable storage, and losing the content of either cache does not lose data.
- The calling application stores the data in its own durable storage and loads it into Permafrost.
- The external cache can lose any node at any time, for example when Redis restarts or removes entries to stay under its memory limit. Permafrost does not assume that a node it wrote to the external cache is still there.
- The calling application registers a loader, which is an `async` function. When a value is a cache miss, Permafrost awaits the loader with one dimension and one key, and the loader returns the value from the application's storage.
- Because a read can call the loader or the external cache, operations that read a value are `async`. Operations that only use data already in memory, such as comparing two hashes, are regular functions.
- Permafrost stores the value that the loader returns as it stores any other value, then returns it to the reader. Validation at the edge applies to it like any other incoming data.
- If the external cache holds only some of the nodes of a value, Permafrost treats the value as a cache miss and calls the loader.
- If no loader is registered, a cache miss raises an error.
- The loader reloads single values. Rebuilding a whole dimension whose root hash the external cache has lost is done by the calling application (see [Share dimensions between processes](#9-share-dimensions-between-processes)).

### 11. Clean up unused data

- Permafrost provides a cleanup operation. The calling application runs it on its own schedule. Permafrost does not start threads or tasks on its own.
- Permafrost also provides an optional helper that runs the cleanup operation at a fixed interval as an `asyncio` task in the application's event loop, for applications that do not want to schedule it themselves. It runs in the same thread as reads and writes, because Permafrost is not thread-safe (see [Out of scope](#out-of-scope)).
- A separate operating-system process cannot free memory inside the application's process, so cleanup of the in-memory cache runs inside that process.
- Permafrost records the last time each value was read, in the value cache next to the root of the value. Because cleanup evicts whole values (see [References between nodes](#references-between-nodes)), it does not need a read time on every node.
- Cleanup does two different things:
  - Eviction: it removes from the in-memory cache the values that nobody has read for a configurable time, for example 60 minutes. A later read of an evicted value fetches it from the external cache, or calls the loader.
  - Garbage collection: Python frees nodes as soon as nothing refers to them (see [What keeps nodes in memory](#what-keeps-nodes-in-memory)), so cleanup does not need to find them.
- A node shared by an evicted value and a value still in memory stays in memory, because the value still in memory refers to it.
- A value that no dimension refers to any more, for example after its key was deleted, stays in the value cache until eviction removes it.
- Proposed: Permafrost does not delete nodes from the external cache. The external cache removes nodes itself, with an expiry time on each entry and a memory limit. For Redis, Permafrost sets an expiry time when it writes a node and extends it when it reads the node. Because no process deletes nodes, a process cannot remove a node that another process sharing the same external cache still uses.
- Each cache layer has its own idle time. For example, the in-memory cache evicts after 60 minutes, and entries in the external cache expire after 24 hours.
- Without an external cache, eviction is optional. When it is turned on, a read of an evicted value calls the loader.
- The in-memory cache can also have a memory limit. When it reaches the limit, cleanup evicts the values read least recently first.

### 12. Accurate static types

- A type checker checks the code that uses Permafrost. Every public function and class declares exact types, and the library does not declare `Any`.
- Storing, freezing, updating and merging a model returns the model's own class. A scalar keeps its type. Read-only collections take the item types of the input.
- A store is declared with the type of its values, and its loader must return that type.
- Where a type cannot be known, such as the content at a path in a diff, the declared type is `object`, so that the calling code checks the type before it uses the value.
- The types are exact when values are declared with models, scalars, or read-only abstract types such as `Mapping` and `Sequence`. A value declared as `list`, `dict` or `set` inside another value holds the read-only class at run time.
- The type check runs with the tests, and a type error fails them.

## Out of scope

- Tracking state or history of changes over time. The existing project notes say this could be added later as a separate class built on Permafrost.
- Persisting data. The calling application stores data durably and loads it into Permafrost.
- Backends other than Redis for the first version.
- Preventing or merging changes that two processes make to the same dimension at the same time.
- Deciding when a process checks for changes that other processes made to a dimension.
- Rebuilding a dimension whose root hash the external cache has lost.
- Thread safety. Permafrost is not safe to use from several threads at the same time. The calling application makes sure that only one thread uses a given set of Permafrost objects.
- A loader that loads several keys or a whole dimension in one call. The first version calls the loader once for each key.

## Open questions

- External cache: for deep values, fetching one level at a time can take many requests. Is the depth of real values small enough for this to be acceptable?
- Can a value be indexed by more than one dimension at once, such as branch and time?
- Merge rules: confirm the rules carried over from `AGENTS.md`, and define how an incremental update to a list works.
- Memory targets: the expected number of dimensions, number of keys and size of a value, and the acceptable memory use for each new dimension.
- Whether replacing the content of a dimension can check first that the dimension still has an expected root hash. Preventing concurrent changes from several processes is out of scope.

## Measured memory and creation time

Measured on 2026-10-16 with Python 3.12.13 and Pydantic 2.12.4, on a model with four fields (`str`, `int`, `bool`, `tuple[str, ...]`). Each row is the time to create one instance and the memory used by one instance.

| Class | Time per instance | Memory per instance |
| --- | --- | --- |
| Pydantic model, `frozen=True`, created with validation | 643 ns | 537 bytes |
| Pydantic model, `frozen=True`, created with `model_construct()` (no validation) | 1,090 ns | 481 bytes |
| Dataclass, `frozen=True` | 300 ns | 105 bytes |
| Dataclass, `frozen=True, slots=True` | 303 ns | 73 bytes |
| Plain class with `__slots__` | 304 ns | 73 bytes |
| Pydantic dataclass, `frozen=True, slots=True`, created with validation | 990 ns | 129 bytes |
| Pydantic dataclass, `frozen=True, slots=True`, created without validation | 238 ns | 73 bytes |
| Same, inheriting from a base class with a `__weakref__` slot, created without validation | not measured | 89 bytes |

The Pydantic dataclass rows were measured on 2026-10-19 with the same versions. "Without validation" means creating the object with `object.__new__` and setting each field with `object.__setattr__`. The instance is equal to a validated one, has no `__dict__`, and raises `FrozenInstanceError` on assignment. The validated instance uses more memory because validation creates new copies of the field values (strings, integers, tuples) instead of reusing the ones passed in. The instance itself is the same size.

What this shows:

- A Pydantic instance uses about 6 to 7 times the memory of a frozen dataclass with `__slots__`. Pydantic stores each instance's fields in a `__dict__` and keeps extra bookkeeping attributes on each instance.
- Creating a Pydantic instance takes about twice as long as creating a frozen dataclass.
- `model_construct()` skips validation but is slower than validated creation in this test, so it is not a fast way to create Pydantic instances inside the library.
- A Pydantic dataclass with `__slots__`, created without validation, is as small and as fast to create as a standard dataclass.

For comparison, a plain Python dictionary that maps 10,000 keys to hashes uses about 200 KB, not counting the key and hash strings, which are shared. Copying such a dictionary for each of 1,000 dimensions would use about 200 MB. This is why a dimension points to the root hash of a key map instead of holding its own dictionary.

Memory for one registry entry with a 16-byte hash as the key, measured on 2026-10-19 with the same versions:

| Registry | Memory per entry |
| --- | --- |
| `weakref.WeakValueDictionary` | 189 bytes |
| Plain `dict` | 101 bytes |
