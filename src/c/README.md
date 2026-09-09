# Signaloid C0 Compute-Module C Library

C sources and headers for the Signaloid C0 compute modules. The library covers
both sides of the interface.

- **Device side.** Code that runs on the module. A Hardware Abstraction Layer
  (HAL) keeps the variant-specific details behind one API, so a single
  application source tree can target any compute-module variant unchanged.
  Through the HAL, device-side code sets the module's status register, drives
  its LEDs and debug pins, and reads the command register that the host
  writes. Device-side code never starts a transfer. The code waits for a
  command from the host and returns results through the shared MMIO output
  buffer. The device side comprises `C0HAL.h`, `C0mmioCommonHAL.h`,
  `C0Logger.h`, and the per-variant `HAL.h` and `Constants.h` headers.
- **Host side.** Libraries for building C host applications that control a
  module over its block-device interface. These libraries perform block reads
  and writes at the variant's offsets, register access, and
  configuration-status decoding and printing. The host side comprises the
  per-variant `HostUtils.h` headers and their `lib/` implementations. These
  libraries use POSIX file I/O and run on Linux and macOS, which are the only
  officially tested operating systems.

## Build targets

`BUILD_FOR` selects the variant for **device-side** code only. Define it to one of the identifiers
in `include/SignaloidBuildTargets.h`. `C0HAL.h` dispatches on it to pull in that variant's HAL, and
rejects an unknown value with an `#error`.

| `BUILD_FOR`                 | Compute module | HAL header            |
| --------------------------- | -------------- | --------------------- |
| `SIGNALOID_C0_MICROSD`      | C0-microSD     | `C0microSD/HAL.h`     |
| `SIGNALOID_C0_MICROSD_PLUS` | C0-microSD+    | `C0microSDPlus/HAL.h` |
| `SIGNALOID_C0_SD`           | C0-SD          | `C0SD/HAL.h`          |


`SignaloidBuildTargets.h` reserves `SIGNALOID_CLOUD_DEVELOPER_PLATFORM` for the Signaloid Cloud
Developer Platform. That target has no HAL in this library.


Host-side code does not use `BUILD_FOR` at all. A host application picks its variant by including
that variant's `HostUtils.h` and linking the matching `lib/C0<variant>/HostUtils.c`, so one host
binary targets one module.

## Directory layout

```
src/c/
  include/
    C0HAL.h .................... public HAL entry point; include this from app code.
                                 Declares the variant-agnostic HAL API and shared
                                 output/input buffer accessors, and dispatches to the
                                 active variant's HAL header based on BUILD_FOR.
    C0mmioCommonHAL.h .......... shared MMIO memory model (single buffer split into
                                 output/input windows) reused by several variants.
    C0SoCStatus.h .............. common host/device status-register values.
    C0Logger.h ................. logging helper built on top of the HAL.
    SignaloidBuildTargets.h .... BUILD_FOR target identifiers.
    C0<variant>/ ............... one folder per compute-module variant:
      Constants.h .............. register/buffer offsets (often sourced from the
                                 variant's regmap under ../regmaps/C0<variant>/).
      HAL.h .................... variant register layout, config-register union,
                                 register-access macros and buffer sizing.
      HostUtils.h .............. host-side helpers for talking to the variant.
  src/
    C0Logger.c ................. logger implementation (variant-agnostic).
    C0<variant>/HAL.c .......... variant HAL implementation (device/SoC side). Each
                                 file is guarded so it compiles to an empty
                                 translation unit unless it matches BUILD_FOR.
  lib/
    C0<variant>/HostUtils.c .... host-side helper implementation.
  regmaps/
    C0<variant>/ ............... auto-generated register maps for the variant.
```

