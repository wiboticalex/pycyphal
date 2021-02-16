# Copyright (c) 2020 UAVCAN Consortium
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

import os
import sys
import math
import shutil
from typing import Iterable, Dict, Iterator, Tuple, List
import asyncio
from pathlib import Path
import dataclasses
import pytest
import pyuavcan
from ._subprocess import BackgroundChildProcess


DEMO_APP_NODE_ID = 42
DEMO_DIR = Path(__file__).absolute().parent.parent.parent / "demo"


def mirror(env: Dict[str, str]) -> Dict[str, str]:
    maps = {
        "UAVCAN__PUB__": "UAVCAN__SUB__",
        "UAVCAN__SRV__": "UAVCAN__CLN__",
    }
    maps.update({v: k for k, v in maps.items()})

    def impl() -> Iterator[Tuple[str, str]]:
        for k, v in env.items():
            for m in maps:
                if m in k:
                    k = k.replace(m, maps[m])
                    break
            yield k, v

    return dict(impl())


@dataclasses.dataclass(frozen=True)
class RunConfig:
    env: Dict[str, str]


def _get_run_configs() -> Iterable[RunConfig]:
    yield RunConfig({"UAVCAN__UDP__IP__STRING": "127.0.0.0"})
    yield RunConfig({"UAVCAN__SERIAL__PORT__STRING": "socket://localhost:50905"})
    yield RunConfig(
        {
            "UAVCAN__UDP__IP__STRING": "127.0.0.0",
            "UAVCAN__SERIAL__PORT__STRING": "socket://localhost:50905",
        }
    )
    if sys.platform.startswith("linux"):
        yield RunConfig(
            {
                "UAVCAN__CAN__IFACE__STRING": "socketcan:vcan0",
                "UAVCAN__CAN__MTU__NATURAL16": "8",
            }
        )
        yield RunConfig(
            {
                "UAVCAN__CAN__IFACE__STRING": " ".join(f"socketcan:vcan{i}" for i in range(3)),
                "UAVCAN__CAN__MTU__NATURAL16": "64",
            }
        )


@pytest.mark.parametrize("parameters", [(idx == 0, rc) for idx, rc in enumerate(_get_run_configs())])  # type: ignore
@pytest.mark.asyncio  # type: ignore
async def _unittest_slow_demo_app(
    compiled: Iterator[List[pyuavcan.dsdl.GeneratedPackageInfo]],
    parameters: Tuple[bool, RunConfig],
) -> None:
    import uavcan.node
    import uavcan.register
    import uavcan.si.sample.temperature
    import uavcan.si.unit.temperature
    import uavcan.si.unit.voltage
    import sirius_cyber_corp
    import pyuavcan.application  # pylint: disable=redefined-outer-name

    asyncio.get_running_loop().slow_callback_duration = 3.0
    _ = compiled

    first_run, run_config = parameters
    if first_run:
        # At the first run, force the demo script to regenerate packages.
        # The following runs shall not force this behavior to save time and enhance branch coverage.
        print("FORCE DSDL RECOMPILATION")
        shutil.rmtree(Path(".demo_dsdl_compiled").resolve(), ignore_errors=True)

    # The demo may need to generate packages as well, so we launch it first.
    env = run_config.env.copy()
    env.update(
        {
            # Other registers beyond the transport settings:
            "UAVCAN__NODE__ID__NATURAL16": str(DEMO_APP_NODE_ID),
            "UAVCAN__DIAGNOSTIC__SEVERITY__NATURAL16": "2",
            "UAVCAN__DIAGNOSTIC__TIMESTAMP__BIT": "1",
            "UAVCAN__SUB__TEMPERATURE_SETPOINT__ID__NATURAL16": "2345",
            "UAVCAN__SUB__TEMPERATURE_MEASUREMENT__ID__NATURAL16": "2346",
            "UAVCAN__PUB__HEATER_VOLTAGE__ID__NATURAL16": "2347",
            "UAVCAN__SRV__LEAST_SQUARES__ID__NATURAL16": "123",
            "THERMOSTAT__PID__GAINS__REAL32": "0.1 0.0 0.0",  # Gain 0.1
            # Various low-level items:
            "PYUAVCAN_LOGLEVEL": "INFO",
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),  # https://github.com/appveyor/ci/issues/1995
        }
    )
    demo_proc = BackgroundChildProcess(
        "python",
        "-m",
        "coverage",
        "run",
        str(DEMO_DIR / "demo_app.py"),
        environment_variables=env,
    )
    assert demo_proc.alive
    print("DEMO APP STARTED WITH PID", demo_proc.pid, "FROM", Path.cwd())

    try:
        local_node_info = uavcan.node.GetInfo_1_0.Response(
            protocol_version=uavcan.node.Version_1_0(*pyuavcan.UAVCAN_SPECIFICATION_VERSION),
            software_version=uavcan.node.Version_1_0(*pyuavcan.__version_info__[:2]),
            name="org.uavcan.pyuavcan.test.demo_app",
        )
        env = mirror(env)
        env["UAVCAN__NODE__ID__NATURAL16"] = "123"
        registers = pyuavcan.application.register.parse_environment_variables(env)
        node = pyuavcan.application.make_node(local_node_info, registers=registers)
        node.start()
        del node.registry["thermostat*"]
    except Exception:
        demo_proc.kill()
        raise

    try:
        sub_heartbeat = node.make_subscriber(uavcan.node.Heartbeat_1_0)
        cln_get_info = node.make_client(uavcan.node.GetInfo_1_0, DEMO_APP_NODE_ID)
        cln_command = node.make_client(uavcan.node.ExecuteCommand_1_1, DEMO_APP_NODE_ID)
        cln_register = node.make_client(uavcan.register.Access_1_0, DEMO_APP_NODE_ID)

        pub_setpoint = node.make_publisher(uavcan.si.unit.temperature.Scalar_1_0, "temperature_setpoint")
        pub_measurement = node.make_publisher(uavcan.si.sample.temperature.Scalar_1_0, "temperature_measurement")
        sub_heater_voltage = node.make_subscriber(uavcan.si.unit.voltage.Scalar_1_0, "heater_voltage")
        cln_least_squares = node.make_client(
            sirius_cyber_corp.PerformLinearLeastSquaresFit_1_0, DEMO_APP_NODE_ID, "least_squares"
        )

        # At the first run, the usage demo might take a long time to start because it has to compile DSDL.
        # That's why we wait for it here to announce readiness by subscribing to the heartbeat.
        assert demo_proc.alive
        first_hb_transfer = await sub_heartbeat.receive_for(100.0)  # Pick a sensible start-up timeout.
        print("FIRST HEARTBEAT:", first_hb_transfer)
        assert first_hb_transfer
        assert first_hb_transfer[1].source_node_id == DEMO_APP_NODE_ID
        assert first_hb_transfer[1].transfer_id < 10  # We may have missed a couple but not too many!
        assert demo_proc.alive
        # Once the heartbeat is in, we know that the demo is ready for being tested.

        # Validate GetInfo.
        cln_get_info.priority = pyuavcan.transport.Priority.EXCEPTIONAL
        cln_get_info.transfer_id_counter.override(22)
        info_transfer = await cln_get_info.call(uavcan.node.GetInfo_1_0.Request())
        print("GET INFO RESPONSE:", info_transfer)
        assert info_transfer
        info, transfer = info_transfer
        assert transfer.source_node_id == DEMO_APP_NODE_ID
        assert transfer.transfer_id == 22
        assert transfer.priority == pyuavcan.transport.Priority.EXCEPTIONAL
        assert isinstance(info, uavcan.node.GetInfo_1_0.Response)
        assert info.name.tobytes().decode() == "org.uavcan.pyuavcan.demo.demo_app"
        assert info.protocol_version.major == pyuavcan.UAVCAN_SPECIFICATION_VERSION[0]
        assert info.protocol_version.minor == pyuavcan.UAVCAN_SPECIFICATION_VERSION[1]
        assert info.software_version.major == 1
        assert info.software_version.minor == 0

        # Test the linear regression service.
        solution_transfer = await cln_least_squares.call(
            sirius_cyber_corp.PerformLinearLeastSquaresFit_1_0.Request(
                points=[
                    sirius_cyber_corp.PointXY_1_0(x=1, y=2),
                    sirius_cyber_corp.PointXY_1_0(x=10, y=20),
                ]
            )
        )
        print("LINEAR REGRESSION RESPONSE:", info_transfer)
        assert solution_transfer
        solution, transfer = solution_transfer
        assert transfer.source_node_id == DEMO_APP_NODE_ID
        assert transfer.transfer_id == 0
        assert transfer.priority == pyuavcan.transport.Priority.NOMINAL
        assert isinstance(solution, sirius_cyber_corp.PerformLinearLeastSquaresFit_1_0.Response)
        assert solution.slope == pytest.approx(2.0)
        assert solution.y_intercept == pytest.approx(0.0)

        solution_transfer = await cln_least_squares.call(sirius_cyber_corp.PerformLinearLeastSquaresFit_1_0.Request())
        print("LINEAR REGRESSION RESPONSE:", info_transfer)
        assert solution_transfer
        solution, _ = solution_transfer
        assert isinstance(solution, sirius_cyber_corp.PerformLinearLeastSquaresFit_1_0.Response)
        assert not math.isfinite(solution.slope)
        assert not math.isfinite(solution.y_intercept)

        # Validate the thermostat.
        for _ in range(2):
            assert await pub_setpoint.publish(uavcan.si.unit.temperature.Scalar_1_0(kelvin=315.0))
            assert await pub_measurement.publish(uavcan.si.sample.temperature.Scalar_1_0(kelvin=300.0))
            await asyncio.sleep(0.5)
        rx_voltage = await sub_heater_voltage.receive_for(timeout=3.0)
        assert rx_voltage
        msg_voltage, _ = rx_voltage
        assert isinstance(msg_voltage, uavcan.si.unit.voltage.Scalar_1_0)
        assert msg_voltage.volt == pytest.approx(1.5)  # The error is 15 kelvin, P-gain is 0.1 (see env vars above)

        # Check the state registers.
        rx_access = await cln_register.call(
            uavcan.register.Access_1_0.Request(uavcan.register.Name_1_0("thermostat.setpoint"))
        )
        assert rx_access
        access_resp, _ = rx_access
        assert isinstance(access_resp, uavcan.register.Access_1_0.Response)
        assert not access_resp.mutable
        assert not access_resp.persistent
        assert access_resp.value.real32
        assert access_resp.value.real32.value[0] == pytest.approx(315.0)

        rx_access = await cln_register.call(
            uavcan.register.Access_1_0.Request(uavcan.register.Name_1_0("thermostat.error"))
        )
        assert rx_access
        access_resp, _ = rx_access
        assert isinstance(access_resp, uavcan.register.Access_1_0.Response)
        assert not access_resp.mutable
        assert not access_resp.persistent
        assert access_resp.value.real32
        assert access_resp.value.real32.value[0] == pytest.approx(15.0)

        # Test the command execution service.
        # Bad command.
        result_transfer = await cln_command.call(
            uavcan.node.ExecuteCommand_1_1.Request(
                command=uavcan.node.ExecuteCommand_1_1.Request.COMMAND_STORE_PERSISTENT_STATES
            )
        )
        print("BAD COMMAND RESPONSE:", info_transfer)
        assert result_transfer
        result, transfer = result_transfer
        assert transfer.source_node_id == DEMO_APP_NODE_ID
        assert transfer.transfer_id == 0
        assert transfer.priority == pyuavcan.transport.Priority.NOMINAL
        assert isinstance(result, uavcan.node.ExecuteCommand_1_1.Response)
        assert result.status == result.STATUS_BAD_COMMAND
        # Factory reset -- remove the register file.
        assert demo_proc.alive
        result_transfer = await cln_command.call(
            uavcan.node.ExecuteCommand_1_1.Request(command=uavcan.node.ExecuteCommand_1_1.Request.COMMAND_FACTORY_RESET)
        )
        print("FACTORY RESET COMMAND RESPONSE:", info_transfer)
        assert result_transfer
        result, transfer = result_transfer
        assert transfer.source_node_id == DEMO_APP_NODE_ID
        assert transfer.transfer_id == 1
        assert transfer.priority == pyuavcan.transport.Priority.NOMINAL
        assert isinstance(result, uavcan.node.ExecuteCommand_1_1.Response)
        assert result.status == result.STATUS_SUCCESS

        # Validate the heartbeats (all of them).
        prev_hb_transfer = first_hb_transfer
        num_heartbeats = 0
        while True:
            hb_transfer = await sub_heartbeat.receive_for(0.1)
            if hb_transfer is None:
                break
            hb, transfer = hb_transfer
            assert num_heartbeats <= transfer.transfer_id <= 300
            assert transfer.priority == pyuavcan.transport.Priority.NOMINAL
            assert transfer.source_node_id == DEMO_APP_NODE_ID
            assert hb.health.value == hb.health.NOMINAL
            assert hb.mode.value == hb.mode.OPERATIONAL
            assert num_heartbeats <= hb.uptime <= 300
            assert hb.uptime == prev_hb_transfer[0].uptime + 1
            assert transfer.transfer_id == prev_hb_transfer[1].transfer_id + 1
            prev_hb_transfer = hb_transfer
            num_heartbeats += 1
        assert num_heartbeats > 0

        demo_proc.wait(10.0, interrupt=True)
    finally:
        node.close()
        demo_proc.kill()
        await asyncio.sleep(2.0)  # Let coroutines terminate properly to avoid resource usage warnings.
