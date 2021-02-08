# Copyright (C) 2021  UAVCAN Consortium  <uavcan.org>
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from __future__ import annotations
from fnmatch import fnmatchcase
from typing import List, TypeVar, Optional, Iterator
import logging
import pyuavcan
from . import backend
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
    """
    The register repository is the main access point for the application to its registers.
    It is a facade that provides user-friendly API on top of multiple underlying register backends
    (see :class:`backend.Backend`).
    """

    def __init__(self, *backends: backend.Backend) -> None:
        self._backends: List[backend.Backend] = []
        for b in backends:
            self.connect(b)

    def connect(self, b: backend.Backend) -> None:
        """
        Connect a new backend to this repository. Count, keys, and ordering will be invalidated.
        If a register exists in more than one repository, only the first copy will be used;
        however, the count will include all redundant registers.
        """
        self._backends.append(b)

    def close(self) -> None:
        """
        Closes all storage backends.
        """
        for b in self._backends:
            b.close()
        self._backends.clear()

    def keys(self) -> List[str]:
        """
        >>> from pyuavcan.application.register.backend.sqlite import SQLiteBackend
        >>> from pyuavcan.application.register.backend.dynamic import DynamicBackend
        >>> b0 = SQLiteBackend()
        >>> b0.set("c", Value())
        >>> b0.set("a", Value())
        >>> b1 = DynamicBackend()
        >>> b1.register("b", lambda: Value())
        >>> Repository(b0, b1).keys()  # Sorted lexicographically per backend.
        ['a', 'c', 'b']
        >>> Repository(b1, b0).keys()  # Sorted lexicographically per backend.
        ['b', 'a', 'c']
        """
        return [n for b in self._backends for n in b.keys()]

    def get_name_at_index(self, index: int) -> Optional[str]:
        """
        >>> from pyuavcan.application.register.backend.sqlite import SQLiteBackend
        >>> from pyuavcan.application.register.backend.dynamic import DynamicBackend
        >>> b0 = SQLiteBackend()
        >>> b0.set("c", Value())
        >>> b0.set("a", Value())
        >>> b1 = DynamicBackend()
        >>> b1.register("b", lambda: Value())
        >>> r = Repository(b0, b1)
        >>> r.get_name_at_index(0), r.get_name_at_index(1), r.get_name_at_index(2), r.get_name_at_index(3)
        ('a', 'c', 'b', None)
        """
        try:
            return self.keys()[index]  # This is hugely inefficient. Should iterate through backends instead.
        except LookupError:
            return None

    def get(self, name: str) -> Optional[ValueProxy]:
        """
        >>> from uavcan.primitive.array import Bit_1_0
        >>> from pyuavcan.application.register.backend.sqlite import SQLiteBackend
        >>> b0 = SQLiteBackend()
        >>> rs = Repository(b0)
        >>> rs.get("foo") is None                       # No such register --> None.
        True
        >>> b0.set("foo", Value(bit=Bit_1_0([True, False])))
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
        for b in self._backends:
            ent = b.get(name)
            if ent is not None:
                return ValueProxy(ent.value)
        return None

    def set(self, name: str, value: RelaxedValue) -> None:
        """
        Set if the register exists and the type of the value is matching or can be converted to the register's type.
        The mutability flag may be ignored depending on which backend the register is stored at.

        :raises: :class:`MissingRegisterError` (subclass of :class:`KeyError`) if the register does not exist.
                 :class:`ValueConversionError` if the register exists but the value cannot be converted to its type.

        >>> from uavcan.primitive.array import Bit_1_0
        >>> from pyuavcan.application.register.backend.sqlite import SQLiteBackend
        >>> from pyuavcan.application.register.backend.dynamic import DynamicBackend
        >>> b0 = SQLiteBackend()
        >>> b1 = DynamicBackend()
        >>> r = Repository(b0, b1)
        >>> r.set("foo", True)                      # No such register, will fail. # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
        MissingRegisterError: 'foo'
        >>> b0.set("foo", Value(bit=Bit_1_0([True])))       # Create a new register.
        >>> r.get("foo").bools                              # Yup, created.
        [True]
        >>> r.set("foo", False)                             # Now it can be set.
        >>> r.get("foo").bools
        [False]
        >>> r.set("foo", [True, False])                     # Wrong dimensionality. # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
        ValueConversionError: ...
        >>> my_dynamic_register = Value(bit=Bit_1_0([True, False, False]))
        >>> def set_my_dynamic_register(v: Value):
        ...     global my_dynamic_register
        ...     my_dynamic_register = v
        >>> b1.register("bar", lambda: my_dynamic_register, set_my_dynamic_register)
        >>> r.get("bar").bools
        [True, False, False]
        >>> r.set("bar", [0, 1.5, -5])     # The value type is converted automatically.
        >>> r.get("bar").bools
        [False, True, True]
        """
        for b in self._backends:
            e = b.get(name)
            if e is not None:
                c = ValueProxy(e.value)
                c.assign(value)
                b.set(name, c.value)
                break
        else:
            raise MissingRegisterError(name)

    def access(self, name: str, value: Value) -> ValueWithFlags:
        """
        Perform the set/get transaction as defined by the RPC-service ``uavcan.register.Access``.
        No exceptions are raised. This method is intended for use with the register RPC-service implementations
        (essentially, this method is the entire implementation, just bind it to the session and you're all set).

        >>> from uavcan.primitive import String_1_0
        >>> from uavcan.primitive.array import Bit_1_0
        >>> from pyuavcan.application.register.backend.sqlite import SQLiteBackend
        >>> from pyuavcan.application.register.backend.dynamic import DynamicBackend
        >>> b0 = SQLiteBackend()
        >>> b1 = DynamicBackend()
        >>> r = Repository(b0, b1)
        >>> bool(r.access("foo", Value()).value.empty)                  # No such register.
        True
        >>> b0.set("foo", Value(bit=Bit_1_0([True])))
        >>> b1.register("bar", lambda: Value(bit=Bit_1_0([False])))
        >>> v = r.access("foo", Value())                                # Read access.
        >>> (v.bools, v.mutable, v.persistent)
        ([True], True, False)
        >>> v = r.access("bar", Value())                                # Read access.
        >>> (v.bools, v.mutable, v.persistent)
        ([False], False, False)
        >>> r.access("foo", Value(bit=Bit_1_0([False]))).bools          # Write access.
        [False]
        >>> r.access("foo", Value(string=String_1_0("Hello"))).bools    # Write access, bad type ignored.
        [False]
        >>> r.access("bar", Value(bit=Bit_1_0([True]))).bools           # Write access, not writable.
        [False]
        """
        for b in self._backends:
            e = b.get(name)
            if e is None:
                continue
            if e.mutable and not value.empty:
                c = ValueProxy(e.value)
                try:
                    c.assign(value)
                except ValueError:
                    pass
                else:
                    b.set(name, c.value)
                    e = b.get(name)
            return ValueWithFlags(e.value, mutable=e.mutable, persistent=b.persistent)
        return ValueWithFlags(Value(), False, False)

    def delete(self, wildcard: str) -> None:
        """
        Remove registers that match the specified wildcard from all backends. Matching is case-sensitive.

        >>> from pyuavcan.application.register.backend.sqlite import SQLiteBackend
        >>> from pyuavcan.application.register.backend.dynamic import DynamicBackend
        >>> b0 = SQLiteBackend()
        >>> b0.set("foo.bar", Value())
        >>> b0.set("zoo.bar", Value())
        >>> b1 = DynamicBackend()
        >>> b1.register("foo.baz", lambda: Value())
        >>> r = Repository(b0, b1)
        >>> r.delete("foo.*")
        >>> r.keys()
        ['zoo.bar']
        """
        for b in self._backends:
            names = [n for n in b.keys() if fnmatchcase(n, wildcard)]
            _logger.debug("%r: Deleting %d registers matching %r from %r: %r", self, len(names), wildcard, b, names)
            b.delete(names)

    def __getitem__(self, item: str) -> ValueProxy:
        """
        Like :meth:`get`, but if the register is missing it raises :class:`MissingRegisterError`
        (subclass of :class:`KeyError`) instead of returning None.

        >>> from pyuavcan.application.register.backend.sqlite import SQLiteBackend
        >>> from uavcan.primitive.array import Bit_1_0
        >>> b0 = SQLiteBackend()
        >>> r = Repository(b0)
        >>> r["foo"]                                           # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
        MissingRegisterError: 'foo'
        >>> b0.set("foo", Value(bit=Bit_1_0([True])))
        >>> r["foo"].bools
        [True]
        >>> r["foo"].ints
        [1]
        >>> r["foo"].floats
        [1.0]
        """
        e = self.get(item)
        if e is None:
            raise MissingRegisterError(item)
        return e

    def __iter__(self) -> Iterator[str]:
        """
        >>> from pyuavcan.application.register.backend.sqlite import SQLiteBackend
        >>> from pyuavcan.application.register.backend.dynamic import DynamicBackend
        >>> b0 = SQLiteBackend()
        >>> b0.set("c", Value())
        >>> b0.set("a", Value())
        >>> b1 = DynamicBackend()
        >>> b1.register("b", lambda: Value())
        >>> list(Repository(b0, b1))
        ['a', 'c', 'b']
        """
        return iter(self.keys())

    def __len__(self) -> int:
        """
        >>> from pyuavcan.application.register.backend.sqlite import SQLiteBackend
        >>> from pyuavcan.application.register.backend.dynamic import DynamicBackend
        >>> b0 = SQLiteBackend()
        >>> b0.set("c", Value())
        >>> b0.set("a", Value())
        >>> b1 = DynamicBackend()
        >>> b1.register("b", lambda: Value())
        >>> len(Repository(b0, b1))
        3
        """
        return sum(x.count() for x in self._backends)

    def __repr__(self) -> str:
        return pyuavcan.util.repr_attributes(self, self._backends)


_logger = logging.getLogger(__name__)
