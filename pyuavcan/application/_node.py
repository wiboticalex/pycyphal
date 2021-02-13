# Copyright (c) 2019 UAVCAN Consortium
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from __future__ import annotations
from typing import Union, Callable, Tuple, Type, TypeVar, Dict, Optional, Iterator, List
from collections.abc import Mapping
from pathlib import Path
import asyncio
import logging
import uavcan.node
import pyuavcan
from pyuavcan.presentation import Presentation, ServiceRequestMetadata, Publisher, Subscriber, Server, Client
from . import heartbeat_publisher
from . import register
from ._function import Function


NodeInfo = uavcan.node.GetInfo_1_0.Response

MessageClass = TypeVar("MessageClass", bound=pyuavcan.dsdl.CompositeObject)
ServiceClass = TypeVar("ServiceClass", bound=pyuavcan.dsdl.ServiceObject)


_logger = logging.getLogger(__name__)


class Node:
    """
    This is the top-level abstraction representing a UAVCAN node on the bus.
    This class automatically instantiates the following application-layer function implementations:

    - :class:`heartbeat_publisher.HeartbeatPublisher`
    - :class:`register.RegisterServer`
    - read the attribute documentation for further details.

    Start the instance when initialization is finished by invoking :meth:`start`.
    """

    def __init__(
        self,
        presentation: Presentation,
        info: NodeInfo,
        register_file: Union[None, str, Path] = None,
        *,
        environment_variables: Optional[Dict[str, str]] = None,
    ):
        """
        :param presentation:
            The node takes ownership of the supplied presentation controller.
            Ownership here means that the controller will be closed (along with all sessions and other resources)
            when the node is closed.

        :param info:
            The info structure is sent as a response to requests of type ``uavcan.node.GetInfo``;
            the corresponding server instance is established and run by the node class automatically.

        :param register_file:
            Path to the SQLite file containing the register database; or, in other words,
            the configuration file of this application/node.
            If not provided (default), the registers of this instance will be stored in-memory,
            meaning that no persistent configuration will be kept anywhere.
            If path is provided but the file does not exist, it will be created automatically.
            See :attr:`registry`, :meth:`create_register`.

        :param environment_variables:
            The register values passed via environment variables will be automatically parsed and for each
            register the method :meth:`create_register` will be invoked (with overwrite flag set).
            See :func:`register.parse_environment_variables` for additional details.

            If None (default), the variables are taken from :attr:`os.environ`.
            To disable variable parsing, pass an empty dict here.
        """
        self._presentation = presentation
        self._info = info
        self._started = False
        self._functions: List[Function] = []

        from .register.backend.sqlite import SQLiteBackend
        from .register.backend.dynamic import DynamicBackend

        self._reg_db = SQLiteBackend(register_file or "")
        self._reg_dynamic = DynamicBackend()
        self._registry = register.Registry([self._reg_db, self._reg_dynamic])
        for name, value in register.parse_environment_variables(environment_variables):
            self.create_register(name, value, overwrite=True)

        self._srv_info = self.get_server(uavcan.node.GetInfo_1_0)
        self._init_functions()

    def _init_functions(self) -> None:
        from ._register_server import RegisterServer

        self._attach_function(heartbeat_publisher.HeartbeatPublisher(self))
        self._attach_function(RegisterServer(self))

    @property
    def presentation(self) -> Presentation:
        """Provides access to the underlying instance of :class:`Presentation`."""
        return self._presentation

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """Shortcut for ``self.presentation.loop``"""
        return self.presentation.loop

    @property
    def id(self) -> Optional[int]:
        """Shortcut for ``self.presentation.transport.local_node_id``"""
        return self.presentation.transport.local_node_id

    @property
    def registry(self) -> register.Registry:
        """
        Provides access to the local registry instance (see :class:`pyuavcan.application.register.Registry`).
        The registry manages UAVCAN registers as defined by the standard network service ``uavcan.register``.

        The registers store the configuration parameters of the current application, both standard
        (like subject-IDs, service-IDs, transport configuration, the local node-ID, etc.)
        and application-specific ones.

        Note that it is not possible to create new registers using this interface;
        for that, see :meth:`create_register`.

        See also :meth:`make_publisher`, :meth:`make_subscriber`, :meth:`make_client`, :meth:`get_server`.
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
        """
        Create new registers (define register schema).

        :param name: The name of the register.

        :param value_or_getter_or_getter_setter:
            - If this is a :class:`register.Value` or `register.ValueProxy` (the latter is supported for convenience),
              the value will be written into the registry file (see constructor).

            - If this is a callable, it will be invoked whenever this register is read.
              The return type of the callable is either :class:`register.Value` or `register.ValueProxy`.
              Such register will be reported as immutable.
              The registry file is not affected and therefore this change is not persistent.

            - If this is a tuple of two callables, then the first one is a getter that is invoked on read (see above),
              and the second is setter that is invoked on write with a single argument of type :class:`register.Value`.
              It is guaranteed that the type of the value passed into the setter is always the same as that which
              is returned by the getter.
              The type conversion is performed automatically by polling the getter beforehand to discover the type.
              The registry file is not affected and therefore this change is not persistent.

        :param overwrite:
            By default, if the register under the specified name already exists, nothing will be done,
            which allows applications to define default settings at startup by simply invoking this method for
            every known register (i.e., configuration parameter).

            This behavior can be changed by setting this flag to True, which will cause the register to be
            unconditionally overwritten even if the type is different (no type conversion will take place).
        """
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

    def make_publisher(self, dtype: Type[MessageClass], port_name: str = "") -> Publisher[MessageClass]:
        """
        Wrapper over :meth:`Presentation.make_publisher` that takes the subject-ID from the standard register
        ``uavcan.pub.PORT_NAME.id``.
        If the register is missing, the fixed subject-ID is used unless it is also missing.
        The type information is automatically exposed via ``uavcan.pub.PORT_NAME.type`` based on dtype.
        For details on the standard registers see Specification.

        :raises: :class:`register.MissingRegisterError` if the register is missing and no fixed port-ID is defined.
        """
        return self.presentation.make_publisher(dtype, self._resolve_port(dtype, "pub", port_name))

    def make_subscriber(self, dtype: Type[MessageClass], port_name: str = "") -> Subscriber[MessageClass]:
        """
        Wrapper over :meth:`Presentation.make_subscriber` that takes the subject-ID from the standard register
        ``uavcan.sub.PORT_NAME.id``.
        If the register is missing, the fixed subject-ID is used unless it is also missing.
        The type information is automatically exposed via ``uavcan.sub.PORT_NAME.type`` based on dtype.
        For details on the standard registers see Specification.

        :raises: :class:`register.MissingRegisterError` if the register is missing and no fixed port-ID is defined.
        """
        return self.presentation.make_subscriber(dtype, self._resolve_port(dtype, "sub", port_name))

    def make_client(self, dtype: Type[ServiceClass], server_node_id: int, port_name: str = "") -> Client[ServiceClass]:
        """
        Wrapper over :meth:`Presentation.make_client` that takes the service-ID from the standard register
        ``uavcan.cln.PORT_NAME.id``.
        If the register is missing, the fixed service-ID is used unless it is also missing.
        The type information is automatically exposed via ``uavcan.cln.PORT_NAME.type`` based on dtype.
        For details on the standard registers see Specification.

        :raises: :class:`register.MissingRegisterError` if the register is missing and no fixed port-ID is defined.
        """
        return self.presentation.make_client(
            dtype,
            service_id=self._resolve_port(dtype, "cln", port_name),
            server_node_id=server_node_id,
        )

    def get_server(self, dtype: Type[ServiceClass], port_name: str = "") -> Server[ServiceClass]:
        """
        Wrapper over :meth:`Presentation.get_server` that takes the service-ID from the standard register
        ``uavcan.srv.PORT_NAME.id``.
        If the register is missing, the fixed service-ID is used unless it is also missing.
        The type information is automatically exposed via ``uavcan.srv.PORT_NAME.type`` based on dtype.
        For details on the standard registers see Specification.

        :raises: :class:`register.MissingRegisterError` if the register is missing and no fixed port-ID is defined.
        """
        return self.presentation.get_server(dtype, self._resolve_port(dtype, "srv", port_name))

    def _resolve_port(self, dtype: Type[pyuavcan.dsdl.CompositeObject], kind: str, name: str) -> int:
        model = pyuavcan.dsdl.get_model(dtype)
        name = name or str(model).lower()  # Convenience tweak: make the port name default to the data type name.
        id_register_name = f"uavcan.{kind}.{name}.id"
        try:
            port_id = int(self.registry[id_register_name])
        except register.MissingRegisterError as ex:
            if not model.has_fixed_port_id:
                raise register.MissingRegisterError(
                    f"Cannot initialize {kind}-port {name!r} because register "
                    f"{id_register_name!r} is missing and no fixed port-ID is defined for {model}. "
                    f"Check if the environment variables are passed correctly or if the application is using the "
                    f"correct register file."
                ) from ex
            port_id = model.fixed_port_id
            # Expose the port-ID information to other network participants. This is not mandatory though.
            self.create_register(id_register_name, lambda: register.Value(natural16=register.Natural16([port_id])))
        # Expose the type information to other network participants.
        self.create_register(
            f"uavcan.{kind}.{name}.type", lambda: register.Value(string=register.String(str(model))), overwrite=True
        )
        _logger.debug("%r: Port-ID %r %r resolved as %r", self, kind, name, port_id)
        return port_id

    @property
    def info(self) -> NodeInfo:
        """Provides access to the local node info structure. See :class:`pyuavcan.application.NodeInfo`."""
        return self._info

    @property
    def heartbeat_publisher(self) -> heartbeat_publisher.HeartbeatPublisher:
        """Provides access to the heartbeat publisher instance of this node."""
        for fun in self._functions:
            if isinstance(fun, heartbeat_publisher.HeartbeatPublisher):
                return fun
        assert False

    def start(self) -> None:
        """
        Starts the GetInfo server in the background, the heartbeat publisher, etc.
        Those will be automatically terminated when the node is closed.
        Does nothing if already started.
        """

        async def _handle_get_info(_req: uavcan.node.GetInfo_1_0.Request, _meta: ServiceRequestMetadata) -> NodeInfo:
            return self._info

        if not self._started:
            for fun in self._functions:
                fun.start()
            self._srv_info.serve_in_background(_handle_get_info)
            self._started = True

    def close(self) -> None:
        """
        Closes the underlying presentation instance, application-level functions, and all other entities.
        Does nothing if already closed.
        The user does not have to close every port manually as it will be done automatically.
        """
        try:
            for fun in self._functions:
                try:
                    fun.close()
                except Exception as ex:  # pragma: no cover
                    _logger.exception("%r: Failed to close %r: %s", self, fun, ex)
            self._srv_info.close()
            self._registry.close()
        finally:
            self._presentation.close()

    def _attach_function(self, fun: Function) -> None:
        """
        Attached functions are automatically :meth:`Function.start`-ed when this node is :meth:`start`-ed.
        If the node is already started when a new function is attached, the function is started immediately.
        Likewise, when the node is closed, all functions are closed.

        :raises: :class:`ValueError` if :attr:`Function.node` of the added function is not this node.
        """
        if self is not fun.node:
            raise ValueError("This function belongs to a different node")
        if self._started:
            fun.start()
        self._functions.append(fun)

    def __repr__(self) -> str:
        return pyuavcan.util.repr_attributes(self, self._info, self._presentation, self.registry)

    @staticmethod
    def from_registers(
        info: NodeInfo,
        register_file: Union[None, str, Path] = None,
        environment_variables: Optional[Dict[str, str]] = None,
        *,
        reconfigurable_transport: bool = False,
    ) -> Node:
        from .register.backend.sqlite import SQLiteBackend

        db = SQLiteBackend(register_file or "")
        registry = register.Registry([db])
        try:
            for name, value in register.parse_environment_variables(environment_variables):
                db.set(name, value)
            transport = construct_transport_from_registers(registry, reconfigurable=reconfigurable_transport)
            return Node(
                Presentation(transport),
                info,
                register_file,
                environment_variables=environment_variables,
            )
        finally:
            registry.close()


def construct_transport_from_registers(
    registers: Mapping[str, register.ValueProxy],
    *,
    reconfigurable: bool,
) -> pyuavcan.transport.Transport:
    # noinspection PyPep8Naming
    Ty = TypeVar("Ty", int, float, bool, str, bytes)

    def get(name: str, ty: Type[Ty]) -> Optional[Ty]:
        try:
            return ty(registers[name])
        except KeyError:
            return None

    node_id = get("uavcan.node.id", int)

    def udp() -> Iterator[pyuavcan.transport.Transport]:
        try:
            ip_list = str(registers["uavcan.udp.ip"]).split()
        except KeyError:
            return

        from pyuavcan.transport.udp import UDPTransport

        mtu = get("uavcan.udp.mtu", int) or min(UDPTransport.VALID_MTU_RANGE)
        srv_mult = int(get("uavcan.udp.duplicate_service_transfers", bool) or False) + 1
        for ip in ip_list:
            yield UDPTransport(ip, node_id, mtu=mtu, service_transfer_multiplier=srv_mult)

    def serial() -> Iterator[pyuavcan.transport.Transport]:
        try:
            port_list = str(registers["uavcan.serial.port"]).split()
        except KeyError:
            return

        from pyuavcan.transport.serial import SerialTransport

        srv_mult = int(get("uavcan.serial.duplicate_service_transfers", bool) or False) + 1
        baudrate = get("uavcan.serial.baudrate", int)
        for port in port_list:
            yield SerialTransport(port, node_id, service_transfer_multiplier=srv_mult, baudrate=baudrate)

    def can() -> Iterator[pyuavcan.transport.Transport]:
        try:
            iface_list = str(registers["uavcan.can.iface"]).split()
        except KeyError:
            return

        from pyuavcan.transport.can import CANTransport

        reg_mtu = "uavcan.can.mtu"
        reg_bitrate = "uavcan.can.bitrate"
        mtu = get(reg_mtu, int)
        bitrate: Union[int, Tuple[int, int]]
        try:
            bitrate_list = registers[reg_bitrate].ints
        except KeyError:
            bitrate = (1_000_000, 4_000_000) if mtu is None or mtu > 8 else 1_000_000
        else:
            bitrate = (bitrate_list[0], bitrate_list[1]) if len(bitrate_list) > 1 else bitrate_list[0]

        for iface in iface_list:
            media: pyuavcan.transport.can.media.Media
            if iface.lower().startswith("socketcan:"):
                from pyuavcan.transport.can.media.socketcan import SocketCANMedia

                mtu = mtu or (8 if isinstance(bitrate, int) else 64)
                media = SocketCANMedia(iface.split(":")[-1], mtu=mtu)
            else:
                from pyuavcan.transport.can.media.pythoncan import PythonCANMedia

                media = PythonCANMedia(iface, bitrate, mtu)
            yield CANTransport(media, node_id)

    def loopback() -> Iterator[pyuavcan.transport.Transport]:
        if registers.get("uavcan.loopback"):
            from pyuavcan.transport.loopback import LoopbackTransport

            yield LoopbackTransport(node_id)

    transports = *udp(), *serial(), *can(), *loopback()
    if not reconfigurable:
        if not transports:
            raise KeyError(
                f"The available registers do not encode a valid transport configuration. "
                f"For reference, the defined register names are: {list(registers)}"
            )
        if len(transports) == 1:
            return transports[0]

    from pyuavcan.transport.redundant import RedundantTransport

    red = RedundantTransport()
    for tr in transports:
        red.attach_inferior(tr)
    return red
