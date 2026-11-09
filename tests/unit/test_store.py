"""Scenarios of specs/dimensions/spec.md and specs/value-loader/spec.md, in one process."""

import asyncio

import pytest

from permafrost import (
    CacheMissError,
    DiffEntry,
    DimensionExistsError,
    DimensionNotFoundError,
    KeyChange,
    KeyNotFoundError,
    Store,
    UnsupportedTypeError,
    freeze,
    hash_of,
    merge,
    registry,
    set_in,
)
from permafrost.nodes import KMBranch


@pytest.fixture
async def store():
    s = Store()
    await s.create_dimension("main")
    return s


# Store and read a value by key


async def test_read_back_a_stored_value(store):
    await store.set("main", "device-1", {"a": 1})
    assert await store.get("main", "device-1") == {"a": 1}


async def test_read_a_key_that_the_dimension_does_not_have():
    calls = []

    async def loader(d, k):
        calls.append((d, k))

    store = Store(loader=loader)
    await store.create_dimension("main")
    with pytest.raises(KeyNotFoundError):
        await store.get("main", "never-stored")
    assert calls == []


async def test_set_returns_the_read_only_value(store):
    value = await store.set("main", "k", {"a": [1]})
    assert value == freeze({"a": [1]})
    assert value is await store.get("main", "k")


# Delete


async def test_delete_a_key_on_a_branch(store):
    await store.set("main", "device-1", {"a": 1})
    await store.create_dimension("branch-a", source="main")
    await store.delete("branch-a", "device-1")
    assert await store.get("main", "device-1") == {"a": 1}
    with pytest.raises(KeyNotFoundError):
        await store.get("branch-a", "device-1")
    with pytest.raises(KeyNotFoundError):
        await store.delete("branch-a", "device-1")


# Change several keys in one commit


@pytest.fixture
async def abc(store):
    for key in "abc":
        await store.set("main", key, 0)
    return store


async def _values(store, dimension="main"):
    return {key: await store.get(dimension, key) for key in await store.keys(dimension)}


async def test_set_and_delete_several_keys(abc):
    result = await abc.set_many("main", {"a": 1, "b": 1}, delete=["c"])
    assert result == {"a": 1, "b": 1}
    assert await _values(abc) == {"a": 1, "b": 1}


async def test_reader_during_a_change_views_all_or_nothing(abc):
    viewed = []
    done = False

    async def reader():
        while not done:
            viewed.append(tuple([await abc.get("main", key) for key in "abc"]))
            await asyncio.sleep(0)

    async def writer():
        nonlocal done
        await asyncio.sleep(0)
        await abc.set_many("main", {"a": 1, "b": 1, "c": 1})
        await asyncio.sleep(0)
        done = True

    await asyncio.gather(reader(), writer())
    assert set(viewed) <= {(0, 0, 0), (1, 1, 1)}
    assert viewed[-1] == (1, 1, 1)


async def test_set_many_gives_the_same_root_hash_as_single_changes(abc):
    await abc.create_dimension("copy", source="main")
    await abc.set_many("main", {"a": 1, "d": {"x": [1]}}, delete=["b", "c"])
    await abc.set("copy", "a", 1)
    await abc.set("copy", "d", {"x": [1]})
    await abc.delete("copy", "b")
    await abc.delete("copy", "c")
    assert abc.root_hash("main") == abc.root_hash("copy")


async def test_set_many_returns_read_only_values(store):
    result = await store.set_many("main", {"k": {"l": [1, 2]}})
    assert result["k"] == freeze({"l": [1, 2]})
    assert not isinstance(result["k"]["l"], list)
    assert result["k"] is await store.get("main", "k")


@pytest.mark.parametrize("values", [{}, {"a": 0, "b": 0}])
async def test_set_many_that_changes_nothing_keeps_the_root(abc, values):
    before = abc.root_hash("main")
    assert await abc.set_many("main", values) == values
    assert abc.root_hash("main") == before


async def test_set_many_with_a_value_that_cannot_be_frozen_changes_nothing(abc):
    before = abc.root_hash("main")
    with pytest.raises(UnsupportedTypeError):
        await abc.set_many("main", {"a": 1, "b": object()})
    assert abc.root_hash("main") == before
    assert await _values(abc) == {"a": 0, "b": 0, "c": 0}


async def test_set_many_deleting_a_missing_key_changes_nothing(abc):
    before = abc.root_hash("main")
    with pytest.raises(KeyNotFoundError):
        await abc.set_many("main", {"a": 1}, delete=["missing"])
    assert abc.root_hash("main") == before
    assert await abc.get("main", "a") == 0


async def test_set_many_with_the_same_key_to_set_and_delete(abc):
    before = abc.root_hash("main")
    with pytest.raises(ValueError, match="overlap"):
        await abc.set_many("main", {"a": 1}, delete=["a"])
    assert abc.root_hash("main") == before


async def test_set_many_on_an_unknown_dimension(store):
    with pytest.raises(DimensionNotFoundError):
        await store.set_many("nope", {"a": 1})


# Update


async def test_merge_into_the_value_under_a_key(store):
    await store.set("main", "k", {"a": 1})
    new = await store.update("main", "k", lambda v: merge(v, {"b": 2}))
    assert new == {"a": 1, "b": 2}
    assert await store.get("main", "k") == {"a": 1, "b": 2}


async def test_update_with_set_in_and_an_async_function(store):
    await store.set("main", "k", {"a": {"b": 1}})
    await store.update("main", "k", lambda v: set_in(v, ("a", "b"), 2))

    async def change(v):
        await asyncio.sleep(0)
        return set_in(v, ("a", "c"), 3)

    assert await store.update("main", "k", change) == {"a": {"b": 2, "c": 3}}


async def test_update_a_missing_key(store):
    with pytest.raises(KeyNotFoundError):
        await store.update("main", "nope", lambda v: v)


async def test_concurrent_updates_in_one_process_do_not_overwrite_each_other(store):
    await store.set("main", "counter", {"n": 0})

    async def increment(v):
        await asyncio.sleep(0)
        return set_in(v, ("n",), v["n"] + 1)

    await asyncio.gather(*(store.update("main", "counter", increment) for _ in range(20)))
    assert (await store.get("main", "counter"))["n"] == 20


# Dimensions


async def test_list_dimensions_is_a_detached_snapshot():
    store = Store()
    assert store.dimensions() == []
    await store.create_dimension("main")
    await store.create_dimension("branch-a", source="main")
    names = store.dimensions()
    assert sorted(names) == ["branch-a", "main"]
    names.clear()
    assert sorted(store.dimensions()) == ["branch-a", "main"]
    snapshot = store.dimensions()
    await store.create_dimension("branch-b", source="main")
    assert sorted(snapshot) == ["branch-a", "main"]
    assert sorted(store.dimensions()) == ["branch-a", "branch-b", "main"]


async def test_create_an_empty_dimension():
    store = Store()
    await store.create_dimension("snapshot-2026-10-24")
    assert await store.keys("snapshot-2026-10-24") == []


async def test_has_dimension_without_backend():
    async def loader(dimension: str, key: str) -> int:
        raise AssertionError("Checking a dimension must not call the loader")

    store = Store[int](loader=loader)
    assert await store.has_dimension("main") is False
    assert store.dimensions() == []
    await store.create_dimension("main")
    assert await store.has_dimension("main") is True
    await store.set("main", "k", 1)
    assert await store.has_dimension("main") is True
    assert await store.has_dimension("unknown") is False
    assert store.dimensions() == ["main"]


async def test_create_a_dimension_that_exists(store):
    with pytest.raises(DimensionExistsError):
        await store.create_dimension("main")


async def test_use_a_dimension_that_does_not_exist(store):
    with pytest.raises(DimensionNotFoundError):
        await store.get("nope", "k")
    with pytest.raises(DimensionNotFoundError):
        await store.set("nope", "k", 1)
    with pytest.raises(DimensionNotFoundError):
        await store.create_dimension("copy", source="nope")
    with pytest.raises(DimensionNotFoundError):
        store.root_hash("nope")


async def test_change_the_copy(store):
    await store.set("main", "device-1", {"v": 1})
    await store.create_dimension("branch-a", source="main")
    await store.set("branch-a", "device-1", {"v": 2})
    assert await store.get("main", "device-1") == {"v": 1}
    await store.set("main", "device-2", {"v": 3})
    with pytest.raises(KeyNotFoundError):
        await store.get("branch-a", "device-2")


class _Counter:
    def __init__(self):
        self.created = []

    def node_created(self, h, node):
        self.created.append(node)


async def _fill(store, dimension, n):
    for i in range(n):
        await store.set(dimension, f"key-{i}", {"id": i, "name": f"device-{i}"})


async def test_cost_of_creating_a_dimension():
    store = Store()
    await store.create_dimension("main")
    await _fill(store, "main", 10_000)
    counter = _Counter()
    registry.add_listener(counter)
    try:
        await store.create_dimension("branch-a", source="main")
    finally:
        registry.remove_listener(counter)
    assert counter.created == []
    assert store.root_hash("branch-a") == store.root_hash("main")


# Replace the content of a dimension with the content of another


async def test_switch_a_dimension_to_a_candidate(store):
    await store.set("main", "device-1", {"v": 1})
    await store.set("main", "device-2", {"v": 2})
    await store.create_dimension("candidate", source="main")
    await store.set("candidate", "device-1", {"v": 10})
    await store.delete("candidate", "device-2")
    await store.set("candidate", "device-3", {"v": 3})
    await store.replace_dimension("main", "candidate")
    assert store.root_hash("main") == store.root_hash("candidate")
    assert sorted(await store.keys("main")) == ["device-1", "device-3"]
    assert await store.get("main", "device-1") == {"v": 10}
    assert await store.diff_keys("main", "candidate") == []


async def test_changes_after_a_replace_stay_in_one_dimension(store):
    await store.create_dimension("candidate", source="main")
    await store.set("candidate", "k", {"v": 1})
    await store.replace_dimension("main", "candidate")
    await store.set("candidate", "k", {"v": 2})
    await store.set("main", "other", {"v": 3})
    assert await store.get("main", "k") == {"v": 1}
    with pytest.raises(KeyNotFoundError):
        await store.get("candidate", "other")


async def test_put_the_previous_content_back(store):
    await store.set("main", "k", {"v": 1})
    before = store.root_hash("main")
    await store.create_dimension("backup", source="main")
    await store.set("main", "k", {"v": 2})
    await store.set("main", "new", {"v": 3})
    await store.replace_dimension("main", "backup")
    assert store.root_hash("main") == before
    assert await store.keys("main") == ["k"]
    assert await store.get("main", "k") == {"v": 1}


async def test_replace_a_dimension_with_itself(store):
    await store.set("main", "k", {"v": 1})
    before = store.root_hash("main")
    await store.replace_dimension("main", "main")
    assert store.root_hash("main") == before


async def test_replace_a_dimension_that_does_not_exist(store):
    with pytest.raises(DimensionNotFoundError):
        await store.replace_dimension("nope", "main")
    with pytest.raises(DimensionNotFoundError):
        store.root_hash("nope")


async def test_replace_with_a_source_that_does_not_exist(store):
    await store.set("main", "k", {"v": 1})
    before = store.root_hash("main")
    with pytest.raises(DimensionNotFoundError):
        await store.replace_dimension("main", "nope")
    assert store.root_hash("main") == before


async def test_cost_of_replacing_a_dimension():
    store = Store()
    await store.create_dimension("main")
    await _fill(store, "main", 1000)
    await store.create_dimension("candidate", source="main")
    await store.set("candidate", "key-0", {"id": 0, "name": "renamed"})
    counter = _Counter()
    registry.add_listener(counter)
    try:
        await store.replace_dimension("main", "candidate")
    finally:
        registry.remove_listener(counter)
    assert counter.created == []
    assert store.root_hash("main") == store.root_hash("candidate")


async def test_same_keys_added_in_different_orders():
    store = Store()
    await store.create_dimension("one")
    await store.create_dimension("two")
    for k in ("a", "b", "c"):
        await store.set("one", k, {"k": k})
    for k in ("c", "a", "b"):
        await store.set("two", k, {"k": k})
    assert store.root_hash("one") == store.root_hash("two")


async def test_revert_a_change(store):
    await store.set("main", "k", {"v": 1})
    await store.create_dimension("branch-a", source="main")
    before = store.root_hash("branch-a")
    await store.set("branch-a", "k", {"v": 2})
    assert store.root_hash("branch-a") != before
    await store.set("branch-a", "k", {"v": 1})
    assert store.root_hash("branch-a") == before


async def test_list_keys_without_reading_values():
    calls = []

    async def loader(d, k):
        calls.append(k)

    store = Store(loader=loader)
    await store.create_dimension("main")
    await store.set("main", "a", 1)
    await store.set("main", "b", 2)
    assert sorted(await store.keys("main")) == ["a", "b"]
    assert calls == []


async def test_scalar_values(store):
    await store.set("main", "n", 5)
    await store.set("main", "s", "text")
    await store.set("main", "none", None)
    assert await store.get("main", "n") == 5
    assert await store.get("main", "s") == "text"
    assert await store.get("main", "none") is None


# Keys that differ between two dimensions


async def test_one_key_changed_on_a_branch(monkeypatch):
    from permafrost import keymap

    store = Store()
    await store.create_dimension("main")
    await _fill(store, "main", 10_000)
    await store.create_dimension("branch-a", source="main")
    await store.set("branch-a", "key-1234", {"id": -1})

    reads = []
    original = keymap._diff_nodes

    def counting(a, b, out):
        if type(a) is KMBranch and type(b) is KMBranch and a._pf_hash != b._pf_hash:
            reads.append(a)
        original(a, b, out)

    monkeypatch.setattr(keymap, "_diff_nodes", counting)
    assert await store.diff_keys("main", "branch-a") == [KeyChange("key-1234", "changed")]
    assert 1 <= len(reads) <= 6


async def test_added_and_removed_keys_between_dimensions(store):
    await store.set("main", "a", 1)
    await store.set("main", "b", 2)
    await store.create_dimension("branch-a", source="main")
    await store.delete("branch-a", "a")
    await store.set("branch-a", "c", 3)
    assert await store.diff_keys("main", "branch-a") == [
        KeyChange("a", "removed"),
        KeyChange("c", "added"),
    ]


async def test_two_dimensions_with_the_same_root_hash(store):
    await store.set("main", "a", 1)
    await store.create_dimension("branch-a", source="main")
    assert await store.diff_keys("main", "branch-a") == []


# Diff of one key in two dimensions (specs/value-diff/spec.md)


async def test_key_changed_on_a_branch(store):
    await store.set("main", "k", {"name": "r1", "site": "paris"})
    await store.create_dimension("branch-a", source="main")
    await store.update("branch-a", "k", lambda v: set_in(v, ("site",), "lyon"))
    assert await store.diff("main", "branch-a", "k") == [DiffEntry(("site",), "changed", "paris", "lyon")]


async def test_key_in_only_one_dimension(store):
    await store.create_dimension("branch-a", source="main")
    await store.set("branch-a", "new", {"a": 1})
    assert await store.diff("main", "branch-a", "new") == [DiffEntry((), "added", None, {"a": 1})]
    assert await store.diff("branch-a", "main", "new") == [DiffEntry((), "removed", {"a": 1}, None)]
    with pytest.raises(KeyNotFoundError):
        await store.diff("main", "branch-a", "nowhere")


# Many dimensions in one process


async def test_one_thousand_copies_of_a_dimension():
    store = Store()
    await store.create_dimension("main")
    await _fill(store, "main", 10_000)
    before = registry.size()
    for i in range(1000):
        await store.create_dimension(f"branch-{i}", source="main")
    assert registry.size() == before


# Loader (specs/value-loader/spec.md)


def _evict_all(store):
    store._values.clear()
    store._read_at.clear()


async def test_loader_is_called_on_a_cache_miss(collect):
    calls = []

    async def loader(dimension, key):
        calls.append((dimension, key))
        return {"loaded": key}

    store = Store(loader=loader)
    await store.create_dimension("main")
    await store.set("main", "k", {"loaded": "k"})
    _evict_all(store)
    collect()
    assert await store.get("main", "k") == {"loaded": "k"}
    assert calls == [("main", "k")]
    # Second read after a load: from memory.
    assert await store.get("main", "k") == {"loaded": "k"}
    assert calls == [("main", "k")]


async def test_application_storage_holds_newer_data(collect):
    async def loader(dimension, key):
        return {"version": 2}

    store = Store(loader=loader)
    await store.create_dimension("main")
    await store.set("main", "k", {"version": 1})
    _evict_all(store)
    collect()
    assert await store.get("main", "k") == {"version": 2}
    assert await store.keys("main") == ["k"]
    from permafrost import keymap

    assert keymap.get(store._dims["main"], "k") == hash_of({"version": 2})


async def test_no_loader_registered(collect):
    store = Store()
    await store.create_dimension("main")
    await store.set("main", "k", {"no-loader": 1})
    _evict_all(store)
    collect()
    with pytest.raises(CacheMissError) as info:
        await store.get("main", "k")
    assert info.value.dimension == "main" and info.value.key == "k"


async def test_loader_fails(collect):
    async def loader(dimension, key):
        raise RuntimeError("database down")

    store = Store(loader=loader)
    await store.create_dimension("main")
    await store.set("main", "k", {"loader-fails": 1})
    root = store.root_hash("main")
    _evict_all(store)
    collect()
    with pytest.raises(RuntimeError, match="database down"):
        await store.get("main", "k")
    assert store.root_hash("main") == root


async def test_compare_two_dimensions_held_in_memory(store):
    await store.create_dimension("branch-a", source="main")
    # root_hash is a regular function: no await.
    assert store.root_hash("main") == store.root_hash("branch-a")


async def test_value_still_in_memory_elsewhere_is_not_loaded(collect):
    async def loader(dimension, key):
        raise AssertionError("loader must not be called")

    store = Store(loader=loader)
    await store.create_dimension("main")
    held = await store.set("main", "k", {"held": [1, 2]})
    _evict_all(store)
    collect()
    assert await store.get("main", "k") is held
