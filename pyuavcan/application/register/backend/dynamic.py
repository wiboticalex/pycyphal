# Copyright (C) 2021  UAVCAN Consortium  <uavcan.org>
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from __future__ import annotations
from typing import Tuple, Optional, Callable, Dict, List, Sequence
import logging
from . import Entry, BackendError, Backend, Value


GetSetPair = Tuple[
    Callable[[], Value],
    Optional[Callable[[Value], None]],
]


class DynamicBackend(Backend):
    """
    Register backend where register access is delegated to external getters and setters.
    It does not store values internally.
    """

    def __init__(self):
        self._reg: Dict[str, GetSetPair] = {}  # This dict is always sorted lexicographically by key!

    @property
    def location(self) -> str:
        return ""

    @property
    def persistent(self) -> bool:
        return False

    def count(self) -> int:
        return len(self._reg)

    def keys(self) -> List[str]:
        return list(self._reg.keys())

    def get_name_at_index(self, index: int) -> Optional[str]:
        try:
            return self.keys()[index]
        except LookupError:
            return None

    def get(self, name: str) -> Optional[Entry]:
        try:
            getter, setter = self._reg[name]
        except LookupError:
            _logger.debug("%r: Get %r -> (nothing)", self, name)
            return None
        try:
            value = getter()
        except Exception as ex:
            raise BackendError(f"Unhandled exception in getter for {name!r}: {ex}") from ex
        e = Entry(value, mutable=setter is not None)
        _logger.debug("%r: Get %r -> %r", self, name, e)
        return e

    def set(self, name: str, value: Value) -> None:
        """
        If the register does not exist or is not mutable (no setter), nothing will be done.
        """
        try:
            _, setter = self._reg[name]
        except LookupError:
            setter = None
        if setter is not None:
            _logger.debug("%r: Set %r <- %r", self, name, value)
            setter(value)
        else:
            _logger.debug("%r: Set %r not supported", self, name)

    def delete(self, names: Sequence[str]) -> None:
        _logger.debug("%r: Delete %r", self, names)
        for n in names:
            try:
                del self._reg[n]
            except LookupError:
                pass

    def close(self) -> None:
        self._reg.clear()

    def register(
        self,
        name: str,
        getter: Callable[[], Value],
        setter: Optional[Callable[[Value], None]] = None,
    ) -> None:
        """
        Add a new dynamic register. If such name is already registered, it is overwritten.
        If only getter is provided, the register will be treated as immutable.
        """
        items = list(self._reg.items())
        items.append((name, (getter, setter)))
        self._reg = dict(sorted(items, key=lambda x: x[0]))


_logger = logging.getLogger(__name__)


def _unittest_dyn() -> None:
    from uavcan.primitive import String_1_0 as String

    b = DynamicBackend()
    assert not b.persistent
    assert b.count() == 0
    assert b.keys() == []
    assert b.get("foo") is None
    assert b.get_name_at_index(0) is None
    b.delete(["foo"])

    bar = Value(string=String())

    def set_bar(v: Value) -> None:
        nonlocal bar
        bar = v

    b.register("foo", lambda: Value(string=String("Hello")))
    b.register("bar", lambda: bar, set_bar)
    assert b.count() == 2
    assert b.keys() == ["bar", "foo"]
    assert b.get_name_at_index(0) == "bar"
    assert b.get_name_at_index(1) == "foo"
    assert b.get_name_at_index(2) is None

    e = b.get("foo")
    assert e
    assert not e.mutable
    assert e.value.string
    assert e.value.string.value.tobytes().decode() == "Hello"

    e = b.get("bar")
    assert e
    assert e.mutable
    assert e.value.string
    assert e.value.string.value.tobytes().decode() == ""

    b.set("foo", Value(string=String("world")))
    b.set("bar", Value(string=String("world")))

    e = b.get("foo")
    assert e
    assert not e.mutable
    assert e.value.string
    assert e.value.string.value.tobytes().decode() == "Hello"

    e = b.get("bar")
    assert e
    assert e.mutable
    assert e.value.string
    assert e.value.string.value.tobytes().decode() == "world"

    b.delete(["foo"])
    assert b.count() == 1
    assert b.keys() == ["bar"]

    b.close()
    assert b.count() == 0
