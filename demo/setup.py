#!/usr/bin/env python
# This is a setup.py demo that shows how to distribute compiled DSDL definitions with Python packages.
# This is better than checking in generated code under version control (seriously, don't do this).
# Distributed under CC0 1.0 Universal (CC0 1.0) Public Domain Dedication.
# type: ignore

import setuptools
import logging
import distutils.command.build_py
from pathlib import Path

NAME = "demo_app"

DSDL_NAMESPACE_DIRS = [  # DSDL namespace directories that are to be compiled.
    "public_regulated_data_types/uavcan",  # All UAVCAN applications without exception need the standard namespace.
    "custom_data_types/sirius_cyber_corp",
    # "public_regulated_data_types/reg",  # Many applications need the non-standard regulated namespace as well.
]


# noinspection PyUnresolvedReferences
class BuildPy(distutils.command.build_py.build_py):
    def run(self):
        # The application should expect to find the compiled DSDL under this path in the distribution archive:
        output = Path(self.build_lib, NAME, "compiled_dsdl").resolve()
        if not self.dry_run:
            import pyuavcan

            for nd in DSDL_NAMESPACE_DIRS:
                pyuavcan.dsdl.compile(nd, lookup_directories=DSDL_NAMESPACE_DIRS, output_directory=output)
        super().run()


logging.basicConfig(level=logging.INFO, format="%(levelname)-3.3s %(name)s: %(message)s")

# This is only a basic example. Normally you would use setup.cfg or pyproject.toml.
setuptools.setup(
    name=NAME,
    py_modules=["demo_app"],
    cmdclass={"build_py": BuildPy},  # Override the standard build_py command to add code generation step.
)
