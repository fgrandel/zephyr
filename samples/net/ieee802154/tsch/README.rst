.. _tsch-sample:

TSCH Sample Application
#######################

Overview
********

The TSCH sample application can be built as a full functionality PAN coordinator
and as a reduced functionality end device. It configures a minimal schedule and
works similarly as the echo sample. The end device will automatically join the
TSCH PAN network based on beacons sent out by the PAN coordinator and will then
start to receive and echo packets sent by the coordinator.

The source code for this sample application can be found at:
:zephyr_file:`samples/net/ieee802154/tsch`.

Requirements
************

The sample currently supports boards with TI's CC13xx Sub-Gigahertz SoCs. In
principle it can be built with any SoC that supports timed TX. At least two
boards are required, one configured as PAN coordinator, the other as an end
device.

Building and Running
********************

The following configuration files will provide the base configuration and extra
configuration depending on the chosen device role:

- :file:`prj.conf`
  Basic configuration file that sets the common configuration for a TSCH network
  and contains several commented out configuration options that might optionally
  be of interest in the TSCH context.

- :file:`overlay-pancoordinator.conf`
  Additional configuration required to turn the device into a PAN coordinator.
  Exactly one PAN coordinator is required per PAN.

- :file:`overlay-enddevice.conf`
  Additional configuration required to turn the device into an end device. One
  or several end devices can be added to the network.

To build the PAN coordinator for one of the CC1352 Sub-Gigahertz SensorTag
development boards:

.. zephyr-app-commands::
   :zephyr-app: samples/net/ieee802154/tsch
   :board: [cc1352r_sensortag | cc1352p1_launchxl]
   :conf: "prj.conf overlay-pancoordinator.conf"
   :gen-args: -DEXTRA_DTC_OVERLAY_FILE=ti-cc13xx.overlay
   :goals: build
   :compact:

To build an end device for one of the CC1352 Sub-Gigahertz SensorTag development
boards:

.. zephyr-app-commands::
   :zephyr-app: samples/net/ieee802154/tsch
   :board: [cc1352r_sensortag | cc1352p1_launchxl]
   :conf: "prj.conf overlay-enddevice.conf"
   :gen-args: -DEXTRA_DTC_OVERLAY_FILE=ti-cc13xx.overlay
   :goals: build
   :compact:
