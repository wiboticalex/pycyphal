# Copyright (C) 2021  UAVCAN Consortium  <uavcan.org>
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from __future__ import annotations
import abc
import typing
import dataclasses
import pyuavcan
from uavcan.register import Value_1_0 as Value


class StorageError(RuntimeError):
    """
    Unsuccessful storage transaction. This is a very low-level error representing a system configuration issue.
    """


@dataclasses.dataclass(frozen=True)
class Entry:
    value: Value
    mutable: bool


class Storage(abc.ABC):
    """
    Register storage backend interface.
    """

    @property
    @abc.abstractmethod
    def location(self) -> str:
        """
        The physical storage location for the data (e.g., file name).
        """
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def persistent(self) -> bool:
        """
        An in-memory DB is reported as non-persistent.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def count(self) -> int:
        """
        Number of registers.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get_names(self) -> typing.List[str]:
        """
        :returns: List of all registers ordered lexicographically.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get_name_at_index(self, index: int) -> typing.Optional[str]:
        """
        :returns: Name of the register at the specified index or None if the index is out of range.
            The ordering is guaranteed to be stable as long as the set of registers is not modified.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get(self, name: str) -> typing.Optional[Entry]:
        """
        :returns: None if no such register is available; otherwise value and mutability flag.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def set(self, name: str, e: Entry) -> None:
        """
        If the register does not exist, it will be created.
        If exists, it will be overwritten unconditionally with the specified value and mutability flag.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def delete(self, names: typing.Sequence[str]) -> None:
        """
        Removes specified registers from the storage.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    def __repr__(self) -> str:
        return pyuavcan.util.repr_attributes(self, repr(self.location))
