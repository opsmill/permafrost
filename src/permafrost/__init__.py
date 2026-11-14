"""Permafrost stores deeply nested, read-only data across many dimensions, and stores each identical node once."""

from .backends import Backend, MemoryBackend
from .codec import Scalar
from .diff import DiffEntry, diff
from .errors import (
    CacheMissError,
    DimensionExistsError,
    DimensionNotFoundError,
    KeyNotFoundError,
    PathError,
    PermafrostError,
    ReadOnlyError,
    UnsupportedTypeError,
)
from .nodes import FrozenDict, FrozenList, FrozenModel, FrozenSet, FrozenTuple
from .ops import Path, Step, delete_in, merge, set_in
from .store import KeyChange, Loader, Snapshot, Store
from .types import TypeHandler, equal, freeze, hash_of, register_class, register_type

__all__ = [
    "Backend",
    "CacheMissError",
    "DiffEntry",
    "DimensionExistsError",
    "DimensionNotFoundError",
    "FrozenDict",
    "FrozenList",
    "FrozenModel",
    "FrozenSet",
    "FrozenTuple",
    "KeyChange",
    "KeyNotFoundError",
    "Loader",
    "MemoryBackend",
    "Path",
    "PathError",
    "PermafrostError",
    "ReadOnlyError",
    "Scalar",
    "Snapshot",
    "Step",
    "Store",
    "TypeHandler",
    "UnsupportedTypeError",
    "delete_in",
    "diff",
    "equal",
    "freeze",
    "hash_of",
    "merge",
    "register_class",
    "register_type",
    "set_in",
]
