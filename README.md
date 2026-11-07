# Permafrost

Permafrost is a Python library that stores deeply nested, read-only data across many dimensions, such as branches, and stores each identical piece of data only once. `SPEC.md` describes what the library does and how it is built.

Requires Python 3.12 or later. Install the `redis` extra to use the Redis backend.

## Development and tests

Install the development dependencies with `uv sync --locked`. The full suite requires a
running Docker daemon accessible to the current user. Testcontainers pulls
`redis:7.4.2-alpine`, waits for Redis to respond, and manages one container per pytest
session. It uses a dynamically mapped port and removes the container on teardown.
The integration tests create isolated key prefixes and do not need `REDIS_URL`.
Docker must be able to pull the Redis image and Testcontainers' cleanup helper image.

Run the full suite with `uv run pytest`, only Redis integration tests with
`uv run pytest tests/integration`, or unit tests without Docker with `uv run pytest tests/unit`.
CI runs these directories in separate steps, with integration tests running only
after unit tests pass. CI runs both on Python 3.12, 3.13 and 3.14, and runs lint,
formatting and type checks once, on the Python version in `.python-version`.
Redis integration tests fail if Docker or container startup is unavailable; they
are not silently skipped. The standalone Redis example still uses `REDIS_URL`,
and its example test is skipped when that variable is unset.

Run the type checker with `uv run ty check`, lint with `uv run ruff check`, and
check formatting with `uv run ruff format --check`.

## Read-only values

`freeze()` returns the read-only form of a value. Dictionaries, lists, tuples and sets become `FrozenDict`, `FrozenList`, `FrozenTuple` and `FrozenSet`. They support the same read operations as the built-in types and compare equal to built-in values with the same content. Every operation that would change them raises an error.

```python
from permafrost import FrozenDict, freeze

config = {"hostname": "r1", "vlans": [10, 20], "tags": {"core"}}
frozen = freeze(config)

assert isinstance(frozen, FrozenDict)
assert frozen == config
assert frozen["vlans"][0] == 10

try:
    frozen["hostname"] = "r2"
except TypeError:
    pass

# Later changes to the input have no effect on the frozen value.
config["vlans"].append(30)
assert frozen["vlans"] == [10, 20]
```

Each node has a 16-byte hash computed from its content. Two values with the same content have the same hash, whatever the order of dictionary keys or set items, and Permafrost holds one object for each hash:

```python
from permafrost import equal, freeze, hash_of

a = freeze({"x": 1, "y": [1, 2]})
b = freeze({"y": [1, 2], "x": 1})

assert hash_of(a) == hash_of(b)
assert equal(a, b)
assert a is b
assert hash_of([1, 2]) != hash_of((1, 2))
```

### Supported types

- Scalars: `str`, `int`, `float`, `bool`, `None`, `bytes`, `datetime.date` and `datetime.datetime`, and members of enum classes that subclass `str` or `int`, such as `StrEnum`, `IntEnum` and `IntFlag`. They are stored inside their parent node.
- `dict`, `list`, `tuple`, `set` and `frozenset`.
- Pydantic dataclasses that inherit from `FrozenModel`, and other frozen dataclasses that support weak references.
- Pydantic models (`BaseModel`) with `frozen=True`. Extra fields are not supported.
- Other types, through a type handler.

`1`, `1.0` and `True` are equal in Python but have different hashes in Permafrost, because their encodings differ. For the same reason, an enum member and its plain value have different hashes, and so do members of two enum classes with the same value.

### Models

The preferred way to define a data type is a frozen, slotted Pydantic dataclass that inherits from `FrozenModel`. `FrozenModel` adds the slots that Permafrost needs: one for weak references and one for the hash.

```python
from pydantic.dataclasses import dataclass

from permafrost import FrozenModel, freeze


@dataclass(frozen=True, slots=True)
class Interface(FrozenModel):
    name: str
    vlans: tuple[int, ...] = ()


# Validation happens here, at the edge, when the application calls the class.
eth0 = Interface(name="eth0", vlans=(10, 20))

frozen = freeze(eth0)
assert type(frozen) is Interface
assert frozen == eth0
```

Permafrost creates and updates model instances without calling the class, so Pydantic validation and `__post_init__` do not run inside the library. Validate data before you pass it to Permafrost, if it is not validated already.

Permafrost freezes the fields of a model too. A field declared as `list[str]` holds a `FrozenList` after `freeze()`, and type checkers do not report this difference. Declare such fields as `tuple[str, ...]` when you can.

To rebuild a model from the external cache, Permafrost imports its class by `module:qualname`. Register classes that cannot be imported that way, such as classes defined inside a function, with `register_class(cls, "a-name")` before you store any instance.

### Enum members

A member of an enum class that subclasses `str` or `int` is a scalar: Permafrost stores it inside its parent node, as a dictionary key or value, a collection item or a model field, without any registration. The encoding names the enum class and holds the value of the member, so reading a value back returns the same member, not its plain value. To rebuild a member from the external cache, Permafrost imports its class by `module:qualname`, and `register_class()` accepts enum classes that cannot be imported that way. Members of other enum classes need a type handler.

```python
from enum import Enum

from pydantic import BaseModel, ConfigDict

from permafrost import freeze, hash_of


class State(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"


class Node(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    state: State


frozen = freeze({State.PRESENT: [Node(name="r1", state=State.PRESENT)]})
assert frozen[State.PRESENT][0].state is State.PRESENT
assert hash_of(State.PRESENT) != hash_of("present")
```

### Type handlers

`register_type()` tells Permafrost how to store a type it does not support on its own. `encode` returns a supported value with the content of the object. `decode` receives the frozen form of that value and returns an instance. The instance must be read-only and support weak references: Permafrost does not check that it is read-only.

A type handler registered for an enum class that subclasses `str` or `int` takes priority: Permafrost stores each member through the handler, as a node of its own, and the hashes of values already stored this way do not change. Without the handler, the members are stored as scalars and get new hashes.

```python
from permafrost import freeze, hash_of, register_type


class Prefix:
    __slots__ = ("network", "length", "__weakref__")

    def __init__(self, network: str, length: int) -> None:
        object.__setattr__(self, "network", network)
        object.__setattr__(self, "length", length)

    def __setattr__(self, name, value):
        raise AttributeError("Prefix is read-only")

    def __eq__(self, other):
        return isinstance(other, Prefix) and (self.network, self.length) == (other.network, other.length)

    __hash__ = None


register_type(
    Prefix,
    name="example.Prefix",
    encode=lambda p: (p.network, p.length),
    decode=lambda t: Prefix(t[0], t[1]),
)

frozen = freeze({"loopback": Prefix("10.0.0.1", 32)})
assert frozen["loopback"] == Prefix("10.0.0.1", 32)
assert hash_of(Prefix("10.0.0.0", 8)) != hash_of(Prefix("10.0.0.0", 16))
```

## Changing values

Stored data changes only through `set_in()`, `delete_in()` and `merge()`. Each returns a new version of the value and leaves the previous version unchanged. Only the changed node and its parents are new: every other node is the same object in both versions.

A path is a sequence of steps: dictionary keys, list or tuple indexes, and model field names. A step that does not exist raises `PathError`, except that `set_in()` adds a missing key at the end of the path. An empty path also raises `PathError`: to replace a whole value, call `freeze()` on the new value.

```python
from permafrost import delete_in, freeze, set_in

before = freeze({"r1": {"vlans": [10, 20]}, "r2": {"vlans": [30]}})

after = set_in(before, ("r1", "vlans", 0), 11)
assert after == {"r1": {"vlans": [11, 20]}, "r2": {"vlans": [30]}}
assert before["r1"]["vlans"][0] == 10
assert after["r2"] is before["r2"]

after = set_in(before, ("r3",), {"vlans": []})
assert "r3" in after

after = delete_in(before, ("r1", "vlans", 1))
assert after["r1"]["vlans"] == [10]
```

`merge()` merges a partial value into an existing value. Dictionaries merge key by key and models field by field, at every level, and keys that exist only in the partial value are added. When both sides hold a list, `lists` selects what happens:

- `"replace"`, the default: the list from the partial value replaces the existing list.
- `"append"`: the result holds the items of the existing list followed by the items of the partial list.

Any other value in the partial value replaces the existing one.

```python
from permafrost import merge

device = {"name": "r1", "config": {"ntp": ["10.0.0.1"], "dns": "10.0.0.53"}}

assert merge(device, {"config": {"ntp": ["10.0.0.2"]}}) == {
    "name": "r1",
    "config": {"ntp": ["10.0.0.2"], "dns": "10.0.0.53"},
}
assert merge(device, {"config": {"ntp": ["10.0.0.2"]}}, lists="append")["config"]["ntp"] == [
    "10.0.0.1",
    "10.0.0.2",
]
```

## Comparing values

`equal(a, b)` compares the hashes of two values without reading their children. Comparing two frozen values with `==` also compares their hashes.

`diff(a, b)` returns a list of `DiffEntry` objects. Each entry gives the `path`, the `kind` of change (`"added"`, `"removed"` or `"changed"`), the `old` content and the `new` content. The content at a path can be of any type, so `old` and `new` are declared as `object`: check their type with `isinstance()` before you use them.

- Dictionaries are compared key by key and models field by field.
- Lists and tuples are compared index by index. Items past the end of the shorter side are reported as added or removed.
- Sets report each item present on one side only, with the item as the last step of the path.
- Values of different types or model classes are reported as one `"changed"` entry.

The diff skips every subtree whose hash is the same on both sides, so the time it takes grows with the size of the change, not with the size of the values.

```python
from permafrost import DiffEntry, diff, equal

before = {"r1": {"vlans": [10, 20], "site": "paris"}}
after = {"r1": {"vlans": [10, 20, 30], "site": "lyon"}}

assert not equal(before, after)
assert diff(before, after) == [
    DiffEntry(path=("r1", "vlans", 2), kind="added", old=None, new=30),
    DiffEntry(path=("r1", "site"), kind="changed", old="paris", new="lyon"),
]
```

## Dimensions

A `Store` holds dimensions. A dimension is a name, such as a branch name, that points to the root hash of a key map: a tree that maps each key to the hash of its value. Keys are strings. Operations that can read from the external cache or call the loader are `async`.

```python
import asyncio

from permafrost import KeyChange, Store, merge


async def main():
    store = Store()
    await store.create_dimension("main")
    await store.set("main", "r1", {"site": "paris", "vlans": [10, 20]})
    await store.set("main", "r2", {"site": "lyon", "vlans": [30]})

    # Creating a dimension from another copies one hash: no key and no value is copied.
    await store.create_dimension("branch-a", source="main")
    assert store.root_hash("branch-a") == store.root_hash("main")

    await store.update("branch-a", "r1", lambda v: merge(v, {"vlans": [40]}, lists="append"))
    assert (await store.get("branch-a", "r1"))["vlans"] == [10, 20, 40]
    assert (await store.get("main", "r1"))["vlans"] == [10, 20]

    assert await store.diff_keys("main", "branch-a") == [KeyChange("r1", "changed")]
    changes = await store.diff("main", "branch-a", "r1")
    assert [(c.path, c.kind, c.new) for c in changes] == [(("vlans", 2), "added", 40)]

    await store.delete("branch-a", "r2")
    assert sorted(await store.keys("branch-a")) == ["r1"]


asyncio.run(main())
```

- `create_dimension(name, source=None)` creates an empty dimension, or one with the keys and values of `source`.
- `replace_dimension(name, source)` replaces the keys and values of the existing dimension `name` with those of `source` (see [Replacing the content of a dimension](#replacing-the-content-of-a-dimension)).
- `set`, `get`, `delete` and `update` store, read, delete and change the value under a key. `update(dimension, key, change)` stores `change(value)`, where `change` is usually a call to `set_in()`, `delete_in()` or `merge()`, or an `async` function.
- `keys(dimension)` returns the keys in no defined order, without reading any value.
- `diff_keys(a, b)` returns the keys whose values differ between two dimensions. `diff(a, b, key)` returns the diff of one key. Both skip every part whose hash is the same on both sides.
- `root_hash(dimension)` returns the root hash of a dimension held in memory. Two dimensions with the same keys and values have the same root hash, whatever the order in which the keys were added.

Two tasks in the same process that change the same dimension run one after the other. A `Store` is not thread-safe.

### Replacing the content of a dimension

An application that validates a change before it applies it can build the change in a candidate dimension, then switch the original dimension to it in one step with `replace_dimension(name, source)`. Like `create_dimension(name, source)`, this copies one hash, but `name` must already exist. Readers of `name` get either the previous content or the content of `source`, never part of each. With an external cache, the store writes only the new root hash of `name`, because every node is already there.

To put the previous content back if a later step fails, copy the dimension before the change and replace it with the copy.

```python
import asyncio

from permafrost import Store


async def main():
    store = Store()
    await store.create_dimension("main")
    await store.set("main", "r1", {"site": "paris"})
    before = store.root_hash("main")

    # Keep the current content, then build and validate the change in a candidate.
    await store.create_dimension("main-backup", source="main")
    await store.create_dimension("main-candidate", source="main")
    await store.set("main-candidate", "r1", {"site": "lyon"})
    await store.set("main-candidate", "r2", {"site": "nice"})

    # Switch main to the candidate in one step.
    await store.replace_dimension("main", "main-candidate")
    assert store.root_hash("main") == store.root_hash("main-candidate")
    assert sorted(await store.keys("main")) == ["r1", "r2"]

    # A later step failed: put the previous content back.
    await store.replace_dimension("main", "main-backup")
    assert store.root_hash("main") == before
    assert await store.get("main", "r1") == {"site": "paris"}


asyncio.run(main())
```

`replace_dimension()` does not check that `name` still holds the content it held when the candidate was created. A change made to `name` in the meantime is replaced. Permafrost does not yet provide a way to delete the candidate and backup dimensions.

### Loading values from the application's storage

Permafrost only caches data. When a value is in no cache, the store awaits the loader with the dimension name and the key, stores the value that the loader returns, and returns it. If the loader returns content different from what the key map refers to, the store stores the loaded value under the key, so the root hash of the dimension changes.

```python
import asyncio

from permafrost import Store

DATABASE = {("main", "r1"): {"site": "paris"}}


async def load(dimension: str, key: str):
    return DATABASE[(dimension, key)]


async def main():
    store = Store(loader=load)
    await store.create_dimension("main")
    await store.set("main", "r1", DATABASE[("main", "r1")])
    assert await store.get("main", "r1") == {"site": "paris"}


asyncio.run(main())
```

### Errors

All errors inherit from `PermafrostError`.

- `KeyNotFoundError`: the dimension does not hold the key. The loader is not called.
- `DimensionNotFoundError`: the dimension is neither in memory nor in the external cache, or the external cache lost part of its key map. `replace_dimension()` raises it for a `name` or a `source` that does not exist, and does not create `name`. The application rebuilds the dimension from its own storage with the regular write operations.
- `DimensionExistsError`: `create_dimension()` was called with a name that already exists.
- `CacheMissError`: the value is in no cache and no loader is registered.
- `PathError`: a path does not exist in a value or cannot be changed.
- `ReadOnlyError`: code tried to change a frozen collection.
- `UnsupportedTypeError`: a value contains a type that Permafrost cannot store.

## Cleanup

Python frees a node as soon as nothing refers to it. The value cache of a store holds each value that was read or written recently, so the value stays in memory until cleanup evicts it. The application runs cleanup on its own schedule: Permafrost does not start threads or tasks on its own.

```python
import asyncio

from permafrost import MemoryBackend, Store


async def main():
    store = Store(
        backend=MemoryBackend(),
        idle_time=60 * 60,  # evict values that nobody has read for 60 minutes
        memory_limit=500 * 1024 * 1024,  # and the values read least recently above about 500 MB
    )
    await store.create_dimension("main")
    await store.set("main", "r1", {"site": "paris"})

    evicted = await store.cleanup()
    assert evicted == 0

    # Or let Permafrost run cleanup every 5 minutes as a task in this event loop.
    store.start_cleanup(interval=5 * 60)
    store.stop_cleanup()


asyncio.run(main())
```

- Eviction by idle time is on by default with an external cache, because a later read fetches the value from there. Without an external cache it is off by default, because a later read calls the loader. Turn it on with `eviction=True`.
- The memory limit applies to an estimate of the memory of the cached values. A node shared by two values counts in both, so the estimate is higher than the real memory use.
- A value that no dimension refers to any more, for example after its key was deleted, stays in the value cache until cleanup evicts it.
- With an external cache, cleanup also extends the expiry time of the values read since the previous cleanup and of the key maps of the dimensions in memory, so that entries in use in this process do not expire for other processes.
- The task that `start_cleanup()` starts logs and skips a cleanup that fails, and runs again at the next interval.

## Type checking

Permafrost declares an exact type for every public function and class, so a type checker checks the code that stores and reads values. The project checks its own types with [ty](https://github.com/astral-sh/ty).

- `freeze()`, `set_in()`, `delete_in()` and `merge()` return the class of a model, the type of a scalar, and a read-only collection with the item types of the input. A `dict[str, int]` becomes a `FrozenDict[str, int]`, a `list[int]` a `FrozenList[int]`, a `tuple[int, ...]` a `FrozenTuple[int]` and a `set[int]` a `FrozenSet[int]`.
- `Store[V]` stores and returns values of type `V`, and its loader must return a `V`.
- Where the type cannot be known, Permafrost declares `object`, so that the type checker asks you to check the type with `isinstance()` before you use the value. This is the case for the `old` and `new` content of a `DiffEntry`, and for objects of a registered type.

The types are exact when you declare values with models, scalars, or the read-only abstract types `Mapping`, `Sequence` and `AbstractSet` (`collections.abc.Set`). A value declared as `list`, `dict` or `set` inside another value holds the read-only class at run time: `freeze()` on a `dict[str, list[int]]` returns a `FrozenDict[str, list[int]]` whose values are `FrozenList` objects, which have no `append()`. This is the same rule as for model fields declared as `list`.

A dictionary whose values have different types gives a union, or `object`, for each value. Check the type of a value with `isinstance()` before you read it, or define a model for the data, whose fields have exact types.

Declare the type of a store when you create it, in one of these two ways. A store created as `Store()` without a type is not checked.

```python
import asyncio
from typing import assert_type

from pydantic.dataclasses import dataclass

from permafrost import FrozenModel, Store, merge


@dataclass(frozen=True, slots=True)
class Router(FrozenModel):
    name: str
    site: str


async def load_router(dimension: str, key: str) -> Router:
    return Router(name=key, site="paris")


async def main():
    store = Store[Router](loader=load_router)
    routers: Store[Router] = Store(loader=load_router)
    await store.create_dimension("main")
    await store.set("main", "r1", Router(name="r1", site="paris"))
    router = await store.get("main", "r1")
    assert_type(router, Router)
    moved = merge(router, {"site": "lyon"})
    assert_type(moved, Router)
    assert moved.site == "lyon"


asyncio.run(main())
```

`register_type(cls, *, name, encode, decode)` is typed by the class and by the type of the encoded value: `encode` takes a `cls` instance and returns an `E`, and `decode` takes an `E` and returns a `cls` instance. `decode` receives the frozen form of the encoded value, so declare `E` with `tuple` or `Sequence`, not `list`.

## External cache

A store can fetch nodes that are not in its memory from an external cache. Without one, every value stays in memory until cleanup evicts it, which is the setup for development and tests.

The external cache holds each node as its own entry under its hash, with each child written as its hash, so a node shared by several values or dimensions is stored once there too.

- A write sends all the new nodes in one request, then the new root hash of the dimension.
- A read of a value that is not in memory fetches all the missing nodes of one level of the tree in one request, then the next level. The number of requests grows with the depth of the value.
- If the external cache lost any node of a value, the read is a cache miss and calls the loader.

Permafrost never deletes node entries. The external cache removes them itself with an expiry time and its memory limit.

### Redis

Install the `redis` extra (`pip install "permafrost[redis]"`) and create the backend from a URL or from a `redis.asyncio.Redis` client created with `decode_responses=False`, which is the default:

```python
import asyncio

import fakeredis

from permafrost import Store
from permafrost.backends.redis import RedisBackend


async def main():
    # In production: RedisBackend.from_url("redis://localhost:6379/0", node_ttl=24 * 3600)
    backend = RedisBackend(fakeredis.FakeAsyncRedis(), prefix="permafrost:", node_ttl=24 * 3600)
    store = Store(backend)
    await store.create_dimension("main")
    await store.set("main", "r1", {"site": "paris"})
    assert await store.get("main", "r1") == {"site": "paris"}


asyncio.run(main())
```

- Node entries are named `<prefix>node:<hex hash>` and expire after `node_ttl` seconds, 24 hours by default. Reading a node from Redis extends its expiry time.
- Dimension root hashes are named `<prefix>dim:<name>` and have no expiry time, because losing one makes the whole dimension unreachable for other processes. Redis can still lose them, for example on a restart.

### Writing a backend

A backend is any object with these `async` methods. `MemoryBackend` is an example that keeps entries in the current process.

```python
from collections.abc import Mapping, Sequence


class MyBackend:
    async def get_many(self, hashes: Sequence[bytes]) -> list[bytes | None]: ...
    async def set_many(self, nodes: Mapping[bytes, bytes]) -> None: ...
    async def touch(self, hashes: Sequence[bytes]) -> None: ...
    async def get_root(self, dimension: str) -> bytes | None: ...
    async def set_root(self, dimension: str, root: bytes) -> None: ...
```

`get_many` returns `None` for each hash it does not hold. `set_many` and `touch` set and extend the expiry time of node entries. `set_root` stores a root hash with no expiry time.

### Sharing dimensions between processes

Processes that use the same external cache share their dimensions. Each change to a dimension writes its new nodes, then its new root hash. A process that reads a dimension it does not hold in memory reads the root hash from the external cache and fetches the key map, one level at a time, then the values it reads.

A process that holds a dimension in memory does not see changes that other processes make until the application calls `reload()`. Nodes never change, so the root hash is the only thing to check.

```python
import asyncio

from permafrost import MemoryBackend, Store


async def main():
    shared = MemoryBackend()  # stands for a Redis server shared by two processes
    process_a = Store(shared)
    process_b = Store(shared)

    await process_a.create_dimension("main")
    await process_a.set("main", "r1", {"site": "paris"})
    assert await process_b.get("main", "r1") == {"site": "paris"}

    await process_a.set("main", "r1", {"site": "lyon"})
    assert await process_b.get("main", "r1") == {"site": "paris"}

    await process_b.reload("main")
    assert await process_b.get("main", "r1") == {"site": "lyon"}


asyncio.run(main())
```

Permafrost writes a root hash without checking whether another process changed it since. When two processes change the same dimension at the same time, the root hash written last replaces the other, and the other change is lost. The application is responsible for preventing concurrent changes to the same dimension from several processes.

When a dimension is neither in memory nor in the external cache, or the external cache lost part of its key map, reading it raises `DimensionNotFoundError`. The loader is not called in this case: the application rebuilds the dimension from its own storage with `create_dimension()` and `set()`.
