.. _demo_app:

Demo
====

The reader is assumed to have at least skimmed through *The UAVCAN Guide* beforehand.
See `uavcan.org <https://uavcan.org>`_ for details.

This demo has been tested against GNU/Linux and Windows; it is also expected to work with any other major OS.


Custom data types
-----------------

The demo relies on two vendor-specific data types located in the root namespace ``sirius_cyber_corp``.
The root namespace directory layout is as follows::

    sirius_cyber_corp/                              <-- root namespace directory
        PerformLinearLeastSquaresFit.1.0.uavcan     <-- service type definition
        PointXY.1.0.uavcan                          <-- nested message type definition

The referenced DSDL definitions are provided below.

``sirius_cyber_corp.PerformLinearLeastSquaresFit.1.0``:

.. literalinclude:: /../demo/custom_data_types/sirius_cyber_corp/PerformLinearLeastSquaresFit.1.0.uavcan
   :linenos:

``sirius_cyber_corp.PointXY.1.0``:

.. literalinclude:: /../demo/custom_data_types/sirius_cyber_corp/PointXY.1.0.uavcan
   :linenos:


Application source code
-----------------------

The demo relies on the custom data types presented above.
To run it, copy-paste its source code into a file on your computer and update the DSDL paths to match your environment.
The public regulated data types can be cloned from https://github.com/UAVCAN/public_regulated_data_types .

.. literalinclude:: /../demo/demo_app.py
   :linenos:


Just-in-time vs. ahead-of-time DSDL compilation
+++++++++++++++++++++++++++++++++++++++++++++++

The demo application will transpile the required DSDL namespaces just-in-time at launch.
While this approach works for some applications, those that are built for redistribution at large (e.g., via PyPI)
may benefit from compiling DSDL ahead-of-time (at build time)
and including the compilation outputs into the redistributable package.

Ahead-of-time DSDL compilation can be trivially implemented in ``setup.py``:

.. literalinclude:: /../demo/setup.py
   :linenos:


Running the application
-----------------------

If you just run the demo application as-is,
you will notice that it fails with an error referring to some *missing registers*.

As explained in the comments (and --- in great detail --- in the UAVCAN Specification),
registers are basically named values that keep various configuration parameters of the local UAVCAN node (application).
Some of these parameters are used by the business logic of the application (e.g., PID gains);
others are used by the UAVCAN stack (e.g., port-IDs, node-ID, transport configuration, logging, and so on).
Registers of the latter category are all named with the same prefix ``uavcan.``,
and their names and semantics are regulated by the Specification to ensure consistency across the ecosystem.

So the application fails with an error that says that it doesn't know how to reach the UAVCAN network it is supposed
to be part of because there are no registers to read that information from.
We can resolve this by passing the correct register values via environment variables:

..  code-block:: sh

    export UAVCAN__NODE__ID__NATURAL16=42                           # Set the local node-ID 42 (anonymous by default)
    export UAVCAN__UDP__IP__STRING="127.9.0.0"                      # Use UAVCAN/UDP transport via 127.9.0.42 (sic!)
    export UAVCAN__SUB__TEMPERATURE_SETPOINT__ID__NATURAL16=2345    # Subject "temperature_setpoint"    on ID 2345
    export UAVCAN__SUB__TEMPERATURE_MEASUREMENT__ID__NATURAL16=2346 # Subject "temperature_measurement" on ID 2346
    export UAVCAN__PUB__HEATER_VOLTAGE__ID__NATURAL16=2347          # Subject "heater_voltage"          on ID 2347
    export UAVCAN__SRV__LEAST_SQUARES__ID__NATURAL16=123            # Service "least_squares"           on ID 123
    export UAVCAN__DIAGNOSTIC__SEVERITY__NATURAL16=2                # This is optional to enable logging via UAVCAN

    python demo_app.py                                              # Run the application!

The snippet is valid for sh/bash/zsh; if you are using PowerShell on Windows, replace ``export`` with ``$env:``.

An environment variable named like ``UAVCAN__SUB__TEMPERATURE_SETPOINT__ID__NATURAL16``
sets the register ``uavcan.sub.temperature_setpoint.id`` of type ``natural16``.
You can find the name/type mapping details documented in
:func:`pyuavcan.application.register.parse_environment_variables`.

In PyUAVCAN, registers are normally stored in the *register file*, in our case it's ``my_registers.db``
(the UAVCAN Specification does not regulate how the registers are to be stored, this is an implementation detail).
Once you started the application with a specific configuration, it will store the values in the register file,
so the next time you can run it without passing any environment variables at all.

The registers of any UAVCAN node are exposed to other network participants via the standard RPC-services
defined in the standard DSDL namespace ``uavcan.register``.
This means that other nodes on the network can reconfigure our demo application via UAVCAN directly,
without the need to resort to any secondary management interfaces.
This is equally true for software nodes like our demo application and hardware nodes like embedded devices.


Poking the application using Yakut
----------------------------------

The demo is running now so we can interact with it and see how it responds.
We could write another script for that using PyUAVCAN, but in this section we will instead use
`Yakut <https://github.com/UAVCAN/yakut>`_ --- a simple CLI tool for diagnostics and management of UAVCAN networks.


How to use Yakut
++++++++++++++++

If you don't have Yakut installed on your system yet, do it now by following its documentation.

Yakut requires us to compile our DSDL namespaces beforehand using ``yakut compile`` (update paths as necessary):

.. code-block:: sh

    yakut compile  custom_data_types/sirius_cyber_corp  public_regulated_data_types/uavcan

The outputs will be stored in the current working directory.
If you decided to change the working directory or move the compilation outputs,
make sure to export the ``YAKUT_PATH`` environment variable pointing to the correct location.

The commands shown later need to operate on the same network as the demo.
In the above example we configured the demo to use UAVCAN/UDP via 127.9.0.42.
We can specify any other address with prefix 127.9 for Yakut; for instance:

..  code-block:: sh

    export YAKUT_TRANSPORT="UDP('127.9.0.111')"

Again, if you are using PowerShell on Windows, replace ``export`` with ``$env:``.
Further snippets will not include this remark.


Interacting with the application
++++++++++++++++++++++++++++++++

To listen to the demo's heartbeat and diagnostics, run the following commands in new terminals:

..  code-block:: sh

    export YAKUT_TRANSPORT="UDP('127.9.0.111')"
    yakut sub uavcan.node.Heartbeat.1.0     # You should see heartbeats being printed continuously.

..  code-block:: sh

    export YAKUT_TRANSPORT="UDP('127.9.0.111')"
    yakut sub uavcan.diagnostic.Record.1.1  # This one will not show anything yet -- read on.

Now we can actually see how the simple thermostat node is operating.
Add another subscriber to see the published voltage command:

..  code-block:: sh

    export YAKUT_TRANSPORT="UDP('127.9.0.111')"
    yakut sub -M 2347.uavcan.si.unit.voltage.Scalar.1.0

And publish the setpoint along with measurement (process variable):

..  code-block:: sh

    export YAKUT_TRANSPORT="UDP('127.9.0.111')"
    yakut pub 2345.uavcan.si.unit.temperature.Scalar.1.0   'kelvin: 250' \
              2346.uavcan.si.sample.temperature.Scalar.1.0 'kelvin: 240' \
              -N10                                                          # Repeat 10 times

You should see the voltage subscriber (subject-ID 2347) print something along these lines:

..  code-block:: yaml

    ---
    2347:
      volt: 1.1999999284744263

    # And so on...

Okay, the thermostat is working.
If you change the setpoint (subject-ID 2345) or measurement (subject-ID 2346),
you will see the published command messages (subject-ID 2347) update accordingly.

One important feature of the register interface is that it allows one to monitor internal states of the application,
which is critical for debugging.
In some way it is similar to performance counters or tracing probes:

..  code-block:: sh

    yakut call 42 uavcan.register.Access.1.0 'name: {name: thermostat.error}'

We will see the current value of the temperature error registered by the thermostat:

..  code-block:: yaml

    ---
    384:
      timestamp:
        microsecond: 0
      mutable: false
      persistent: false
      value:
        real32:
          value:
          - 10.0

Field ``mutable: false`` says that this register cannot be modified and ``persistent: false`` says that
it is not committed to any persistent storage. Together they mean that the value is computed at runtime dynamically.

We can use the very same interface to query or modify the configuration parameters.
For example, we can change the PID gains of the thermostat:

..  code-block:: sh

    yakut call 42 uavcan.register.Access.1.0 '{name: {name: thermostat.pid.gains}, value: {integer8: {value: [2, 0, 0]}}}'

Which results in:

..  code-block:: yaml

    ---
    384:
      timestamp:
        microsecond: 0
      mutable: true
      persistent: true
      value:
        real32:
          value:
          - 2.0
          - 0.0
          - 0.0

A careful reader would notice that the assigned value was of type ``integer8``, whereas the result is ``real32``.
This is because the register server does implicit type conversion to the type specified by the application.
The UAVCAN Specification does not require this behavior, though, so some simpler nodes (embedded systems in particular)
may just reject mis-typed requests.

If you restart the application now, you will see it use the the updated PID gains.

Now let's try the linear regression service:

.. code-block:: sh

    yakut call 42 123.sirius_cyber_corp.PerformLinearLeastSquaresFit.1.0 'points: [{x: 10, y: 3}, {x: 20, y: 4}]'

The response should look like:

..  code-block:: yaml

    ---
    123:
      slope: 0.1
      y_intercept: 2.0

And the diagnostic subscriber we started in the beginning should print a log record.


Building a network
------------------

In this section we will introduce an additional node that will simulate the controlled plant.

TODO

.. literalinclude:: /../demo/plant.py
   :linenos:


Orchestration
-------------

..  important::

    Yakut Orchestrator is in an alpha preview stage.
    Breaking changes may be introduced between minor versions until Yakut v1.0 is released.

    Yakut Orchestrator does not support Windows currently.

Manual management of environment variables and node processes may work in simple setups, but it doesn't really scale.
Practical cyber-physical systems require a better way of managing UAVCAN networks that may simultaneously include
software nodes executed on the local or remote computers along with specialized bare-metal nodes running on
dedicated hardware.

One solution to this is Yakut Orchestrator --- an interpreter of a simple YAML-based domain-specific language
that allows one to define process groups and manage them atomically.
The language has first-class support for registers --- instead of relying on environment variables,
one can define registers using a human-friendly syntax without the need to explicitly specify their types
(the tool will deduce the correct types automatically).

Here's an example orchestration file (orc-file) ``launch.orc.yaml``:

.. literalinclude:: /../demo/launch.orc.yaml
   :linenos:
   :language: yaml

Those familiar with ROS may find it somewhat similar to *roslaunch*.

The orc-file can be executed as ``yakut orc launch.orc.yaml``, or simply ``./launch.orc.yaml``
(use ``--verbose`` to see which environment variables are passed to each launched process).
Having started it, execute the setpoint & measurement publication command introduced earlier,
and you should see the following output appear in the terminal:

..  code-block:: yaml

    ---
    8184:
      _metadata_:
        timestamp:
          system: 1613597110.155263
          monotonic: 1149486.479633
        priority: optional
        transfer_id: 0
        source_node_id: 42
      timestamp:
        microsecond: 1613597110154721
      severity:
        value: 2
      text: 'root: Application started with PID gains: 0.100 0.000 0.000'

    ---
    2347:
      volt: 1.1999999284744263

    ---
    2347:
      volt: 1.1999999284744263

    # And so on...

For more info about this tool refer to the Yakut documentation.
