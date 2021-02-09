# Copyright (c) 2019 UAVCAN Consortium
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from __future__ import annotations
from typing import Union, Callable, Tuple
from pathlib import Path
import logging
import uavcan.node
import pyuavcan
from .heartbeat_publisher import HeartbeatPublisher
from .diagnostic import DiagnosticSubscriber
from . import register


NodeInfo = uavcan.node.GetInfo_1_0.Response


_logger = logging.getLogger(__name__)


class Node:
    """
    This is the top-level abstraction representing a UAVCAN node on the bus.
    This class is just a minor addition on top of the lower-level abstractions of the library
    implementing commonly-used/mandatory functions of the protocol such as heartbeat reporting and responding
    to node info requests ``uavcan.node.GetInfo``.

    Start the instance when initialization is finished by invoking :meth:`start`.

    This class automatically instantiates the following application-level function implementations:

    - :class:`HeartbeatPublisher` (see :attr:`heartbeat_publisher`).

    - :class:`register.Repository` (see :attr:`registry`) along with the implementation of the standard
      register network service ``uavcan.register``.

    Additionally, if enabled via the corresponding constructor arguments, optional application-level function
    implementations are instantiated as described in the constructor documentation.
    """

    def __init__(
        self,
        presentation: pyuavcan.presentation.Presentation,
        info: NodeInfo,
        registry_file: Union[None, str, Path] = None,
        *,
        parse_environment_variables: bool = True,
        with_diagnostic_subscriber: bool = False,
    ):
        """
        :param presentation: The node takes ownership of the supplied presentation controller.
            Ownership here means that the controller will be closed (along with all sessions and other resources)
            when the node is closed.

        :param info: The info structure is sent as a response to requests of type ``uavcan.node.GetInfo``;
            the corresponding server instance is established and run by the node class automatically.

        :param registry_file: Path to the SQLite file containing the register database; or, in other words,
            the configuration file of this application and node.
            If not provided (default), the registers of this instance will be stored in-memory,
            meaning that no persistent configuration will be stored anywhere.
            If path is provided but the file does not exist, it will be created automatically.
            See :attr:`registry`, :meth:`create_register`.

        :param parse_environment_variables: If True (default), the registry will be automatically updated/populated
            based on the register values passed via environment variables.
            See :func:`register.parse_environment_variables`.

        :param with_diagnostic_subscriber: If True, an instance of
            :class:`pyuavcan.application.diagnostic.DiagnosticSubscriber` will be constructed to channel
            standard UAVCAN diagnostic messages into the local Python logging facility.
        """
        self._presentation = presentation
        self._info = info
        self._heartbeat_publisher = HeartbeatPublisher(self._presentation)
        self._srv_info = self._presentation.get_server_with_fixed_service_id(uavcan.node.GetInfo_1_0)

        from .register.backend.sqlite import SQLiteBackend
        from .register.backend.dynamic import DynamicBackend

        self._reg_db = SQLiteBackend(registry_file or "")
        self._reg_dynamic = DynamicBackend()
        self._registry = register.Registry([self._reg_db, self._reg_dynamic])
        self._reg_server = register.Server(self._presentation, self._registry)

        if parse_environment_variables:
            for name, value in register.parse_environment_variables():
                _logger.debug("%r: Register from envvar: %r %r", self, name, value)
                self._reg_db.set(name, value)

        self._diagnostic_subscriber = DiagnosticSubscriber(self._presentation) if with_diagnostic_subscriber else None
        self._started = False

    @property
    def presentation(self) -> pyuavcan.presentation.Presentation:
        """Provides access to the underlying instance of :class:`pyuavcan.presentation.Presentation`."""
        return self._presentation

    @property
    def registry(self) -> register.Registry:
        """
        Provides access to the local registry instance (see :class:`pyuavcan.application.register.Registry`).
        The registry manages UAVCAN registers as defined by the standard network service ``uavcan.register``.

        The registers store the configuration parameters of the current application, both standard
        (like subject-IDs, service-IDs, transport configuration, the local node-ID, etc.)
        and application-specific ones.
        """
        return self._registry

    def create_register(
        self,
        name: str,
        value_or_getter_or_getter_setter: Union[
            register.Value,
            register.ValueProxy,
            Callable[[], Union[register.Value, register.ValueProxy]],
            Tuple[
                Callable[[], Union[register.Value, register.ValueProxy]],
                Callable[[register.Value], None],
            ],
        ],
        overwrite: bool = False,
    ) -> None:
        if not overwrite and self.registry.get(name) is not None:
            _logger.debug("%r: Register %r already exists and overwrite not enabled", self, name)
            return

        def unwrap(x: Union[register.Value, register.ValueProxy]) -> register.Value:
            if isinstance(x, register.ValueProxy):
                return x.value
            return x

        v = value_or_getter_or_getter_setter
        _logger.debug("%r: Create register %r = %r", self, name, v)
        if isinstance(v, (register.Value, register.ValueProxy)):
            self._reg_db.set(name, unwrap(v))
        elif callable(v):
            self._reg_dynamic.register(name, lambda: unwrap(v()))  # type: ignore
        elif isinstance(v, tuple) and len(v) == 2 and all(map(callable, v)):
            g, s = v
            self._reg_dynamic.register(name, lambda: unwrap(g()), s)
        else:  # pragma: no cover
            raise TypeError(f"Invalid register creation argument: {v}")

    @property
    def info(self) -> NodeInfo:
        """Provides access to the local node info structure. See :class:`pyuavcan.application.NodeInfo`."""
        return self._info

    @property
    def heartbeat_publisher(self) -> HeartbeatPublisher:
        """Provides access to the heartbeat publisher instance of this node."""
        return self._heartbeat_publisher

    def start(self) -> None:
        """
        Starts the GetInfo server in the background, the heartbeat publisher, etc.
        Those will be automatically terminated when the node is closed.
        Does nothing if already started.
        """
        if not self._started:
            self._srv_info.serve_in_background(self._handle_get_info_request)
            self._heartbeat_publisher.start()
            self._reg_server.start()
            if self._diagnostic_subscriber is not None:
                self._diagnostic_subscriber.start()
            self._started = True

    def close(self) -> None:
        """
        Closes the underlying presentation instance, application-level functions, and all other entities.
        Does nothing if already closed.
        """
        try:
            self._heartbeat_publisher.close()
            self._srv_info.close()
            self._reg_server.close()
            self._registry.close()
            if self._diagnostic_subscriber is not None:
                self._diagnostic_subscriber.close()
        finally:
            self._presentation.close()

    async def _handle_get_info_request(
        self, _: uavcan.node.GetInfo_1_0.Request, metadata: pyuavcan.presentation.ServiceRequestMetadata
    ) -> NodeInfo:
        _logger.debug("%s got a node info request: %s", self, metadata)
        return self._info

    def __repr__(self) -> str:
        return pyuavcan.util.repr_attributes(
            self,
            info=self._info,
            heartbeat=self._heartbeat_publisher.make_message(),
            registry=self.registry,
            presentation=self._presentation,
        )
