# `signaloid_utilities` Python Package

Host-side Python libraries and toolkits for the Signaloid C0 compute modules. Each compute-module
family has its own sub-package with its own README. This directory holds the shared modules.

The `c0microsd/` and `c0sd/` packages run on a Linux or macOS host (the only officially tested operating systems) and act on a compute module in
that host's card slot. 
The `sddev/` package runs on the Raspberry Pi hosting the SD-Dev, driving the Raspberry Pi GPIO lines and reading an I2C ADC.

## Sub-packages

| Package      | Runs on                             | Acts on                                         | Documentation                   |
| ------------ | ----------------------------------- | ----------------------------------------------- | ------------------------------- |
| `c0microsd/` | a Linux or macOS host               | a C0-microSD in that host's card slot           | [README](./c0microsd/README.md) |
| `c0sd/`      | a Linux or macOS host               | a C0-microSD+ or C0-SD in that host's card slot | [README](./c0sd/README.md)      |
| `sddev/`     | the Raspberry Pi hosting the SD-Dev | the SD-Dev's own GPIO lines and onboard ADC     | [README](./sddev/README.md)     |

The [CircuitPython port](../../circuitpython/README.md) mirrors this package's layout and shares
its interface logic and register maps through symlinks, replacing only the transport.

## Shared modules

| Module                       | Purpose                                                                                                                                                                                    |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `common/raw_block_device.py` | Cache-bypassing reads and writes to a device node. `UnifiedBlockDevice` picks the right unbuffered implementation for Linux or macOS.                                                      |
| `common/bitstream_prefix.py` | Read and decode the JSON metadata prefix of a Signaloid bitstream, and check its CRC.                                                                                                      |
| `regmap_loader.py`           | Load a variant's auto-generated register-map package. Defaults to the built-in maps, which cover the shipped hardware. `--regmap-path` overrides them for a custom hardware configuration. |
| `debug_logger.py`            | The `C0_debug_logger.py` tool, below.                                                                                                                                                      |

## Using the `C0_debug_logger.py` tool
`C0_debug_logger.py` reads the debug log buffer of a connected compute module and prints it to the
console. `C0_debug_logger.py` works across every module in the family. Pass `--variant` to say
which module is connected. The repository root contains a symlink to this script.

```
usage: C0_debug_logger.py [-h] [--variant {C0-microSD,C0-microSD+,C0-SD}] [-r] [-s] [-d]
                          [--uint-dump] [--uint-dump-stop-word STOP_WORD]
                          [--polling-rate POLLING_RATE] [--no-clear] [--no-header]
                          [--no-header-styling] [--one-shot] [--packet-size PACKET_SIZE]
                          [-o OUTPUT_FILE]
                          device_path

Read debug logs from Signaloid compute modules and print them to the console.

positional arguments:
  device_path           Path of the device to read from (e.g., /dev/disk4).

options:
  -h, --help            show this help message and exit
  --variant {C0-microSD,C0-microSD+,C0-SD}
                        C0-microSD variant.
  -r, --reset-on-launch
                        Reset the core on launch. Only applicable to the C0-microSD+.
  -s, --stop-on-exit    Stop the core on exit. Only applicable to the C0-microSD+.
  -d, --hex-dump        Print the debug log in a hex dump format.
  --uint-dump           Print the debug log as a list of 4-byte hex numbers.
  --uint-dump-stop-word STOP_WORD
                        Print the debug log as a list of 4-byte hex numbers.
  --polling-rate POLLING_RATE
                        Polling rate (in seconds).
  --no-clear            Do not clear the console before printing logs.
  --no-header           Do not print the header with timestamp and device path.
  --no-header-styling   Do not print the header box.
  --one-shot            Print only a single shot of the debug log and exit.
  --packet-size PACKET_SIZE
                        Size of each packet to read (in bytes). [Default: 512]
  -o OUTPUT_FILE, --output-file OUTPUT_FILE
                        Redirect the log to a file.
```

The C logger, `C0Logger.c`, writes the device side of this log. See
[Signaloid C0 Compute-Module C Library](../../c/README.md).

### Examples
The following examples assume the compute module is located at `/dev/sda`.

Follow the log of a connected C0-microSD:
```sh
sudo python3 ./C0_debug_logger.py /dev/sda
```

Reset a C0-microSD+ on launch, stop its core on exit, and follow the log:
```sh
sudo python3 ./C0_debug_logger.py /dev/sda --variant C0-microSD+ -r -s
```

Take a single snapshot of a C0-SD's log as a hex dump:
```sh
sudo python3 ./C0_debug_logger.py /dev/sda --variant C0-SD --one-shot -d
```

Log to a file instead of the console:
```sh
sudo python3 ./C0_debug_logger.py /dev/sda -o debug.log
```
