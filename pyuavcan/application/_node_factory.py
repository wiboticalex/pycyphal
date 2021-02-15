# Copyright (c) 2021 UAVCAN Consortium
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from __future__ import annotations
from typing import Callable, Tuple, Optional, Union, Dict
from pathlib import Path
import logging
import pyuavcan
from ._node import Node, NodeInfo
from . import register
from .register.backend.sqlite import SQLiteBackend
from .register.backend.dynamic import DynamicBackend
from ._transport_factory import make_transport


class DefaultNode(Node):
    """
    This is a Voldemort type, hence it doesn't need public docs.
    """

    def __init__(
        self,
        presentation: pyuavcan.presentation.Presentation,
        info: NodeInfo,
        backend_sqlite: SQLiteBackend,
        backend_dynamic: DynamicBackend,
    ) -> None:
        self._presentation = presentation
        self._info = info

        self._backend_sqlite = backend_sqlite
        self._backend_dynamic = backend_dynamic
        self._registry = register.Registry([self._backend_sqlite, self._backend_dynamic])

        super().__init__()

    @property
    def presentation(self) -> pyuavcan.presentation.Presentation:
        return self._presentation

    @property
    def info(self) -> NodeInfo:
        return self._info

    @property
    def registry(self) -> register.Registry:
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

        def strictify(x: Union[register.Value, register.ValueProxy]) -> register.Value:
            if isinstance(x, register.ValueProxy):
                return x.value
            return x

        v = value_or_getter_or_getter_setter
        _logger.debug("%r: Create register %r = %r", self, name, v)
        if isinstance(v, (register.Value, register.ValueProxy)):
            self._backend_sqlite[name] = strictify(v)
        elif callable(v):
            self._backend_dynamic[name] = lambda: strictify(v())  # type: ignore
        elif isinstance(v, tuple) and len(v) == 2 and all(map(callable, v)):
            g, s = v
            self._backend_dynamic[name] = (lambda: strictify(g())), s
        else:  # pragma: no cover
            raise TypeError(f"Invalid type of register creation argument: {type(v).__name__}")


def make_node(
    info: NodeInfo,
    register_file: Union[None, str, Path] = None,
    environment_variables: Optional[Dict[str, str]] = None,
    *,
    transport: Optional[pyuavcan.transport.Transport] = None,
    reconfigurable_transport: bool = False,
) -> Node:
    """
    Initialize a new node by parsing the configuration encoded in the UAVCAN registers.

    If ``transport`` is given, it will be used as-is (but see argument docs below).
    If not given, a new transport instance will be constructed using :func:`make_transport`.

    Prior to construction, the register file will be updated/extended based on the register values passed via the
    environment variables (if any).
    Environment variables that encode empty-valued registers trigger removal of such registers from the file
    (non-existent registers do not trigger an error).

    Aside from the registers that encode the transport configuration (which are documented in :func:`make_transport`),
    the following registers are considered.
    They are split into groups by application-layer function they configure.

    ..  list-table:: :mod:`pyuavcan.application.diagnostic`
        :widths: 1 1 9
        :header-rows: 1

        * - Register name
          - Register type
          - Register semantics

        * - ``uavcan.diagnostic.severity``
          - ``natural16[1]``
          - If defined and the value is a valid severity level as defined in ``uavcan.diagnostic.Severity``,
            the node will publish its application log records of matching severity level to the standard subject
            ``uavcan.diagnostic.Record`` using :class:`pyuavcan.application.diagnostic.DiagnosticPublisher`.
            This is done by installing a root handler in :mod:`logging`.

        * - ``uavcan.diagnostic.timestamp``
          - ``bit[1]``
          - If defined and true, the published log messages will initialize the synchronized ``timestamp`` field
            from the log record timestamp provided by the :mod:`logging` library.
            This is only safe if the UAVCAN network is known to be synchronized on the same time system as the
            wall clock of the local computer.
            Otherwise, the timestamp is left at zero (which means "unknown" per Specification).

    Additional functions and their respective registers will be added later (e.g., time synchronization,
    file server, etc.).

    :param info:
        Response object to ``uavcan.node.GetInfo``.

    :param register_file:
        Path to the SQLite file containing the register database; or, in other words,
        the configuration file of this application/node.
        If not provided (default), the registers of this instance will be stored in-memory,
        meaning that no persistent configuration will be kept anywhere.
        If path is provided but the file does not exist, it will be created automatically.
        See :attr:`Node.registry`, :meth:`Node.create_register`.

    :param environment_variables:
        The register values passed via environment variables will be automatically parsed and for each
        register the respective entry in the register file will be updated/created.
        See :func:`register.parse_environment_variables` for additional details.

        If None (default), the variables are taken from :attr:`os.environ`.
        To disable variable parsing, pass an empty dict here.

    :param transport:
        If not provided (default), a new transport instance will be initialized based on the available registers using
        :func:`make_transport`.
        If provided, the node will be constructed with this transport instance and take its ownership.

    :param reconfigurable_transport:
        If True, the node will be constructed with :mod:`pyuavcan.transport.redundant`,
        which permits runtime reconfiguration.
        If the transport argument is given and it is not a redundant transport, it will be wrapped into one.
        Also see :func:`make_transport`.

    :returns: The constructed node instance.

    :raises: See :func:`make_transport`.

    ..  todo::

        Consider extending this factory with a capability to automatically run the node-ID allocation client
        :class:`pyuavcan.application.plug_and_play.Allocatee` if the available registers do not encode a non-anonymous
        node-ID value.

        Until this is implemented, to run the allocator one needs to construct the transport manually using
        :func:`make_transport`, then run the allocation client, then re-construct the transport again with the
        obtained node-ID value, then invoke this factory with the existing transport.

        While tedious, this is not that much of a problem because the PnP protocol is mostly intended for
        hardware nodes rather than software ones.
        A typical software node would typically obtain its node-ID from the launcher (like Yakut Orchestrator).
    """
    from pyuavcan.transport.redundant import RedundantTransport

    db = SQLiteBackend(register_file or "")

    def init_transport() -> pyuavcan.transport.Transport:
        if transport is None:
            out = make_transport(register.Registry([db]), reconfigurable=reconfigurable_transport)
            if out is not None:
                return out
            raise register.MissingRegisterError(
                f"Available registers do not encode a valid transport configuration: {list(db)}"
            )
        if not isinstance(transport, RedundantTransport) and reconfigurable_transport:
            out = RedundantTransport()
            out.attach_inferior(transport)
            return out
        return transport

    try:
        for name, value in register.parse_environment_variables(environment_variables):
            if value.empty:  # Remove register under this name.
                try:
                    del db[name]
                except LookupError:
                    pass
            else:
                db[name] = value

        presentation = pyuavcan.presentation.Presentation(init_transport())
        node = DefaultNode(
            presentation,
            info,
            db,
            DynamicBackend(),
        )

        # Check if any application-layer functions require instantiation.
        _make_diagnostic_publisher(node)
    except Exception:
        db.close()  # We do not close the database at normal exit because it's handed over to the node.
        raise
    return node


def _make_diagnostic_publisher(node: Node) -> None:
    try:
        uavcan_severity = int(node.registry["uavcan.diagnostic.severity"])
    except KeyError:
        return

    from .diagnostic import DiagnosticSubscriber, DiagnosticPublisher

    try:
        level = DiagnosticSubscriber.SEVERITY_UAVCAN_TO_PYTHON[uavcan_severity]
    except KeyError:
        return

    diag_publisher = DiagnosticPublisher(node, level=level)
    try:
        diag_publisher.timestamping_enabled = bool(node.registry["uavcan.diagnostic.timestamp"])
    except KeyError:
        pass
    logging.root.addHandler(diag_publisher)
    node.add_lifetime_hooks(None, lambda: logging.root.removeHandler(diag_publisher))


_logger = logging.getLogger(__name__)
