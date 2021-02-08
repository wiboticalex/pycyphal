# Copyright (C) 2021  UAVCAN Consortium  <uavcan.org>
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from __future__ import annotations
from fnmatch import fnmatchcase
from typing import List, TypeVar, Optional, Iterator, Union
import logging
import pyuavcan
from . import storage
from ._value import RelaxedValue, ValueProxy, Value


PrimitiveType = TypeVar("PrimitiveType", bound=pyuavcan.dsdl.CompositeObject)


class MissingRegisterError(KeyError):
    """
    Raised when the user attempts to access a register that is not defined,
    and the requested operation is not going to create it.
    """


class ValueWithFlags(ValueProxy):
    """
    This is like :class:`ValueProxy` but extended with register flags.
    """

    def __init__(self, msg: Value, mutable: bool, persistent: bool) -> None:
        super().__init__(msg)
        self._mutable = mutable
        self._persistent = persistent

    @property
    def mutable(self) -> bool:
        return self._mutable

    @property
    def persistent(self) -> bool:
        return self._persistent

    def __repr__(self) -> str:
        return pyuavcan.util.repr_attributes(self, self.value, mutable=self.mutable, persistent=self.persistent)


class Repository:
    def __init__(self, backend: storage.Storage) -> None:
        """
        :param backend: The storage backend to store the data in. The persistence flag is inherited from it.
        """
        self._storage = backend

    def close(self) -> None:
        """
        Closes the storage instance. Further access may no longer be possible.
        """
        self._storage.close()

    def keys(self) -> List[str]:
        """
        >>> from pyuavcan.application.register.storage.sqlite import SQLiteStorage
        >>> rs = Repository(SQLiteStorage())
        >>> rs.create("b", Value())
        >>> rs.create("a", Value())
        >>> rs.keys()  # Sorted lexicographically.
        ['a', 'b']
        """
        return self._storage.keys()

    def get_name_at_index(self, index: int) -> Optional[str]:
        """
        >>> from pyuavcan.application.register.storage.sqlite import SQLiteStorage
        >>> rs = Repository(SQLiteStorage())
        >>> rs.create("foo", Value())
        >>> rs.get_name_at_index(0)
        'foo'
        >>> rs.get_name_at_index(1) is None
        True
        """
        return self._storage.get_name_at_index(index)

    def get(self, name: str) -> Optional[ValueProxy]:
        """
        >>> from pyuavcan.application.register.storage.sqlite import SQLiteStorage
        >>> from uavcan.primitive.array import Bit_1_0
        >>> rs = Repository(SQLiteStorage())
        >>> rs.get("foo") is None                       # No such register --> None.
        True
        >>> rs.create("foo", Value(bit=Bit_1_0([True, False])))
        >>> e = rs.get("foo")
        >>> e.bools    # Use the proxy properties to automatically convert the register value to a native type.
        [True, False]
        >>> e.ints
        [1, 0]
        >>> e.floats
        [1.0, 0.0]
        >>> e.value.bit.value[0], e.value.bit.value[1]  # Or just access the underlying DSDL value directly.
        (True, False)
        """
        ent = self._storage.get(name)
        return ValueProxy(ent.value) if ent is not None else None

    def set(self, name: str, value: RelaxedValue) -> None:
        """
        Set if the register exists and the type of the value is matching or can be converted to the register's type.
        The mutability flag is ignored.

        :raises: :class:`MissingRegisterError` (subclass of :class:`KeyError`) if the register does not exist.
                 :class:`ValueConversionError` if the register exists but the value cannot be converted to its type.

        >>> from pyuavcan.application.register.storage.sqlite import SQLiteStorage
        >>> rs = Repository(SQLiteStorage())
        >>> rs.set("foo", True)                      # No such register, will fail. # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
        MissingRegisterError: 'foo'
        >>> from uavcan.primitive.array import Bit_1_0
        >>> rs.create("foo", Value(bit=Bit_1_0([True])))    # Create explicitly.
        >>> rs.get("foo").bools                             # Yup, created.
        [True]
        >>> rs.set("foo", False)                            # Now it can be set.
        >>> rs.get("foo").bools
        [False]
        >>> rs.set("foo", [True, False])                    # Wrong dimensionality. # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
        ValueConversionError: ...
        """
        e = self._storage.get(name)
        if not e:
            raise MissingRegisterError(name)
        converted = ValueProxy(e.value)
        converted.assign(value)
        self._storage.set(name, storage.Entry(converted.value, mutable=e.mutable))

    def create(self, name: str, value: Union[Value, ValueProxy], *, mutable: bool = True) -> None:
        """
        If the register exists, behaves like :meth:`set` and the flags are ignored. Otherwise it is created.
        """
        if isinstance(value, ValueProxy):
            value = value.value
        assert isinstance(value, Value)
        try:
            self.set(name, value)
        except MissingRegisterError:
            self._storage.set(name, storage.Entry(value, mutable=mutable))

    def access(self, name: str, value: Value) -> ValueWithFlags:
        """
        Perform the set/get transaction as defined by the RPC-service ``uavcan.register.Access``.
        No exceptions are raised. This method is intended for use with RPC-service implementations.

        >>> from pyuavcan.application.register.storage.sqlite import SQLiteStorage
        >>> rs = Repository(SQLiteStorage())
        >>> bool(rs.access("foo", Value()).value.empty)                       # No such register.
        True
        >>> from uavcan.primitive.array import Bit_1_0
        >>> rs.create("foo", Value(bit=Bit_1_0([True])))
        >>> v = rs.access("foo", Value())                                     # Read access.
        >>> (v.bools, v.mutable, v.persistent)
        ([True], True, False)
        >>> rs.access("foo", Value(bit=Bit_1_0([False]))).bools               # Write access.
        [False]
        """
        e = self._storage.get(name)
        if not e:
            return ValueWithFlags(Value(), False, False)
        converted = ValueProxy(e.value)
        try:
            converted.assign(value)
        except ValueError:
            pass
        else:
            if e.mutable:
                e = storage.Entry(converted.value, mutable=e.mutable)
                self._storage.set(name, e)
        return ValueWithFlags(e.value, mutable=e.mutable, persistent=self._storage.persistent)

    def delete(self, wildcard: str) -> None:
        """
        Remove all registers that match the specified wildcard. Matching is case-sensitive.

        >>> from pyuavcan.application.register.storage.sqlite import SQLiteStorage
        >>> rs = Repository(SQLiteStorage())
        >>> rs.create("foo.bar", Value())
        >>> rs.create("foo.baz", Value())
        >>> rs.create("zoo.bar", Value())
        >>> rs.delete("foo.*")
        >>> rs.keys()
        ['zoo.bar']
        """
        names = [n for n in self.keys() if fnmatchcase(n, wildcard)]
        _logger.debug("Deleting %d registers matching %r: %r", len(names), wildcard, names)
        self._storage.delete(names)

    def __getitem__(self, item: str) -> ValueProxy:
        """
        Like :meth:`get`, but if the register is missing it raises :class:`MissingRegisterError`
        (subclass of :class:`KeyError`) instead of returning None.

        >>> from pyuavcan.application.register.storage.sqlite import SQLiteStorage
        >>> from uavcan.primitive.array import Bit_1_0
        >>> rs = Repository(SQLiteStorage())
        >>> rs["foo"]                                           # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
        MissingRegisterError: 'foo'
        >>> rs.create("foo", Value(bit=Bit_1_0([True])))
        >>> rs["foo"].bools
        [True]
        >>> rs["foo"].ints
        [1]
        >>> rs["foo"].floats
        [1.0]
        """
        e = self.get(item)
        if e is None:
            raise MissingRegisterError(item)
        return e

    def __iter__(self) -> Iterator[str]:
        """
        >>> from pyuavcan.application.register.storage.sqlite import SQLiteStorage
        >>> rs = Repository(SQLiteStorage())
        >>> rs.create("b", Value())
        >>> rs.create("a", Value())
        >>> list(rs)
        ['a', 'b']
        """
        return iter(self.keys())

    def __len__(self) -> int:
        """
        >>> from pyuavcan.application.register.storage.sqlite import SQLiteStorage
        >>> rs = Repository(SQLiteStorage())
        >>> rs.create("b", Value())
        >>> rs.create("a", Value())
        >>> len(rs)
        2
        """
        return self._storage.count()

    def __repr__(self) -> str:
        return pyuavcan.util.repr_attributes(self, self._storage, count=len(self))


_logger = logging.getLogger(__name__)
