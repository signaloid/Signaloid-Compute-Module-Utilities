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


import json
import struct
import sys
import time

if sys.implementation.name != "circuitpython":
    from typing import Literal
    from ..common.raw_block_device import UnifiedBlockDevice


from .constants import BOOTLOADER_CONSTANTS, SOC_CONSTANTS

SIGNALOID_SOC_STATUS_WAIT_FOR_COMMAND = 0


from ..common.bitstream_prefix import crc32, find_json_object

SIGNALOID_SOC_STATUS_CALCULATING = 1
SIGNALOID_SOC_STATUS_DONE = 2
SIGNALOID_SOC_STATUS_INVALID_COMMAND = 3

K_CALCULATE_NO_COMMAND = 0


class C0microSDInterface:
    """Communication interface for C0-microSD.

    This class provides basic functionality for interfacing with the
    Signaloid C0-microSD.
    """

    # 128 KiB offset for hardware status
    DEVICE_CONFIGURATION_STATUS_OFFSET = 0x20000
    BOOTLOADER_CHECK_WORD = b"SBLD"
    SOC_CHECK_WORD = b"SSOC"

    def __init__(
        self,
        target_device: str,
        force_transactions: bool = False,
    ) -> None:
        """
        Initializes the C0-microSD interface.

        :param target_device:       The block device name of the C0-microSD,
                                    e.g. `/dev/disk4`.
        :param force_transactions:  Force executing read/write transactions,
                                    even when errors occur. Probably never
                                    needed. Default `False`.
        """
        self.target_device = target_device

        self.configuration: Literal["bootloader", "soc"] | None = None
        self.configuration_version: tuple[int, int] | None = None
        self.configuration_state: int | None = None
        self.configuration_switching: bool = False
        self.force_transactions: bool = force_transactions

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

    def get_status(self) -> None:
        """
        Reads configuration status from the C0-microSD.
        """
        data = self._read(self.DEVICE_CONFIGURATION_STATUS_OFFSET, 12)
        # Decode configuration id register
        configuration_id = data[0:4]
        if configuration_id == self.BOOTLOADER_CHECK_WORD:
            self.configuration = "bootloader"
        elif configuration_id == self.SOC_CHECK_WORD:
            self.configuration = "soc"
        elif not self.force_transactions:
            raise RuntimeError("Error: Device is not a C0-microSD.")
        # Decode configuration data register
        configuration_version = data[4:8]
        major_version = (
            configuration_version[0] << 8
        ) | configuration_version[1]
        minor_version = (
            configuration_version[2] << 8
        ) | configuration_version[3]
        self.configuration_version = (major_version, minor_version)
        # Decode configuration state register
        self.configuration_state = struct.unpack(">I", data[8:12])[0]
        self.configuration_switching = (
            bool(self.configuration_state & 1)
            if self.configuration_state is not None
            else False
        )

        if self.configuration_switching and not self.force_transactions:
            print(self)
            raise RuntimeError(
                "Error: Device is in configuration switching mode. "
                "Power-cycle the device and try again."
            )

    def __str__(self) -> str:
        value = "Signaloid C0-microSD"
        if self.configuration == "bootloader":
            value += " | Loaded configuration: Bootloader"
        elif self.configuration == "soc":
            value += " | Loaded configuration: Signaloid SoC"
        else:
            value += " | Loaded configuration: UNKNOWN"

        if self.configuration_version is not None:
            major_version = self.configuration_version[0]
            minor_version = self.configuration_version[1]
            value += f" | Version: {major_version}.{minor_version}"
        else:
            value += " | Version: N/A"

        if self.configuration_switching:
            value += " | State SWITCHING"
        else:
            value += " | State IDLE"
        return value

    def find_json_string(self, data: bytes) -> dict | None:
        """
        Attempts to decode the first valid JSON object from a byte stream.
        Assumes input is <= 4 KB and encoded in ASCII.

        :param data: The byte stream to scan.
        :return: The decoded JSON object, or None if none is found.
        """
        return find_json_object(data)

    def get_bitstream_prefix(
        self,
        bitstream_offset: int,
    ) -> tuple[dict | None, int, int]:
        """
        Reads the prefix section of a bitstream

        :param bitstream_offset: Offset of bitstream in flash memory
        """
        # We assume that the prefix is never going to be larger than 4K
        self.get_status()
        prefix_chunk = self._read(bitstream_offset, 4096)
        # Decode prefix chunk to find prefix
        prefix = find_json_object(prefix_chunk)

        if prefix is None:
            raise ValueError("Could not find bitstream prefix section.")

        try:
            major_bitstream_version = int(str(prefix["v"]).split(".")[0])
        except Exception:
            major_bitstream_version = 1
        # Use the bitstream version to know exactly how to
        # find start and end of prefix. This is required to calculate
        # the CRC
        prefix_start_word = BOOTLOADER_CONSTANTS[
            major_bitstream_version
        ].kBitstreamPrefixStart
        prefix_end_word = BOOTLOADER_CONSTANTS[
            major_bitstream_version
        ].kBitstreamPrefixEnd

        prefix_start = prefix_chunk.find(prefix_start_word)
        prefix_end = prefix_chunk.find(prefix_end_word, prefix_start)
        prefix_end += len(prefix_end_word)

        return prefix, prefix_start, prefix_end

    def verify_bitstream_crc(
        self,
        bitstream_offset: int,
        bitstream_crc: int,
        bitstream_prefix_size: int,
        bitstream_size: int,
    ) -> bool:
        """
        Verifies a the crc32 checksum of a bitstream

        :param bitstream_offset: Offset of bitstream in flash memory
        :param bitstream_crc: Expected crc of bitstream
        :param bitstream_size: Expected size of bitstream in bytes
        """

        bitstream = self._read(
            bitstream_offset, bitstream_prefix_size + bitstream_size
        )

        bitstream_data = bitstream[bitstream_prefix_size:]
        actual_crc = crc32(bitstream_data)

        return actual_crc == bitstream_crc

    def print_bitstream_information(self, offset) -> None:
        """
        Reads and prints bitstream prefix from a specific offset in the
        device. Also runs crc verification if prefix is in json format and
        includes `bitstream_crc` and `bitstream_size` attributes

        :param offset: Offset of bitstream in flash memory
        """

        prefix, _, prefix_end = self.get_bitstream_prefix(offset)
        prefix_text = json.dumps(prefix, separators=(', ', ': '))
        print(f"    Bitstream prefix section: {prefix_text}")

        try:
            if "bitstream_crc" in prefix:
                bitstream_crc = prefix["bitstream_crc"]
            elif "crc" in prefix:
                bitstream_crc = prefix["crc"]

            if "bitstream_size" in prefix:
                bitstream_size = prefix["bitstream_size"]
            elif "size" in prefix:
                bitstream_size = prefix["size"]

            crc_pass = self.verify_bitstream_crc(
                offset,
                bitstream_crc,
                prefix_end,
                bitstream_size,
            )

            if crc_pass:
                print("    Bitstream CRC verification: PASS")
            else:
                print("    Bitstream CRC verification: FAIL")
        except Exception:
            print("    Unable to parse prefix for CRC verification")


class C0microSDSignaloidSoCInterface(C0microSDInterface):
    """Communication interface for C0-microSD Signaloid SoC configuration.

    This class extends the C0microSDInterface class to include constants and
    routines for interfacing with the Signaloid C0-microSD when the Signaloid
    SoC is loaded. You can use this class to read and write to/from the
    MISO/MOSI buffers, issue commands, and probe the status of the SoC.
    """

    DEBUG_LOG_BUFFER_SIZE_BYTES = 512

    def __init__(
        self,
        target_device: str,
        force_transactions: bool = False,
    ) -> None:
        super().__init__(target_device, force_transactions)
        self.get_status()

        soc_major_version = (
            self.configuration_version[0] if self.configuration_version else 0
        )
        self.MOSI_BUFFER_SIZE_BYTES = SOC_CONSTANTS[
            soc_major_version
        ].kMosiBufferSizeBytes

        self.MISO_BUFFER_SIZE_BYTES = SOC_CONSTANTS[
            soc_major_version
        ].kMisoBufferSizeBytes

        self.STATUS_REGISTER_OFFSET = SOC_CONSTANTS[
            soc_major_version
        ].kStatusRegisterOffset

        self.SOC_CONTROL_REGISTER_OFFSET = SOC_CONSTANTS[
            soc_major_version
        ].kSOCControlRegisterOffset

        self.COMMAND_REGISTER_OFFSET = SOC_CONSTANTS[
            soc_major_version
        ].kCommandRegisterOffset

        self.MOSI_BUFFER_OFFSET = SOC_CONSTANTS[
            soc_major_version
        ].kMOSIBufferOffset

        self.MISO_BUFFER_OFFSET = SOC_CONSTANTS[
            soc_major_version
        ].kMISOBufferOffset

        self.INPUT_BUFFER_SIZE_BYTES = self.MOSI_BUFFER_SIZE_BYTES
        self.OUTPUT_BUFFER_SIZE_BYTES = self.MISO_BUFFER_SIZE_BYTES

    def write_signaloid_soc_MOSI_buffer(self, buffer: bytes) -> None:
        """
        Writes data to the C0-microSD MOSI buffer.

        :param buffer: The data buffer to write.
        """
        if len(buffer) > self.MOSI_BUFFER_SIZE_BYTES:
            raise ValueError(
                "Buffer size exceeds maximum allowed "
                f"size of {self.MOSI_BUFFER_SIZE_BYTES} bytes."
            )

        self._write(self.MOSI_BUFFER_OFFSET, buffer)

    def read_signaloid_soc_MISO_buffer(
        self,
        size: int | None = None,
    ) -> bytes:
        """
        Reads data from the C0-microSD MISO buffer.

        :param size: Size in bytes of data to read.

        :return: The read buffer
        """
        if size is None:
            size = self.MISO_BUFFER_SIZE_BYTES

        if size > self.MISO_BUFFER_SIZE_BYTES:
            raise ValueError(
                "Read MISO size exceeds"
                f" {self.MISO_BUFFER_SIZE_BYTES} bytes."
            )
        return self._read(self.MISO_BUFFER_OFFSET, size)

    def write_input_buffer(self, buffer: bytes) -> None:
        """
        Writes data to the C0-microSD input/MOSI buffer.

        :param buffer: The data buffer to write.
        """
        return self.write_signaloid_soc_MOSI_buffer(buffer)

    def read_output_buffer(self, size: int | None = None) -> bytes:
        """
        Reads data from the C0-microSD output/MISO buffer.

        :param size: Size in bytes of data to read.

        :return: The read buffer
        """
        return self.read_signaloid_soc_MISO_buffer(size)

    def read_debug_log_buffer(
        self,
        size: int = DEBUG_LOG_BUFFER_SIZE_BYTES,
    ) -> bytes:
        """
        Reads data from the C0-microSD UART buffer.

        :param size: Size in bytes of data to read.

        :return: The read buffer
        """

        if size > self.MISO_BUFFER_SIZE_BYTES:
            raise ValueError(
                "Read UART size exceeds"
                f" {self.MISO_BUFFER_SIZE_BYTES} bytes."
            )

        DEBUG_LOG_BUFFER_OFFSET = (
            self.MISO_BUFFER_OFFSET + self.MISO_BUFFER_SIZE_BYTES - size
        )
        return self._read(DEBUG_LOG_BUFFER_OFFSET, size)

    def send_signaloid_soc_command(self, value: int) -> None:
        """
        Sends a command to the C0-microSD device.

        :param value: The uint32_t value to write
        """
        # Pack the uint32_t value into a 4-byte buffer and send it
        self._write(self.COMMAND_REGISTER_OFFSET, struct.pack("<I", value))

    def get_signaloid_soc_status(self) -> int:
        """
        Reads the C0-microSD status register.

        :return: The read uint32_t value
        """
        buffer = self._read(self.STATUS_REGISTER_OFFSET, 4)
        # Unpack the buffer to get the uint32_t value
        return struct.unpack("<I", buffer)[0]

    def calculate_command(
        self,
        command: int,
        idle_command: int = K_CALCULATE_NO_COMMAND,
        poll_sleep_time: float = 0.5,
        skip_MISO_read: bool = False,
        verbose: bool = True,
        timeout_waiting_to_start: float = 0.5,
    ) -> bytes | None:
        """
        Basic command calculation routine. This function sends a command to
        the C0-microSD, polls the device until it reports that the calculation
        has finished, and finally returns the MISO buffer data.

        :param command:                     The C0-microSD command.
        :param idle_command:                This is the command that will be
                                            sent after the calculation is
                                            complete. The default is
                                            K_CALCULATE_NO_COMMAND.
        :param poll_sleep_time:             The time between each status check
                                            to see if command has finished
                                            processing. Default 0.5s.
        :param skip_MISO_read:              Skip reading MISO buffer after
                                            command has finished processing.
                                            Default `False`.
        :param verbose:                     Whether to print verbose messages
                                            or stay silent. Default `True`.
        :param timeout_waiting_to_start:    Timeout time before device is
                                            considered blocked. Prevents
                                            waiting indefinitely.

        :return: The MISO buffer contents after the command has finished.
        """
        data_buffer = None

        self.send_signaloid_soc_command(command)
        if verbose:
            print("Waiting for calculation to finish.", end="")

        start_time = time.time()
        while True:
            # Get status of Signaloid C0-microSD compute module
            soc_status = self.get_signaloid_soc_status()

            if soc_status == SIGNALOID_SOC_STATUS_CALCULATING:
                # Signaloid C0-microSD compute module is still calculating
                if verbose:
                    print(".", end="")
                time.sleep(poll_sleep_time)
            elif soc_status == SIGNALOID_SOC_STATUS_DONE:
                # Signaloid C0-microSD completed calculation
                if verbose:
                    print("\n")
                if not skip_MISO_read:
                    if verbose:
                        print("Read data content...")
                    data_buffer = self.read_signaloid_soc_MISO_buffer()
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

        while (
            self.get_signaloid_soc_status()
            != SIGNALOID_SOC_STATUS_WAIT_FOR_COMMAND
        ):
            self.send_signaloid_soc_command(idle_command)

        return data_buffer
