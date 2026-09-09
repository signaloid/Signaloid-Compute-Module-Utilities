#!/usr/bin/env python3

# Copyright (c) 2026, Signaloid.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import argparse
import sys
import os
import json
import re
import time
from typing import TYPE_CHECKING, Any, Optional, Type

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src" / "python"))

from signaloid_utilities.c0sd.interface import (
    C0SDBaseInterface,
    C0microSDPlusInterface,
    C0SDInterface,
    UnsupportedConfigureAction,
    SoCRunning,
    identity_tag_is_blank,
)

APP_VERSION = "2.5"  # Application version
MAX_FLASH_ATTEMPTS = 5  # Maximum flashing attempts

# Known bitstream-metadata keys and their display labels, in print order.
# Any keys not listed here are printed generically after these.
BITSTREAM_METADATA_LABELS = [
    ("compute_module_type", "Compute module type"),
    ("bitstream_creation_date", "Creation date"),
    ("type", "Bitstream type"),
    ("v", "Metadata schema"),
    ("bitstream_size", "Bitstream size"),
    ("bitstream_crc", "Bitstream CRC"),
]


class _C0SDToolkitMixin:
    """Variant-agnostic toolkit operations.

    Combined with a variant interface (which supplies the offsets, the
    config-register methods, and the `apply_configure_action` dispatch)
    to form a concrete `*Toolkit` class.
    """

    if TYPE_CHECKING:
        # Supplied by the variant interface this mixin is combined with.
        BITSTREAM_OFFSET: int
        OTP_OFFSET: Optional[int]

        def read_flash(self, offset: int, size: int) -> bytes: ...
        def write_flash(self, offset: int, data: bytes) -> int: ...
        def read_otp(self, offset: int, size: int) -> bytes: ...
        def soc_is_running(self) -> bool: ...

    def _strip_trailing_bytes(
            self, byte_array: bytearray, byte: int
            ) -> bytearray:
        end = len(byte_array)
        while end > 0 and byte_array[end - 1] == byte:
            end -= 1
        return byte_array[:end]

    def flash_and_verify(
        self, file_data: bytes, flash_offset: int, max_attempts: int
    ) -> bool:
        input_file_bytes = len(file_data)
        for i in range(1, max_attempts + 1):
            print(
                f"Attempt {i} of {max_attempts}: Flashing... ",
                end="",
                flush=True
            )
            self.write_flash(flash_offset, file_data)
            print("Verifying...")
            data_to_verify = self.read_flash(flash_offset, input_file_bytes)
            if data_to_verify == file_data:
                print("Success: The data matches.")
                return True
            else:
                print("Error: The data do not match.")
        return False

    def print_device_identity(self) -> None:
        """Print the device serial number and UUID read from the OTP.

        A blank (all-0xFF) field is reported as "not provisioned". Any
        device-read error is caught and reported without aborting, so a
        failure to read the OTP never suppresses the bitstream information
        that follows. Prints nothing on a variant that exposes no OTP
        region.
        """
        if self.OTP_OFFSET is None:
            return
        try:
            serial_number = self.get_serial_number()
            uuid = self.get_uuid()
        except OSError as error:
            print(f"Unable to read device identity from OTP: {error}")
            return
        print(f"Device Serial Number: {serial_number or 'not provisioned'}")
        print(f"Device UUID: {uuid or 'not provisioned'}")
        print()

    def print_bitstream_information(
        self, offset, raw: bool = False
    ) -> Optional[dict[str, Any]]:
        """Decode and print the bitstream metadata and verify its CRC."""
        try:
            meta = self.read_bitstream_metadata(offset)
        except ValueError:
            print("    No Signaloid metadata found in bitstream prefix.")
            return None

        if raw:
            print(
                "    Bitstream prefix section: "
                f"{json.dumps(meta, separators=(', ', ': '))}"
            )
        else:
            self._print_bitstream_metadata(meta)

        self._verify_and_report(offset, meta)
        return meta

    def _verify_and_report(self, offset, meta) -> None:
        """Verify the bitstream CRC and print the result."""
        crc_pass = self.verify_bitstream_crc(offset, meta)
        if crc_pass is None:
            print(
                "    Bitstream CRC verification: "
                "unable to verify (no CRC field)"
            )
        elif crc_pass:
            print("    Bitstream CRC verification: PASS")
        else:
            print("    Bitstream CRC verification: FAIL")

    def _print_bitstream_metadata(self, meta: dict[str, Any]) -> None:
        """Print known metadata fields with friendly labels, then any
        remaining keys generically, in the 4-space-indented style."""
        shown = set()
        for key, label in BITSTREAM_METADATA_LABELS:
            if key in meta:
                value = meta[key]
                if key == "bitstream_crc" and isinstance(value, int):
                    value = f"0x{value:08X}"
                elif key == "bitstream_size":
                    value = f"{value} bytes"
                print(f"    {label}: {value}")
                shown.add(key)
        for key, value in meta.items():
            if key not in shown:
                print(f"    {key}: {value}")


class C0microSDPlusToolkit(_C0SDToolkitMixin, C0microSDPlusInterface):
    pass


class C0SDToolkit(_C0SDToolkitMixin, C0SDInterface):
    pass


TOOLKIT_BY_VARIANT: dict = {
    "C0-microSD+": C0microSDPlusToolkit,
    "C0-SD":       C0SDToolkit,
}


def _toolkit_cls_for(variant: str) -> Type[C0SDBaseInterface]:
    return TOOLKIT_BY_VARIANT[variant]


class IdentityMismatch(Exception):
    """The capacity and the identity tag disagree about which device this is."""


def _variant_by_tag() -> dict[bytes, str]:
    """Identity tag bytes -> variant name."""
    return {
        cls.IDENTITY_TAG: name
        for name, cls in TOOLKIT_BY_VARIANT.items()
        if cls.IDENTITY_TAG is not None
    }


def _detect_variant(
    target_device: str, regmap_path: Optional[str]
) -> Optional[str]:
    """Identify the compute module, preferring the on-device identity tag.

    Order is capacity first, then the tag:

      1. read the advertised block-device capacity;
      2. find the variant whose regmap says it advertises that capacity;
      3. read the identity tag at that variant's own address;
      4. require the two to agree.

    Taking the capacity first means the tag is only ever read at *this*
    device's address, so we never probe the other variant's -- which matters:
    the C0-SD's tag address is past the end of a C0-microSD+ (the kernel
    rejects that), and the C0-microSD+'s lands inside the C0-SD's PSRAM window.

    Unlike the bitstream prefix this works with the SoC core RUNNING, which is
    the point -- the flash access gate answers the prefix with zeros.

    Returns the variant name, or None when the device carries no identity tag
    and no decodable metadata. Raises IdentityMismatch when the two signals
    disagree, or the tag is present but unrecognised. Device errors propagate.
    """
    # One probe per variant: the addresses come from each variant's regmap via
    # its __init__, so they are instance attributes like every other offset.
    probes = {
        name: cls(target_device, regmap_path=regmap_path)
        for name, cls in TOOLKIT_BY_VARIANT.items()
    }
    try:
        any_probe = next(iter(probes.values()))
        capacity = any_probe.device.capacity_bytes()

        matched = [
            name for name, probe in probes.items()
            if probe.EXPECTED_CAPACITY_BYTES == capacity
        ]
        from_capacity = matched[0] if len(matched) == 1 else None

        tag: Optional[bytes] = None
        identity_offset = 0
        if from_capacity is not None:
            identity_offset = probes[from_capacity].IDENTITY_OFFSET
            tag = probes[from_capacity].read_identity_tag()

        if tag is not None and not identity_tag_is_blank(tag):
            from_tag = _variant_by_tag().get(tag)
            if from_tag is None:
                # Tags are matched exactly, so an unknown-but-valid future tag
                # is indistinguishable from junk. Print the bytes, or a new
                # variant is undiagnosable from the error alone.
                raise IdentityMismatch(
                    f"unrecognised device identity tag {tag!r} at "
                    f"0x{identity_offset:08X}. This toolkit may predate the "
                    "device."
                )
            if from_tag != from_capacity:
                raise IdentityMismatch(
                    f"the device advertises {capacity} bytes, which is "
                    f"{from_capacity}, but its identity tag {tag!r} says "
                    f"{from_tag}. Refusing to guess: check the flashed "
                    "bitstream matches the board."
                )
            return from_tag

        # No tag: either a bitstream predating the identity register, or a
        # capacity we do not recognise. Fall back to the metadata prefix,
        # which only works with the core stopped.
        print(
            "Note: no device identity tag found; falling back to the bitstream "
            "metadata prefix, which requires the SoC core to be stopped.",
            file=sys.stderr,
        )
        try:
            meta = any_probe.read_bitstream_metadata(any_probe.BITSTREAM_OFFSET)
        except SoCRunning:
            print(
                "      ...and the core is running, so the prefix reads as "
                "zeros. Pass --variant to identify the device explicitly.",
                file=sys.stderr,
            )
            return None
        except ValueError:
            return None
        declared = meta.get("compute_module_type")
        if declared in TOOLKIT_BY_VARIANT:
            return str(declared)
        return None
    finally:
        for probe in probes.values():
            try:
                probe.device.close()
            except Exception:
                pass


def _resolve_variant(args) -> str:
    """Resolve the target variant, auto-detecting it when --variant is
    omitted.

    - --variant omitted + detected   -> use the detected variant.
    - --variant omitted + undetected -> error asking for --variant.
    - --variant given                -> use it (override), warning on a
      detected mismatch or when detection was not possible.

    Detection messages go to stderr so stdout stays clean for command
    output.
    """
    detected = _detect_variant(args.target_device, args.regmap_path)

    if args.variant is None:
        if detected is None:
            raise ValueError(
                "Could not identify the compute module from the device's "
                "bitstream. Re-run with --variant {C0-microSD+,C0-SD}."
            )
        print(f"Detected compute module: {detected}")
        return detected

    if detected is None:
        print(
            "Warning: could not identify the compute module from the "
            f"device's bitstream to confirm --variant '{args.variant}'; "
            f"proceeding as '{args.variant}'.",
        )
    elif detected != args.variant:
        print(
            f"Warning: --variant is '{args.variant}' but the device's "
            f"bitstream declares '{detected}'; proceeding as "
            f"'{args.variant}'.",
        )
    return args.variant


def _make_toolkit(args) -> C0SDBaseInterface:
    """Construct the toolkit for the resolved variant.

    The variant is auto-detected from the device when ``--variant`` is
    omitted (see ``_resolve_variant``). ``--regmap-path`` is forwarded to
    the variant interface so offsets are sourced from that regmap package
    (or the built-in regmaps when None).
    """
    variant = _resolve_variant(args)
    return _toolkit_cls_for(variant)(
        args.target_device, regmap_path=args.regmap_path
    )


def confirm_action() -> bool:
    while True:
        response = input(
            "WARNING: This action may render the device inoperable. "
            "Proceed? (y/n): "
        ).lower()
        if response == "y":
            return True
        elif response == "n":
            return False
        else:
            print("Invalid input. Please enter 'y' for yes or 'n' for no.")


def parse_size(size_str):
    match = re.match(r"(\d+)([KMG]?)", size_str.upper())
    if not match:
        raise ValueError("Invalid padding size format. "
                         "Use a number or a number with suffix (K, M, G).")

    size = int(match.group(1))
    suffix = match.group(2)

    if suffix == 'K':
        return size * 1024
    elif suffix == 'M':
        return size * (1024 ** 2)
    elif suffix == 'G':
        return size * (1024 ** 3)
    else:
        return size


def open_and_pad_file(input_file: str, pad_size: Optional[int]):
    file_data = None
    try:
        with open(input_file, "rb") as src:
            file_data = src.read()
    except PermissionError:
        raise PermissionError(
            "Permission denied: You do not have the "
            f"necessary permissions to access {input_file}."
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            f"File not found: The file {input_file} does not exist."
        )

    print("Filename: ", input_file)
    print("File size: ", len(file_data), "bytes.")

    if pad_size is not None:
        pad_size = parse_size(pad_size)

        if pad_size > len(file_data):
            file_data = file_data + (b'\x00' * (pad_size - len(file_data)))
            print(f"Input file padded to {pad_size} bytes.")
        elif pad_size < len(file_data):
            print("Warning: The specified padding size is smaller than the "
                  "input file size. No padding applied.")
    return file_data


def _exit_for_exception(e: Exception) -> None:
    print(f"{e}\nAn error occurred, aborting.", file=sys.stderr)
    if isinstance(e, SoCRunning):
        # Not a fault: the device is fine, the caller just asked for
        # something that needs the core stopped. Distinct code so scripts can
        # tell "stop the core and retry" from a real I/O failure.
        exit(os.EX_TEMPFAIL)
    elif isinstance(e, IdentityMismatch):
        exit(os.EX_DATAERR)
    elif isinstance(e, ValueError):
        exit(os.EX_DATAERR)
    elif isinstance(e, FileNotFoundError):
        exit(os.EX_NOINPUT)
    elif isinstance(e, PermissionError):
        exit(os.EX_NOPERM)
    else:
        exit(os.EX_SOFTWARE)


def handle_info(args):
    try:
        toolkit = _make_toolkit(args)

        # Everything below this point reads the SPI flash or the OTP, both of
        # which the access gate answers with zeros while the core runs.
        stopped_here = False
        if toolkit.soc_is_running():
            if not getattr(args, "stop_core", False):
                raise SoCRunning("read the device information")
            print("Stopping the SoC core (--stop-core)...")
            toolkit.apply_configure_action("core-stop")
            time.sleep(0.5)
            stopped_here = True

        try:
            toolkit.print_device_identity()
            print("Reading bitstream:")
            toolkit.print_bitstream_information(
                toolkit.BITSTREAM_OFFSET, raw=args.raw
            )
        finally:
            # Restart in a finally: if the read fails halfway we still hand the
            # device back in the state we found it, rather than silently
            # leaving someone's application stopped.
            if stopped_here:
                print("Restarting the SoC core...")
                toolkit.apply_configure_action("core-start")

        print("Done.")
        exit(os.EX_OK)
    except Exception as e:
        _exit_for_exception(e)


def handle_status(args):
    try:
        toolkit = _make_toolkit(args)
        toolkit.verbose_status()
        exit(os.EX_OK)
    except Exception as e:
        _exit_for_exception(e)


def handle_flash_application(args):
    try:
        toolkit = _make_toolkit(args)
        file_data = open_and_pad_file(args.app_path, args.p)
        print("Stopping the Signaloid SoC...")
        toolkit.apply_configure_action("core-stop", lambda: True)
        time.sleep(0.5)  # Allow time for the SoC to stop
        print("Flashing Signaloid SoC application...")
        if not toolkit.flash_and_verify(
            file_data,
            toolkit.APPLICATION_BINARY_OFFSET,
            MAX_FLASH_ATTEMPTS
        ):
            exit(os.EX_SOFTWARE)
    except Exception as e:
        _exit_for_exception(e)


def handle_flash_bitstream(args):
    try:
        toolkit = _make_toolkit(args)
        file_data = open_and_pad_file(args.bs_path, args.p)
        if not confirm_action():
            print("Aborting.")
            exit(os.EX_USAGE)
        print("Stopping the Signaloid SoC...")
        toolkit.apply_configure_action("core-stop", lambda: True)
        print("Unlocking the bitstream section...")
        toolkit.unlock_bitstream(lambda: True)
        print("Flashing bitstream...")
        flashed = toolkit.flash_and_verify(
            file_data,
            toolkit.BITSTREAM_OFFSET,
            MAX_FLASH_ATTEMPTS
        )
        print("Locking the bitstream section...")
        toolkit.lock_bitstream()
        if not flashed:
            exit(os.EX_SOFTWARE)
    except Exception as e:
        _exit_for_exception(e)


def handle_configuration(args):
    try:
        toolkit = _make_toolkit(args)
        if args.action == "unlock-bitstream":
            toolkit.unlock_bitstream(confirm_action)
        elif args.action == "lock-bitstream":
            toolkit.lock_bitstream()
        else:
            toolkit.apply_configure_action(args.action, confirm_action)
    except UnsupportedConfigureAction as e:
        print(f"Error: {e}", file=sys.stderr)
        exit(os.EX_USAGE)
    except Exception as e:
        _exit_for_exception(e)


# One-line summary of what each subcommand does, shown in the toolkit
# help epilog so a single `--help` lists every capability of the tool.
_COMMAND_DESCRIPTIONS: list[tuple[str, str]] = [
    ("info",
     "Decode and print bitstream metadata, and verify the bitstream CRC."),
    ("status",
     "Print the COMMAND, CONFIG and STATUS registers "
     "(plus SD_CONFIG where present)."),
    ("flash-application <app_path> [-p SIZE]",
     "Flash an application binary to the device's user-data flash "
     "region (optionally zero-padded to SIZE)."),
    ("flash-bitstream <bs_path> [-p SIZE]",
     "Stop the SoC core, unlock the bitstream region, flash a bitstream, "
     "then re-lock it (optionally zero-padded to SIZE)."),
    ("configure / config <action>",
     "Apply a single configuration action (see 'configure actions' "
     "below)."),
]


def _config_actions_for(variant: str) -> list[tuple[str, bool]]:
    """Return the sorted ``(action, needs_confirmation)`` list for a variant.

    Constructs the variant toolkit with a placeholder device path purely
    to read its ``SUPPORTED_ACTIONS`` table; no device I/O happens at
    construction time.
    """
    toolkit = _toolkit_cls_for(variant)
    return [
        (name, bool(toolkit.SUPPORTED_ACTIONS[name][3]))
        for name in sorted(toolkit.SUPPORTED_ACTIONS)
    ]


def _format_config_actions(selected_variant: Optional[str]) -> str:
    """Build the 'configure actions' help block.

    Lists the actions for ``selected_variant`` when it names a known
    variant; otherwise lists them for every supported variant.
    """
    if selected_variant in TOOLKIT_BY_VARIANT:
        variants = [selected_variant]
    else:
        variants = list(TOOLKIT_BY_VARIANT)

    lines = ["configure actions (use as: configure <action>):"]
    for variant in variants:
        lines.append(f"  {variant}:")
        for name, needs_confirm in _config_actions_for(variant):
            suffix = "  (prompts for confirmation)" if needs_confirm else ""
            lines.append(f"    {name}{suffix}")
    return "\n".join(lines)


def _build_epilog(selected_variant: Optional[str]) -> str:
    """Assemble the full help epilog: command summaries + config actions."""
    lines = ["commands:"]
    for name, description in _COMMAND_DESCRIPTIONS:
        lines.append(f"  {name}")
        lines.append(f"      {description}")
    return "\n".join(lines) + "\n\n" + _format_config_actions(selected_variant)


def create_parser(selected_variant: Optional[str] = None):
    parser = argparse.ArgumentParser(
        description=f"Signaloid C0-SD toolkit. Version {APP_VERSION}",
        epilog=_build_epilog(selected_variant),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=True
    )

    parser.add_argument(
        "target_device",
        help="Target device path"
    )

    parser.add_argument(
        "--variant",
        choices=["C0-microSD+", "C0-SD"],
        default=None,
        help="Hardware variant. Default: auto-detect from the device's "
             "bitstream; required if it cannot be identified."
    )

    parser.add_argument(
        "--regmap-path",
        default=None,
        help="Path to the regmap package directory for the selected "
             "--variant (defaults to the built-in regmaps)."
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="<command>"
    )

    p_info = subparsers.add_parser(
        "info",
        help="Print target device info and bitstream metadata."
    )
    p_info.add_argument(
        "--raw",
        action="store_true",
        help="Print the raw JSON metadata object instead of labelled fields."
    )
    p_info.add_argument(
        "--stop-core",
        action="store_true",
        help="Stop the SoC core for the duration of the read, then restart it. "
             "Without this, reading a device whose core is running is refused. "
             "The flash and OTP would read back as zeros."
    )
    p_info.set_defaults(func=handle_info)

    p_status = subparsers.add_parser(
        "status",
        help="Print verbose status (COMMAND, CONFIG, STATUS, "
             "and SD_CONFIG on C0-SD)."
    )
    p_status.set_defaults(func=handle_status)

    p_flash_app = subparsers.add_parser(
        "flash-application",
        help="Flash an application binary"
    )
    p_flash_app.add_argument(
        "app_path",
        help="Path to the application binary to flash"
    )
    p_flash_app.add_argument(
        "-p",
        required=False,
        type=str,
        help=("Pad input file with zeros to target size.")
    )
    p_flash_app.set_defaults(func=handle_flash_application)

    p_flash_bs = subparsers.add_parser(
        "flash-bitstream",
        help="Flash a bitstream file"
    )
    p_flash_bs.add_argument(
        "bs_path",
        help="Path to the bitstream file to flash"
    )
    p_flash_bs.add_argument(
        "-p",
        required=False,
        type=str,
        help=("Pad input file with zeros to target size.")
    )
    p_flash_bs.set_defaults(func=handle_flash_bitstream)

    # Per-variant validation happens in handle_configuration; argparse
    # accepts any string and we print the variant-specific list of
    # available actions on mismatch.
    p_configure = subparsers.add_parser(
        "configure",
        aliases=["config"],
        help="Apply a configuration action (per-variant; "
             "see the action list in the main --help).",
        epilog=_format_config_actions(selected_variant),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p_configure.add_argument(
        "action",
        help="Configuration action to perform "
             "(supported set depends on --variant)"
    )
    p_configure.set_defaults(func=handle_configuration)

    return parser


def _preparse_variant(argv: list[str]) -> Optional[str]:
    """Extract ``--variant`` from ``argv`` to shape the help text.

    Returns the variant only when it names a known one, so the help
    epilog can narrow to that variant; otherwise None (help lists all
    variants). Validation of an unknown value is left to the real parser.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--variant")
    known, _ = pre.parse_known_args(argv)
    return known.variant if known.variant in TOOLKIT_BY_VARIANT else None


def main(explicit_args: list[str] | None = None):
    argv = list(explicit_args) if explicit_args is not None else sys.argv[1:]
    parser = create_parser(selected_variant=_preparse_variant(argv))
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
