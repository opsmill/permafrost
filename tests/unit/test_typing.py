"""Scenarios of specs/static-typing/spec.md.

The type checker checks each ``assert_type`` against the declared types, and
``tests/unit/test_type_check.py`` fails when it reports an error. A line that
expects a type error carries ``# ty: ignore[<rule>]``; the type check reports
an ignore comment that is not needed, so each such line keeps checking that
the error is still reported. At run time ``assert_type`` returns its argument,
so pytest runs the same code.
"""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from datetime import date
from typing import assert_type

import pytest

from permafrost import (
    DiffEntry,
    FrozenDict,
    FrozenList,
    FrozenSet,
    FrozenTuple,
    Snapshot,
    Step,
    Store,
    TypeHandler,
    delete_in,
    diff,
    freeze,
    merge,
    register_type,
    set_in,
)

from ..models import Device, Interface, Item, Level, Site, State


class Prefix:
    """A read-only type stored through a type handler."""

    __slots__ = ("__weakref__", "length", "network")

    def __init__(self, network: str, length: int) -> None:
        object.__setattr__(self, "network", network)
        object.__setattr__(self, "length", length)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Prefix is read-only")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Prefix) and (self.network, self.length) == (other.network, other.length)

    __hash__ = None


def _encode_prefix(prefix: Prefix) -> tuple[str, int]:
    return (prefix.network, prefix.length)


def _decode_prefix(encoded: tuple[str, int]) -> Prefix:
    return Prefix(encoded[0], encoded[1])


def _encode_device(device: Device) -> tuple[str, int]:
    return (device.name, 0)


# Scalars keep their type


def _freeze_scalars(number: int, text: str, data: bytes, day: date) -> None:
    assert_type(freeze(number), int)
    assert_type(freeze(text), str)
    assert_type(freeze(data), bytes)
    assert_type(freeze(day), date)
    assert_type(freeze(None), None)


def test_freeze_a_scalar() -> None:
    _freeze_scalars(1, "a", b"a", date(2026, 10, 25))


def _freeze_enum_members(state: State, level: Level) -> None:
    assert_type(freeze(state), State)
    assert_type(freeze(level), Level)
    assert_type(freeze(Item(name="a", state=state)).state, State)


def test_freeze_an_enum_member() -> None:
    _freeze_enum_members(State.PRESENT, Level.HIGH)


# Models keep their class


def test_freeze_a_model() -> None:
    assert_type(freeze(Device(name="r1")), Device)
    assert_type(freeze(Interface(name="eth0")), Interface)
    assert_type(freeze(Site(name="paris")), Site)


# Read-only collections are generic


def test_freeze_a_dictionary_of_scalars() -> None:
    value: dict[str, int] = {"a": 1}
    frozen = freeze(value)
    assert_type(frozen, FrozenDict[str, int])
    assert_type(frozen["a"], int)


def test_nested_value_declared_with_an_abstract_type() -> None:
    value: dict[str, Sequence[int]] = {"a": [1, 2]}
    frozen = freeze(value)
    assert_type(frozen, FrozenDict[str, Sequence[int]])
    # The read-only list that the value holds at run time is a Sequence[int].
    assert isinstance(frozen["a"], FrozenList)


def test_nested_value_declared_with_a_mutable_type() -> None:
    value: dict[str, list[int]] = {"a": [1, 2]}
    frozen = freeze(value)
    assert_type(frozen, FrozenDict[str, list[int]])
    # At run time the value holds a read-only list, as the documentation states.
    assert isinstance(frozen["a"], FrozenList)


def test_freeze_lists_tuples_and_sets() -> None:
    assert_type(freeze([1, 2]), FrozenList[int])
    assert_type(freeze((1, 2)), FrozenTuple[int])
    assert_type(freeze({1, 2}), FrozenSet[int])
    assert_type(freeze(frozenset({"a"})), FrozenSet[str])
    mixed: tuple[int, str] = (1, "a")
    assert_type(freeze(mixed), FrozenTuple[int | str])


def _freeze_abstract(mapping: Mapping[str, int], sequence: Sequence[int], items: AbstractSet[int]) -> None:
    assert_type(freeze(mapping), FrozenDict[str, int])
    # A sequence can be a list or a tuple at run time, so it stays a Sequence.
    assert_type(freeze(sequence), Sequence[int])
    assert_type(freeze(items), FrozenSet[int])


def test_freeze_values_declared_with_abstract_types() -> None:
    _freeze_abstract({"a": 1}, (1, 2), {1})


def test_freeze_a_read_only_value() -> None:
    frozen_dict = freeze({"a": 1})
    frozen_list = freeze([1])
    frozen_tuple = freeze((1,))
    frozen_set = freeze({1})
    assert_type(freeze(frozen_dict), FrozenDict[str, int])
    assert_type(freeze(frozen_list), FrozenList[int])
    assert_type(freeze(frozen_tuple), FrozenTuple[int])
    assert_type(freeze(frozen_set), FrozenSet[int])


def test_reading_a_read_only_collection() -> None:
    devices = freeze({"r1": Device(name="r1")})
    for key, device in devices.items():
        assert_type(key, str)
        assert_type(device, Device)
    interfaces = freeze([Interface(name="eth0")])
    assert_type(interfaces[0], Interface)
    assert_type(interfaces[0:1], tuple[Interface, ...])
    assert_type(devices.get("r1"), Device | None)


# Type handlers are typed by their class


def test_type_handler() -> None:
    handler = register_type(Prefix, name="tests.typing.Prefix", encode=_encode_prefix, decode=_decode_prefix)
    assert_type(handler, TypeHandler[Prefix, tuple[str, int]])
    # A registered type is stored through its handler; its class is only known at run time.
    assert_type(freeze(Prefix("10.0.0.0", 8)), object)


def test_encode_function_for_another_class() -> None:
    register_type(
        Prefix,
        name="tests.typing.Prefix.wrong",
        encode=_encode_device,  # ty: ignore[invalid-argument-type]
        decode=_decode_prefix,
    )


# Update operations keep the type of the value


def test_update_a_model_at_a_path() -> None:
    device = Device(name="r1", interfaces=(Interface(name="eth0"),))
    assert_type(set_in(device, ("name",), "r2"), Device)
    assert_type(set_in(device, ("interfaces", 0, "vlans"), (10,)), Device)
    site = Site(name="paris", devices=["r1"])
    assert_type(delete_in(site, ("devices", 0)), Site)


def test_update_a_dictionary_at_a_path() -> None:
    value: dict[str, int] = {"a": 1, "b": 2}
    assert_type(set_in(value, ("a",), 3), FrozenDict[str, int])
    assert_type(delete_in(value, ("b",)), FrozenDict[str, int])
    assert_type(set_in([1, 2], (0,), 3), FrozenList[int])
    assert_type(delete_in((1, 2), (0,)), FrozenTuple[int])


def test_path_with_a_list_as_a_step() -> None:
    value = {("x", 1): "tuple key"}
    # A tuple is a valid step, because a dictionary key can be a tuple.
    assert set_in(value, (("x", 1),), "new")[("x", 1)] == "new"
    # A list is not: the type checker reports it, and at run time it is not a valid key.
    with pytest.raises(TypeError, match="unhashable"):
        set_in(value, (["x", 1],), "new")  # ty: ignore[no-matching-overload]


def test_merge_keeps_the_type_of_the_value() -> None:
    device = Device(name="r1")
    assert_type(merge(device, {"site": "lyon"}), Device)
    assert_type(merge(device, Device(name="r2")), Device)
    value: dict[str, int] = {"a": 1}
    assert_type(merge(value, {"b": 2}), FrozenDict[str, int])
    # Another kind of partial value replaces the whole value, so the result is open.
    assert_type(merge(value, 5), object)


# Diff content is open


def test_diff_content_is_object() -> None:
    entries = diff({"a": Device(name="r1")}, {"a": Device(name="r2")})
    assert_type(entries, list[DiffEntry])
    entry = entries[0]
    assert_type(entry.path, tuple[Step, ...])
    assert_type(entry.old, object)
    assert_type(entry.new, object)
    # Reading an attribute needs narrowing first.
    with pytest.raises(AttributeError):
        entry.new.name  # ty: ignore[unresolved-attribute]  # noqa: B018
    assert isinstance(entry.new, str)
    assert entry.new == "r2"


# The store is typed by its value type


async def test_store_of_models() -> None:
    store = Store[Device]()
    assert_type(store.dimensions(), list[str])
    assert_type(await store.has_dimension("main"), bool)
    await store.create_dimension("main")
    assert_type(await store.set("main", "r1", Device(name="r1")), Device)
    assert_type(await store.get("main", "r1"), Device)
    updated = await store.update("main", "r1", lambda device: merge(device, {"site": "lyon"}))
    assert_type(updated, Device)


async def test_snapshot_of_a_store_of_models() -> None:
    store = Store[Device]()
    await store.create_dimension("main")
    await store.set("main", "r1", Device(name="r1"))
    snap = await store.snapshot("main")
    assert_type(snap, Snapshot[Device])
    assert_type(snap.get("r1"), Device)
    assert_type(snap.keys(), list[str])
    assert_type(snap.root_hash, bytes)
    assert_type("r1" in snap, bool)


async def _load_device(dimension: str, key: str) -> Device:
    return Device(name=key)


async def _load_text(dimension: str, key: str) -> str:
    return key


def test_loader_of_the_value_type() -> None:
    store: Store[Device] = Store(loader=_load_device)
    assert_type(store, Store[Device])


def test_loader_of_another_type() -> None:
    Store[Device](loader=_load_text)  # ty: ignore[invalid-argument-type]


async def test_store_of_dictionaries() -> None:
    store = Store[Mapping[str, int]]()
    await store.create_dimension("main")
    stored: dict[str, int] = {"a": 1}
    await store.set("main", "k", stored)
    value = await store.get("main", "k")
    assert_type(value, Mapping[str, int])
    assert value["a"] == 1
