# C0-SD Family Utilities

Host-side Python support for the Signaloid **C0-SD family** of compute modules, namely the
**C0-microSD+** and the **C0-SD**.

|                   |                                                                        |
| ----------------- | ---------------------------------------------------------------------- |
| **Runs on**       | a Linux or macOS host                                                  |
| **Acts on**       | a C0-microSD+ or C0-SD in that host's card slot                        |
| **Tool**          | `C0_SD_toolkit.py` (symlinked at the repository root)                  |
| **Library**       | `interface.py`, providing `C0microSDPlusInterface` and `C0SDInterface` |
| **Register maps** | `regmaps/c0microsdplus/`, `regmaps/c0sd/` (auto-generated)             |
| **Requirements**  | Python 3.10 or later, no additional packages                           |

The [`c0microsd`](../c0microsd/README.md) package handles the original C0-microSD. To control
either module from a microcontroller over SPI instead of from a host computer, see
[CircuitPython Support for the Signaloid C0 Compute Modules](../../../circuitpython/README.md).

## Installation

These libraries and toolkits ship as the `signaloid-utilities` Python package. Installing it puts
the toolkits on your `PATH` and makes `signaloid_utilities` importable from your own code.

Install the latest revision straight from the repository:

```sh
pip install "git+https://github.com/signaloid/Signaloid-Compute-Module-Utilities.git"
```

The package needs nothing beyond the Python standard library, so a plain virtual environment is
enough. The package bundles the register maps for both variants.

### Import example

Pick the class for your module, `C0microSDPlusInterface` or `C0SDInterface`. Both take the device
path and expose the same API.

```python
from signaloid_utilities.c0sd.interface import C0SDInterface

c0 = C0SDInterface("/dev/sda")

print(c0.get_serial_number(), c0.get_uuid())
print(c0.read_bitstream_metadata())

# Start the SoC core and exchange data with it.
c0.apply_configure_action("core-start")
c0.write_input_buffer(b"\x01\x00\x00\x00")
print(c0.read_output_buffer(4))
```

The register maps bundled with the package match the shipped hardware, so normal use needs no
further configuration. Two constructor arguments on `C0microSDPlusInterface` and `C0SDInterface`
override them for a custom hardware configuration. `regmap_path=` names a directory holding an
alternative regmap package, which is what the toolkit's `--regmap-path` option passes, and works on
a host only. `regmap=` takes an already-imported regmap namespace and takes precedence over
`regmap_path=`. The CircuitPython port uses `regmap=`, because it imports its register maps
directly.

Reading and writing a raw block device needs elevated privileges, so run your script with `sudo`
just as you would the toolkit.

## Interfacing with the Signaloid C0-SD family
When connected to a host computer, a C0-microSD+ or C0-SD presents itself as an unformatted block
storage device. The host computer communicates with the device through block reads and writes to a set of
pre-defined addresses. In contrast to the C0-microSD, these modules operate in a single mode, which
supports flashing and running new application binaries, as well as updating the FPGA bitstream. The
`--variant` option selects the target module. When it is omitted, the toolkit auto-detects the
module from the device's bitstream (see *Selecting the compute module* below).

## Using the `C0_SD_toolkit.py` tool
You can use the `C0_SD_toolkit.py` Python script to configure a C0-microSD+ or C0-SD and flash new
firmware. Signaloid writes and tests the script against Python 3.10, and the script does not use
any additional libraries. The program's command-line arguments and usage examples follow.

```
usage: C0_SD_toolkit.py [-h] [--variant {C0-microSD+,C0-SD}] [--regmap-path REGMAP_PATH]
                        target_device <command> ...

Signaloid C0-SD toolkit. Version 2.5

positional arguments:
  target_device         Target device path
  <command>
    info                Print target device info and bitstream metadata.
    status              Print verbose status (COMMAND, CONFIG, STATUS, and SD_CONFIG on C0-SD).
    flash-application   Flash an application binary
    flash-bitstream     Flash a bitstream file
    configure (config)  Apply a configuration action (per-variant; see the action list in the main
                        --help).

options:
  -h, --help            show this help message and exit
  --variant {C0-microSD+,C0-SD}
                        Hardware variant. Default: auto-detect from the device's bitstream;
                        required if it cannot be identified.
  --regmap-path REGMAP_PATH
                        Path to the regmap package directory for the selected --variant (defaults
                        to the built-in regmaps).
```

`--regmap-path` is for custom hardware configurations only. The built-in register maps cover the
shipped modules, so you should not need it.

### Commands

| Command                                  | What it does                                                                                                         |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `info`                                   | Decode and print bitstream metadata, and verify the bitstream CRC.                                                   |
| `status`                                 | Print the COMMAND, CONFIG and STATUS registers (plus SD_CONFIG where present).                                       |
| `flash-application <app_path> [-p SIZE]` | Flash an application binary to the device's user-data flash region (optionally zero-padded to SIZE).                 |
| `flash-bitstream <bs_path> [-p SIZE]`    | Stop the SoC core, unlock the bitstream region, flash a bitstream, then re-lock it (optionally zero-padded to SIZE). |
| `configure` / `config <action>`          | Apply a single configuration action (see *Configuration actions* below).                                             |

### Selecting the compute module (`--variant`)
The toolkit works with both the C0-microSD+ and the C0-SD. The toolkit resolves which variant a
command targets in the following three ways.

- **`--variant` omitted.** The toolkit auto-detects the module by decoding the JSON metadata prefix
  of the device's bitstream and reading its `compute_module_type` field. The toolkit looks the field
  up by key on the decoded JSON, so detection does not depend on the order or position of the
  fields, and future bitstream revisions may reorder them. If the toolkit identifies the module, it
  uses that variant.
- **`--variant` omitted and the toolkit cannot identify the module**, for example a blank device or
  a bitstream without Signaloid metadata. The toolkit exits with an error asking you to pass
  `--variant`.
- **`--variant` given.** The given variant overrides auto-detection. If the given variant does not
  match the variant declared by the bitstream, or the toolkit could not identify the bitstream, the
  toolkit prints a warning and proceeds with the variant you specified.

The toolkit writes detection messages and warnings to `stderr`, so they do not interfere with
command output.

### The `info` command
`info` prints the device serial number and UUID, decodes and prints the bitstream's embedded metadata 
(compute module type, creation date, bitstream type, metadata-schema version, size, and CRC), and verifies the bitstream CRC.


```
usage: C0_SD_toolkit.py target_device info [-h] [--raw]

options:
  -h, --help  show this help message and exit
  --raw       Print the raw JSON metadata object instead of labelled fields.
```

### Configuration actions
Use these as `configure <action>`. The supported set depends on the variant, because the two modules
expose different LEDs and debug pins.

| Action                                                                     | C0-microSD+ | C0-SD |
| -------------------------------------------------------------------------- | ----------- | ----- |
| `core-start`, `core-stop`                                                  | ✓           | ✓     |
| `lock-bitstream`                                                           | ✓           | ✓     |
| `unlock-bitstream` (prompts for confirmation)                              | ✓           | ✓     |
| `sw-led-on`, `sw-led-off`                                                  | ✓           | ✓     |
| `green-led-on`, `green-led-off`                                            | ✓           | ✓     |
| `red-led-on`, `red-led-off`                                                | ✓           | ✗     |
| `blue-led-on`, `blue-led-off`                                              | ✓           | ✗     |
| `debug-pin-0-on`, `debug-pin-0-off`                                        | ✓           | ✗     |
| `debug-pin-1-on`, `debug-pin-1-off`                                        | ✓           | ✗     |
| `debug-pin-2-on`, `debug-pin-2-off`                                        | ✓           | ✗     |
| `debug-pin-on`, `debug-pin-off`                                            | ✗           | ✓     |
| `write-crc-force-ok-enable`, `write-crc-force-ok-disable`                  | ✓           | ✓     |
| `write-crc-force-write-enable`, `write-crc-force-write-disable`            | ✓           | ✓     |
| `write-crc-irq-connect`, `write-crc-irq-disconnect`, `write-crc-irq-clear` | ✓           | ✓     |

An `unlock-bitstream` action only takes effect while the SoC core is stopped. The hardware resets
`BITSTREAM_UNLOCK` to zero while the core is running, so starting the core re-locks the bitstream
region.

### Examples
The following examples assume the target device is located at `/dev/sda`. They omit `--variant`, so
the toolkit auto-detects the module from the device. Pass `--variant=C0-microSD+` or
`--variant=C0-SD` to force a specific one.

Print target device info and verify the bitstream CRC:
```sh
sudo python3 C0_SD_toolkit.py /dev/sda info
```

Flash new Signaloid SoC application binary:
```sh
sudo python3 C0_SD_toolkit.py /dev/sda flash-application program.bin
```

Flash new FPGA bitstream:
```sh
sudo python3 C0_SD_toolkit.py /dev/sda flash-bitstream bitstream.bin
```

Start the Signaloid SoC core:
```sh
sudo python3 C0_SD_toolkit.py /dev/sda config core-start
```

Stop and reset the Signaloid SoC core:
```sh
sudo python3 C0_SD_toolkit.py /dev/sda config core-stop
```
