# Copyright (C) 2021  UAVCAN Consortium  <uavcan.org>
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from __future__ import annotations
from typing import Union, Iterable, List, Any, Optional, no_type_check
import numpy
import pyuavcan
from pyuavcan.dsdl import get_attribute
from .backend import Value
from . import String, Unstructured, Bit
from . import Integer8, Integer16, Integer32, Integer64
from . import Natural8, Natural16, Natural32, Natural64
from . import Real16, Real32, Real64


class ValueConversionError(ValueError):
    """
    Raised when there is no known conversion between the argument and the specified register.
    """


VALUE_OPTION_NAMES = [x for x in dir(Value) if not x.startswith("_")]


class ValueProxy:
    """
    This a wrapper over the standard ``uavcan.register.Value`` with convenience accessors added that enable
    automatic conversion (with implicit casting) between native Python types and DSDL types.

    >>> from uavcan.primitive.array import Bit_1_0
    >>> from uavcan.primitive import String_1_0
    >>> from uavcan.primitive import Unstructured_1_0
    >>> p = ValueProxy(Value(bit=Bit_1_0([True, False])))
    >>> p.bools
    [True, False]
    >>> p.ints
    [1, 0]
    >>> p.floats
    [1.0, 0.0]
    >>> p.assign([0, 1.0])
    >>> p.bools
    [False, True]
    >>> p = ValueProxy(Value(bit=Bit_1_0([False])))
    >>> bool(p)
    False
    >>> int(p)
    0
    >>> float(p)
    0.0
    >>> p = ValueProxy(Value(string=String_1_0("Hello world!")))
    >>> str(p)
    'Hello world!'
    >>> bytes(p)
    b'Hello world!'
    >>> p.assign('Another string')
    >>> str(p)
    'Another string'
    >>> bytes(p)
    b'Another string'
    >>> p = ValueProxy(Value(unstructured=Unstructured_1_0(b"ab01")))
    >>> str(p)
    'ab01'
    >>> bytes(p)
    b'ab01'
    >>> p.assign("String implicitly converted to bytes")
    >>> bytes(p)
    b'String implicitly converted to bytes'
    """

    def __init__(self, msg: Value) -> None:
        from copy import copy

        self._value = copy(msg)

    @property
    def value(self) -> Value:
        """Access to the underlying standard DSDL type ``uavcan.register.Value``."""
        return self._value

    def assign(self, source: RelaxedValue) -> None:
        """
        Converts the value from the source into the type of the current instance, and updates this instance.
        If such conversion is not possible, :class:`ValueConversionError` is raised.
        """
        opt_to = _get_option_name(self._value)
        res = _do_convert(self._value, _strictify(source))
        if res is None:
            raise ValueConversionError(f"Source {source!r} cannot be assigned to {self!r}")
        assert _get_option_name(res) == opt_to
        self._value = res

    @property
    def floats(self) -> List[float]:
        """
        Converts the value to a list of floats, or raises :class:`ValueConversionError` if not possible.
        """

        def cast(a: Any) -> List[float]:
            return [float(x) for x in a.value]

        v = self._value
        # fmt: off
        if v.bit:       return cast(v.bit)
        if v.integer8:  return cast(v.integer8)
        if v.integer16: return cast(v.integer16)
        if v.integer32: return cast(v.integer32)
        if v.integer64: return cast(v.integer64)
        if v.natural8:  return cast(v.natural8)
        if v.natural16: return cast(v.natural16)
        if v.natural32: return cast(v.natural32)
        if v.natural64: return cast(v.natural64)
        if v.real16:    return cast(v.real16)
        if v.real32:    return cast(v.real32)
        if v.real64:    return cast(v.real64)
        # fmt: on
        raise ValueConversionError(f"{v!r} cannot be represented numerically")

    @property
    def ints(self) -> List[int]:
        """
        Converts the value to a list of ints, or raises :class:`ValueConversionError` if not possible.
        """
        return [round(x) for x in self.floats]

    @property
    def bools(self) -> List[bool]:
        """
        Converts the value to a list of bools, or raises :class:`ValueConversionError` if not possible.
        """
        return [bool(x) for x in self.ints]

    def __float__(self) -> float:
        """Takes the first item from :attr:`floats`."""
        return self.floats[0]

    def __int__(self) -> int:
        """Takes the first item from :attr:`ints`."""
        return round(float(self))

    def __bool__(self) -> bool:
        """Takes the first item from :attr:`bools`."""
        return bool(int(self))

    def __str__(self) -> str:
        v = self._value
        if v.empty:
            return ""
        if v.string:
            return str(v.string.value.tobytes().decode("utf8"))
        if v.unstructured:
            return str(v.unstructured.value.tobytes().decode("utf8", "ignore"))
        raise ValueConversionError(f"{v!r} cannot be converted to string")

    def __bytes__(self) -> bytes:
        v = self._value
        if v.empty:
            return b""
        if v.string:
            return bytes(v.string.value.tobytes())
        if v.unstructured:
            return bytes(v.unstructured.value.tobytes())
        raise ValueConversionError(f"{v!r} cannot be converted to bytes")

    def __repr__(self) -> str:
        return pyuavcan.util.repr_attributes(self, repr(self._value))


RelaxedValue = Union[
    # Explicit values
    ValueProxy,
    Value,
    # Value option types
    String,
    Unstructured,
    Bit,
    Integer8,
    Integer16,
    Integer32,
    Integer64,
    Natural8,
    Natural16,
    Natural32,
    Natural64,
    Real16,
    Real32,
    Real64,
    # Native types
    str,
    bytes,
    bool,
    int,
    float,
    # Native collections
    Iterable[bool],
    Iterable[int],
    Iterable[float],
    numpy.ndarray,
]
"""
These types can be automatically converted to :class:`Value` with a particular option selected.
"""


def _do_convert(to: Value, s: Value) -> Optional[Value]:
    """
    This is a bit rough around the edges; consider it to be an MVP.
    """
    if to.empty or s.empty:  # Everything is convertible to empty, and empty is convertible to everything.
        return to
    if (to.string and s.string) or (to.unstructured and s.unstructured):
        return s
    if to.string and s.unstructured:
        return Value(string=String(s.unstructured.value))
    if to.unstructured and s.string:
        return Value(unstructured=Unstructured(s.string.value))

    opt_to, opt_s = _get_option_name(to), _get_option_name(s)
    val_to: numpy.ndarray = get_attribute(to, opt_to).value
    val_s: numpy.ndarray = get_attribute(s, opt_s).value
    if len(val_to) != len(val_s):
        return None  # Dimensionality mismatch.
    # At this point it is known that both values are of the same dimension.
    if opt_to == opt_s:  # Also same scalar type -- no further checks needed.
        return s
    # fmt: off
    if to.bit:    return Value(bit=Bit([x != 0 for x in val_s]))
    if to.real16: return Value(real16=Real16(val_s))
    if to.real32: return Value(real32=Real32(val_s))
    if to.real64: return Value(real64=Real64(val_s))
    # fmt: on
    val_s_int = [round(x) for x in val_s]
    del val_s
    # fmt: off
    if to.integer8:  return Value(integer8=Integer8(val_s_int))
    if to.integer16: return Value(integer16=Integer16(val_s_int))
    if to.integer32: return Value(integer32=Integer32(val_s_int))
    if to.integer64: return Value(integer64=Integer64(val_s_int))
    if to.natural8:  return Value(natural8=Natural8(val_s_int))
    if to.natural16: return Value(natural16=Natural16(val_s_int))
    if to.natural32: return Value(natural32=Natural32(val_s_int))
    if to.natural64: return Value(natural64=Natural64(val_s_int))
    # fmt: on
    assert False


def _strictify(s: RelaxedValue) -> Value:
    # fmt: off
    if isinstance(s, Value):                return s
    if isinstance(s, ValueProxy):           return s.value
    if isinstance(s, (bool, int, float)):   return _strictify([s])
    if isinstance(s, str):                  return _strictify(String(s))
    if isinstance(s, bytes):                return _strictify(Unstructured(s))
    # fmt: on
    # fmt: off
    if isinstance(s, String):       return Value(string=s)
    if isinstance(s, Unstructured): return Value(unstructured=s)
    if isinstance(s, Bit):          return Value(bit=s)
    if isinstance(s, Integer8):     return Value(integer8=s)
    if isinstance(s, Integer16):    return Value(integer16=s)
    if isinstance(s, Integer32):    return Value(integer32=s)
    if isinstance(s, Integer64):    return Value(integer64=s)
    if isinstance(s, Natural8):     return Value(natural8=s)
    if isinstance(s, Natural16):    return Value(natural16=s)
    if isinstance(s, Natural32):    return Value(natural32=s)
    if isinstance(s, Natural64):    return Value(natural64=s)
    if isinstance(s, Real16):       return Value(real16=s)
    if isinstance(s, Real32):       return Value(real32=s)
    if isinstance(s, Real64):       return Value(real64=s)
    # fmt: on

    s = list(s)
    if not s:
        return Value()  # Empty list generalized into Value.empty.
    if all(isinstance(x, bool) for x in s):
        return _strictify(Bit(s))
    if all(isinstance(x, (int, bool)) for x in s):
        return _strictify(Natural64(s)) if all(x >= 0 for x in s) else _strictify(Integer64(s))
    if all(isinstance(x, (float, int, bool)) for x in s):
        return _strictify(Real64(s))

    raise ValueConversionError(f"Don't know how to convert {s!r} into {Value}")  # pragma: no cover


def _get_option_name(x: Value) -> str:
    for n in VALUE_OPTION_NAMES:
        if get_attribute(x, n):
            return n
    raise TypeError(f"Invalid value: {x!r}; expected option names: {VALUE_OPTION_NAMES}")  # pragma: no cover


@no_type_check
def _unittest_strictify() -> None:
    import pytest

    v = Value(string=String("abc"))
    assert v is _strictify(v)  # Transparency.
    assert repr(v) == repr(_strictify(ValueProxy(v)))

    assert list(_strictify(+1).natural64.value) == [+1]
    assert list(_strictify(-1).integer64.value) == [-1]
    assert list(_strictify(1.1).real64.value) == [pytest.approx(1.1)]
    assert list(_strictify(True).bit.value) == [True]
    assert _strictify([]).empty

    assert _strictify("Hello").string.value.tobytes().decode() == "Hello"
    assert _strictify(b"Hello").unstructured.value.tobytes() == b"Hello"


@no_type_check
def _unittest_convert() -> None:
    import pytest

    q = Value

    def _once(a: Value, b: RelaxedValue) -> Value:
        c = ValueProxy(a)
        c.assign(b)
        return c.value

    assert _once(q(), q()).empty
    assert _once(q(), String("Hello")).empty
    assert _once(q(string=String("A")), String("B")).string.value.tobytes().decode() == "B"
    assert _once(q(string=String("A")), Unstructured(b"B")).string.value.tobytes().decode() == "B"
    assert list(_once(q(natural16=Natural16([1, 2])), Natural64([1, 2])).natural16.value) == [1, 2]

    # Dimensionality mismatch.
    with pytest.raises(ValueConversionError):
        _once(q(integer16=Integer16([1, 2, 3])), Integer16([1, 2]))

    assert list(_once(q(bit=Bit([False, False])), Integer32([-1, 0])).bit.value) == [True, False]
    assert list(_once(q(integer8=Integer8([0, 1])), Real64([3.3, 6.4])).integer8.value) == [3, 6]
    assert list(_once(q(integer16=Integer16([0, 1])), Real32([3.3, 6.4])).integer16.value) == [3, 6]
    assert list(_once(q(integer32=Integer32([0, 1])), Real16([3.3, 6.4])).integer32.value) == [3, 6]
    assert list(_once(q(integer64=Integer64([0, 1])), Real64([3.3, 6.4])).integer64.value) == [3, 6]
    assert list(_once(q(natural8=Natural8([0, 1])), Real64([3.3, 6.4])).natural8.value) == [3, 6]
    assert list(_once(q(natural16=Natural16([0, 1])), Real64([3.3, 6.4])).natural16.value) == [3, 6]
    assert list(_once(q(natural32=Natural32([0, 1])), Real64([3.3, 6.4])).natural32.value) == [3, 6]
    assert list(_once(q(natural64=Natural64([0, 1])), Real64([3.3, 6.4])).natural64.value) == [3, 6]
    assert list(_once(q(real16=Real16([0])), Bit([True])).real16.value) == [pytest.approx(1.0)]
    assert list(_once(q(real32=Real32([0])), Bit([True])).real32.value) == [pytest.approx(1.0)]
    assert list(_once(q(real64=Real64([0])), Bit([True])).real64.value) == [pytest.approx(1.0)]
