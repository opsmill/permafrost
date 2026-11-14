"""Scenarios of specs/cache-cleanup/spec.md."""

import asyncio
import threading

import pytest

from permafrost import CacheMissError, MemoryBackend, Store, hash_of

MINUTE = 60.0


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += minutes * MINUTE


@pytest.fixture
def clock():
    return Clock()


async def _store(clock, **kwargs):
    store = Store(clock=clock, idle_time=60 * MINUTE, **kwargs)
    await store.create_dimension("main")
    return store


async def test_no_cleanup_call(clock):
    threads = threading.active_count()
    tasks = len(asyncio.all_tasks())
    store = await _store(clock, eviction=True)
    await store.set("main", "k", {"a": 1})
    clock.advance(120)
    assert store.cached_values == 1
    assert threading.active_count() == threads
    assert len(asyncio.all_tasks()) == tasks


async def test_read_refreshes_the_time(clock):
    store = await _store(clock, eviction=True)
    await store.set("main", "k", {"refresh": 1})
    clock.advance(45)
    await store.get("main", "k")
    clock.advance(5)
    assert await store.cleanup() == 0
    assert store.cached_values == 1


async def test_idle_value_is_evicted(clock):
    store = await _store(clock, eviction=True)
    await store.set("main", "old", {"idle": 1})
    clock.advance(30)
    await store.set("main", "new", {"idle": 2})
    clock.advance(31)
    assert await store.cleanup() == 1
    assert hash_of({"idle": 1}) not in store._values
    assert hash_of({"idle": 2}) in store._values


async def test_read_after_eviction_with_an_external_cache(clock, collect):
    async def loader(d, k):
        raise AssertionError("loader must not be called")

    backend = MemoryBackend()
    store = await _store(clock, backend=backend, loader=loader)
    await store.set("main", "k", {"evicted-with-backend": [1, 2]})
    clock.advance(61)
    assert await store.cleanup() == 1
    collect()
    gets = backend.get_requests
    assert await store.get("main", "k") == {"evicted-with-backend": [1, 2]}
    assert backend.get_requests > gets


async def test_read_after_eviction_without_an_external_cache(clock, collect):
    calls = []

    async def loader(d, k):
        calls.append(k)
        return {"evicted-no-backend": 1}

    store = await _store(clock, loader=loader, eviction=True)
    await store.set("main", "k", {"evicted-no-backend": 1})
    clock.advance(61)
    await store.cleanup()
    collect()
    assert await store.get("main", "k") == {"evicted-no-backend": 1}
    assert calls == ["k"]


async def test_evict_one_of_two_values_that_share_a_node(clock, collect):
    store = await _store(clock, eviction=True)
    await store.set("main", "a", {"shared": {"x": [1, 2]}, "id": "a"})
    clock.advance(30)
    await store.set("main", "b", {"shared": {"x": [1, 2]}, "id": "b"})
    clock.advance(31)
    assert await store.cleanup() == 1
    collect()
    b = await store.get("main", "b")
    from permafrost import registry

    assert registry.lookup(hash_of(b["shared"]), object) is b["shared"]


async def test_default_without_an_external_cache(clock):
    store = await _store(clock)
    assert store.eviction is False
    await store.set("main", "k", {"default": 1})
    clock.advance(120)
    assert await store.cleanup() == 0
    assert store.cached_values == 1


async def test_default_with_an_external_cache(clock):
    store = await _store(clock, backend=MemoryBackend())
    assert store.eviction is True


async def test_above_the_limit(clock):
    store = await _store(clock, memory_limit=10**9)
    for i in range(5):
        await store.set("main", f"k{i}", {"payload": list(range(100)), "i": i})
        clock.advance(1)
    one = store.cached_size // 5
    # Read k0 so that k1 becomes the value read least recently.
    await store.get("main", "k0")
    store.memory_limit = store.cached_size - one
    assert await store.cleanup() == 1
    assert hash_of({"payload": list(range(100)), "i": 1}) not in store._values
    assert store.cached_size <= store.memory_limit
    store.memory_limit = 0
    await store.cleanup()
    assert store.cached_values == 0 and store.cached_size == 0


async def test_key_deleted(clock):
    store = await _store(clock, eviction=True)
    await store.set("main", "k", {"deleted": 1})
    await store.delete("main", "k")
    assert hash_of({"deleted": 1}) in store._values
    clock.advance(61)
    await store.cleanup()
    assert hash_of({"deleted": 1}) not in store._values


async def test_run_cleanup_at_an_interval(monkeypatch):
    store = Store()
    runs = []
    original = store.cleanup

    async def counting():
        runs.append(1)
        return await original()

    monkeypatch.setattr(store, "cleanup", counting)
    task = store.start_cleanup(0.01)
    with pytest.raises(RuntimeError):
        store.start_cleanup(0.01)
    await asyncio.sleep(0.1)
    assert len(runs) > 1
    store.stop_cleanup()
    await asyncio.sleep(0)
    assert task.cancelled()
    count = len(runs)
    await asyncio.sleep(0.05)
    assert len(runs) == count


async def test_interval_helper_survives_a_failed_cleanup(monkeypatch):
    store = Store()
    runs = []

    async def failing():
        runs.append(1)
        raise ConnectionError("redis down")

    monkeypatch.setattr(store, "cleanup", failing)
    task = store.start_cleanup(0.01)
    await asyncio.sleep(0.06)
    assert len(runs) > 1 and not task.done()
    task.cancel()


# A snapshot keeps its values in memory: scenarios of specs/dimension-snapshots/spec.md


async def test_snapshot_values_survive_cleanup_with_a_memory_limit(clock, collect):
    store = await _store(clock, memory_limit=1)
    await store.set_many("main", {f"k{i}": {"limit": i, "items": [i, i + 1]} for i in range(5)})
    snap = await store.snapshot("main")
    assert await store.cleanup() == 5
    assert store.cached_values == 0
    collect()
    for i in range(5):
        assert snap.get(f"k{i}") == {"limit": i, "items": [i, i + 1]}


async def test_snapshot_values_survive_eviction_by_idle_time(clock, collect):
    store = await _store(clock, eviction=True)
    await store.set_many("main", {"a": {"idle-snapshot": 1}, "b": {"idle-snapshot": 2}})
    snap = await store.snapshot("main")
    clock.advance(61)
    assert await store.cleanup() == 2
    collect()
    assert snap.get("a") == {"idle-snapshot": 1}
    assert snap.get("b") == {"idle-snapshot": 2}
    # The registry still finds the values that the snapshot holds.
    assert await store.get("main", "a") is snap.get("a")
    store._values.clear()
    store._read_at.clear()
    del snap
    collect()
    with pytest.raises(CacheMissError):
        await store.get("main", "a")


async def test_reads_from_a_snapshot_do_not_refresh_the_read_time(clock):
    store = await _store(clock, eviction=True)
    await store.set("main", "a", {"snapshot-read-time": 1})
    snap = await store.snapshot("main")
    clock.advance(61)
    snap.get("a")
    assert await store.cleanup() == 1
