# Signaloid SD-Dev

This module provides tools that run on the Raspberry Pi carrying the Signaloid SD-Dev. The tools
drive the SD-Dev's GPIO lines to detect, reset, and power-cycle the compute modules in its sockets,
and read the SD-Dev's onboard ADC over I2C to measure the current those modules draw. The tools do
not communicate with the compute modules themselves.

|                  |                                                                                                                       |
| ---------------- | --------------------------------------------------------------------------------------------------------------------- |
| **Runs on**      | a Raspberry Pi hosting the SD-Dev                                                                                     |
| **Acts on**      | the SD-Dev's own GPIO lines and onboard ADC, on that same board                                                       |
| **Tools**        | `SD_Dev_toolkit.py`, `SD_Dev_power_measure.py` (symlinked at the repository root)                                     |
| **Library**      | `sddev.py`, providing `SDDevController` and `SDDevADCInterface`                                                       |
| **Requirements** | Python 3.10 or later, plus the `smbus`, `gpiozero`, and `lgpio` packages. See [requirements.txt](./requirements.txt). |

## Installation

These tools ship as part of the `signaloid-utilities` Python package, but unlike the compute-module
packages they need system libraries and real GPIO, so they only work on the Raspberry Pi hosting the
SD-Dev.

1. Install the prerequisite system packages:
	```sh
	sudo apt update
	sudo apt install liblgpio-dev python3-dev swig build-essential -y
	```

2. Create a virtual environment and install the package along with the `smbus`, `gpiozero`, and
   `lgpio` dependencies:
	```sh
	python3 -m venv .venv
	source .venv/bin/activate
	pip install -r requirements.txt
	pip install "git+https://github.com/signaloid/Signaloid-Compute-Module-Utilities.git"
	```

`SD_Dev_power_measure.py` reads the ADC over I2C, so enable the I2C kernel module as well. On the
official Raspberry Pi OS images, `raspi-config` does that.

Installing the package puts both tools on your `PATH` as `SDDev-toolkit` and
`SDDev-power-measure`. They take the same arguments as the scripts documented below, so
`SDDev-toolkit -p -v` and `python3 ./SD_Dev_toolkit.py -p -v` are equivalent.

### Import example

Importing `sddev` takes ownership of the SD-Dev's GPIO pins at module level, so import it only on
the Raspberry Pi, and only when no other program has taken ownership of those pins.

```python
from signaloid_utilities.sddev.sddev import SDDevADCInterface, SDDevController

controller = SDDevController()
full_size_sd_present, micro_sd_present = controller.detect_cards()
controller.refresh_sd_cards(dynamic=True)

# Channel 1 is the microSD socket, channel 0 the full-size SD socket.
adc = SDDevADCInterface(target_smbus_number=1, channel=1)
print(adc.read_converted_current_measurement(), "A")
```

## Using the `SD_Dev_toolkit.py` tool
You can use the `SD_Dev_toolkit.py` to detect and power-cycle the SD cards on-board the SD-Dev.
```
usage: SD_Dev_toolkit.py [-h] [-p] [-v]

Signaloid SD_Dev_toolkit. Version 0.1

options:
  -h, --help         Show this help message and exit.
  -p, --power-cycle  Power-cycle the onboard full-size SD and microSD cards.
  -v, --verbose      Verbose printing
```

## Using the `SD_Dev_power_measure.py` tool
You can use the `SD_Dev_power_measure.py` to read and log power measurement data using the SD-Dev
on-board current sense circuitry. ADC channel 0 corresponds to the full-size SD card socket and
channel 1 to the microSD card socket. This needs the I2C kernel module enabled, as described under
Installation above.
```
usage: SD_Dev_power_measure.py [-h] [-s SMBUS_NUMBER] [-o OUTPUT_FILENAME] [-c {0,1}] [-g {1,2,4,8}] [-r {12,14,16}]

Signaloid SD_Dev_power_measure. Version 0.1

options:
  -h, --help            Show this help message and exit.
  -s SMBUS_NUMBER, --smbus-number SMBUS_NUMBER
                        Specify the target smbus number. (default: 1)
  -o OUTPUT_FILENAME, --output_filename OUTPUT_FILENAME
                        Filename of output csv file. When set, the application will log measurements to this file. (default: None)
  -c {0,1}, --channel {0,1}
                        ADC channel. Channel 0 corresponds to the full-size SD card socket and channel 1 to the microSD card socket. (default: 1)
  -g {1,2,4,8}, --gain {1,2,4,8}
                        ADC Programmable Gain Amplifier (PGA) gain. (default: 4)
  -r {12,14,16}, --samle-rate-bits {12,14,16}
                        Sample bits. (default: 12)
```
