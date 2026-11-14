"""Scenarios of specs/external-cache/spec.md that do not name Redis."""

import msgpack
import pytest

from permafrost import Backend, CacheMissError, MemoryBackend, Store, codec, freeze, hash_of, registry
from permafrost.types import walk

from ..models import State
from ..process import Process


def _forget(store, collect):
    """Evict every value from the store and free the nodes that nothing else holds."""
    store._values.clear()
    store._read_at.clear()
    collect()


@pytest.fixture
async def setup():
    backend = MemoryBackend()
    store = Store(backend)
    await store.create_dimension("main")
    return backend, store


async def test_use_without_an_external_cache():
    store = Store()
    await store.create_dimension("main")
    await store.set("main", "k", {"a": 1})
    assert await store.get("main", "k") == {"a": 1}
    assert store.backend is None


async def test_use_a_backend_written_by_the_application(setup):
    backend, store = setup
    assert isinstance(backend, Backend)
    await store.set("main", "k", {"custom-backend": [1]})
    assert hash_of({"custom-backend": [1]}) in backend.nodes


async def test_shared_node_written_once(setup):
    backend, store = setup
    await store.set("main", "a", {"shared-once": {"x": [1, 2]}, "id": 1})
    sets = backend.set_requests
    await store.set("main", "b", {"shared-once": {"x": [1, 2]}, "id": 2})
    assert backend.set_requests == sets + 1
    shared = hash_of({"x": [1, 2]})
    assert shared in backend.nodes
    assert shared not in backend.last_set
    assert hash_of({"shared-once": {"x": [1, 2]}, "id": 2}) in backend.last_set
    # The second write sent only its new root and the new key map nodes.
    last = backend.log[-2:]
    assert last == ["set_many", "set_root"]


async def test_store_a_value_with_many_nodes(setup):
    backend, store = setup
    value = {f"n{i}": {"items": [i, {"deep": i}]} for i in range(17)}
    sets = backend.set_requests
    await store.set("main", "many", value)
    assert backend.set_requests == sets + 1
    node = freeze(value)
    assert all(hash_of(n) in backend.nodes for n in walk(node))


async def test_fetch_a_value_of_depth_3(setup, collect):
    backend, store = setup
    await store.set("main", "k", {"depth": {"two": ["three", 3]}})
    _forget(store, collect)
    assert registry.lookup(hash_of({"depth": {"two": ["three", 3]}}), object) is None
    gets = backend.get_requests
    assert await store.get("main", "k") == {"depth": {"two": ["three", 3]}}
    assert backend.get_requests - gets <= 3


async def test_nodes_already_in_memory_are_not_fetched(setup, collect):
    backend, store = setup
    held = freeze({"kept": ["in", "memory"]})
    await store.set("main", "k", {"outer": held, "other": {"fetched": [1]}})
    _forget(store, collect)
    backend.requested.clear()
    value = await store.get("main", "k")
    assert value["outer"] is held
    assert hash_of(held) not in backend.requested
    assert hash_of({"fetched": [1]}) in backend.requested


async def test_read_a_fetched_node_again(setup, collect):
    backend, store = setup
    await store.set("main", "k", {"fetched-once": [1]})
    _forget(store, collect)
    await store.get("main", "k")
    gets = backend.get_requests
    await store.get("main", "k")
    assert backend.get_requests == gets


async def test_expiry_extended_on_read(setup, collect):
    backend, store = setup
    await store.set("main", "k", {"touch-me": [1, 2]})
    _forget(store, collect)
    backend.touched.clear()
    await store.get("main", "k")
    assert hash_of({"touch-me": [1, 2]}) in backend.touched
    assert hash_of([1, 2]) in backend.touched


async def test_cleanup_extends_expiry_of_values_read_since_the_previous_cleanup(setup):
    backend, store = setup
    await store.set("main", "k", {"read-since": [1]})
    await store.cleanup()
    backend.touched.clear()
    await store.get("main", "k")
    await store.cleanup()
    assert hash_of({"read-since": [1]}) in backend.touched
    assert hash_of([1]) in backend.touched
    assert store.root_hash("main") in backend.touched
    backend.touched.clear()
    await store.cleanup()
    assert hash_of({"read-since": [1]}) not in backend.touched


async def test_one_child_entry_is_missing(setup, collect):
    backend, store = setup
    calls = []

    async def loader(d, k):
        calls.append(k)
        return {"partial": {"child": [1]}}

    store.loader = loader
    await store.set("main", "k", {"partial": {"child": [1]}})
    _forget(store, collect)
    backend.drop([hash_of({"child": [1]})])
    assert await store.get("main", "k") == {"partial": {"child": [1]}}
    assert calls == ["k"]
    # The loaded nodes were written to the external cache again.
    assert hash_of({"child": [1]}) in backend.nodes


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(b"\xc1", id="not-msgpack"),
        pytest.param(b"\x92\x01\x05", id="dict-payload-not-an-array"),
        pytest.param(b"\x91\x01", id="missing-payload"),
        pytest.param(b"\x92\x2a\x90", id="unknown-tag"),
    ],
)
async def test_malformed_entry_is_a_cache_miss(setup, collect, entry):
    backend, store = setup
    calls = []

    async def loader(d, k):
        calls.append(k)
        return {"malformed": [1]}

    store.loader = loader
    await store.set("main", "k", {"malformed": [1]})
    _forget(store, collect)
    backend.nodes[hash_of({"malformed": [1]})] = entry
    assert await store.get("main", "k") == {"malformed": [1]}
    assert calls == ["k"]


async def test_model_class_that_cannot_be_imported_is_a_cache_miss(setup, collect):
    from pydantic.dataclasses import dataclass

    from permafrost import FrozenModel, types

    @dataclass(frozen=True, slots=True)
    class Local(FrozenModel):
        name: str

    _backend, store = setup
    calls = []

    async def loader(d, k):
        calls.append(k)
        return {"local": Local(name="x")}

    store.loader = loader
    await store.set("main", "k", {"local": Local(name="x")})
    _forget(store, collect)
    # Another process would not know the class: its path cannot be imported.
    info = types._model_info(Local)
    assert info is not None
    path = info.path
    del types._classes_by_path[path]
    assert await store.get("main", "k") == {"local": Local(name="x")}
    assert calls == ["k"]


async def test_scalar_value_root_in_the_external_cache(setup, collect):
    _backend, store = setup
    await store.set("main", "n", 12345)
    _forget(store, collect)
    assert await store.get("main", "n") == 12345


async def test_loaded_value_rewrites_a_lost_node_still_held_in_memory(setup, collect):
    backend, store = setup

    async def loader(d, k):
        return {"heal": {"child": [7]}}

    store.loader = loader
    child = freeze({"child": [7]})  # still held in memory by this process
    await store.set("main", "k", {"heal": child})
    _forget(store, collect)
    root = hash_of({"heal": child})
    backend.drop([root, hash_of(child)])
    await store.get("main", "k")
    # Other processes need both entries: the loaded value is written with every node.
    assert root in backend.nodes
    assert hash_of(child) in backend.nodes


async def test_read_an_enum_member_from_the_external_cache():
    backend = MemoryBackend()
    with Process():
        writer = Store(backend)
        await writer.create_dimension("main")
        await writer.set("main", "k", {"s": State.PRESENT, State.ABSENT: [1]})
    with Process():
        reader = Store(backend)
        value = await reader.get("main", "k")
    assert value["s"] is State.PRESENT
    assert type(value["s"]) is State
    assert {type(k) for k in value} == {str, State}


def _dict_with_enum(name, value):
    """Return the encoding of a dictionary node whose key ``"s"`` holds an enum member named ``name``."""
    member = msgpack.ExtType(codec.EXT_ENUM, msgpack.packb([name, value], use_bin_type=True))
    return msgpack.packb([codec.TAG_DICT, [["s", member]]], use_bin_type=True)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        pytest.param("tests.missing:Nope", "present", id="unknown-module"),
        pytest.param("tests.models:Nope", "present", id="unknown-class"),
        pytest.param("tests.models:Item", "present", id="not-an-enum"),
        pytest.param("tests.models:State", "gone", id="not-a-member"),
    ],
)
async def test_enum_class_cannot_be_found_when_rebuilding(setup, collect, name, value):
    backend, store = setup
    calls = []

    async def loader(d, k):
        calls.append(k)
        return {"s": State.PRESENT}

    store.loader = loader
    await store.set("main", "k", {"s": State.PRESENT})
    _forget(store, collect)
    backend.nodes[hash_of({"s": State.PRESENT})] = _dict_with_enum(name, value)
    assert await store.get("main", "k") == {"s": State.PRESENT}
    assert calls == ["k"]


# Snapshots: scenarios of specs/dimension-snapshots/spec.md with an external cache


async def test_snapshot_reads_values_evicted_from_memory(setup, collect):
    backend, store = setup

    async def loader(d, k):
        raise AssertionError("loader must not be called")

    store.loader = loader
    await store.set_many("main", {"a": {"evicted-snap": [1]}, "b": {"evicted-snap": [2]}})
    _forget(store, collect)
    gets = backend.get_requests
    snap = await store.snapshot("main")
    assert snap.get("a") == {"evicted-snap": [1]}
    assert snap.get("b") == {"evicted-snap": [2]}
    # Both values were fetched together: one request for each tree level.
    assert backend.get_requests == gets + 2


async def test_snapshot_loads_only_the_value_with_a_lost_node(setup, collect):
    backend, store = setup
    calls = []

    async def loader(d, k):
        calls.append(k)
        return {"lost-snap": {"child": ["b"]}}

    store.loader = loader
    values = {key: {"lost-snap": {"child": [key]}} for key in ("a", "b", "c")}
    await store.set_many("main", values)
    root = store.root_hash("main")
    _forget(store, collect)
    backend.drop([hash_of({"child": ["b"]})])
    snap = await store.snapshot("main")
    assert calls == ["b"]
    for key, value in values.items():
        assert snap.get(key) == value
    assert snap.root_hash == root
    # The loaded nodes were written to the external cache again.
    assert hash_of({"child": ["b"]}) in backend.nodes


async def test_snapshot_cache_miss_with_an_external_cache_and_no_loader(setup, collect):
    backend, store = setup
    await store.set_many("main", {"a": {"miss-snap": 1}, "b": {"miss-snap": 2}})
    _forget(store, collect)
    backend.drop([hash_of({"miss-snap": 2})])
    with pytest.raises(CacheMissError) as info:
        await store.snapshot("main")
    assert info.value.key == "b"


async def test_fetch_many_returns_the_complete_trees(setup, collect):
    backend, store = setup
    await store.set_many("main", {"a": {"many": [1]}, "b": {"many": [2]}})
    _forget(store, collect)
    backend.drop([hash_of([2])])
    a, b = hash_of({"many": [1]}), hash_of({"many": [2]})
    fetched = await store._fetch_many([a, b, a])
    assert list(fetched) == [a]
    assert fetched[a] == {"many": [1]}
