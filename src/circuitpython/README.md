# CircuitPython Support for the Signaloid C0 Compute Modules

A CircuitPython port of the host interface, for controlling a compute module from a microcontroller
over SD-over-SPI rather than from a computer over a block device.

|                  |                                                                            |
| ---------------- | -------------------------------------------------------------------------- |
| **Runs on**      | a CircuitPython microcontroller board                                      |
| **Acts on**      | a C0-microSD, C0-microSD+, or C0-SD wired to that board's SPI bus          |
| **Requirements** | CircuitPython with the `busio`, `digitalio`, and `microcontroller` modules |

## Layout

The package mirrors the host [`signaloid_utilities`](../python/signaloid_utilities/README.md)
package, so the interface logic and the register maps are shared with it verbatim through symlinks.
Only the transport differs.

```
signaloid_utilities/
  common/
    sd_over_spi_protocol.py .... SDOverSPI. The SD command layer: command framing, CRC7/CRC16,
                                 response parsing and block transfers. Module-agnostic.
    sd_over_spi_mixin.py ....... SDOverSPITransportMixin. Replaces the block-device _read,
                                 _write and _open_device of the host interfaces with SD
                                 block transfers over SPI.
    bitstream_prefix.py ........ symlink to the host package's common/bitstream_prefix.py.
  c0microsd/
    interface.py ............... C0microSDSignaloidSoCInterfaceSDSPI.
    sd_interface.py ............ symlink to the host package's c0microsd/interface.py.
    constants.py ............... symlink to the host package's c0microsd/constants.py.
  c0sd/
    interface.py ............... C0microSDPlusInterfaceSDSPI and C0SDInterfaceSDSPI.
    sd_interface.py ............ symlink to the host package's c0sd/interface.py.
    regmaps/ ................... symlink to the host package's c0sd/regmaps/.
```

Each SD-over-SPI class lists `SDOverSPITransportMixin` before the shared host interface in its
bases, so the mixin's transport methods take precedence. Everything above the transport, including
the register offsets and the Signaloid SoC protocol, comes from the host interface unchanged.

Copy the whole `signaloid_utilities/` folder onto the board's filesystem, resolving the symlinks,
alongside your `code.py`.

## Usage

Pick the class for your module.

| Compute module | Class                                 | Import from                               |
| -------------- | ------------------------------------- | ----------------------------------------- |
| C0-microSD     | `C0microSDSignaloidSoCInterfaceSDSPI` | `signaloid_utilities.c0microsd.interface` |
| C0-microSD+    | `C0microSDPlusInterfaceSDSPI`         | `signaloid_utilities.c0sd.interface`      |
| C0-SD          | `C0SDInterfaceSDSPI`                  | `signaloid_utilities.c0sd.interface`      |

Every class takes an initialized `SPI` bus, the chip-select pin wired to the module, and a
transaction timeout in retries.

```python
import board
import busio
from signaloid_utilities.c0sd.interface import C0SDInterfaceSDSPI

spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI, MISO=board.MISO)
c0 = C0SDInterfaceSDSPI(spi, board.CS, timeout=1000)

print(c0.get_serial_number(), c0.get_uuid())

c0.apply_configure_action("core-start")
c0.write_input_buffer(b"\x01\x00\x00\x00")
print(c0.read_output_buffer(4))
```


```python
from signaloid_utilities.c0microsd.interface import C0microSDSignaloidSoCInterfaceSDSPI

c0 = C0microSDSignaloidSoCInterfaceSDSPI(spi, board.CS, timeout=1000)
print(c0.configuration)
```

