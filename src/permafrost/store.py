"""Dimensions, the value cache, the external cache, the loader and cleanup."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import weakref
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import cast

from . import codec, keymap, registry
from .backends.base import Backend
from .diff import ADDED, REMOVED, DiffEntry, diff
from .errors import CacheMissError, DimensionExistsError, DimensionNotFoundError, KeyNotFoundError
from .keymap import Kind
from .nodes import KMBranch
from .types import build, children, encode_node, estimate_size, freeze, hash_of, is_scalar, node_hash, walk

logger = logging.getLogger(__name__)

# An async function that returns the value of a key in a dimension, from the application's own storage.
type Loader[V] = Callable[[str, str], Awaitable[V]]


class _Missing(Enum):
    """Marks a value or node that no cache holds. ``None`` cannot, because it is a valid value."""

    MISSING = 0


_MISSING = _Missing.MISSING


@dataclass(frozen=True, slots=True)
class KeyChange:
    """A key whose value differs between two dimensions."""

    key: str
    kind: Kind


class Store[V]:
    """Holds dimensions of keys and values, and the caches behind them.

    ``V`` is the type of the values, as the application declares it, for
    example ``Store[Device]``. Declare it with a model, a scalar type or a
    read-only abstract type such as ``Mapping[str, int]``: the store holds and
    returns the read-only form of each value.

    ``backend`` is the optional external cache. ``loader`` is an ``async``
    function that the store awaits with a dimension name and a key when a
    value is in no cache. Cleanup evicts values that nobody has read for
    ``idle_time`` seconds when ``eviction`` is on, which is the default only
    with a backend, and evicts the values read least recently while the
    estimated memory of the cached values is above ``memory_limit`` bytes.

    A store is not thread-safe. Use it from one thread and one event loop.
    """

    def __init__(
        self,
        backend: Backend | None = None,
        loader: Loader[V] | None = None,
        *,
        idle_time: float = 3600.0,
        eviction: bool | None = None,
        memory_limit: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a store with no dimension.

        With a backend, the store registers itself as a node listener, so it knows which nodes it still has to write.
        """
        self.backend = backend
        self.loader = loader
        self.idle_time = idle_time
        self.eviction = backend is not None if eviction is None else eviction
        self.memory_limit = memory_limit
        self._clock = clock
        self._dims: dict[str, KMBranch] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Value cache: value hash → value root, in order of last read, oldest first.
        self._values: dict[bytes, object] = {}
        self._read_at: dict[bytes, float] = {}
        self._sizes: dict[bytes, int] = {}
        self._total_size = 0
        self._read_since_cleanup: set[bytes] = set()
        # Nodes created in this process that the backend may not hold yet.
        self._pending: weakref.WeakValueDictionary[bytes, object] = weakref.WeakValueDictionary()
        self._cleanup_task: asyncio.Task[None] | None = None
        if backend is not None:
            for h, node in registry.items():
                self._pending[h] = node
            registry.add_listener(self)

    def node_created(self, h: bytes, node: object) -> None:
        """Record a node created in this process as one to write to the backend."""
        self._pending[h] = node

    # ------------------------------------------------------------------
    # Dimensions

    async def create_dimension(self, name: str, source: str | None = None) -> None:
        """Create a dimension, empty or with the keys and values of ``source``.

        The new dimension shares every node with ``source``: this copies one hash.
        """
        async with self._lock(name):
            if name in self._dims or (self.backend is not None and await self.backend.get_root(name) is not None):
                raise DimensionExistsError(name)
            root = keymap.EMPTY if source is None else await self._root(source)
            await self._commit(name, root)

    async def replace_dimension(self, name: str, source: str) -> None:
        """Replace the keys and values of the existing dimension ``name`` with those of ``source``.

        ``name`` then shares every node with ``source``: this copies one hash
        and commits it in one step, so readers of ``name`` get either the
        previous content or the content of ``source``, never part of each.
        Later changes to either dimension do not affect the other.
        """
        async with self._lock(name):
            current = self._dims.get(name)
            if current is None and (self.backend is None or await self.backend.get_root(name) is None):
                raise DimensionNotFoundError(name)
            await self._commit(name, await self._root(source), previous=current)

    def root_hash(self, dimension: str) -> bytes:
        """Return the root hash of a dimension held in memory.

        Two dimensions with the same keys and values have the same root hash.
        """
        root = self._dims.get(dimension)
        if root is None:
            raise DimensionNotFoundError(dimension, "is not in memory")
        return root._pf_hash

    async def keys(self, dimension: str) -> list[str]:
        """Return the keys of a dimension, in no defined order, without reading any value."""
        return list(keymap.keys(await self._root(dimension)))

    async def reload(self, dimension: str) -> None:
        """Switch a dimension to the root hash that the external cache holds now.

        Use it to pick up changes that another process made. Without a backend it does nothing.
        """
        if self.backend is None:
            return
        async with self._lock(dimension):
            h = await self.backend.get_root(dimension)
            if h is None:
                raise DimensionNotFoundError(dimension)
            current = self._dims.get(dimension)
            if current is not None and current._pf_hash == h:
                return
            self._dims[dimension] = await self._fetch_root(dimension, h)

    async def diff_keys(self, a: str, b: str) -> list[KeyChange]:
        """Return the keys whose values differ from dimension ``a`` to dimension ``b``, sorted by key."""
        changes = keymap.diff(await self._root(a), await self._root(b))
        return [KeyChange(key, kind) for key, kind in sorted(changes)]

    # ------------------------------------------------------------------
    # Values

    async def get(self, dimension: str, key: str) -> V:
        """Return the value stored under ``key`` in ``dimension``."""
        root = await self._root(dimension)
        vh = keymap.get(root, key)
        if vh is None:
            raise KeyNotFoundError(dimension, key)
        return self._typed(await self._value(dimension, key, vh))

    async def set(self, dimension: str, key: str, value: V) -> V:
        """Store ``value`` under ``key`` in ``dimension`` and return its read-only form."""
        frozen = self._typed(freeze(value))
        vh = hash_of(frozen)
        async with self._lock(dimension):
            root = await self._root(dimension)
            await self._commit(dimension, keymap.set(root, key, vh), (frozen,), previous=root)
        self._cache(vh, frozen)
        return frozen

    async def delete(self, dimension: str, key: str) -> None:
        """Delete ``key`` from ``dimension``. Other dimensions keep it."""
        async with self._lock(dimension):
            root = await self._root(dimension)
            if keymap.get(root, key) is None:
                raise KeyNotFoundError(dimension, key)
            await self._commit(dimension, keymap.delete(root, key))

    async def update(self, dimension: str, key: str, change: Callable[[V], V | Awaitable[V]]) -> V:
        """Replace the value under ``key`` with ``change(value)`` and return the new value.

        ``change`` is usually a call to ``set_in``, ``delete_in`` or ``merge``.
        It can also be an ``async`` function.
        """
        async with self._lock(dimension):
            root = await self._root(dimension)
            vh = keymap.get(root, key)
            if vh is None:
                raise KeyNotFoundError(dimension, key)
            current = self._typed(await self._value(dimension, key, vh, locked=True))
            result = change(current)
            if inspect.isawaitable(result):
                result = await result
            new = self._typed(freeze(result))
            nh = hash_of(new)
            await self._commit(dimension, keymap.set(root, key, nh), (new,), previous=root)
        self._cache(nh, new)
        return new

    async def diff(self, a: str, b: str, key: str) -> list[DiffEntry]:
        """Return the diff of the value under ``key`` from dimension ``a`` to dimension ``b``.

        When only one dimension holds the key, the diff is one entry for the whole value.
        """
        ha = keymap.get(await self._root(a), key)
        hb = keymap.get(await self._root(b), key)
        if ha is None and hb is None:
            raise KeyNotFoundError(a, key)
        if ha == hb:
            return []
        old = None if ha is None else await self._value(a, key, ha)
        new = None if hb is None else await self._value(b, key, hb)
        if ha is None:
            return [DiffEntry((), ADDED, None, new)]
        if hb is None:
            return [DiffEntry((), REMOVED, old, None)]
        return diff(old, new)

    # ------------------------------------------------------------------
    # Cleanup

    async def cleanup(self) -> int:
        """Evict idle values and values above the memory limit. Return the number of values evicted.

        With a backend, it also extends the expiry time of the values read
        since the previous cleanup and of the key maps of the dimensions in memory.
        """
        evicted = 0
        if self.eviction:
            now = self._clock()
            for h in list(self._values):
                if now - self._read_at[h] <= self.idle_time:
                    break
                self._evict(h)
                evicted += 1
        if self.memory_limit is not None:
            while self._total_size > self.memory_limit and self._values:
                self._evict(next(iter(self._values)))
                evicted += 1
        if self.backend is not None:
            await self._extend_expiry()
        return evicted

    def start_cleanup(self, interval: float) -> asyncio.Task[None]:
        """Run ``cleanup()`` every ``interval`` seconds as a task in the running event loop.

        Stop it with ``stop_cleanup()`` or by cancelling the returned task.
        """
        if self._cleanup_task is not None and not self._cleanup_task.done():
            raise RuntimeError("the cleanup task is already running")

        async def run() -> None:
            """Await ``cleanup()`` every ``interval`` seconds and log any error, so the task keeps running."""
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.cleanup()
                except Exception:
                    logger.exception("Permafrost cleanup failed")

        self._cleanup_task = asyncio.get_running_loop().create_task(run(), name="permafrost-cleanup")
        return self._cleanup_task

    def stop_cleanup(self) -> None:
        """Stop the task that ``start_cleanup()`` started."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None

    @property
    def cached_values(self) -> int:
        """Number of values held in the value cache."""
        return len(self._values)

    @property
    def cached_size(self) -> int:
        """Estimated memory of the cached values, in bytes. Counted only when a memory limit is set."""
        return self._total_size

    # ------------------------------------------------------------------
    # Internals

    def _typed(self, value: object) -> V:
        """Return ``value`` with the type ``V`` that the application declared."""
        # ``V`` is the application's declaration of what it stores under its
        # keys. The caches hold nodes without a type, and ``freeze()`` cannot
        # map a type parameter to its read-only form, so every value that the
        # store returns gets the declared type here, and only here.
        return cast(V, value)

    def _lock(self, dimension: str) -> asyncio.Lock:
        """Return the lock of a dimension, creating it on first use."""
        lock = self._locks.get(dimension)
        if lock is None:
            lock = self._locks[dimension] = asyncio.Lock()
        return lock

    def _cache(self, h: bytes, value: object) -> None:
        """Put a value in the value cache as the most recently read one.

        With a memory limit, the estimated size of a new value is added to the
        total. With a backend, the value is recorded as read since the previous
        cleanup.
        """
        if h in self._values:
            del self._values[h]
            del self._read_at[h]
        elif self.memory_limit is not None:
            size = estimate_size(value)
            self._sizes[h] = size
            self._total_size += size
        self._values[h] = value
        self._read_at[h] = self._clock()
        if self.backend is not None:
            self._read_since_cleanup.add(h)

    def _evict(self, h: bytes) -> None:
        """Remove a value from the value cache and subtract its estimated size."""
        del self._values[h]
        del self._read_at[h]
        self._total_size -= self._sizes.pop(h, 0)

    async def _root(self, dimension: str) -> KMBranch:
        """Return the key map root of a dimension.

        When the dimension is not in memory, read its root hash from the
        backend and fetch its key map. Raise ``DimensionNotFoundError`` when
        neither holds it.
        """
        root = self._dims.get(dimension)
        if root is not None:
            return root
        if self.backend is None:
            raise DimensionNotFoundError(dimension)
        h = await self.backend.get_root(dimension)
        if h is None:
            raise DimensionNotFoundError(dimension)
        return self._dims.setdefault(dimension, await self._fetch_root(dimension, h))

    async def _fetch_root(self, dimension: str, h: bytes) -> KMBranch:
        """Fetch the key map with root hash ``h``. Raise ``DimensionNotFoundError`` when it is lost or not a key map."""
        root = await self._fetch(h)
        if root is _MISSING:
            raise DimensionNotFoundError(dimension, "has key map nodes that the external cache lost")
        if type(root) is not KMBranch:
            raise DimensionNotFoundError(dimension, "has a root hash that is not a key map in the external cache")
        return root

    async def _value(self, dimension: str, key: str, vh: bytes, *, locked: bool = False) -> object:
        """Return the value with hash ``vh`` stored under ``key`` in ``dimension``.

        Look in the value cache, then the registry, then the backend, then call
        the loader. When the loaded value has another hash, store it under
        ``key``, unless ``locked`` says that the caller holds the lock and
        stores its own result. Raise ``CacheMissError`` when no cache holds the
        value and there is no loader.
        """
        value = self._values.get(vh, _MISSING)
        if value is _MISSING:
            value = registry.lookup(vh, object)
            if value is None:
                value = _MISSING
        if value is _MISSING and self.backend is not None:
            value = await self._fetch(vh)
        if value is not _MISSING:
            self._cache(vh, value)
            return value
        if self.loader is None:
            raise CacheMissError(dimension, key)
        loaded = freeze(await self.loader(dimension, key))
        lh = hash_of(loaded)
        # Write every node of the loaded value: the external cache lost at least
        # one of them, and this process can still hold it from before.
        await self._send((loaded,), every_node=True)
        if not locked and lh != vh:
            # When locked, the caller holds the lock and stores its own result.
            async with self._lock(dimension):
                root = await self._root(dimension)
                if keymap.get(root, key) == vh:
                    await self._commit(dimension, keymap.set(root, key, lh), (loaded,), previous=root)
        self._cache(lh, loaded)
        return loaded

    async def _commit(
        self, dimension: str, root: KMBranch, values: Iterable[object] = (), *, previous: KMBranch | None = None
    ) -> None:
        """Write new nodes, then the root hash, then switch the dimension in memory. The lock must be held."""
        await self._send((root, *values))
        if root is previous:
            return
        if self.backend is not None:
            await self.backend.set_root(dimension, root._pf_hash)
        self._dims[dimension] = root

    async def _send(self, roots: Iterable[object], *, every_node: bool = False) -> None:
        """Write to the backend, in one request, the nodes of these trees that it may not hold.

        With ``every_node``, write every node of the trees.
        """
        if self.backend is None:
            return
        out: dict[bytes, bytes] = {}
        stack = list(roots)
        while stack:
            node = stack.pop()
            if is_scalar(node):
                out[hash_of(node)] = encode_node(node)
                continue
            h = node_hash(node)
            if h in out or (not every_node and self._pending.get(h) is None):
                continue
            out[h] = encode_node(node)
            stack.extend(children(node))
        if out:
            await self.backend.set_many(out)
            for h in out:
                self._pending.pop(h, None)

    async def _fetch(self, root_hash: bytes) -> object | _Missing:
        """Fetch a tree from the backend, one level at a time. Return ``_MISSING`` if any node is missing."""
        assert self.backend is not None
        payloads: dict[bytes, list[object]] = {}
        # Strong references to the nodes already in memory, so that they stay
        # in the registry until the tree is built.
        held: list[object] = []
        level = [root_hash]
        while level:
            need = []
            for h in dict.fromkeys(level):
                if h in payloads:
                    continue
                node = registry.lookup(h, object)
                if node is None:
                    need.append(h)
                else:
                    held.append(node)
            if not need:
                break
            found = await self.backend.get_many(need)
            level = []
            for h, data in zip(need, found):
                if data is None:
                    return _MISSING
                try:
                    payload = codec.unpack(data)
                except ValueError as exc:
                    logger.warning("cannot decode node %s from the external cache: %s", h.hex(), exc)
                    return _MISSING
                payloads[h] = payload
                level.extend(codec.refs_in(payload))
        if payloads:
            await self.backend.touch(list(payloads))
        built: dict[bytes, object] = {}

        def resolve(h: bytes) -> object:
            """Return the node with hash ``h``: from the registry, or built from its fetched payload."""
            node = registry.lookup(h, object)
            if node is not None:
                return node
            node = built.get(h, _MISSING)
            if node is _MISSING:
                node = built[h] = build(h, payloads[h], resolve)
            return node

        try:
            return resolve(root_hash)
        except (ImportError, AttributeError, KeyError, ValueError) as exc:
            logger.warning("cannot rebuild node %s from the external cache: %s", root_hash.hex(), exc)
            return _MISSING

    async def _extend_expiry(self) -> None:
        """Extend the expiry time of the values read since the previous cleanup and of the key maps in memory."""
        assert self.backend is not None
        hashes: set[bytes] = set()
        for h in self._read_since_cleanup:
            value = self._values.get(h, _MISSING)
            if value is _MISSING:
                continue
            if is_scalar(value):
                hashes.add(h)
            else:
                hashes.update(node_hash(n) for n in walk(value))
        self._read_since_cleanup.clear()
        stack: list[object] = list(self._dims.values())
        while stack:
            node = stack.pop()
            h = node_hash(node)
            if h in hashes:
                continue
            hashes.add(h)
            stack.extend(children(node))
        if hashes:
            await self.backend.touch(list(hashes))
