# Source Files

Common libraries for building applications that run on a Signaloid C0 compute module, or that
communicate with one from another machine. Each tree has its own README.

| Tree                                                                                     | Language      | Runs on                               | Acts on                                                         |
| ---------------------------------------------------------------------------------------- | ------------- | ------------------------------------- | --------------------------------------------------------------- |
| [`c/`](./c/README.md), device side                                                       | C             | the compute module                    | the module's own registers, LEDs, and debug pins                |
| [`c/`](./c/README.md), host side                                                         | C             | a Linux or macOS host                 | a compute module in that host's card slot                       |
| [`python/signaloid_utilities/`](./python/signaloid_utilities/README.md), except `sddev/` | Python 3.10+  | a Linux or macOS host                 | a compute module in that host's card slot                       |
| [`python/signaloid_utilities/sddev/`](./python/signaloid_utilities/sddev/README.md)      | Python 3.10+  | the Raspberry Pi hosting the SD-Dev   | the SD-Dev's own GPIO lines and onboard ADC, on that same board |
| [`circuitpython/`](./circuitpython/README.md)                                            | CircuitPython | a CircuitPython microcontroller board | a compute module wired to that board's SPI bus                  |

The host-side Python libraries run on Linux and macOS, the only officially tested operating systems. 
The two SD-Dev tools run on the Raspberry Pi hosting the SD-Dev.
