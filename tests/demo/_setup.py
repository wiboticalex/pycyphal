# Copyright (c) 2021 UAVCAN Consortium
# This software is distributed under the terms of the MIT License.
# Author: Pavel Kirienko <pavel@uavcan.org>

from typing import Any
from ._subprocess import BackgroundChildProcess


def _unittest_slow_demo_setup_py(cd_to_demo: Any) -> None:
    _ = cd_to_demo
    proc = BackgroundChildProcess("python", "setup.py", "build")
    exit_code, stdout = proc.wait(120)
    print(stdout)
    assert exit_code == 0
