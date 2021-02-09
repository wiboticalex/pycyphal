# Copyright (C) 2021  UAVCAN Consortium  <uavcan.org>
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from __future__ import annotations
import logging
import pyuavcan
from pyuavcan.presentation import Presentation, ServiceRequestMetadata
from uavcan.register import Access_1_0 as Access
from uavcan.register import List_1_0 as List
from uavcan.register import Name_1_0 as Name
from . import Registry, ValueConversionError, MissingRegisterError


class Server:
    """
    Implementation of the standard network service ``uavcan.register``; specifically, List and Access.

    This server implements automatic type conversion by invoking
    :meth:`pyuavcan.application.register.ValueProxy.assign` on every set request.
    This means that, for example, one can successfully modify a register of type
    ``bool[x]`` by sending a set request of type ``real64[x]``, or ``string`` with ``unstructured``, etc.

    Here is a demo. First, set up the test environment:

    >>> from pyuavcan.transport.loopback import LoopbackTransport
    >>> from pyuavcan.presentation import Presentation
    >>> pres = Presentation(LoopbackTransport(1))

    Populate a registry (register repository):

    >>> from pyuavcan.application.register import Registry, Value, ValueProxy, Integer64, Real16, Unstructured
    >>> from pyuavcan.application.register.backend.sqlite import SQLiteBackend
    >>> from tempfile import mktemp
    >>> b0 = SQLiteBackend(mktemp(".db"))
    >>> b0.set("foo", Value(integer64=Integer64([1, 20, -100])))
    >>> registry = Registry([b0])

    Instantiate and launch the server:

    >>> srv = Server(pres, registry)
    >>> srv.start()

    It is now running using background async tasks. List registers:

    >>> import uavcan.register
    >>> from asyncio import get_event_loop
    >>> cln_list = pres.make_client_with_fixed_service_id(uavcan.register.List_1_0, server_node_id=1)
    >>> response, _ = get_event_loop().run_until_complete(cln_list.call(uavcan.register.List_1_0.Request(index=0)))
    >>> response.name.name.tobytes().decode()   # The dummy register we created above.
    'foo'
    >>> response, _ = get_event_loop().run_until_complete(cln_list.call(uavcan.register.List_1_0.Request(index=1)))
    >>> response.name.name.tobytes().decode()   # Out of range -- empty string returned to indicate that.
    ''

    Get the dummy register created above:

    >>> cln_access = pres.make_client_with_fixed_service_id(uavcan.register.Access_1_0, server_node_id=1)
    >>> request = uavcan.register.Access_1_0.Request()
    >>> request.name.name = "foo"
    >>> response, _ = get_event_loop().run_until_complete(cln_access.call(request))
    >>> response.mutable, response.persistent
    (True, True)
    >>> ValueProxy(response.value).ints
    [1, 20, -100]

    Set a new value and read it back.
    Notice that the type does not match but it is automatically converted by the server.

    >>> request.value.real16 = Real16([3.14159, 2.71828, -500])  # <-- the type is different but it's okay.
    >>> response, _ = get_event_loop().run_until_complete(cln_access.call(request))
    >>> ValueProxy(response.value).ints     # Automatically converted.
    [3, 3, -500]
    >>> registry["foo"].ints                # Yup, the register is, indeed, updated by the server.
    [3, 3, -500]

    If the type cannot be converted or the register is immutable, the write is ignored,
    as prescribed by the register network service definition:

    >>> request.value.unstructured = Unstructured(b'Hello world!')
    >>> response, _ = get_event_loop().run_until_complete(cln_access.call(request))
    >>> ValueProxy(response.value).ints  # Conversion is not possible, same value retained.
    [3, 3, -500]

    An attempt to access a non-existent register returns an empty value:

    >>> request.name.name = 'bar'
    >>> response, _ = get_event_loop().run_until_complete(cln_access.call(request))
    >>> response.value.empty is not None
    True

    Close the instance afterwards:

    >>> srv.close()
    >>> registry.close()
    >>> pres.close()
    """

    def __init__(self, presentation: Presentation, registry: Registry) -> None:
        """
        :param presentation: RPC-service instances will be constructed from this presentation instance.

        :param registry: The ownership is not transferred, meaning that the user should close the
            registry manually when done.
        """
        self._registry = registry
        self._srv_list = presentation.get_server_with_fixed_service_id(List)
        self._srv_access = presentation.get_server_with_fixed_service_id(Access)

    def start(self) -> None:
        self._srv_list.serve_in_background(self._handle_list)
        self._srv_access.serve_in_background(self._handle_access)

    def close(self) -> None:
        self._srv_list.close()
        self._srv_access.close()

    async def _handle_list(self, request: List.Request, metadata: ServiceRequestMetadata) -> List.Response:
        name = self._registry.get_name_at_index(request.index)
        _logger.debug("%r: List request index %r name %r %r", self, request.index, name, metadata)
        if name is not None:
            return List.Response(Name(name))
        return List.Response()

    async def _handle_access(self, request: Access.Request, metadata: ServiceRequestMetadata) -> Access.Response:
        name = request.name.name.tobytes().decode("utf8", "ignore")
        v = self._registry.get(name)
        if v is not None and v.mutable and not request.value.empty:
            try:
                v.assign(request.value)
                self._registry.set(name, v)
            except ValueConversionError as ex:
                _logger.debug("%r: Conversion from %r to %r is not possible: %s", self, request.value, v.value, ex)
            except MissingRegisterError as ex:  # pragma: no cover
                _logger.warning("%r: The register has gone away: %s", self, ex)
            v = self._registry.get(name)  # Read back one more time just in case to confirm write.
        if v is not None:
            response = Access.Response(
                mutable=v.mutable,
                persistent=v.persistent,
                value=v.value,
            )
        else:
            response = Access.Response()  # No such register
        _logger.debug("%r: Access %r: %r %r", self, metadata, request, response)
        return response

    def __repr__(self) -> str:
        return pyuavcan.util.repr_attributes(self, self._registry)


_logger = logging.getLogger(__name__)
