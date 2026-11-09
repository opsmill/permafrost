"""Existing cache contracts exercised through the real Redis protocol."""

import pytest
from redis.asyncio import Redis

from permafrost import Backend, CacheMissError, DimensionNotFoundError, KeyNotFoundError, Store, hash_of, registry
from permafrost.backends.redis import RedisBackend

from ..process import Process

pytestmark = pytest.mark.redis


async def test_batch_round_trip(redis_backend: RedisBackend[Redis]) -> None:
    assert isinstance(redis_backend, Backend)
    first, second, missing = b"a" * 16, b"b" * 16, b"c" * 16
    await redis_backend.set_many({first: b"\x00\xff\x80", second: b""})
    assert await redis_backend.get_many([second, missing, first, first]) == [
        b"",
        None,
        b"\x00\xff\x80",
        b"\x00\xff\x80",
    ]


async def test_empty_batches(redis_backend: RedisBackend[Redis]) -> None:
    await redis_backend.set_many({})
    await redis_backend.touch([])
    assert await redis_backend.get_many([]) == []


async def test_roots_and_prefix_isolation(redis_backend: RedisBackend[Redis]) -> None:
    isolated = RedisBackend(redis_backend.client, prefix=f"{redis_backend.prefix}other:")
    node = b"a" * 16
    assert await redis_backend.get_root("main") is None
    await redis_backend.set_many({node: b"primary"})
    await redis_backend.set_root("main", node)
    assert await isolated.get_many([node]) == [None]
    assert await isolated.get_root("main") is None
    await isolated.set_many({node: b"isolated"})
    await isolated.set_root("main", b"b" * 16)
    assert await redis_backend.get_many([node]) == [b"primary"]
    assert await redis_backend.get_root("main") == node
    assert await isolated.get_many([node]) == [b"isolated"]
    assert await isolated.get_root("main") == b"b" * 16


async def test_node_expiry_and_persistent_dimension_root(redis_backend: RedisBackend[Redis]) -> None:
    redis_backend.node_ttl = 600
    with Process():
        store = Store[object](redis_backend)
        await store.create_dimension("main")
        await store.set("main", "k", {"nested": [1, 2]})
        root = store.root_hash("main")
    # Check both value levels and the key map, not just the value root.
    for node in [hash_of({"nested": [1, 2]}), hash_of([1, 2]), root]:
        ttl = await redis_backend.client.ttl(f"{redis_backend.prefix}node:{node.hex()}")
        assert 590 <= ttl <= 600
    assert await redis_backend.get_root("main") == root
    assert await redis_backend.client.ttl(f"{redis_backend.prefix}dim:main") == -1


async def test_fetch_refreshes_expiry(redis_backend: RedisBackend[Redis], redis_peer: RedisBackend[Redis]) -> None:
    value = {"touch": [3, 4]}
    with Process():
        writer = Store[object](redis_backend)
        await writer.create_dimension("main")
        await writer.set("main", "k", value)
    keys = [f"{redis_backend.prefix}node:{hash_of(node).hex()}" for node in [value, [3, 4]]]
    for key in keys:
        assert await redis_backend.client.expire(key, 100)
    with Process():
        assert registry.lookup(hash_of(value), object) is None
        assert await Store[object](redis_peer).get("main", "k") == value
    for key in keys:
        ttl = await redis_peer.client.ttl(key)
        assert redis_peer.node_ttl - 10 <= ttl <= redis_peer.node_ttl


async def test_cold_reader_reconstructs_nested_value(
    redis_backend: RedisBackend[Redis], redis_peer: RedisBackend[Redis]
) -> None:
    value = {"device": {"ports": [{"name": "eth0", "vlans": [10, 20]}]}, "raw": b"\x00\xff"}
    assert redis_backend.client is not redis_peer.client
    with Process():
        writer = Store[object](redis_backend)
        await writer.create_dimension("main")
        await writer.set("main", "router", value)
        root = writer.root_hash("main")
    with Process():
        assert registry.lookup(hash_of(value), object) is None
        reader = Store[object](redis_peer)
        assert await reader.keys("main") == ["router"]
        assert await reader.get("main", "router") == value
        assert reader.root_hash("main") == root


async def test_dimension_discovery_does_not_load_from_redis(
    redis_backend: RedisBackend[Redis], redis_peer: RedisBackend[Redis]
) -> None:
    with Process():
        writer = Store[int](redis_backend)
        await writer.create_dimension("main")
        await writer.set("main", "k", 1)
        root = writer.root_hash("main")
        assert writer.dimensions() == ["main"]

    async def loader(dimension: str, key: str) -> int:
        raise AssertionError("Dimension discovery must not call the loader")

    with Process():
        reader = Store[int](redis_peer, loader=loader)
        assert registry.lookup(root, object) is None
        assert reader.dimensions() == []
        assert await reader.has_dimension("unknown") is False
        assert await reader.has_dimension("main") is True
        assert reader.dimensions() == []
        assert registry.lookup(root, object) is None
        with pytest.raises(DimensionNotFoundError):
            reader.root_hash("main")

        assert await reader.get("main", "k") == 1
        assert reader.dimensions() == ["main"]
        assert reader.root_hash("main") == root

        assert await redis_backend.client.delete(f"{redis_backend.prefix}dim:main") == 1
        assert await reader.has_dimension("main") is True
        assert reader.dimensions() == ["main"]
        assert await Store[int](redis_backend).has_dimension("main") is False


async def test_reader_sees_updates_only_after_reload(
    redis_backend: RedisBackend[Redis], redis_peer: RedisBackend[Redis]
) -> None:
    a, b = Process(), Process()
    with a:
        writer = Store[object](redis_backend)
        await writer.create_dimension("main")
        await writer.set("main", "k", {"version": 1})
    with b:
        assert registry.lookup(hash_of({"version": 1}), object) is None
        reader = Store[object](redis_peer)
        assert await reader.get("main", "k") == {"version": 1}
        original_root = reader.root_hash("main")
    with a:
        await writer.set("main", "k", {"version": 2})
    with b:
        assert await reader.get("main", "k") == {"version": 1}
        assert reader.root_hash("main") == original_root
        assert registry.lookup(hash_of({"version": 2}), object) is None
        await reader.reload("main")
        assert await reader.get("main", "k") == {"version": 2}
        assert reader.root_hash("main") == writer.root_hash("main")


async def test_change_to_several_keys_is_read_by_another_process(
    redis_backend: RedisBackend[Redis], redis_peer: RedisBackend[Redis]
) -> None:
    with Process():
        writer = Store[object](redis_backend)
        await writer.create_dimension("main")
        await writer.set_many("main", {"a": 0, "b": 0, "c": 0})
        result = await writer.set_many("main", {"a": {"kinds": [1, 2]}, "d": {"new": True}}, delete=["c"])
        assert result == {"a": {"kinds": [1, 2]}, "d": {"new": True}}
        root = writer.root_hash("main")
    assert await redis_peer.get_root("main") == root
    with Process():
        assert registry.lookup(hash_of({"kinds": [1, 2]}), object) is None
        reader = Store[object](redis_peer)
        assert await reader.get("main", "a") == {"kinds": [1, 2]}
        assert await reader.get("main", "b") == 0
        assert await reader.get("main", "d") == {"new": True}
        with pytest.raises(KeyNotFoundError):
            await reader.get("main", "c")
        assert reader.root_hash("main") == root


async def test_failed_change_to_several_keys_keeps_the_redis_root(redis_backend: RedisBackend[Redis]) -> None:
    with Process():
        store = Store[object](redis_backend)
        await store.create_dimension("main")
        await store.set_many("main", {"a": 0, "b": 0})
        before = await redis_backend.get_root("main")
        assert before == store.root_hash("main")
        with pytest.raises(KeyNotFoundError):
            await store.set_many("main", {"a": 1}, delete=["missing"])
        assert await redis_backend.get_root("main") == before
        assert store.root_hash("main") == before


async def test_dimension_copy_remains_independent(
    redis_backend: RedisBackend[Redis], redis_peer: RedisBackend[Redis]
) -> None:
    with Process():
        writer = Store[object](redis_backend)
        await writer.create_dimension("main")
        await writer.set("main", "k", {"site": "paris"})
        await writer.create_dimension("branch", source="main")
        assert writer.root_hash("branch") == writer.root_hash("main")
        await writer.set("branch", "k", {"site": "lyon"})
        await writer.set("main", "only-main", 1)
    with Process():
        assert registry.lookup(hash_of({"site": "paris"}), object) is None
        assert registry.lookup(hash_of({"site": "lyon"}), object) is None
        reader = Store[object](redis_peer)
        assert await reader.get("main", "k") == {"site": "paris"}
        assert await reader.get("branch", "k") == {"site": "lyon"}
        assert await reader.keys("branch") == ["k"]
        assert set(await reader.keys("main")) == {"k", "only-main"}


async def test_missing_child_calls_loader_and_repairs_redis(
    redis_backend: RedisBackend[Redis], redis_peer: RedisBackend[Redis]
) -> None:
    value = {"outer": {"child": [7]}}
    child_hash = hash_of({"child": [7]})
    with Process():
        writer = Store[object](redis_backend)
        await writer.create_dimension("main")
        await writer.set("main", "k", value)
    assert await redis_backend.client.delete(f"{redis_backend.prefix}node:{child_hash.hex()}") == 1
    root_data, child_data = await redis_backend.get_many([hash_of(value), child_hash])
    assert root_data is not None
    assert child_data is None
    calls: list[tuple[str, str]] = []

    async def loader(dimension: str, key: str) -> object:
        calls.append((dimension, key))
        return value

    with Process():
        assert registry.lookup(hash_of(value), object) is None
        reader = Store[object](redis_peer, loader=loader)
        assert await reader.get("main", "k") == value
        assert await reader.get("main", "k") == value
        assert calls == [("main", "k")]
    assert (await redis_backend.get_many([child_hash]))[0] is not None
    with Process():
        assert registry.lookup(hash_of(value), object) is None
        assert await Store[object](redis_backend).get("main", "k") == value
    assert calls == [("main", "k")]


async def test_missing_value_without_loader_raises_cache_miss(
    redis_backend: RedisBackend[Redis], redis_peer: RedisBackend[Redis]
) -> None:
    value = {"lost": [42]}
    with Process():
        writer = Store[object](redis_backend)
        await writer.create_dimension("main")
        await writer.set("main", "k", value)
    assert await redis_backend.client.delete(f"{redis_backend.prefix}node:{hash_of(value).hex()}") == 1
    with Process():
        assert registry.lookup(hash_of(value), object) is None
        with pytest.raises(CacheMissError) as error:
            await Store[object](redis_peer).get("main", "k")
        assert error.value.dimension == "main"
        assert error.value.key == "k"


@pytest.mark.parametrize("previously_created", [False, True], ids=["unknown", "lost-root"])
async def test_missing_dimension_does_not_call_loader(
    redis_backend: RedisBackend[Redis], redis_peer: RedisBackend[Redis], previously_created: bool
) -> None:
    if previously_created:
        with Process():
            writer = Store[object](redis_backend)
            await writer.create_dimension("main")
            await writer.set("main", "k", {"exists": [1]})
        assert await redis_backend.client.delete(f"{redis_backend.prefix}dim:main") == 1
    calls: list[tuple[str, str]] = []

    async def loader(dimension: str, key: str) -> object:
        calls.append((dimension, key))
        return {"unexpected": True}

    with Process(), pytest.raises(DimensionNotFoundError, match="main"):
        await Store[object](redis_peer, loader=loader).get("main", "k")
    assert calls == []
