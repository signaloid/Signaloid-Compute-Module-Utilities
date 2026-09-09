# C0-microSD Utilities

Host-side Python support for the [Signaloid C0-microSD hot-pluggable hardware
module](https://github.com/signaloid/C0-microSD-hardware).

|                  |                                                                                     |
| ---------------- | ----------------------------------------------------------------------------------- |
| **Runs on**      | a Linux or macOS host                                                               |
| **Acts on**      | a C0-microSD in that host's card slot                                               |
| **Tool**         | `C0_microSD_toolkit.py` (symlinked at the repository root)                          |
| **Library**      | `interface.py`, providing `C0microSDInterface` and `C0microSDSignaloidSoCInterface` |
| **Requirements** | Python 3.10 or later, no additional packages                                        |

The [`c0sd`](../c0sd/README.md) package handles the C0-microSD+ and the C0-SD. To control a
C0-microSD from a microcontroller over SPI instead of from a host computer, see
[CircuitPython Support for the Signaloid C0 Compute Modules](../../../circuitpython/README.md).

## Installation

These libraries and toolkits ship as the `signaloid-utilities` Python package. Installing it puts
the toolkits on your `PATH` and makes `signaloid_utilities` importable from your own code.

Install the latest revision straight from the repository:

```sh
pip install "git+https://github.com/signaloid/Signaloid-Compute-Module-Utilities.git"
```

The package needs nothing beyond the Python standard library, so a plain virtual environment is
enough.

### Import example

`C0microSDSignaloidSoCInterface` reads the device status as it is constructed, so the active
configuration is available immediately:

```python
from signaloid_utilities.c0microsd.interface import C0microSDSignaloidSoCInterface

c0 = C0microSDSignaloidSoCInterface("/dev/sda")

print(c0.configuration)          # 'bootloader' or 'soc'
print(c0.configuration_version)

# Send a word to the Signaloid SoC and read its reply.
c0.write_signaloid_soc_MOSI_buffer(b"\x01\x00\x00\x00")
print(c0.read_signaloid_soc_MISO_buffer(4))
```

Reading and writing a raw block device needs elevated privileges, so run your script with `sudo`
just as you would the toolkit.

## Interfacing with the Signaloid C0-microSD
When connected to a host computer, the Signaloid C0-microSD presents itself as an unformatted block
storage device. Your host application communicates with the device through block reads and writes to
a set of pre-defined addresses. The C0-microSD can operate in two different modes when connected to
a host, `Bootloader` mode and `Signaloid SoC` mode.

- `Bootloader` mode allows flashing new bitstreams and firmware to the device.
- `Signaloid SoC` mode runs the built-in Signaloid SoC, which features a subset of Signaloid's
  uncertainty-tracking technology.

Interfacing with the C0-microSD varies depending on the active mode. `interface.py` provides the
classes for building host applications that talk to the device while the Signaloid SoC mode is
active.

## Using the `C0_microSD_toolkit.py` tool
You can use the `C0_microSD_toolkit.py` Python script to configure the C0-microSD and flash new
firmware. The script uses only Python standard libraries. Following are the program's command-line arguments and usage examples:

```
usage: C0_microSD_toolkit.py [-h] -t TARGET_DEVICE [-b INPUT_FILE] [-p PAD_SIZE]
                             [-u | -U | -q | -w | -s | -i | -y] [-f]

Signaloid C0-microSD-toolkit. Version 2.0

options:
  -h, --help        Show this help message and exit.
  -t TARGET_DEVICE  Specify the target device path.
  -b INPUT_FILE     Specify the input file for flashing (required with -u, -q, or -w).
  -p PAD_SIZE       Pad input file with zeros to target size.
  -u                Flash user data.
  -U                Flash user data with auto switching to and from bootloader mode.
  -q                Flash new Bootloader bitstream.
  -w                Flash new Signaloid SoC bitstream.
  -s                Switch boot mode.
  -i                Print target C0-microSD information, and run data verification.
  -y                Flash warmboot sector.
  -f                Force flash sequence (do not check for bootloader).
```

> [!IMPORTANT]
> All options except of `-s` and `-U` require the C0-microSD to be in **Bootloader** mode. The `-U`
> option switches to bootloader mode itself, flashes, and switches back, prompting you to reboot
> the device at each switch.

### Examples
The following examples assume that the C0-microSD is located in`/dev/sda`.

Flash new custom user bitstream:
```sh
sudo python3 ./C0_microSD_toolkit.py -t /dev/sda -b user-bitstream.bin
```

Flash new user data:
```sh
sudo python3 ./C0_microSD_toolkit.py -t /dev/sda -b program.bin -u
```

Flash new Bootloader bitstream:
```sh
sudo python3 ./C0_microSD_toolkit.py -t /dev/sda -b bootloader-bitstream.bin -q
```

Flash new Signaloid SoC bitstream:
```sh
sudo python3 ./C0_microSD_toolkit.py -t /dev/sda -b signaloid-soc.bin -w
```

Toggle boot mode of C0-microSD:
```sh
sudo python3 ./C0_microSD_toolkit.py -t /dev/sda -s
```

Print target C0-microSD information and verify loaded bitstreams:
```sh
sudo python3 ./C0_microSD_toolkit.py -t /dev/sda -i
```

> [!NOTE]
> Using the `-s` option toggles the active configuration. If the device has booted in `Bootloader`
> mode, this option switches it to `Signaloid SoC` mode. If the device has booted in `Signaloid SoC`
> mode, this option switches it to `Bootloader` mode.
