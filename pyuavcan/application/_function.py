# Copyright (c) 2021 UAVCAN Consortium
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from __future__ import annotations
import abc
import pyuavcan.application


class Function(abc.ABC):
    """
    An abstract application-layer function such as heartbeat publisher, register interface, PnP allocator, etc.
    All application-layer function implementations implement this interface.
    """

    @property
    @abc.abstractmethod
    def node(self) -> pyuavcan.application.Node:
        """
        The node instance this function operates upon.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def start(self) -> None:
        """
        This method shall be invoked once to bring the function into active state.
        It is idempotent.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def close(self) -> None:
        """
        This method shall be invoked once to stop the function and dispose of the resources.
        It is idempotent.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return pyuavcan.util.repr_attributes(self, node=self.node)
