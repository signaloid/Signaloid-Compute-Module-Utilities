# Signaloid Compute Module Utilities
This repository offers a set of common C and Python libraries for building host applications that interact
with Signaloid Compute modules like the [Signaloid C0-microSD hot-pluggable hardware module](https://github.com/signaloid/C0-microSD-hardware), as well as toolkits, which you can use to flash new firmware to the devices.

Each toolkit and library targets specific hardware.

## Supported hardware

Three compute modules (the **C0-microSD**, the **C0-microSD+** and the **C0-SD**) and the
**SD-Dev** carrier board that hosts them on a Raspberry Pi.

## Toolkits
Each script is available at the repository root as a symlink into `src/python/signaloid_utilities/`.
Each tool name below links to its documentation.

| Tool                                                                                                | Runs on                             | Acts on                                         | What it does                                                                           |
| --------------------------------------------------------------------------------------------------- | ----------------------------------- | ----------------------------------------------- | -------------------------------------------------------------------------------------- |
| [`C0_microSD_toolkit.py`](./src/python/signaloid_utilities/c0microsd/README.md)                     | a Linux or macOS host               | a C0-microSD in that host's card slot           | Flash bitstreams, firmware and user data. Switch boot mode. Print device info          |
| [`C0_SD_toolkit.py`](./src/python/signaloid_utilities/c0sd/README.md)                               | a Linux or macOS host               | a C0-microSD+ or C0-SD in that host's card slot | Flash bitstreams and application binaries. Inspect status. Apply configuration actions |
| [`C0_debug_logger.py`](./src/python/signaloid_utilities/README.md#using-the-c0_debug_loggerpy-tool) | a Linux or macOS host               | any compute module in that host's card slot     | Read and follow the device's debug log                                                 |
| [`SD_Dev_toolkit.py`](./src/python/signaloid_utilities/sddev/README.md)                             | the Raspberry Pi hosting the SD-Dev | the SD-Dev's own GPIO lines                     | Detect and power-cycle the onboard SD cards                                            |
| [`SD_Dev_power_measure.py`](./src/python/signaloid_utilities/sddev/README.md)                       | the Raspberry Pi hosting the SD-Dev | the SD-Dev's onboard ADC, over I2C              | Read and log power measurements from the onboard current-sense circuitry               |

## Libraries

| Library                                                                                          | Language      | Runs on                               | Acts on                                                         |
| ------------------------------------------------------------------------------------------------ | ------------- | ------------------------------------- | --------------------------------------------------------------- |
| [`src/c/`](./src/c/README.md), device side                                                       | C             | the compute module                    | the module's own registers, LEDs, and debug pins                |
| [`src/c/`](./src/c/README.md), host side                                                         | C             | a Linux or macOS host                 | a compute module in that host's card slot                       |
| [`src/python/signaloid_utilities/`](./src/python/signaloid_utilities/README.md), except `sddev/` | Python 3.10+  | a Linux or macOS host                 | a compute module in that host's card slot                       |
| [`src/python/signaloid_utilities/sddev/`](./src/python/signaloid_utilities/sddev/README.md)      | Python 3.10+  | the Raspberry Pi hosting the SD-Dev   | the SD-Dev's own GPIO lines and onboard ADC, on that same board |
| [`src/circuitpython/`](./src/circuitpython/README.md)                                            | CircuitPython | a CircuitPython microcontroller board | a compute module wired to that board's SPI bus                  |


The C tree covers both sides of the interface. A Hardware Abstraction Layer serves code that runs on
the module, so one application source tree can target any compute module unchanged by setting
`BUILD_FOR`. Host-side libraries serve C host applications that control a module over its
block-device interface.

## Requirements
This package requires **Python 3.10 or later**. The host-side Python tools and libraries run on Linux and macOS, the only officially tested operating systems. 
The two SD-Dev tools run on the Raspberry Pi hosting the SD-Dev.

The `C0_microSD_toolkit.py`, `C0_SD_toolkit.py`, and `C0_debug_logger.py` scripts require no additional libraries.

The `SD_Dev_toolkit.py` and `SD_Dev_power_measure.py` scripts require the `smbus`, `gpiozero`, and `lgpio` packages. See [requirements.txt](./src/python/signaloid_utilities/sddev/requirements.txt).

## Installing the Python package
The Python side of this repository is also packaged, as `signaloid-utilities`. Installing it puts the
toolkits on your `PATH` and makes `signaloid_utilities` importable from your own code, so you do not
have to run the scripts from a clone.

```sh
pip install "git+https://github.com/signaloid/Signaloid-Compute-Module-Utilities.git"
```

Each package README covers installation and shows an import example for that compute module. See
[c0microsd](./src/python/signaloid_utilities/c0microsd/README.md#installation),
[c0sd](./src/python/signaloid_utilities/c0sd/README.md#installation), and
[sddev](./src/python/signaloid_utilities/sddev/README.md#installation).

## Repository layout

```
C0_*.py, SD_Dev_*.py ....... symlinks to the toolkit entry points under src/python/
src/
  c/ ....................... C HAL and logger for the module, plus host-side C libraries
  python/ .................. host-side Python package (signaloid_utilities)
  circuitpython/ ........... CircuitPython port of the compute-module interface
```
