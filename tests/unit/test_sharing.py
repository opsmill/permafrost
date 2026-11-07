"""Scenarios of specs/dimension-sharing/spec.md."""

import fakeredis
import pytest

from permafrost import DimensionNotFoundError, MemoryBackend, Store, hash_of, keymap
from permafrost.backends.redis import RedisBackend

from ..process import Process


@pytest.fixture
def backend():
    return MemoryBackend()


@pytest.fixture
def a():
    return Process()


@pytest.fixture
def b():
    return Process()


async def test_order_of_writes(backend, a):
    with a:
        store = Store(backend)
        await store.create_dimension("main")
        backend.log.clear()
        await store.set("main", "k", {"order": 1})
    assert backend.log == ["set_many", "set_root"]


class FailingBackend(MemoryBackend):
    fail = False

    async def set_many(self, nodes):
        if self.fail:
            raise ConnectionError("external cache unavailable")
        await super().set_many(nodes)


async def test_failed_write_leaves_the_dimension_unchanged(a):
    backend = FailingBackend()
    with a:
        store = Store(backend)
        await store.create_dimension("main")
        await store.set("main", "k", {"v": 1})
        before = store.root_hash("main")
        backend.fail = True
        with pytest.raises(ConnectionError):
            await store.set("main", "k", {"v": 2})
        assert store.root_hash("main") == before
        assert backend.roots["main"] == before
        assert await store.get("main", "k") == {"v": 1}
        backend.fail = False
        await store.set("main", "k", {"v": 2})
        assert backend.roots["main"] == store.root_hash("main")


async def test_replace_a_dimension_writes_only_the_root_hash(backend, a):
    with a:
        store = Store(backend)
        await store.create_dimension("main")
        for i in range(200):
            await store.set("main", f"key-{i}", {"id": i})
        await store.create_dimension("candidate", source="main")
        await store.set("candidate", "key-0", {"id": 0, "changed": True})
        backend.log.clear()
        await store.replace_dimension("main", "candidate")
        assert backend.log == ["set_root"]
        assert backend.roots["main"] == store.root_hash("candidate")


async def test_other_process_reads_the_replaced_dimension(backend, a, b):
    with a:
        store_a = Store(backend)
        await store_a.create_dimension("main")
        await store_a.set("main", "k", {"v": 1})
    with b:
        store_b = Store(backend)
        assert await store_b.get("main", "k") == {"v": 1}
    with a:
        await store_a.create_dimension("candidate", source="main")
        await store_a.set("candidate", "k", {"v": 2})
        await store_a.replace_dimension("main", "candidate")
    with b:
        assert await store_b.get("main", "k") == {"v": 1}
        await store_b.reload("main")
        assert await store_b.get("main", "k") == {"v": 2}
        assert await Store(backend).get("main", "k") == {"v": 2}


async def test_replace_a_dimension_held_only_in_the_external_cache(backend, a, b):
    with a:
        store_a = Store(backend)
        await store_a.create_dimension("main")
        await store_a.set("main", "k", {"v": 1})
    with b:
        store_b = Store(backend)
        await store_b.create_dimension("candidate")
        await store_b.set("candidate", "k", {"v": 2})
        await store_b.replace_dimension("main", "candidate")
        assert backend.roots["main"] == store_b.root_hash("candidate")
        assert await store_b.get("main", "k") == {"v": 2}


async def test_replace_a_dimension_that_no_process_wrote(backend, a):
    with a:
        store = Store(backend)
        await store.create_dimension("candidate")
        with pytest.raises(DimensionNotFoundError):
            await store.replace_dimension("main", "candidate")
        assert "main" not in backend.roots


async def test_second_process_reads_a_key(backend, a, b):
    with a:
        store_a = Store(backend)
        await store_a.create_dimension("main")
        await store_a.set("main", "k", {"from": "process A", "items": [1, {"x": 2}]})
    with b:
        store_b = Store(backend)
        gets = backend.get_requests
        assert await store_b.get("main", "k") == {"from": "process A", "items": [1, {"x": 2}]}
        assert backend.get_requests > gets
        assert store_b.root_hash("main") == store_a.root_hash("main")


async def test_second_process_reads_many_keys(backend, a, b):
    with a:
        store_a = Store(backend)
        await store_a.create_dimension("main")
        for i in range(2000):
            await store_a.set("main", f"key-{i}", {"id": i})
    with b:
        store_b = Store(backend)
        assert len(await store_b.keys("main")) == 2000
        assert await store_b.get("main", "key-1999") == {"id": 1999}


async def test_unknown_dimension(backend, b):
    with b:
        store = Store(backend)
        with pytest.raises(DimensionNotFoundError):
            await store.get("never-written", "k")


async def test_key_map_node_lost_by_the_external_cache(backend, a, b):
    with a:
        store_a = Store(backend)
        await store_a.create_dimension("main")
        for i in range(500):
            await store_a.set("main", f"key-{i}", {"id": i})
        root = store_a._dims["main"]
        lost = next(n for n in keymap.nodes(root) if n is not root)
        backend.drop([lost._pf_hash])

    calls = []

    async def loader(d, k):
        calls.append(k)

    with b:
        store_b = Store(backend, loader=loader)
        with pytest.raises(DimensionNotFoundError):
            await store_b.get("main", "key-1")
        assert calls == []


async def test_root_hash_lost_by_the_external_cache(backend, a, b):
    with a:
        store_a = Store(backend)
        await store_a.create_dimension("main")
        await store_a.set("main", "k", 1)
    backend.roots.clear()
    with b, pytest.raises(DimensionNotFoundError):
        await Store(backend).get("main", "k")


async def test_root_hash_that_is_not_a_key_map(backend, a, b):
    with a:
        store_a = Store(backend)
        await store_a.create_dimension("main")
        await store_a.set("main", "k", {"a": 1})
        backend.roots["main"] = hash_of({"a": 1})
    with b, pytest.raises(DimensionNotFoundError):
        await Store(backend).get("main", "k")


async def test_pick_up_another_process_change(backend, a, b):
    with a:
        store_a = Store(backend)
        await store_a.create_dimension("main")
        await store_a.set("main", "k", {"v": 1})
    with b:
        store_b = Store(backend)
        assert await store_b.get("main", "k") == {"v": 1}
    with a:
        await store_a.set("main", "k", {"v": 2})
    with b:
        await store_b.reload("main")
        assert await store_b.get("main", "k") == {"v": 2}
        assert store_b.root_hash("main") == store_a.root_hash("main")


async def test_no_reload(backend, a, b):
    with a:
        store_a = Store(backend)
        await store_a.create_dimension("main")
        await store_a.set("main", "k", {"v": 1})
    with b:
        store_b = Store(backend)
        assert await store_b.get("main", "k") == {"v": 1}
    with a:
        await store_a.set("main", "k", {"v": 2})
    with b:
        assert await store_b.get("main", "k") == {"v": 1}


async def test_reload_without_a_change_or_a_backend(backend, a):
    with a:
        store = Store(backend)
        await store.create_dimension("main")
        root = store._dims["main"]
        await store.reload("main")
        assert store._dims["main"] is root
        local = Store()
        await local.create_dimension("main")
        await local.reload("main")


async def test_reload_a_dimension_that_the_external_cache_lost(backend, a):
    with a:
        store = Store(backend)
        await store.create_dimension("main")
        backend.roots.clear()
        with pytest.raises(DimensionNotFoundError):
            await store.reload("main")


async def test_two_processes_change_the_same_dimension(backend, a, b):
    with a:
        store_a = Store(backend)
        await store_a.create_dimension("main")
    with b:
        store_b = Store(backend)
        await store_b.keys("main")
    with a:
        await store_a.set("main", "from-a", 1)
    with b:
        await store_b.set("main", "from-b", 2)
        assert backend.roots["main"] == store_b.root_hash("main")
    with Process():
        reader = Store(backend)
        assert await reader.keys("main") == ["from-b"]


async def test_create_a_dimension_that_another_process_created(backend, a, b):
    from permafrost import DimensionExistsError

    with a:
        await Store(backend).create_dimension("main")
    with b, pytest.raises(DimensionExistsError):
        await Store(backend).create_dimension("main")


async def test_root_hash_entry_in_redis(a):
    backend = RedisBackend(fakeredis.FakeAsyncRedis(), prefix="share:")
    with a:
        store = Store(backend)
        await store.create_dimension("main")
        await store.set("main", "k", {"v": 1})
    assert await backend.client.ttl("share:dim:main") == -1
