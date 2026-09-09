#   Copyright (c) 2026, Signaloid.
#
#   Permission is hereby granted, free of charge, to any person obtaining a
#   copy of this software and associated documentation files (the "Software"),
#   to deal in the Software without restriction, including without limitation
#   the rights to use, copy, modify, merge, publish, distribute, sublicense,
#   and/or sell copies of the Software, and to permit persons to whom the
#   Software is furnished to do so, subject to the following conditions:
#
#   The above copyright notice and this permission notice shall be included in
#   all copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
#   FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
#   DEALINGS IN THE SOFTWARE.

"""SD-over-SPI transport for Signaloid C0 compute modules.

Implements the host side of SPI mode as defined by the SD Physical Layer
Specification: the power-up and identification sequence, the six-byte command
frame with its CRC7, the R1/R1b/R2/R3/R7 response formats, and single- and
multiple-block reads and writes with their CRC16-suffixed data tokens.

The transport needs nothing from the host beyond a `busio.SPI` bus in mode 0
and one output pin for chip select, so it runs on any CircuitPython board that
can expose those. Every wait is bounded both by a byte count and by wall-clock
time, so a card that stops answering raises `SDOverSPIError` instead of
blocking.
"""

try:
    from typing import Callable, Dict, List, Optional, Union

    # Everything the SPI bus can transmit from, or receive into.
    ByteSource = Union[List[int], bytes, bytearray, memoryview]
except Exception:
    pass

import time
from array import array

import digitalio
from busio import SPI
from microcontroller import Pin


class SDOverSPIError(RuntimeError):
    """An SD-over-SPI transaction failed."""


class SDOverSPI:
    class CMDs:
        """
        SD card command codes.

        Not all commands are supported by the Signaloid C0-microSD.
        """

        CMD0_GO_IDLE_STATE = 0
        CMD1_SEND_OP_COND = 1
        CMD6_SWITCH_FUNC = 6
        CMD8_SEND_IF_COND = 8
        CMD9_SEND_CSD = 9
        CMD10_SEND_CID = 10
        CMD12_STOP_TRANSMISSION = 12
        CMD13_SEND_STATUS = 13
        CMD16_SET_BLOCKLEN = 16
        CMD17_READ_SINGLE_BLOCK = 17
        CMD18_READ_MULTIPLE_BLOCK = 18
        CMD24_WRITE_BLOCK = 24
        CMD25_WRITE_MULTIPLE_BLOCK = 25
        CMD27_PROGRAM_CSD = 27
        CMD28_SET_WRITE_PROT = 28
        CMD29_CLR_WRITE_PROT = 29
        CMD30_SEND_WRITE_PROT = 30
        CMD32_ERASE_WR_BLK_START_ADDR = 32
        CMD33_ERASE_WR_BLK_END_ADDR = 33
        CMD38_ERASE = 38
        CMD42_LOCK_UNLOCK = 42
        CMD55_APP_CMD = 55
        CMD56_GEN_CMD = 56
        CMD58_READ_OCR = 58
        CMD59_CRC_ON_OFF = 59
        ACMD13_SD_STATUS = 13
        ACMD22_SEND_NUM_WR_BLOCKS = 22
        ACMD23_SET_WR_BLK_ERASE_COUNT = 23
        ACMD41_SD_SEND_OP_COND = 41
        ACMD42_SET_CLR_CARD_DETECT = 42
        ACMD51_SEND_SCR = 51

    class RESPONSES:
        """SD card response codes."""

        BUSY = 0x00
        READY = 0xFE
        R1_IN_IDLE_STATE = 0x01
        R1_INITIALIZED = 0x00

    class CONTROL_TOKENS:
        """SD card control tokens."""

        CMD17_START_BLOCK_TOKEN = 0b11111110
        CMD18_START_BLOCK_TOKEN = 0b11111110
        CMD24_START_BLOCK_TOKEN = 0b11111110
        CMD25_START_BLOCK_TOKEN = 0b11111100
        CMD25_STOP_BLOCK_TOKEN = 0b11111101

    class R1_ERRORS:
        """Error bits of an R1 response byte."""

        PARAMETER_ERROR = 0x40
        ADDRESS_ERROR = 0x20
        ERASE_SEQUENCE_ERROR = 0x10
        COM_CRC_ERROR = 0x08
        ILLEGAL_COMMAND = 0x04
        ERASE_RESET = 0x02
        IN_IDLE_STATE = 0x01

        # Every bit except in-idle-state, which reports progress rather than
        # an error.
        MASK = 0x7E

        NAMES = (
            (0x40, "parameter error"),
            (0x20, "address error"),
            (0x10, "erase sequence error"),
            (0x08, "com crc error"),
            (0x04, "illegal command"),
            (0x02, "erase reset"),
        )

    class DATA_ERRORS:
        """Error bits of a data error token."""

        NAMES = (
            (0x08, "out of range"),
            (0x04, "card ECC failed"),
            (0x02, "CC error"),
            (0x01, "error"),
        )

    class DATA_RESPONSE:
        """Status field of the data response token that follows a written
        block."""

        # A data response token reads `x x x 0 <status:3> 1`, so bit 4 clear
        # and bit 0 set identify the token in the byte stream.
        FRAME_MASK = 0b00010001
        FRAME_VALUE = 0b00000001

        MASK = 0b00011111
        ACCEPTED = 0b00000101
        CRC_ERROR = 0b00001011
        WRITE_ERROR = 0b00001101

    # The byte the host holds on DI while it clocks the card, and the value a
    # released DO line reads back as.
    IDLE_BYTE = 0xFF

    # Default length of a data block, in bytes.
    DEFAULT_BLOCK_SIZE = 512

    # Clock rate the card is identified at. The card is only guaranteed to
    # accept its first commands at or below this rate.
    IDENTIFICATION_BAUDRATE = 400000

    # Idle clocks the card is given before its first command, expressed as
    # bytes and as seconds. The card needs whichever is longer.
    POWER_UP_IDLE_BYTES = 10
    POWER_UP_IDLE_SECONDS = 0.001

    # Longest a card may take to leave the idle state and finish initializing.
    INITIALIZATION_TIMEOUT = 1.0

    # Longest a card may take to answer a command, or to start a data block.
    RESPONSE_TIMEOUT = 0.1

    # Longest a card may hold DO low while it programs a written block.
    BUSY_TIMEOUT = 0.5

    # Longest a command may be retransmitted for while it waits for an
    # expected response.
    COMMAND_TIMEOUT = 1.0

    # Longest to wait for another user of the SPI bus to release it.
    BUS_TIMEOUT = 5.0

    # Size of the buffer that backs reads and runs of idle bytes.
    SCRATCH_SIZE = 16

    _CRC16_TABLE = None

    def __init__(
        self,
        spi: SPI,
        cs_pin: Pin,
        timeout: int = 1000,
        dummy_bytes_count: int = 2,
        block_size: int = DEFAULT_BLOCK_SIZE,
        identification_baudrate: int = IDENTIFICATION_BAUDRATE,
        crc_check: bool = True,
        crc_on: bool = False,
    ) -> None:
        """
        Initializes the SD card interface.

        :param spi: The SPI bus to use for communication. It must be in SPI
                    mode 0 (polarity 0, phase 0).
        :param cs_pin: The pin to use for the chip select signal.
        :param timeout: The timeout for communication in times of retries.
        :param dummy_bytes_count: The number of dummy bytes to send whenever
                                    needed.
        :param block_size: The data block length to set on the card, in bytes.
        :param identification_baudrate: The clock rate to identify the card
                                    at. The bus is returned to its previous
                                    rate once identification finishes.
        :param crc_check: Verify the CRC16 that suffixes every data block the
                                    card sends.
        :param crc_on: Ask the card to check the CRC of the tokens it
                                    receives (CMD59). The Signaloid compute
                                    modules acknowledge CMD59 but then reject
                                    block reads, so leave this off for them.
                                    A card that does not support CMD59 at all
                                    is left in its default non-protected
                                    mode, in which the CRC it sends is not
                                    guaranteed to be meaningful.
        """
        self.timeout = timeout
        self.dummy_bytes_count = dummy_bytes_count
        self.block_size = block_size
        self.identification_baudrate = identification_baudrate
        self.crc_check = crc_check
        self.crc_on = crc_on

        self.initialization_timeout = SDOverSPI.INITIALIZATION_TIMEOUT
        self.response_timeout = SDOverSPI.RESPONSE_TIMEOUT
        self.busy_timeout = SDOverSPI.BUSY_TIMEOUT
        self.command_timeout = SDOverSPI.COMMAND_TIMEOUT
        self.bus_timeout = SDOverSPI.BUS_TIMEOUT

        self.spi = spi

        # Initialize the chip select pin as an output and set it to high
        self.cs = digitalio.DigitalInOut(cs_pin)
        self.cs.direction = digitalio.Direction.OUTPUT
        self.cs.value = True

        # A single scratch buffer backs every read and every run of idle
        # bytes, so that polling the card allocates nothing.
        self._scratch = bytearray(SDOverSPI.SCRATCH_SIZE)
        self._read_byte_buffer = bytearray(1)

        # Whether the bus can supply the idle byte itself, which is resolved
        # on the first transfer.
        self._write_value = True

        self.init_cmd_tables()

        self.init()

    # CRC ---------------------------------------------------------------------
    @staticmethod
    def CRC7(data_arr: "ByteSource") -> int:
        """
        Calculates the CRC7 of a byte array.

        :param data_arr: The byte array to calculate the CRC7 of.

        :return: The CRC7 of the byte array.
        """

        crc = 0
        for data in data_arr:
            crc ^= data
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x12) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF

        return crc >> 1

    @classmethod
    def crc16_table(cls) -> "array[int]":
        """
        Returns the CRC16 lookup table, building it on first use.

        :return: The remainder of each of the 16 possible leading nibbles.
        """

        table = cls._CRC16_TABLE
        if table is None:
            values = []
            for value in range(16):
                crc = value << 12
                for _ in range(4):
                    if crc & 0x8000:
                        crc = ((crc << 1) ^ 0x1021) & 0xFFFF
                    else:
                        crc = (crc << 1) & 0xFFFF
                values.append(crc)
            table = array("H", values)
            cls._CRC16_TABLE = table

        return table

    @staticmethod
    def CRC16(data_arr: "ByteSource") -> bytearray:
        """
        Calculates the CRC16 of a byte array.

        :param data_arr: The byte array to calculate the CRC16 of.

        :return: The CRC16 of the byte array, most significant byte first.
        """

        table = SDOverSPI.crc16_table()

        crc = 0
        for data in data_arr:
            crc = ((crc << 4) & 0xFFFF) ^ table[((crc >> 12) ^ (data >> 4)) & 0x0F]
            crc = ((crc << 4) & 0xFFFF) ^ table[((crc >> 12) ^ (data & 0x0F)) & 0x0F]

        return bytearray([crc >> 8, crc & 0xFF])

    @staticmethod
    def test_crc() -> None:
        """
        Tests the CRC7 and CRC16 functions.
        """

        crc7 = SDOverSPI.CRC7([0x40, 0x00, 0x00, 0x00, 0x00])
        correct_crc7 = 0x4A
        assert crc7 == correct_crc7, "CRC7 test failed."

        crc16 = SDOverSPI.CRC16([0xFF] * 512)
        correct_crc16 = bytearray([0x7F, 0xA1])
        assert crc16 == correct_crc16, "CRC16 test failed."

    # Formatting and framing --------------------------------------------------
    @staticmethod
    def raw_data_to_hex_str(data: 'Optional[ByteSource]') -> str:
        """
        Converts a byte array to a hex string representation.

        :param data: The byte array to convert.

        :return: The hex string representation of the byte array.
        """

        if data is None:
            return "[]"

        return "[" + ":".join("{:02x}".format(x) for x in data) + "]"

    @staticmethod
    def generate_cmd(
        cmd_index: int, arguments: "ByteSource"
    ) -> bytearray:
        """
        Generates a command byte array for a SD card command ready to be sent
        through the SPI bus.

        :param cmd_index: The command index, 0 to 63.
        :param arguments: The four command argument bytes, most significant
                            byte first.

        :return: The command byte array.
        """

        if not 0 <= cmd_index <= 63:
            raise ValueError("cmd_index must be in the range 0 to 63")
        if len(arguments) != 4:
            raise ValueError("a command takes exactly four argument bytes")

        start_bit = 0b0
        transmission_bit = 0b1
        end_bit = 0b1

        cmd_byte_arr = bytearray(6)

        cmd_byte_arr[0] = cmd_index
        cmd_byte_arr[0] |= start_bit << 7
        cmd_byte_arr[0] |= transmission_bit << 6

        cmd_byte_arr[1] = arguments[0]
        cmd_byte_arr[2] = arguments[1]
        cmd_byte_arr[3] = arguments[2]
        cmd_byte_arr[4] = arguments[3]

        crc7 = SDOverSPI.CRC7(cmd_byte_arr[0:5])

        cmd_byte_arr[5] = crc7 << 1 | end_bit

        return cmd_byte_arr

    @staticmethod
    def describe_r1(r1: int) -> str:
        """
        Names the error bits set in an R1 response byte.

        :param r1: The R1 response byte.

        :return: A comma separated list of the error names.
        """

        names = [name for bit, name in SDOverSPI.R1_ERRORS.NAMES if r1 & bit]
        return ", ".join(names) if names else "no error"

    def check_r1(self, r1: int, context: str) -> None:
        """
        Raises if an R1 response byte reports an error.

        The in-idle-state bit reports progress rather than an error and is
        accepted.

        :param r1: The R1 response byte.
        :param context: The operation to name in the error message.

        :raises SDOverSPIError: if the card reported an error, or sent no
                                response at all.
        """

        if r1 & 0x80:
            raise SDOverSPIError("{}: no response from the card".format(
                context
            ))

        if r1 & SDOverSPI.R1_ERRORS.MASK:
            raise SDOverSPIError("{}: R1 0x{:02x} ({})".format(
                context, r1, SDOverSPI.describe_r1(r1)
            ))

    # Bus access --------------------------------------------------------------
    @staticmethod
    def _now() -> int:
        """Returns a monotonic timestamp, in nanoseconds."""
        return time.monotonic_ns()

    @staticmethod
    def _deadline(seconds: float) -> int:
        """Returns the timestamp `seconds` from now, in nanoseconds."""
        return time.monotonic_ns() + int(seconds * 1000000000)

    def read_byte(self) -> int:
        """
        Reads one byte from the SD card.

        :return: The byte read.
        """

        self.read_into(self._read_byte_buffer)
        return self._read_byte_buffer[0]

    def read_into(self, buffer: Union[bytearray, memoryview]) -> None:
        """
        Reads from the SD card into an existing buffer, filling it.

        :param buffer: The buffer to fill.
        """

        if self._write_value:
            try:
                self.spi.readinto(buffer, write_value=SDOverSPI.IDLE_BYTE)
                return
            except TypeError:
                # A bus that cannot supply the idle byte is sent one.
                self._write_value = False

        self.spi.write_readinto(
            bytes([SDOverSPI.IDLE_BYTE] * len(buffer)), buffer
        )

    def read_bytes(self, num_bytes: int) -> bytearray:
        """
        Reads a number of bytes from the SD card.

        :param num_bytes: The number of bytes to read.

        :return: The read bytes.
        """

        buffer = bytearray(num_bytes)
        self.read_into(buffer)
        return buffer

    def write_bytes(self, data: "ByteSource") -> None:
        """
        Writes a byte array to the SD card.

        :param data: The byte array to write.
        """

        self.spi.write(data)

    def send_dummy_bytes(self, num_bytes: Optional[int] = None) -> None:
        """
        Sends a number of dummy bytes to the SD card.

        :param num_bytes: The number of dummy bytes to send, overrides the
        default number of dummy bytes.
        """

        if num_bytes is None:
            num_bytes = self.dummy_bytes_count

        scratch = self._scratch
        size = len(scratch)
        while num_bytes > 0:
            chunk = num_bytes if num_bytes < size else size
            self.read_into(memoryview(scratch)[0:chunk])
            num_bytes -= chunk

    # Waiting -----------------------------------------------------------------
    def wait_busy(self, timeout: Optional[float] = None) -> None:
        """
        Waits while the SD card holds DO low to signal that it is busy.

        Returns as soon as the card reads back non-zero, which is also the
        case for a card that never signals busy at all.

        :param timeout: The number of seconds to wait, overrides the default
                        busy timeout.

        :raises SDOverSPIError: if the card is still busy when the timeout is
                                reached.
        """

        limit = self.busy_timeout if timeout is None else timeout
        deadline = SDOverSPI._deadline(limit)
        while True:
            if self.read_byte() != SDOverSPI.RESPONSES.BUSY:
                return
            if SDOverSPI._now() > deadline:
                raise SDOverSPIError(
                    "the card is still busy after {} s".format(limit)
                )

    def wait_ready(self) -> int:
        """
        Waits for the start block token that precedes a data block.

        :return: The start block token.

        :raises SDOverSPIError: if the card sends a data error token, an
                                unexpected byte, or no token at all.
        """

        deadline = SDOverSPI._deadline(self.response_timeout)
        while True:
            token = self.read_byte()

            if token == SDOverSPI.RESPONSES.READY:
                return token

            if token != SDOverSPI.IDLE_BYTE:
                # A data error token replaces the data block and has a zero
                # high nibble; a start block token has an all-ones one.
                if token & 0xF0:
                    raise SDOverSPIError(
                        "unexpected token 0x{:02x} where a data block was "
                        "expected".format(token)
                    )
                names = [
                    name
                    for bit, name in SDOverSPI.DATA_ERRORS.NAMES
                    if token & bit
                ]
                raise SDOverSPIError(
                    "data error token 0x{:02x} ({})".format(
                        token, ", ".join(names)
                    )
                )

            if SDOverSPI._now() > deadline:
                raise SDOverSPIError(
                    "the card sent no data block within {} s".format(
                        self.response_timeout
                    )
                )

    def wait_data_response(self) -> int:
        """
        Waits for the data response token that follows a written block.

        The card may hold DO high for one or more byte times before it drives
        the token.

        :return: The status field of the token.

        :raises SDOverSPIError: if the card sends no token, or rejects the
                                block.
        """

        deadline = SDOverSPI._deadline(self.response_timeout)
        status = None
        for _ in range(self.timeout):
            token = self.read_byte()
            frame = token & SDOverSPI.DATA_RESPONSE.FRAME_MASK
            if frame == SDOverSPI.DATA_RESPONSE.FRAME_VALUE:
                status = token & SDOverSPI.DATA_RESPONSE.MASK
                break
            if SDOverSPI._now() > deadline:
                break

        if status is None:
            raise SDOverSPIError("the card sent no data response token")

        if status == SDOverSPI.DATA_RESPONSE.ACCEPTED:
            return status
        if status == SDOverSPI.DATA_RESPONSE.CRC_ERROR:
            raise SDOverSPIError("SD SPI Write error: rejected CRC")
        if status == SDOverSPI.DATA_RESPONSE.WRITE_ERROR:
            raise SDOverSPIError("SD SPI Write error: rejected write")
        raise SDOverSPIError("SD SPI Write error: 0x{:02x}".format(status))

    # Responses ---------------------------------------------------------------
    def get_R1(self) -> bytearray:
        """
        Tries to read a valid R1 type response from the SD card, until the
        timeout is reached.

        The first byte of every SPI response is an R1 byte, and its most
        significant bit is always zero, which is what frames the response in
        the byte stream.

        :return: The R1 response, or `0xFF` if the card sent none.
        """

        deadline = SDOverSPI._deadline(self.response_timeout)
        result = bytearray([SDOverSPI.IDLE_BYTE])
        for _ in range(self.timeout):
            value = self.read_byte()
            if not value & 0x80:
                result[0] = value
                break
            if SDOverSPI._now() > deadline:
                break

        return result

    def get_R1b(self) -> bytearray:
        """
        Tries to read a valid R1b type response from the SD card, until the
        timeout is reached, and then waits out the busy signal that may
        follow it.

        :return: The R1 byte of the response.
        """

        result = self.get_R1()
        self.wait_busy()
        return result

    def get_R2(self) -> bytearray:
        """
        Reads a valid R2 response from the SD card.

        :return: The R2 response.
        """

        result = self.get_R1()
        result.extend(self.read_bytes(1))
        return result

    def get_R3(self) -> bytearray:
        """
        Reads a valid R3 response from the SD card.

        :return: The R3 response.
        """

        result = self.get_R1()
        result.extend(self.read_bytes(4))
        return result

    def get_R7(self) -> bytearray:
        """
        Reads a valid R7 response from the SD card.

        :return: The R7 response.
        """

        result = self.get_R1()
        result.extend(self.read_bytes(4))
        return result

    def init_cmd_tables(self) -> None:
        """
        Initializes the command response tables.

        Only the commands whose response is not a plain R1 appear; every
        other command is read as R1.
        """

        self.CMD_RESP_TABLE: Dict[int, Callable[["SDOverSPI"], bytearray]] = {
            SDOverSPI.CMDs.CMD8_SEND_IF_COND: SDOverSPI.get_R7,
            SDOverSPI.CMDs.CMD12_STOP_TRANSMISSION: SDOverSPI.get_R1b,
            SDOverSPI.CMDs.CMD13_SEND_STATUS: SDOverSPI.get_R2,
            SDOverSPI.CMDs.CMD28_SET_WRITE_PROT: SDOverSPI.get_R1b,
            SDOverSPI.CMDs.CMD29_CLR_WRITE_PROT: SDOverSPI.get_R1b,
            SDOverSPI.CMDs.CMD38_ERASE: SDOverSPI.get_R1b,
            SDOverSPI.CMDs.CMD58_READ_OCR: SDOverSPI.get_R3,
        }

        self.ACMD_RESP_TABLE: Dict[
            int, Callable[["SDOverSPI"], bytearray]
        ] = {
            SDOverSPI.CMDs.ACMD13_SD_STATUS: SDOverSPI.get_R2,
        }

    # Commands ----------------------------------------------------------------
    def send_single_cmd(
        self,
        cmd_index: int,
        arguments: 'Optional[ByteSource]' = None,
    ) -> bytearray:
        """
        Sends a single CMD to the SD card, and returns the corresponding
        response.

        :param cmd_index: The command index.
        :param arguments: The command arguments.

        :return: The response received.
        """
        if arguments is None:
            arguments = bytes(4)

        cmd = SDOverSPI.generate_cmd(cmd_index, arguments)
        self.spi.write(cmd)

        # The card's response is at least one byte away (N_CR), so whatever
        # is in that byte is not part of it and is stepped over. This is also
        # what lets a read stop command step over the data the card is still
        # transmitting.
        self.read_byte()

        response_func = self.CMD_RESP_TABLE.get(cmd_index, SDOverSPI.get_R1)

        return response_func(self)

    def send_single_acmd(
        self,
        cmd_index: int,
        arguments: 'Optional[ByteSource]' = None,
    ) -> bytearray:
        """
        Sends a single ACMD to the SD card, and returns the corresponding
        response.

        :param cmd_index: The command index.
        :param arguments: The command arguments.

        :return: The response received.
        """
        if arguments is None:
            arguments = bytes(4)

        self.send_cmd(
            cmd_index=SDOverSPI.CMDs.CMD55_APP_CMD,
            loop_until_expected_response=[
                SDOverSPI.RESPONSES.R1_IN_IDLE_STATE,
                SDOverSPI.RESPONSES.R1_INITIALIZED,
            ],
        )

        self.send_dummy_bytes()

        cmd = SDOverSPI.generate_cmd(cmd_index, arguments)
        self.spi.write(cmd)

        self.read_byte()

        response_func = self.ACMD_RESP_TABLE.get(cmd_index, SDOverSPI.get_R1)

        return response_func(self)

    def send_cmd(
        self,
        cmd_index: int,
        arguments: 'Optional[ByteSource]' = None,
        loop_until_expected_response: Optional[List[int]] = None,
        timeout: Optional[int] = None,
    ) -> bytearray:
        """
        Sends a CMD to the SD card, and waits for the corresponding response.

        If loop_until_expected_response is provided, it waits for one of the
        expected responses, until the timeout is reached.
        If no loop_until_expected_response is provided, it only sends a single
        CMD and waits for a single response.

        If a timeout is provided, it replaces the default number of retries.

        :param cmd_index: The command index.
        :param arguments: The command arguments.
        :param loop_until_expected_response: The expected responses.
        :param timeout: The number of retries.

        :return: The response received.
        """
        if arguments is None:
            arguments = bytes(4)

        if timeout is None:
            timeout = self.timeout

        if loop_until_expected_response is None:
            timeout = 1
            loop_until_expected_response = []

        deadline = SDOverSPI._deadline(self.command_timeout)
        res = bytearray([SDOverSPI.IDLE_BYTE])
        for _ in range(timeout):
            res = self.send_single_cmd(cmd_index, arguments)

            if res[0] in loop_until_expected_response:
                break

            self.send_dummy_bytes()

            if SDOverSPI._now() > deadline:
                break

        return res

    def send_acmd(
        self,
        cmd_index: int,
        arguments: 'Optional[ByteSource]' = None,
        loop_until_expected_response: Optional[List[int]] = None,
        timeout: Optional[int] = None,
    ) -> bytearray:
        """
        Sends an ACMD to the SD card, and waits for the corresponding response.

        If loop_until_expected_response is provided, it waits for one of the
        expected responses, until the timeout is reached.
        If no loop_until_expected_response is provided, it only sends a single
        ACMD and waits for a single response.

        If a timeout is provided, it replaces the default number of retries.

        :param cmd_index: The command index.
        :param arguments: The command arguments.
        :param loop_until_expected_response: The expected responses.
        :param timeout: The number of retries.

        :return: The response received.
        """

        if timeout is None:
            timeout = self.timeout

        if loop_until_expected_response is None:
            timeout = 1
            loop_until_expected_response = []

        deadline = SDOverSPI._deadline(self.command_timeout)
        res = bytearray([SDOverSPI.IDLE_BYTE])
        for _ in range(timeout):
            res = self.send_single_acmd(cmd_index, arguments)

            if res[0] in loop_until_expected_response:
                break

            self.send_dummy_bytes()

            if SDOverSPI._now() > deadline:
                break

        return res

    def stop_transmission(self) -> bytearray:
        """
        Ends a multiple block read with CMD12, and waits out the busy signal
        that follows it.

        :return: The R1 byte of the response.
        """

        res = self.send_single_cmd(SDOverSPI.CMDs.CMD12_STOP_TRANSMISSION)
        self.check_r1(res[0], "stop transmission")
        return res

    # Initialization ----------------------------------------------------------
    def _configure_baudrate(self, baudrate: int) -> None:
        """Sets the bus clock rate, keeping SPI mode 0."""
        self.spi.configure(baudrate=baudrate, phase=0, polarity=0)

    def _power_up(self) -> None:
        """
        Clocks the card through its power-up sequence with chip select
        released, so that it is ready to receive its first command.
        """

        self.cs.value = True

        baudrate = int(getattr(self.spi, "frequency", 0) or 0)
        idle_bytes = SDOverSPI.POWER_UP_IDLE_BYTES
        if baudrate:
            seconds = SDOverSPI.POWER_UP_IDLE_SECONDS
            idle_bytes = max(idle_bytes, int(baudrate * seconds) // 8)

        self.send_dummy_bytes(idle_bytes)

    def _go_idle(self) -> None:
        """
        Puts the card into the idle state and into SPI mode with CMD0.

        :raises SDOverSPIError: if the card does not report the idle state.
        """

        res = self.send_cmd(
            cmd_index=SDOverSPI.CMDs.CMD0_GO_IDLE_STATE,
            loop_until_expected_response=[
                SDOverSPI.RESPONSES.R1_IN_IDLE_STATE
            ],
        )
        self.send_dummy_bytes()

        if res[0] != SDOverSPI.RESPONSES.R1_IN_IDLE_STATE:
            raise SDOverSPIError(
                "the card did not enter SPI mode: R1 0x{:02x}".format(res[0])
            )

    def _enable_crc(self) -> None:
        """
        Asks the card to check the CRC of the tokens it receives.

        A card that does not support CMD59 is left in its default
        non-protected mode.
        """

        args = (1).to_bytes(4, "big")
        self.send_cmd(cmd_index=SDOverSPI.CMDs.CMD59_CRC_ON_OFF,
                      arguments=args)
        self.send_dummy_bytes()

    def _wait_initialized(self) -> None:
        """
        Polls the card until it reports that it has finished initializing.

        ACMD41 is tried first, and CMD1 is used instead for cards that reject
        it; the two are defined to behave identically in SPI mode.

        :raises SDOverSPIError: if the card is still initializing when the
                                initialization timeout is reached.
        """

        # The host's supply voltage window. The card ignores the operand in
        # SPI mode; high capacity cards read bit 30 as the host capacity
        # support flag.
        args = (0x40000000).to_bytes(4, "big")

        deadline = SDOverSPI._deadline(self.initialization_timeout)
        use_acmd41 = True
        res = bytearray([SDOverSPI.IDLE_BYTE])

        while SDOverSPI._now() < deadline:
            if use_acmd41:
                res = self.send_single_acmd(
                    SDOverSPI.CMDs.ACMD41_SD_SEND_OP_COND, args
                )
                if res[0] & SDOverSPI.R1_ERRORS.ILLEGAL_COMMAND:
                    use_acmd41 = False
            else:
                res = self.send_single_cmd(SDOverSPI.CMDs.CMD1_SEND_OP_COND)

            self.send_dummy_bytes()

            if res[0] == SDOverSPI.RESPONSES.R1_INITIALIZED:
                return

        raise SDOverSPIError(
            "the card did not finish initializing within {} s: R1 "
            "0x{:02x}".format(self.initialization_timeout, res[0])
        )

    def _set_block_length(self) -> None:
        """
        Sets the card's data block length.

        :raises SDOverSPIError: if the card rejects the block length.
        """

        args = self.block_size.to_bytes(4, "big")
        res = self.send_cmd(
            cmd_index=SDOverSPI.CMDs.CMD16_SET_BLOCKLEN,
            arguments=args,
            loop_until_expected_response=[
                SDOverSPI.RESPONSES.R1_INITIALIZED
            ],
        )
        self.send_dummy_bytes()

        self.check_r1(res[0], "set block length")

    def init(self) -> None:
        """
        Initializes the SD card to the SPI mode.

        Clocks the card through its power-up sequence, switches it to SPI mode
        with CMD0, waits for it to finish initializing, and sets its block
        length. Identification runs at the identification baudrate, and the
        bus is returned to its previous clock rate before this returns.

        :raises SDOverSPIError: if the card does not reach the initialized
                                state.
        """
        self._lock_bus()

        data_baudrate = int(getattr(self.spi, "frequency", 0) or 0)
        slowed = data_baudrate > self.identification_baudrate

        try:
            if slowed:
                self._configure_baudrate(self.identification_baudrate)

            self._power_up()

            self.cs.value = False

            self._go_idle()

            if self.crc_on:
                self._enable_crc()

            self._wait_initialized()

            self._set_block_length()

        finally:
            self.send_dummy_bytes()

            self.cs.value = True
            if slowed:
                self._configure_baudrate(data_baudrate)
            self.spi.unlock()

    # Block transfers ---------------------------------------------------------
    def _lock_bus(self) -> None:
        """
        Takes the SPI bus.

        :raises SDOverSPIError: if another user of the bus does not release it
                                within the bus timeout.
        """

        deadline = SDOverSPI._deadline(self.bus_timeout)
        while not self.spi.try_lock():
            if SDOverSPI._now() > deadline:
                raise SDOverSPIError(
                    "the SPI bus was not released within {} s".format(
                        self.bus_timeout
                    )
                )

    def _acquire_bus(self) -> None:
        """Takes the SPI bus and selects the card."""
        self._lock_bus()
        self.cs.value = False

    def _release_bus(self) -> None:
        """Gives the card its trailing clocks, then deselects it and releases
        the SPI bus."""
        self.send_dummy_bytes()
        self.cs.value = True
        self.spi.unlock()

    def _stop_write(self) -> None:
        """
        Ends a multiple block write with the stop transmission token, and
        waits out the busy signal that follows it.
        """

        self.send_dummy_bytes()
        self.write_bytes(
            bytes([SDOverSPI.CONTROL_TOKENS.CMD25_STOP_BLOCK_TOKEN])
        )
        self.wait_busy()

    def _abandon_read(self) -> None:
        """
        Stops a multiple block read that failed part of the way through, so
        that the card is not left streaming data into the next transaction.
        """

        try:
            self.stop_transmission()
        except Exception:
            pass

    def _abandon_write(self) -> None:
        """
        Ends a multiple block write that failed part of the way through, so
        that the card is not left waiting for another block.
        """

        try:
            self._stop_write()
        except Exception:
            pass

    def _verify_crc16(self, block: Union[bytearray, memoryview]) -> None:
        """
        Reads the CRC16 that suffixes a data block and compares it with the
        CRC16 of the block.

        :param block: The data block the card just sent.

        :raises SDOverSPIError: if the two do not match.
        """

        received = self.read_bytes(2)

        if not self.crc_check:
            return

        expected = SDOverSPI.CRC16(block)
        if received != expected:
            raise SDOverSPIError(
                "CRC16 received: 0x{:02x}{:02x}, calculated: "
                "0x{:02x}{:02x}".format(
                    received[0], received[1], expected[0], expected[1]
                )
            )

    def read_blocks(self, address: int, num_blocks: int) -> bytearray:
        """
        Reads a number of blocks from the SD card.

        :param address: The address to read from.
        :param num_blocks: The number of blocks to read.

        :return: The read bytes.

        :raises SDOverSPIError: if the card rejects the command, reports a
                                data error, or sends a block whose CRC16 does
                                not match.
        """

        if num_blocks < 1:
            raise ValueError("num_blocks must be at least one")

        block_size = self.block_size
        address_bytes = address.to_bytes(4, "big")

        multiple = num_blocks > 1
        cmd_index = (
            SDOverSPI.CMDs.CMD18_READ_MULTIPLE_BLOCK
            if multiple
            else SDOverSPI.CMDs.CMD17_READ_SINGLE_BLOCK
        )

        self._acquire_bus()

        try:
            res = self.send_cmd(
                cmd_index=cmd_index,
                arguments=address_bytes,
                loop_until_expected_response=[
                    SDOverSPI.RESPONSES.R1_INITIALIZED
                ],
            )
            self.check_r1(res[0], "read block command")

            data = bytearray(num_blocks * block_size)
            window = memoryview(data)

            for index in range(num_blocks):
                # Wait for start block token
                self.wait_ready()

                # Read the data, then check its CRC16
                start = index * block_size
                block = window[start:start + block_size]
                self.read_into(block)
                self._verify_crc16(block)

            if multiple:
                self.stop_transmission()

            return data
        except Exception:
            if multiple:
                self._abandon_read()
            raise
        finally:
            self._release_bus()

    def write_blocks(
        self, address: int, blocks: "ByteSource"
    ) -> int:
        """
        Writes a number of blocks to the SD card.

        :param address: The address to write to.
        :param blocks: The blocks to write. Its length must be a non-zero
                        multiple of the block length.

        :return: The number of blocks written.

        :raises SDOverSPIError: if the card rejects the command or any block.
        """

        block_size = self.block_size

        if not len(blocks) or len(blocks) % block_size:
            raise ValueError(
                "blocks must be a non-zero multiple of {} bytes".format(
                    block_size
                )
            )

        num_blocks = len(blocks) // block_size
        address_bytes = address.to_bytes(4, "big")

        multiple = num_blocks > 1
        if multiple:
            cmd_index = SDOverSPI.CMDs.CMD25_WRITE_MULTIPLE_BLOCK
            start_block = bytes(
                [SDOverSPI.CONTROL_TOKENS.CMD25_START_BLOCK_TOKEN]
            )
        else:
            cmd_index = SDOverSPI.CMDs.CMD24_WRITE_BLOCK
            start_block = bytes(
                [SDOverSPI.CONTROL_TOKENS.CMD24_START_BLOCK_TOKEN]
            )

        if isinstance(blocks, (bytes, bytearray, memoryview)):
            source = memoryview(blocks)
        else:
            # The SPI bus transmits from a buffer, so any other sequence of
            # integers is copied into one.
            source = memoryview(bytes(blocks))

        self._acquire_bus()

        try:
            count = 0

            res = self.send_cmd(
                cmd_index=cmd_index,
                arguments=address_bytes,
                loop_until_expected_response=[
                    SDOverSPI.RESPONSES.R1_INITIALIZED
                ],
            )
            self.check_r1(res[0], "write block command")

            for i in range(num_blocks):
                self.send_dummy_bytes()

                self.write_bytes(start_block)

                data = source[i * block_size:(i + 1) * block_size]
                self.write_bytes(data)

                crc16 = SDOverSPI.CRC16(data)
                self.write_bytes(crc16)

                self.wait_data_response()
                count += 1

                self.wait_busy()

            if multiple:
                self._stop_write()

        except Exception:
            if multiple:
                self._abandon_write()
            raise
        finally:
            self._release_bus()

        return count

    def test_rw(self, address: int, num_blocks: int = 2) -> None:
        """
        Tests the read and write operations.

        Overwrites `num_blocks` blocks at `address`, so it must only be
        pointed at storage whose contents can be discarded.

        It reads the blocks at the given address, increments the first byte of
        the first block, and writes the incremented data back. It then reads
        the blocks again, and compares the read data with the written data.

        :param address: The address to test at.
        :param num_blocks: The number of blocks to test with.
        """

        read_data = self.read_blocks(address, num_blocks)

        new_value = (read_data[0] + 1) & 0xFF
        write_data = bytes([new_value] * (self.block_size * num_blocks))
        self.write_blocks(address, write_data)

        read_data = self.read_blocks(address, num_blocks)

        assert (
            read_data == write_data
        ), "Read data does not match written data."
