# Copyright (c) 2020 UAVCAN Consortium
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

import typing
import asyncio
import logging
import pytest
import pyuavcan

if typing.TYPE_CHECKING:
    import pyuavcan.application

_logger = logging.getLogger(__name__)


@pytest.mark.asyncio  # type: ignore
async def _unittest_slow_node_tracker(
    compiled: typing.List[pyuavcan.dsdl.GeneratedPackageInfo], caplog: typing.Any
) -> None:
    from . import get_transport
    from pyuavcan.presentation import Presentation
    from pyuavcan.application import Node, NodeInfo
    from pyuavcan.application.node_tracker import NodeTracker, Entry

    assert compiled

    p_a = Node(Presentation(get_transport(0xA)), NodeInfo(name="org.uavcan.pyuavcan.test.node_tracker.a"))
    p_b = Node(Presentation(get_transport(0xB)), NodeInfo(name="org.uavcan.pyuavcan.test.node_tracker.b"))
    p_c = Node(Presentation(get_transport(0xC)), NodeInfo(name="org.uavcan.pyuavcan.test.node_tracker.c"))
    p_trk = Node(Presentation(get_transport(None)), NodeInfo(name="org.uavcan.pyuavcan.test.node_tracker.trk"))

    try:
        last_update_args: typing.List[typing.Tuple[int, typing.Optional[Entry], typing.Optional[Entry]]] = []

        def simple_handler(node_id: int, old: typing.Optional[Entry], new: typing.Optional[Entry]) -> None:
            last_update_args.append((node_id, old, new))

        def faulty_handler(_node_id: int, _old: typing.Optional[Entry], _new: typing.Optional[Entry]) -> None:
            raise Exception("INTENDED EXCEPTION")

        trk = NodeTracker(p_trk)

        assert not trk.registry
        assert pytest.approx(trk.get_info_timeout) == trk.DEFAULT_GET_INFO_TIMEOUT
        assert trk.get_info_attempts == trk.DEFAULT_GET_INFO_ATTEMPTS

        # Override the defaults to simplify and speed-up testing.
        trk.get_info_timeout = 1.0
        trk.get_info_attempts = 2
        assert pytest.approx(trk.get_info_timeout) == 1.0
        assert trk.get_info_attempts == 2

        with caplog.at_level(logging.CRITICAL, logger=pyuavcan.application.node_tracker.__name__):
            trk.add_update_handler(faulty_handler)
            trk.add_update_handler(simple_handler)

            trk.start()
            trk.start()  # Idempotency

            await asyncio.sleep(9)
            assert not last_update_args
            assert not trk.registry

            # Bring the first node online and make sure it is detected and reported.
            p_a.heartbeat_publisher.vendor_specific_status_code = 0xDE
            p_a.start()
            await asyncio.sleep(9)
            assert len(last_update_args) == 1
            assert last_update_args[0][0] == 0xA
            assert last_update_args[0][1] is None
            assert last_update_args[0][2] is not None
            assert last_update_args[0][2].heartbeat.uptime == 0
            assert last_update_args[0][2].heartbeat.vendor_specific_status_code == 0xDE
            last_update_args.clear()
            assert list(trk.registry.keys()) == [0xA]
            assert 30 >= trk.registry[0xA].heartbeat.uptime >= 2
            assert trk.registry[0xA].heartbeat.vendor_specific_status_code == 0xDE
            assert trk.registry[0xA].info is None

            # Remove the faulty handler -- no point keeping the noise in the log.
            trk.remove_update_handler(faulty_handler)

        # Bring the second node online and make sure it is detected and reported.
        p_b.heartbeat_publisher.vendor_specific_status_code = 0xBE
        p_b.start()
        await asyncio.sleep(9)
        assert len(last_update_args) == 1
        assert last_update_args[0][0] == 0xB
        assert last_update_args[0][1] is None
        assert last_update_args[0][2] is not None
        assert last_update_args[0][2].heartbeat.uptime == 0
        assert last_update_args[0][2].heartbeat.vendor_specific_status_code == 0xBE
        last_update_args.clear()
        assert list(trk.registry.keys()) == [0xA, 0xB]
        assert 60 >= trk.registry[0xA].heartbeat.uptime >= 4
        assert trk.registry[0xA].heartbeat.vendor_specific_status_code == 0xDE
        assert trk.registry[0xA].info is None
        assert 30 >= trk.registry[0xB].heartbeat.uptime >= 2
        assert trk.registry[0xB].heartbeat.vendor_specific_status_code == 0xBE
        assert trk.registry[0xB].info is None

        await asyncio.sleep(9)
        assert not last_update_args
        assert list(trk.registry.keys()) == [0xA, 0xB]
        assert 90 >= trk.registry[0xA].heartbeat.uptime >= 6
        assert trk.registry[0xA].heartbeat.vendor_specific_status_code == 0xDE
        assert trk.registry[0xA].info is None
        assert 60 >= trk.registry[0xB].heartbeat.uptime >= 4
        assert trk.registry[0xB].heartbeat.vendor_specific_status_code == 0xBE
        assert trk.registry[0xB].info is None

        # Create a new tracker, this time with a valid node-ID, and make sure node info is requested.
        # We are going to need a new handler for this.
        num_events_a = 0
        num_events_b = 0
        num_events_c = 0

        def validating_handler(node_id: int, old: typing.Optional[Entry], new: typing.Optional[Entry]) -> None:
            nonlocal num_events_a, num_events_b, num_events_c
            _logger.info("VALIDATING HANDLER %s %s %s", node_id, old, new)
            if node_id == 0xA:
                if num_events_a == 0:  # First detection
                    assert old is None
                    assert new is not None
                    assert new.heartbeat.vendor_specific_status_code == 0xDE
                    assert new.info is None
                elif num_events_a == 1:  # Get info received
                    assert old is not None
                    assert new is not None
                    assert old.heartbeat.vendor_specific_status_code == 0xDE
                    assert new.heartbeat.vendor_specific_status_code == 0xDE
                    assert old.info is None
                    assert new.info is not None
                    assert new.info.name.tobytes().decode() == "org.uavcan.pyuavcan.test.node_tracker.a"
                elif num_events_a == 2:  # Restart detected
                    assert old is not None
                    assert new is not None
                    assert old.heartbeat.vendor_specific_status_code == 0xDE
                    assert new.heartbeat.vendor_specific_status_code == 0xFE
                    assert old.info is not None
                    assert new.info is None
                elif num_events_a == 3:  # Get info after restart received
                    assert old is not None
                    assert new is not None
                    assert old.heartbeat.vendor_specific_status_code == 0xFE
                    assert new.heartbeat.vendor_specific_status_code == 0xFE
                    assert old.info is None
                    assert new.info is not None
                    assert new.info.name.tobytes().decode() == "org.uavcan.pyuavcan.test.node_tracker.a"
                elif num_events_a == 4:  # Offline
                    assert old is not None
                    assert new is None
                    assert old.heartbeat.vendor_specific_status_code == 0xFE
                    assert old.info is not None
                else:
                    assert False
                num_events_a += 1
            elif node_id == 0xB:
                if num_events_b == 0:
                    assert old is None
                    assert new is not None
                    assert new.heartbeat.vendor_specific_status_code == 0xBE
                    assert new.info is None
                elif num_events_b == 1:
                    assert old is not None
                    assert new is not None
                    assert old.heartbeat.vendor_specific_status_code == 0xBE
                    assert new.heartbeat.vendor_specific_status_code == 0xBE
                    assert old.info is None
                    assert new.info is not None
                    assert new.info.name.tobytes().decode() == "org.uavcan.pyuavcan.test.node_tracker.b"
                elif num_events_b == 2:
                    assert old is not None
                    assert new is None
                    assert old.heartbeat.vendor_specific_status_code == 0xBE
                    assert old.info is not None
                else:
                    assert False
                num_events_b += 1
            elif node_id == 0xC:
                if num_events_c == 0:
                    assert old is None
                    assert new is not None
                    assert new.heartbeat.vendor_specific_status_code == 0xF0
                    assert new.info is None
                elif num_events_c == 1:
                    assert old is not None
                    assert new is None
                    assert old.heartbeat.vendor_specific_status_code == 0xF0
                    assert old.info is None
                else:
                    assert False
                num_events_c += 1
            else:
                assert False

        trk.close()
        trk.close()  # Idempotency
        p_trk = Node(Presentation(get_transport(0xDD)), p_trk.info)
        trk = NodeTracker(p_trk)
        trk.add_update_handler(validating_handler)
        trk.start()
        trk.get_info_timeout = 1.0
        trk.get_info_attempts = 2
        assert pytest.approx(trk.get_info_timeout) == 1.0
        assert trk.get_info_attempts == 2

        await asyncio.sleep(9)
        assert num_events_a == 2
        assert num_events_b == 2
        assert num_events_c == 0
        assert list(trk.registry.keys()) == [0xA, 0xB]
        assert 60 >= trk.registry[0xA].heartbeat.uptime >= 8
        assert trk.registry[0xA].heartbeat.vendor_specific_status_code == 0xDE
        assert trk.registry[0xA].info is not None
        assert trk.registry[0xA].info.name.tobytes().decode() == "node-A"
        assert 60 >= trk.registry[0xB].heartbeat.uptime >= 6
        assert trk.registry[0xB].heartbeat.vendor_specific_status_code == 0xBE
        assert trk.registry[0xB].info is not None
        assert trk.registry[0xB].info.name.tobytes().decode() == "node-B"

        # Node B goes offline.
        p_b.close()
        await asyncio.sleep(9)
        assert num_events_a == 2
        assert num_events_b == 3
        assert num_events_c == 0
        assert list(trk.registry.keys()) == [0xA]
        assert 90 >= trk.registry[0xA].heartbeat.uptime >= 12
        assert trk.registry[0xA].heartbeat.vendor_specific_status_code == 0xDE
        assert trk.registry[0xA].info is not None
        assert trk.registry[0xA].info.name.tobytes().decode() == "node-A"

        # Node C appears online. It does not respond to GetInfo.
        p_c.heartbeat_publisher.vendor_specific_status_code = 0xF0
        p_c.start()
        p_c._srv_info.close()  # pylint: disable=protected-access
        await asyncio.sleep(9)
        assert num_events_a == 2
        assert num_events_b == 3
        assert num_events_c == 1
        assert list(trk.registry.keys()) == [0xA, 0xC]
        assert 180 >= trk.registry[0xA].heartbeat.uptime >= 17
        assert trk.registry[0xA].heartbeat.vendor_specific_status_code == 0xDE
        assert trk.registry[0xA].info is not None
        assert trk.registry[0xA].info.name.tobytes().decode() == "node-A"
        assert 30 >= trk.registry[0xC].heartbeat.uptime >= 5
        assert trk.registry[0xC].heartbeat.vendor_specific_status_code == 0xF0
        assert trk.registry[0xC].info is None

        # Node A is restarted. Node C goes offline.
        p_c.close()
        p_a = Node(Presentation(get_transport(0xA)), NodeInfo(name="org.uavcan.pyuavcan.test.node_tracker.a"))
        p_a.heartbeat_publisher.vendor_specific_status_code = 0xFE
        p_a.start()
        await asyncio.sleep(9)
        assert num_events_a == 4  # Two extra events: node restart detection, then get info reception.
        assert num_events_b == 3
        assert num_events_c == 2
        assert list(trk.registry.keys()) == [0xA]
        assert 30 >= trk.registry[0xA].heartbeat.uptime >= 5
        assert trk.registry[0xA].heartbeat.vendor_specific_status_code == 0xFE
        assert trk.registry[0xA].info is not None
        assert trk.registry[0xA].info.name.tobytes().decode() == "node-A"

        # Node A goes offline. No online nodes are left standing.
        p_a.close()
        await asyncio.sleep(9)
        assert num_events_a == 5
        assert num_events_b == 3
        assert num_events_c == 2
        assert not trk.registry

        # Finalization.
        trk.close()
        trk.close()  # Idempotency
    finally:
        for p in [p_a, p_b, p_c, p_trk]:
            p.close()
        await asyncio.sleep(1)  # Let all pending tasks finalize properly to avoid stack traces in the output.
