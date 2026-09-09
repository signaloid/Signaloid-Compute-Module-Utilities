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


import struct
import sys
import time


if sys.implementation.name != "circuitpython":
    from typing import (
        TYPE_CHECKING,
        Any,
        Callable,
        Dict,
        Iterable,
        Optional,
        Tuple,
    )
    from ..common.raw_block_device import UnifiedBlockDevice
else:
    TYPE_CHECKING: bool = False


from ..common.bitstream_prefix import (
    COMMENT_WINDOW_BYTES,
    NEXUS,
    crc32,
    locate_prefix,
    read_prefix_json,
)


SIGNALOID_SOC_STATUS_WAIT_FOR_COMMAND = 0
SIGNALOID_SOC_STATUS_CALCULATING = 1
SIGNALOID_SOC_STATUS_DONE = 2
SIGNALOID_SOC_STATUS_INVALID_COMMAND = 3
BITSTREAM_UNLOCK_KEY = 0x4B4C4E55
BITSTREAM_UNLOCK_KEY_BYTES = b"UNLK"
IDENTITY_SIZE = 8


def identity_tag_is_blank(tag: bytes) -> bool:
    """True for the two values that mean "no identity register here".

    A gated or dropped read returns zeros and an unmapped read returns ones.
    Neither is a valid tag, which is what makes a miss unambiguous.
    """
    return tag in (b"\x00" * len(tag), b"\xFF" * len(tag))


K_CALCULATE_NO_COMMAND = 0

# Bytes per read when checksumming a bitstream.
CRC_CHUNK_BYTES = COMMENT_WINDOW_BYTES


# Anchor for the relative import of the default regmap package.
try:
    _REGMAP_ANCHOR = __package__
except NameError:
    _REGMAP_ANCHOR = __name__.rsplit(".", 1)[0]


def resolve_regmap(
    regmap: Any,
    default_module: str,
    package: str,
    top_attr: str,
    regmap_path: Optional[str] = None,
) -> Any:
    """Return the top-level regmap namespace class (e.g. ``Top``).

    An injected ``regmap`` is used as given. Otherwise the in-tree default,
    or the package at ``regmap_path``, is imported and used.

    Args:
        regmap: Pre-imported regmap namespace, or None to resolve one.
        default_module: Dotted module name of the in-tree regmap package,
            resolved relative to ``package`` (e.g. ``".regmaps.c0sd"``).
        package: Anchor package for the relative ``default_module`` import
            (typically the caller's package, i.e. ``_REGMAP_ANCHOR``).
        top_attr: Name of the top-level namespace attribute to return.
        regmap_path: Optional path to an alternative regmap package
            directory. Host-only; ignored when ``regmap`` is given.

    Returns:
        The top-level regmap namespace class.
    """
    if regmap is not None:
        return regmap

    from ..regmap_loader import load_regmap_namespace

    return load_regmap_namespace(default_module, package, top_attr, regmap_path)


def to_printable(byte: int) -> str:
    """Return the ASCII character for a byte, or '.' if non-printable."""
    return chr(byte) if 32 <= byte <= 126 else "."


def strip_trailing_bytes(data: bytes, value: int) -> bytes:
    """Return ``data`` without its trailing run of ``value`` bytes."""
    end = len(data)
    while end > 0 and data[end - 1] == value:
        end -= 1
    return data[:end]


class UnsupportedConfigureAction(Exception):
    """Raised when a `configure` action is not in the variant's supported set."""

    def __init__(
        self,
        action: str,
        variant_name: str,
        available: Iterable[str],
    ) -> None:
        self.action = action
        self.variant_name = variant_name
        self.available = available
        super().__init__(
            f"action '{action}' is not supported on {variant_name}.\n"
            f"Available actions: {', '.join(sorted(available))}"
        )


class SoCRunning(Exception):
    """Raised when an operation needs the SoC core stopped and it is running.

    The SD flash access gate makes the SPI flash and the OTP window read back
    as zeros while the core runs, and discards writes to them.

    """

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(
            f"cannot {operation} while the SoC core is running. "
            "Stop the core first with `configure core-stop`."
        )


class BitstreamUnlockFailed(Exception):
    """Raised when the bitstream section could not be unlocked.

    On boards with a `bitstream_unlock` register the usual cause is that the
    SoC core is still running: hardware forces the key to 0 while it is, so the
    write is silently discarded.
    """

    def __init__(self, attempts: int, delay: float) -> None:
        self.attempts = attempts
        self.delay = delay
        super().__init__(
            f"failed to unlock the bitstream section after {attempts} attempts "
            f"({delay}s apart). The SoC core must be stopped first."
        )


class C0SDBaseInterface:
    """Communication interface for the C0-SD device family.

    The base class owns all behaviour shared across variants — low-level
    register I/O, MMIO buffer access, command dispatch, and the generic
    `apply_configure_action` driver. Each variant subclass supplies its
    own offsets (via class attributes) and its own SUPPORTED_ACTIONS
    table; the dispatch reads from `self.SUPPORTED_ACTIONS`, so swapping
    the table is enough to retarget a variant.
    """

    DISPLAY_NAME: str = "C0-SD device"

    # Variant-specific layout. Subclasses MUST override these.
    BITSTREAM_OFFSET: int = 0
    APPLICATION_BINARY_OFFSET: int = 0

    COMMAND_REGISTER_OFFSET: int = 0
    CONFIG_REGISTER_OFFSET: int = 0
    STATUS_REGISTER_OFFSET: int = 0

    MMIO_BUFFER_SIZE_BYTES: int = 8192
    MMIO_BUFFER_OFFSET: int = 0

    DEBUG_LOG_BUFFER_SIZE_BYTES = 512

    BITSTREAM_UNLOCK_REGISTER_OFFSET: Optional[int] = None

    # OTP text fields. The OTP region is memory-mapped per variant at
    # OTP_OFFSET. The serial number and UUID live at fixed relative
    # offsets within it.
    OTP_OFFSET: Optional[int] = None
    SERIAL_NUMBER_RELATIVE_OFFSET: int = 0x40
    SERIAL_NUMBER_SIZE: int = 0x40
    UUID_RELATIVE_OFFSET: int = 0x80
    UUID_SIZE: int = 0x40

    # Map action name -> (value, mask, register, confirm). apply_configure_action does:
    #   if confirm: prompt the user via confirm_callback; abort if declined.
    #   new = (current & ~mask) | (value & mask)
    # on the register named by the third element, so entries may target any
    # register in the map. Bitstream lock/unlock target bitstream_unlock on
    # boards that have one (see bitstream_lock_actions);
    # unlock-bitstream additionally has confirm=True.
    # Subclasses override this with the per-variant table.
    SUPPORTED_ACTIONS: Dict[str, Tuple[int, int, int, bool]] = {}

    @staticmethod
    def bitstream_lock_actions(
        unlock_key_offset: int,
    ) -> Dict[str, Tuple[int, int, int, bool]]:
        """Return the bitstream lock/unlock entries for ``SUPPORTED_ACTIONS``.

        The standard scheme: unlocking writes the magic key to the
        bitstream_unlock register, locking writes zero. Hardware forces
        the key to 0 while the SoC core is running, so an unlock only takes
        effect while the core is stopped and starting the core re-locks.

        Subclasses merge this into their own ``SUPPORTED_ACTIONS`` so the
        two actions stay in lock-step across variants while each supplies
        its own register offset.
        """
        return {
            "unlock-bitstream": (BITSTREAM_UNLOCK_KEY, 0xFFFFFFFF, unlock_key_offset, True),
            "lock-bitstream":   (0x00000000,           0xFFFFFFFF, unlock_key_offset, False),
        }

    # An unlock only takes once the SoC core has actually stopped, and a
    # core-stop issued moments earlier may still be draining, so retry rather
    # than failing on the first attempt.
    BITSTREAM_UNLOCK_ATTEMPTS = 10
    BITSTREAM_UNLOCK_RETRY_DELAY_S = 0.2

    def unlock_bitstream(
        self,
        confirm_callback: Optional[Callable[[], bool]] = None,
        verbose: bool = False,
    ) -> None:
        """Unlock the bitstream section, retrying until the key takes.

        Each attempt writes the key and reads it back.

        Raises:
            BitstreamUnlockFailed: if the key has not taken after
                ``BITSTREAM_UNLOCK_ATTEMPTS`` attempts.
        """
        entry = self.SUPPORTED_ACTIONS.get("unlock-bitstream")
        if entry is None:
            # Let apply_configure_action raise the canonical error.
            self.apply_configure_action(
                "unlock-bitstream", confirm_callback, verbose
            )
            return

        # Gate on the confirmation once, here, rather than per attempt: a
        # retry must never re-prompt, and a decline must not be retried past.
        _, _, _, confirm = entry
        if confirm and (confirm_callback is None or not confirm_callback()):
            if verbose:
                print("Applying configure action unlock-bitstream aborted.")
            return

        for attempt in range(self.BITSTREAM_UNLOCK_ATTEMPTS):
            self.apply_configure_action(
                "unlock-bitstream", lambda: True, verbose
            )
            key = self.read_bitstream_unlock_key_bytes()
            if key is None or key == BITSTREAM_UNLOCK_KEY_BYTES:
                return
            if attempt + 1 < self.BITSTREAM_UNLOCK_ATTEMPTS:
                time.sleep(self.BITSTREAM_UNLOCK_RETRY_DELAY_S)

        raise BitstreamUnlockFailed(
            self.BITSTREAM_UNLOCK_ATTEMPTS, self.BITSTREAM_UNLOCK_RETRY_DELAY_S
        )

    def lock_bitstream(self, verbose: bool = False) -> None:
        """Lock the bitstream section."""
        self.apply_configure_action("lock-bitstream", lambda: True, verbose)

    def get_bitstream_unlock_key(self) -> Optional[int]:
        """Return the bitstream unlock key, or None if this board has none."""
        raw = self.read_bitstream_unlock_key_bytes()
        return None if raw is None else struct.unpack("<I", raw)[0]

    def read_bitstream_unlock_key_bytes(self) -> Optional[bytes]:
        """Return the raw 4 bytes of the unlock key, or None if unsupported."""
        if self.BITSTREAM_UNLOCK_REGISTER_OFFSET is None:
            return None
        return self._read(self.BITSTREAM_UNLOCK_REGISTER_OFFSET, 4)

    def _read_otp_text_field(
        self, relative_offset: int, size: int
    ) -> Optional[str]:
        """Read and decode an ASCII text field from the OTP region.

        The OTP text fields are ASCII, right-padded with 0xFF. An
        all-0xFF field is unwritten and represents "not provisioned",
        which is reported here as None.

        Args:
            relative_offset: Byte offset of the field within the OTP
                region (relative to ``OTP_OFFSET``).
            size: Length of the field in bytes.

        Returns:
            The decoded field text, or None if the variant exposes no OTP
            region or the field is unprovisioned (all 0xFF).
        """
        if self.OTP_OFFSET is None:
            return None
        raw = self.read_otp(self.OTP_OFFSET + relative_offset, size)
        stripped = strip_trailing_bytes(raw, 0xFF)
        if not stripped:
            return None
        return "".join(to_printable(byte) for byte in stripped)

    def get_serial_number(self) -> Optional[str]:
        """Return the device serial number from the OTP, or None.

        None is returned when the variant exposes no OTP region or the
        serial-number field is unprovisioned.
        """
        return self._read_otp_text_field(
            self.SERIAL_NUMBER_RELATIVE_OFFSET, self.SERIAL_NUMBER_SIZE
        )

    def get_uuid(self) -> Optional[str]:
        """Return the device UUID from the OTP, or None.

        None is returned when the variant exposes no OTP region or the
        UUID field is unprovisioned.
        """
        return self._read_otp_text_field(
            self.UUID_RELATIVE_OFFSET, self.UUID_SIZE
        )

    def __init__(
        self, target_device: str,
    ) -> None:
        self.target_device = target_device

        self.INPUT_BUFFER_SIZE_BYTES: int = self.MMIO_BUFFER_SIZE_BYTES // 2
        self.INPUT_BUFFER_OFFSET: int = self.MMIO_BUFFER_OFFSET + self.MMIO_BUFFER_SIZE_BYTES // 2

        self.OUTPUT_BUFFER_SIZE_BYTES: int = self.MMIO_BUFFER_SIZE_BYTES // 2
        self.OUTPUT_BUFFER_OFFSET: int = self.MMIO_BUFFER_OFFSET

        self.device = self._open_device(target_device)

    def _open_device(self, target_device: str) -> "UnifiedBlockDevice":
        """Open ``target_device`` as a raw block device.

        Backs ``_read``/``_write``. Ports that reach the compute module over
        another transport override this to return None and supply their own
        ``_read``/``_write``.
        """
        from ..common.raw_block_device import UnifiedBlockDevice

        return UnifiedBlockDevice(path=target_device)

    def _read(self, offset: int, size: int) -> bytes:
        return self.device.read(offset=offset, length=size)

    def _write(self, offset: int, data: bytes) -> int:
        return self.device.write(offset=offset, data=data)

    def __str__(self) -> str:
        return f"Signaloid {self.DISPLAY_NAME}"

    def write_MMIO_buffer(self, buffer: bytes) -> None:
        if len(buffer) > self.MMIO_BUFFER_SIZE_BYTES:
            raise ValueError(
                "Buffer size exceeds maximum allowed "
                f"size of {self.MMIO_BUFFER_SIZE_BYTES} bytes."
            )
        self._write(self.MMIO_BUFFER_OFFSET, buffer)

    def read_MMIO_buffer(self, size: int | None = None) -> bytes:
        if size is None:
            size = self.MMIO_BUFFER_SIZE_BYTES
        if size > self.MMIO_BUFFER_SIZE_BYTES:
            raise ValueError(
                "Read MMIO size exceeds"
                f" {self.MMIO_BUFFER_SIZE_BYTES} bytes."
            )
        return self._read(self.MMIO_BUFFER_OFFSET, size)

    def write_input_buffer(self, buffer: bytes) -> None:
        if len(buffer) > self.INPUT_BUFFER_SIZE_BYTES:
            raise ValueError(
                "Buffer size exceeds maximum allowed "
                f"size of {self.INPUT_BUFFER_SIZE_BYTES} bytes."
            )
        self._write(self.INPUT_BUFFER_OFFSET, buffer)

    def read_output_buffer(self, size: int | None = None) -> bytes:
        if size is None:
            size = self.OUTPUT_BUFFER_SIZE_BYTES
        if size > self.OUTPUT_BUFFER_SIZE_BYTES:
            raise ValueError(
                "Read output buffer size exceeds"
                f" {self.OUTPUT_BUFFER_SIZE_BYTES} bytes."
            )
        return self._read(self.OUTPUT_BUFFER_OFFSET, size)

    def read_debug_log_buffer(
        self,
        size: int = DEBUG_LOG_BUFFER_SIZE_BYTES
    ) -> bytes:
        if size > self.MMIO_BUFFER_SIZE_BYTES:
            raise ValueError(
                "Read UART size exceeds"
                f" {self.MMIO_BUFFER_SIZE_BYTES} bytes."
            )

        DEBUG_LOG_BUFFER_OFFSET = (
            self.OUTPUT_BUFFER_OFFSET + self.OUTPUT_BUFFER_SIZE_BYTES - size
        )
        return self._read(DEBUG_LOG_BUFFER_OFFSET, size)

    def read_bitstream_prefix(
        self, offset: int | None = None, size: int = COMMENT_WINDOW_BYTES
    ) -> bytes:
        """Return the raw ASCII metadata-prefix payload of the bitstream.

        Reads ``size`` bytes at ``offset`` (default ``BITSTREAM_OFFSET``)
        and returns the bytes between the Nexus (``LSCC``) framing markers.

        Args:
            offset: Flash byte offset of the bitstream; defaults to
                ``self.BITSTREAM_OFFSET``.
            size: Number of bytes to read for the prefix scan (the comment
                fits comfortably in the default 4096).

        Returns:
            The metadata payload bytes (framing markers excluded).

        Raises:
            ValueError: if the LSCC framing is not present.
        """
        if offset is None:
            offset = self.BITSTREAM_OFFSET
        chunk = self.read_flash(offset, size)
        prefix_start, prefix_end, _ = locate_prefix(chunk, NEXUS)
        return chunk[prefix_start:prefix_end]

    def read_bitstream_metadata(
        self, offset: int | None = None
    ) -> Dict[str, Any]:
        """Decode the Signaloid JSON metadata embedded in the bitstream.

        Args:
            offset: Flash byte offset of the bitstream; defaults to
                ``self.BITSTREAM_OFFSET``.

        Returns:
            The decoded metadata object.

        Raises:
            ValueError: if no valid JSON metadata object is present.
        """
        if offset is None:
            offset = self.BITSTREAM_OFFSET
        chunk = self.read_flash(offset, COMMENT_WINDOW_BYTES)
        return read_prefix_json(chunk, NEXUS)

    def verify_bitstream_crc(
        self,
        offset: int | None = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool | None:
        """Recompute the CRC-32 of the CRC-covered region and compare.

        Reads ``metadata['bitstream_size']`` bytes from just after the
        comment section and compares their CRC-32 to
        ``metadata['bitstream_crc']`` (accepting the ``crc``/``size``
        aliases).

        Args:
            offset: Flash byte offset of the bitstream; defaults to
                ``self.BITSTREAM_OFFSET``.
            metadata: Previously decoded metadata; read from the device if
                omitted.

        Returns:
            ``True``/``False`` for the comparison, or ``None`` if the
            metadata carries no CRC/size fields.
        """
        if offset is None:
            offset = self.BITSTREAM_OFFSET
        if metadata is None:
            metadata = self.read_bitstream_metadata(offset)

        expected_crc = metadata.get("bitstream_crc", metadata.get("crc"))
        expected_size = metadata.get("bitstream_size", metadata.get("size"))
        if expected_crc is None or expected_size is None:
            return None

        chunk = self.read_flash(offset, COMMENT_WINDOW_BYTES)
        _, _, crc_start = locate_prefix(chunk, NEXUS)

        # Checksummed in chunks so the whole bitstream is never held in RAM.
        # Useful for running on microcontrollers with limited RAM.
        crc = 0
        position = offset + crc_start
        remaining = int(expected_size)
        while remaining > 0:
            size = min(CRC_CHUNK_BYTES, remaining)
            crc = crc32(self.read_flash(position, size), crc)
            position += size
            remaining -= size
        return crc == int(expected_crc)

    def set_command(self, value: int) -> None:
        self._write(self.COMMAND_REGISTER_OFFSET, struct.pack("<I", value))

    def get_command(self) -> int:
        buffer = self._read(self.COMMAND_REGISTER_OFFSET, 4)
        return struct.unpack("<I", buffer)[0]

    def get_status(self) -> int:
        buffer = self._read(self.STATUS_REGISTER_OFFSET, 4)
        return struct.unpack("<I", buffer)[0]

    def calculate_command(
            self,
            command: int,
            idle_command: int = K_CALCULATE_NO_COMMAND,
            poll_sleep_time: float = 0.5,
            skip_MMIO_buffer_read: bool = False,
            verbose: bool = True,
            timeout_waiting_to_start: float = 0.5,
    ) -> bytes:
        data_buffer = None

        self.set_command(command)
        if verbose:
            print("Waiting for calculation to finish.", end="")

        start_time = time.time()
        while True:
            soc_status = self.get_status()

            if soc_status == SIGNALOID_SOC_STATUS_CALCULATING:
                if verbose:
                    print(".", end="")
                time.sleep(poll_sleep_time)
            elif soc_status == SIGNALOID_SOC_STATUS_DONE:
                if verbose:
                    print("\n")
                if not skip_MMIO_buffer_read:
                    if verbose:
                        print("Read data content...")
                    data_buffer = self.read_MMIO_buffer()
                break
            elif soc_status == SIGNALOID_SOC_STATUS_INVALID_COMMAND:
                if verbose:
                    print("\nERROR: Device returned 'Unknown CMD'\n")
                break
            elif soc_status == SIGNALOID_SOC_STATUS_WAIT_FOR_COMMAND:
                if time.time() - start_time < timeout_waiting_to_start:
                    continue

                if verbose:
                    print(f"\nERROR: Timeout waiting for command to start.\n")
                break
            elif soc_status != SIGNALOID_SOC_STATUS_WAIT_FOR_COMMAND:
                if verbose:
                    print(f"\nERROR: Unknown status: {soc_status}\n")
                break

        while (self.get_status() != SIGNALOID_SOC_STATUS_WAIT_FOR_COMMAND):
            self.set_command(idle_command)

        return data_buffer

    IDENTITY_TAG: Optional[bytes] = None

    EXPECTED_CAPACITY_BYTES: int = 0
    IDENTITY_OFFSET: int = 0

    def read_identity_tag(self) -> bytes:
        """Read the raw identity tag.

        Not guarded by require_soc_stopped: the tag sits above the flash access
        gate precisely so it answers in either core state.
        """
        return self._read(self.IDENTITY_OFFSET, IDENTITY_SIZE)

    # CONFIG bit 0 is the RV32RX reset-controller enable: the same bit the
    # `core-start` / `core-stop` configure actions drive.
    CORE_ENABLE_MASK: int = 0x00000001

    def get_config_register(self) -> int:
        buffer = self._read(self.CONFIG_REGISTER_OFFSET, 4)
        return struct.unpack("<I", buffer)[0]

    def soc_is_running(self) -> bool:
        """True when the SoC core is out of reset."""
        return bool(self.get_config_register() & self.CORE_ENABLE_MASK)

    def require_soc_stopped(self, operation: str) -> None:
        """Raise SoCRunning unless the core is stopped."""
        if self.soc_is_running():
            raise SoCRunning(operation)

    def read_flash(self, offset: int, size: int) -> bytes:
        self.require_soc_stopped("read the SPI flash")
        return self._read(offset, size)

    def write_flash(self, offset: int, data: bytes) -> int:
        self.require_soc_stopped("write the SPI flash")
        return self._write(offset, data)

    def read_otp(self, offset: int, size: int) -> bytes:
        self.require_soc_stopped("read the flash OTP")
        return self._read(offset, size)

    def set_config_register(self, value: int) -> None:
        self._write(self.CONFIG_REGISTER_OFFSET, struct.pack("<I", value))

    def apply_configure_action(
        self,
        action: str,
        confirm_callback: Callable[[], bool] | None = None,
        verbose: bool = False,
    ) -> None:
        if action not in self.SUPPORTED_ACTIONS:
            raise UnsupportedConfigureAction(
                action, self.DISPLAY_NAME, self.SUPPORTED_ACTIONS
            )

        value, mask, register_offset, confirm = self.SUPPORTED_ACTIONS[action]
        if confirm and (confirm_callback is None or not confirm_callback()):
            if verbose:
                print(f"Applying configure action {action} aborted.")
            return
        current = struct.unpack("<I", self._read(register_offset, 4))[0]
        updated = (current & ~mask) | (value & mask)
        self._write(register_offset, struct.pack("<I", updated))
        if verbose:
            print(f"Applied configure action: {action}")

    def reset_core(self, timeout: float = 1.0, verbose: bool = False) -> None:
        """Stop the core, wait, then start it again."""
        self.apply_configure_action("core-stop", verbose=verbose)
        time.sleep(timeout)
        self.apply_configure_action("core-start", verbose=verbose)
        time.sleep(timeout)

    def verbose_status(self) -> None:
        """Print the contents of the COMMAND, CONFIG, and STATUS registers."""
        print(f"{'COMMAND':>9}: {self.get_command():#010x}")
        print(f"{'CONFIG':>9}: {self.get_config_register():#010x}")
        print(f"{'STATUS':>9}: {self.get_status():#010x}")


if TYPE_CHECKING:
    _SDConfigMixinBase = C0SDBaseInterface
else:
    _SDConfigMixinBase = object


class SDConfigRegisterMixin(_SDConfigMixinBase):
    """Accessors for the SD config register, shared by variants that have one.

    Subclasses must provide ``SD_CONFIG_REGISTER_OFFSET`` and inherit
    from ``C0SDBaseInterface`` (for ``_read``/``_write`` and the base
    ``verbose_status``). The mixin should come first in the MRO so its
    ``verbose_status`` extends the base report.
    """

    SD_CONFIG_REGISTER_OFFSET: int = 0

    @staticmethod
    def sd_config_actions(
        offset: int,
    ) -> Dict[str, Tuple[int, int, int, bool]]:
        """Return the SD-config-register entries for ``SUPPORTED_ACTIONS``.

        Subclasses merge this into their own ``SUPPORTED_ACTIONS`` so
        the write-crc-* actions stay in lock-step across variants while
        each variant supplies its own register offset.
        """
        return {
            "write-crc-force-ok-enable":     (0x00000001, 0x00000001, offset, False),
            "write-crc-force-ok-disable":    (0x00000000, 0x00000001, offset, False),
            "write-crc-force-write-enable":  (0x00000100, 0x00000100, offset, False),
            "write-crc-force-write-disable": (0x00000000, 0x00000100, offset, False),
            "write-crc-irq-connect":         (0x00010000, 0x00010000, offset, False),
            "write-crc-irq-disconnect":      (0x00000000, 0x00010000, offset, False),
            "write-crc-irq-clear":           (0x00000000, 0x01000000, offset, False),
        }

    def get_sd_config_register(self) -> int:
        buffer: bytes = self._read(self.SD_CONFIG_REGISTER_OFFSET, 4)
        return struct.unpack("<I", buffer)[0]

    def set_sd_config_register(self, value: int) -> None:
        self._write(self.SD_CONFIG_REGISTER_OFFSET, struct.pack("<I", value))

    def get_sd_config_unpacked(self) -> Tuple[bool, ...]:
        """Read the SD config register and unpack its boolean fields.

        Returns:
            Tuple in declaration order: (force_write_crc_ok,
            ignore_write_crc_error, connect_crc_error_to_interrupt,
            crc_error).
        """
        value = self.get_sd_config_register()
        return (
            bool(value & (1 << 0)),
            bool(value & (1 << 8)),
            bool(value & (1 << 16)),
            bool(value & (1 << 24)),
        )

    def set_sd_config_unpacked(
        self,
        force_write_crc_ok: bool = True,
        ignore_write_crc_error: bool = False,
        connect_crc_error_to_interrupt: bool = False,
        crc_error: bool = False,
    ) -> None:
        """Write the SD config register from its boolean fields.

        Each call writes a complete register value. Defaults match the
        RDL reset values (``force_write_crc_ok=True``, rest ``False``),
        so omitted fields take their reset value.

        Args:
            force_write_crc_ok: Force CRC OK response for SD Block Write.
            ignore_write_crc_error: Commit Block Write data even on CRC mismatch.
            connect_crc_error_to_interrupt: Route crc_error to the core IRQ.
            crc_error: CRC error status; write 0 to clear.
        """
        value = (
            (force_write_crc_ok << 0)
            | (ignore_write_crc_error << 8)
            | (connect_crc_error_to_interrupt << 16)
            | (crc_error << 24)
        )
        self.set_sd_config_register(value)

    def modify_sd_config(
        self,
        force_write_crc_ok: Optional[bool] = None,
        ignore_write_crc_error: Optional[bool] = None,
        connect_crc_error_to_interrupt: Optional[bool] = None,
        crc_error: Optional[bool] = None,
    ) -> None:
        """Read-modify-write the SD config register, updating only the fields passed.

        Any argument left as None keeps its current on-device value;
        arguments passed as True/False force the corresponding bit to
        1/0. The whole transaction is one read followed by one write.

        Args:
            force_write_crc_ok: Force CRC OK response for SD Block Write.
            ignore_write_crc_error: Commit Block Write data even on CRC mismatch.
            connect_crc_error_to_interrupt: Route crc_error to the core IRQ.
            crc_error: CRC error status; write 0 to clear.
        """
        fields = (
            (force_write_crc_ok, 0),
            (ignore_write_crc_error, 8),
            (connect_crc_error_to_interrupt, 16),
            (crc_error, 24),
        )
        value = self.get_sd_config_register()
        for field, bit in fields:
            if field is None:
                continue
            mask = 1 << bit
            value = (value & ~mask) | ((1 if field else 0) << bit)
        self.set_sd_config_register(value)

    def verbose_status(self) -> None:
        """Extend the base report with the SD_CONFIG register."""
        C0SDBaseInterface.verbose_status(self)
        print(f"{'SD_CONFIG':>9}: {self.get_sd_config_register():#010x}")


# ---------------------------------------------------------------------------
# Variant subclasses
#
# Each variant supplies its DISPLAY_NAME, the per-device register and
# flash offsets, and its SUPPORTED_ACTIONS table. Behaviour (register I/O
# and configure-action dispatch) is inherited from C0SDBaseInterface.
# ---------------------------------------------------------------------------


class C0microSDPlusInterface(SDConfigRegisterMixin, C0SDBaseInterface):
    """Interface for the C0-microSD+ variant. CLI default."""

    DISPLAY_NAME = "C0-microSD+"
    IDENTITY_TAG = b"S-C0-01\x00"

    def __init__(
            self,
            target_device: str,
            regmap: Optional[Any] = None,
            regmap_path: Optional[str] = None,
            ) -> None:
        top = resolve_regmap(
            regmap, ".regmaps.c0microsdplus", _REGMAP_ANCHOR, "Top", regmap_path
        )
        csr = top.Csr

        self.EXPECTED_CAPACITY_BYTES = top.AxilPibDma.BASE_ADDR
        self.IDENTITY_OFFSET = top.DeviceId.Identity0.ADDR

        self.BITSTREAM_OFFSET = top.SpiFlash.Bitstream.BOTTOM_ENTRY
        self.APPLICATION_BINARY_OFFSET = top.SpiFlash.UserData.BOTTOM_ENTRY

        self.COMMAND_REGISTER_OFFSET = csr.Command.ADDR
        self.CONFIG_REGISTER_OFFSET = csr.Config.ADDR
        self.STATUS_REGISTER_OFFSET = csr.Status.ADDR
        self.SD_CONFIG_REGISTER_OFFSET = csr.SdConfig.ADDR

        self.TRAP_MCAUSE_REGISTER_OFFSET = csr.TrapMcause.ADDR
        self.TRAP_MEPC_REGISTER_OFFSET = csr.TrapMepc.ADDR
        self.TRAP_MTVAL_REGISTER_OFFSET = csr.TrapMtval.ADDR
        self.BITSTREAM_UNLOCK_REGISTER_OFFSET = csr.BitstreamUnlock.ADDR

        self.MMIO_BUFFER_OFFSET = top.IoBuff.BOTTOM_ENTRY
        self.MMIO_BUFFER_SIZE_BYTES = top.IoBuff.SIZE_BYTES

        self.OTP_OFFSET = top.FlashOtp.BOTTOM_ENTRY
        self.OTP_SIZE = top.FlashOtp.SIZE_BYTES

        config = self.CONFIG_REGISTER_OFFSET
        self.SUPPORTED_ACTIONS: Dict[str, Tuple[int, int, int, bool]] = {
            "core-start":       (0x00000001, 0x00000001, config, False),
            "core-stop":        (0x00000000, 0x00000001, config, False),
            "sw-led-on":        (0x0000000C, 0x0000000C, config, False),
            "sw-led-off":       (0x00000000, 0x0000000C, config, False),
            "red-led-on":       (0x00000010, 0x00000010, config, False),
            "red-led-off":      (0x00000000, 0x00000010, config, False),
            "green-led-on":     (0x00000020, 0x00000020, config, False),
            "green-led-off":    (0x00000000, 0x00000020, config, False),
            "blue-led-on":      (0x00000040, 0x00000040, config, False),
            "blue-led-off":     (0x00000000, 0x00000040, config, False),
            "debug-pin-0-on":   (0x00000080, 0x00000080, config, False),
            "debug-pin-0-off":  (0x00000000, 0x00000080, config, False),
            "debug-pin-1-on":   (0x00000100, 0x00000100, config, False),
            "debug-pin-1-off":  (0x00000000, 0x00000100, config, False),
            "debug-pin-2-on":   (0x00000200, 0x00000200, config, False),
            "debug-pin-2-off":  (0x00000000, 0x00000200, config, False),
        }
        self.SUPPORTED_ACTIONS.update(
            self.bitstream_lock_actions(self.BITSTREAM_UNLOCK_REGISTER_OFFSET)
        )
        self.SUPPORTED_ACTIONS.update(
            self.sd_config_actions(self.SD_CONFIG_REGISTER_OFFSET)
        )

        super().__init__(target_device)

    def get_config_register_unpacked(self) -> Tuple[bool, ...]:
        """Read the config register and unpack its boolean fields.

        Returns:
            Tuple in declaration order: (rstn, sw_led_enable, sw_led,
            red_led, green_led, blue_led, debug_pin_0, debug_pin_1,
            debug_pin_2).
        """
        value = self.get_config_register()
        return (
            bool(value & 0x001),
            bool(value & 0x004),
            bool(value & 0x008),
            bool(value & 0x010),
            bool(value & 0x020),
            bool(value & 0x040),
            bool(value & 0x080),
            bool(value & 0x100),
            bool(value & 0x200),
        )

    def set_config_register_unpacked(
        self,
        rstn: bool = False,
        sw_led_enable: bool = False,
        sw_led: bool = False,
        red_led: bool = False,
        green_led: bool = False,
        blue_led: bool = False,
        debug_pin_0: bool = False,
        debug_pin_1: bool = False,
        debug_pin_2: bool = False,
    ) -> None:
        """Write the config register from its boolean fields.

        Each call writes a complete register value. Unspecified fields
        take their RDL reset default (0), so pass only the fields you
        want set to 1.

        Args:
            rstn: Reset CPU (active low).
            sw_led_enable: Enable software management of the onboard red LED.
            sw_led: Onboard red LED (requires sw_led_enable=True).
            red_led: Red LED control.
            green_led: Green LED control.
            blue_led: Blue LED control.
            debug_pin_0: Debug pin 0.
            debug_pin_1: Debug pin 1.
            debug_pin_2: Debug pin 2.
        """
        value = (
            (rstn << 0)
            | (sw_led_enable << 2)
            | (sw_led << 3)
            | (red_led << 4)
            | (green_led << 5)
            | (blue_led << 6)
            | (debug_pin_0 << 7)
            | (debug_pin_1 << 8)
            | (debug_pin_2 << 9)
        )
        self.set_config_register(value)

    def modify_config_register(
        self,
        rstn: Optional[bool] = None,
        sw_led_enable: Optional[bool] = None,
        sw_led: Optional[bool] = None,
        red_led: Optional[bool] = None,
        green_led: Optional[bool] = None,
        blue_led: Optional[bool] = None,
        debug_pin_0: Optional[bool] = None,
        debug_pin_1: Optional[bool] = None,
        debug_pin_2: Optional[bool] = None,
    ) -> None:
        """Read-modify-write the config register, updating only the fields passed.

        Any argument left as None keeps its current on-device value;
        arguments passed as True/False force the corresponding bit to
        1/0. The whole transaction is one read followed by one write.

        Args:
            rstn: Reset CPU (active low).
            sw_led_enable: Enable software management of the onboard red LED.
            sw_led: Onboard red LED (requires sw_led_enable=True).
            red_led: Red LED control.
            green_led: Green LED control.
            blue_led: Blue LED control.
            debug_pin_0: Debug pin 0.
            debug_pin_1: Debug pin 1.
            debug_pin_2: Debug pin 2.
        """
        fields = (
            (rstn, 0),
            (sw_led_enable, 2),
            (sw_led, 3),
            (red_led, 4),
            (green_led, 5),
            (blue_led, 6),
            (debug_pin_0, 7),
            (debug_pin_1, 8),
            (debug_pin_2, 9),
        )
        value = self.get_config_register()
        for field, bit in fields:
            if field is None:
                continue
            mask = 1 << bit
            value = (value & ~mask) | ((1 if field else 0) << bit)
        self.set_config_register(value)

    def get_trap_status(self) -> Tuple[int, int, int]:
        """Read the trap registers (mcause, mepc, mtval) in one transaction.

        The three registers are contiguous in the memory map, so they are read
        together as a single packed 12-byte block. Returns (mcause, mepc,
        mtval); mcause is 0xFFFFFFFF when no trap has been recorded.
        """
        buffer = self._read(self.TRAP_MCAUSE_REGISTER_OFFSET, 12)
        return struct.unpack("<III", buffer)


class C0SDInterface(SDConfigRegisterMixin, C0SDBaseInterface):
    """Interface for the C0-SD variant.

    Offsets are derived from the auto-generated ``regmaps.c0sd`` register
    map, so they stay in lock-step with the hardware definition.
    """

    DISPLAY_NAME = "C0-SD"
    IDENTITY_TAG = b"S-C0-02\x00"

    def __init__(
            self,
            target_device: str,
            regmap: Optional[Any] = None,
            regmap_path: Optional[str] = None,
            ) -> None:
        top = resolve_regmap(
            regmap, ".regmaps.c0sd", _REGMAP_ANCHOR, "Top", regmap_path
        )
        csr = top.Csr

        self.EXPECTED_CAPACITY_BYTES = top.AxilPibDma.BASE_ADDR
        self.IDENTITY_OFFSET = top.DeviceId.Identity0.ADDR

        self.BITSTREAM_OFFSET = top.SpiFlash.Bitstream.BOTTOM_ENTRY
        self.APPLICATION_BINARY_OFFSET = top.SpiFlash.UserData.BOTTOM_ENTRY

        self.COMMAND_REGISTER_OFFSET = csr.Command.ADDR
        self.CONFIG_REGISTER_OFFSET = csr.Config.ADDR
        self.STATUS_REGISTER_OFFSET = csr.Status.ADDR
        self.SD_CONFIG_REGISTER_OFFSET = csr.SdConfig.ADDR

        self.TRAP_MCAUSE_REGISTER_OFFSET = csr.TrapMcause.ADDR
        self.TRAP_MEPC_REGISTER_OFFSET = csr.TrapMepc.ADDR
        self.TRAP_MTVAL_REGISTER_OFFSET = csr.TrapMtval.ADDR
        self.BITSTREAM_UNLOCK_REGISTER_OFFSET = csr.BitstreamUnlock.ADDR

        self.MMIO_BUFFER_OFFSET = top.IoBuff.BOTTOM_ENTRY
        self.MMIO_BUFFER_SIZE_BYTES = top.IoBuff.SIZE_BYTES

        self.OTP_OFFSET = top.FlashOtp.BOTTOM_ENTRY
        self.OTP_SIZE = top.FlashOtp.SIZE_BYTES

        config = self.CONFIG_REGISTER_OFFSET
        self.SUPPORTED_ACTIONS: Dict[str, Tuple[int, int, int, bool]] = {
            "core-start":       (0x00000001, 0x00000001, config, False),
            "core-stop":        (0x00000000, 0x00000001, config, False),
            "sw-led-on":        (0x0000000C, 0x0000000C, config, False),
            "sw-led-off":       (0x00000000, 0x0000000C, config, False),
            "green-led-on":     (0x00000010, 0x00000010, config, False),
            "green-led-off":    (0x00000000, 0x00000010, config, False),
            "debug-pin-on":     (0x00000020, 0x00000020, config, False),
            "debug-pin-off":    (0x00000000, 0x00000020, config, False),
        }
        self.SUPPORTED_ACTIONS.update(
            self.bitstream_lock_actions(self.BITSTREAM_UNLOCK_REGISTER_OFFSET)
        )
        self.SUPPORTED_ACTIONS.update(
            self.sd_config_actions(self.SD_CONFIG_REGISTER_OFFSET)
        )

        super().__init__(target_device)

    def get_config_register_unpacked(self) -> Tuple[bool, ...]:
        """Read the config register and unpack its boolean fields.

        Returns:
            Tuple in declaration order: (rstn, sw_led_enable, sw_led,
            green_led, debug_pin_0).
        """
        value = self.get_config_register()
        return (
            bool(value & 0x01),
            bool(value & 0x04),
            bool(value & 0x08),
            bool(value & 0x10),
            bool(value & 0x20),
        )

    def set_config_register_unpacked(
        self,
        rstn: bool = False,
        sw_led_enable: bool = False,
        sw_led: bool = False,
        green_led: bool = False,
        debug_pin_0: bool = False,
    ) -> None:
        """Write the config register from its boolean fields.

        Each call writes a complete register value. Unspecified fields
        take their RDL reset default (0), so pass only the fields you
        want set to 1.

        Args:
            rstn: Reset CPU (active low).
            sw_led_enable: Enable software management of the onboard red LED.
            sw_led: Onboard red LED (requires sw_led_enable=True).
            green_led: Green LED control.
            debug_pin_0: Debug pin 0.
        """
        value = (
            (rstn << 0)
            | (sw_led_enable << 2)
            | (sw_led << 3)
            | (green_led << 4)
            | (debug_pin_0 << 5)
        )
        self.set_config_register(value)

    def modify_config_register(
        self,
        rstn: Optional[bool] = None,
        sw_led_enable: Optional[bool] = None,
        sw_led: Optional[bool] = None,
        green_led: Optional[bool] = None,
        debug_pin_0: Optional[bool] = None,
    ) -> None:
        """Read-modify-write the config register, updating only the fields passed.

        Any argument left as None keeps its current on-device value;
        arguments passed as True/False force the corresponding bit to
        1/0. The whole transaction is one read followed by one write.

        Args:
            rstn: Reset CPU (active low).
            sw_led_enable: Enable software management of the onboard red LED.
            sw_led: Onboard red LED (requires sw_led_enable=True).
            green_led: Green LED control.
            debug_pin_0: Debug pin 0.
        """
        fields = (
            (rstn, 0),
            (sw_led_enable, 2),
            (sw_led, 3),
            (green_led, 4),
            (debug_pin_0, 5),
        )
        value = self.get_config_register()
        for field, bit in fields:
            if field is None:
                continue
            mask = 1 << bit
            value = (value & ~mask) | ((1 if field else 0) << bit)
        self.set_config_register(value)

    def get_trap_status(self) -> Tuple[int, int, int]:
        """Read the trap registers (mcause, mepc, mtval) in one transaction.

        The three registers are contiguous in the memory map, so they are
        read together as a single packed 12-byte block rather than with
        three separate transactions.

        Returns:
            Tuple in order (mcause, mepc, mtval). ``mcause`` is
            ``0xFFFFFFFF`` when no trap has been recorded.
        """
        buffer = self._read(self.TRAP_MCAUSE_REGISTER_OFFSET, 12)
        return struct.unpack("<III", buffer)

    def verbose_status(self) -> None:
        """Extend the report with the trap registers (mcause/mepc/mtval).

        After the base + SD report (COMMAND, CONFIG, STATUS, SD_CONFIG),
        the trap registers are read in one transaction. ``mcause`` reads
        ``0xFFFFFFFF`` when no trap has occurred; any other value
        indicates a trap, in which case an extra section is printed with
        ``mcause`` as an unsigned integer and ``mepc``/``mtval`` as hex.
        """
        super().verbose_status()

        mcause, mepc, mtval = self.get_trap_status()
        if mcause != 0xFFFFFFFF:
            print("\nA trap has occurred:")
            print(f"{'MCAUSE':>9}: {mcause}")
            print(f"{'MEPC':>9}: {mepc:#010x}")
            print(f"{'MTVAL':>9}: {mtval:#010x}")
