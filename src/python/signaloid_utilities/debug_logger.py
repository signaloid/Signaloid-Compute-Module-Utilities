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


from __future__ import annotations

import argparse
import datetime
import io
import signal
import struct
import sys
import threading
import time
import types
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent / "src" / "python"))


from signaloid_utilities.c0microsd.interface import (
    C0microSDSignaloidSoCInterface,
)
from signaloid_utilities.c0sd.interface import (
    C0microSDPlusInterface,
    C0SDInterface,
)
from signaloid_utilities.c0sd.toolkit import _exit_for_exception


VARIANT_TO_INTERFACE_CLASS: dict[str, type] = {
    "C0-microSD": C0microSDSignaloidSoCInterface,
    "C0-microSD+": C0microSDPlusInterface,
    "C0-SD": C0SDInterface,
}

COMPUTE_MODULE_CLASSES = (
    C0microSDSignaloidSoCInterface
    | C0microSDPlusInterface
    | C0SDInterface
)


DEFAULT_PACKET_SIZE = 512


class C0Logger:
    def __init__(
        self,
        compute_module: COMPUTE_MODULE_CLASSES,
        polling_rate: float = 1,
        output_file: str | io.TextIOBase | None = sys.stdout,
        packet_size: int = DEFAULT_PACKET_SIZE,
        print_hex: bool = False,
        print_uint: bool = False,
        print_uint_stop_word: int | None = None,
        no_clear: bool = False,
        no_header: bool = False,
        no_header_styling: bool = False,
    ):
        self.compute_module = compute_module
        self.polling_rate = polling_rate
        self.output_file = output_file
        self.packet_size = packet_size
        self.print_hex = print_hex
        self.print_uint = print_uint
        self.print_uint_stop_word = print_uint_stop_word
        self.no_clear = no_clear
        self.no_header = no_header
        self.no_header_styling = no_header_styling

        if self.output_file is None or (
            isinstance(self.output_file, str) and self.output_file == ""
        ):
            self.output_file = sys.stdout

        self.keep_running: bool = True
        self._thread: threading.Thread | None = None

        if isinstance(
            self.compute_module,
            (
                C0microSDSignaloidSoCInterface,
                C0microSDPlusInterface,
                C0SDInterface,
            ),
        ):
            self.device_path = self.compute_module.target_device
        else:
            self.device_path = ""

    def write(self, text: str) -> None:
        """Writes the given text to the set file descriptor.

        :param text: The text to write.
        :type text: str
        :raises TypeError: When the file descriptor is not a file or stdout/stderr
        """
        if isinstance(self.output_file, io.TextIOBase):
            print(text, file=self.output_file)
        elif isinstance(self.output_file, str):
            with open(self.output_file, "a") as f:
                print(text, file=f)
        else:
            raise TypeError(f"output_file must be a string path or a text stream.")

    def read_debug_log_buffer_text(self) -> str:
        """Reads the debug log buffer and decodes it to text.

        :return: The decoded text.
        :rtype: str
        """
        uart_buffer: bytes = self.compute_module.read_debug_log_buffer(self.packet_size)
        return uart_buffer.decode("utf-8", errors="replace").replace("\x00", "")

    def read_debug_log_buffer_hex(self) -> str:
        """Reads the debug log buffer and formats it as a classic hex-dump.

        :return: The formatted hex-dump string.
        :rtype: str
        """
        uart_buffer: bytes = self.compute_module.read_debug_log_buffer(
            self.packet_size
        )
        return self.hex_dump(uart_buffer)

    def read_debug_log_buffer_uint(self, stop_word: int | None = None) -> str:
        """Reads the debug log buffer and formats it as a classic hex-dump.

        :return: The formatted hex-dump string.
        :rtype: str
        """
        uart_buffer: bytes = self.compute_module.read_debug_log_buffer(
            self.packet_size
        )
        text = ""
        for i in range(0, self.packet_size, 4):
            num = struct.unpack("<I", uart_buffer[i : i + 4])[0]
            if num == stop_word:
                break
            text += f"{i // 4:>4}: 0x{num:08X}\n"
        return text

    @staticmethod
    def hex_dump(data: bytes) -> str:
        """Formats the given byte array to a classic hex-dump.

        :param data: The given byte array.
        :type data: bytes
        :return: The formatted hex-dump string.
        :rtype: str
        """
        text = "\n"
        text += "╭────┬─────────────────────────────────────────────────╮\n"
        text += "│    │  0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f │\n"
        text += "├────┼─────────────────────────────────────────────────┤\n"
        for i in range(0, len(data), 16):
            chunk = data[i : i + 16]
            address_part = f"{(i // 16):02x}"
            data_part = f"{' '.join(f'{byte:02x}' for byte in chunk):<47}"
            text += f"│ {address_part} │ {data_part} │\n"
        text += "╰────┴─────────────────────────────────────────────────╯\n"

        return text

    def get_formatted_log_text(self) -> str:
        """Reads the debug log buffer and formats it based on the set styling.

        :return: The formatted log string.
        :rtype: str
        """
        text: str = ""

        if not self.no_clear and not isinstance(self.output_file, str):
            # Clear the console
            text += "\033c"

        if not self.no_header:
            header_info = f"{datetime.datetime.now()} [{self.device_path}]"
            if self.no_header_styling:
                text += f"{header_info}:\n"
            else:
                text += f"╭─{'─' * len(header_info)}─╮\n"
                text += f"│ {header_info} │\n"
                text += f"╰─{'─' * len(header_info)}─╯\n"

        if self.print_hex:
            text += self.read_debug_log_buffer_hex()
        elif self.print_uint:
            text += self.read_debug_log_buffer_uint(
                stop_word=self.print_uint_stop_word
            )
        else:
            text += self.read_debug_log_buffer_text()

        return text

    def get_log(self) -> None:
        """Reads the debug log buffer, formats it, and writes it to the target
        output file.
        """
        text = self.get_formatted_log_text()
        self.write(text)

    def start_blocking(self):
        """Continuously reads, formats, and writes the log."""
        self.keep_running: bool = True
        while self.keep_running:
            self.get_log()
            time.sleep(self.polling_rate)
        self.keep_running = True

    def start(self):
        """Starts a new thread to continuously read, format, and write the log."""
        self._thread = threading.Thread(target=self.start_blocking)
        self._thread.start()

    def stop(self):
        """Stops the running thread that continuously reads, formats, and
        writes the log.
        """
        self.keep_running = False
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def close(self) -> None:
        """Callers should use `with` or call `close()` explicitly when exiting
        to ensure threads are stopped.
        """
        self.stop()

    def __del__(self) -> None:
        """Called when deleting the class instance, to ensure threads are
        stopped.
        """
        self.close()

    def __enter__(self):
        """Executed when entering the 'with' block."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> bool | None:
        """Executed when exiting the 'with' block, even if an error occurs."""
        self.close()
        # Returning False allows any internal exceptions to propagate normally
        return False


def parse_arguments(explicit_args: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Read debug logs from Signaloid compute modules and print \
            them to the console.",
    )

    parser.add_argument(
        "device_path",
        type=str,
        help="Path of the device to read from (e.g., /dev/disk4).",
    )

    variant_choices = list(VARIANT_TO_INTERFACE_CLASS.keys())
    parser.add_argument(
        "--variant",
        type=str,
        help="C0-microSD variant.",
        default=variant_choices[0],
        choices=variant_choices,
        required=False,
    )

    parser.add_argument(
        "-r",
        "--reset-on-launch",
        dest="reset_on_launch",
        action="store_true",
        help="Reset the core on launch. Only applicable to the C0-microSD+.",
        default=False,
    )

    parser.add_argument(
        "-s",
        "--stop-on-exit",
        dest="stop_on_exit",
        action="store_true",
        help="Stop the core on exit. Only applicable to the C0-microSD+.",
        default=False,
    )

    parser.add_argument(
        "-d",
        "--hex-dump",
        dest="print_hex",
        action="store_true",
        help="Print the debug log in a hex dump format.",
        default=False,
    )

    parser.add_argument(
        "--uint-dump",
        dest="print_uint",
        action="store_true",
        help="Print the debug log as a list of 4-byte hex numbers.",
        default=False,
    )

    parser.add_argument(
        "--uint-dump-stop-word",
        dest="stop_word",
        type=lambda x: int(x, 0),
        help="Print the debug log as a list of 4-byte hex numbers.",
        default=False,
    )

    parser.add_argument(
        "--polling-rate",
        dest="polling_rate",
        type=float,
        help="Polling rate (in seconds).",
        default=0.5,
    )

    parser.add_argument(
        "--no-clear",
        dest="no_clear",
        action="store_true",
        help="Do not clear the console before printing logs.",
        default=False,
    )

    parser.add_argument(
        "--no-header",
        dest="no_header",
        action="store_true",
        help="Do not print the header with timestamp and device path.",
        default=False,
    )

    parser.add_argument(
        "--no-header-styling",
        dest="no_header_styling",
        action="store_true",
        help="Do not print the header box.",
        default=False,
    )

    parser.add_argument(
        "--one-shot",
        dest="one_shot",
        action="store_true",
        help="Print only a single shot of the debug log and exit.",
        default=False,
    )

    parser.add_argument(
        "--packet-size",
        dest="packet_size",
        type=int,
        help=f"Size of each packet to read (in bytes). [Default: {DEFAULT_PACKET_SIZE}]",
        default=DEFAULT_PACKET_SIZE,
    )

    parser.add_argument(
        "-o",
        "--output-file",
        dest="output_file",
        type=str,
        help="Redirect the log to a file.",
        default=None,
    )

    args = parser.parse_args(explicit_args)
    return args


def main(explicit_args: list[str] | None = None):
    args = parse_arguments(explicit_args)

    compute_module_class = VARIANT_TO_INTERFACE_CLASS[args.variant]
    try:
        compute_module = compute_module_class(args.device_path)
    except Exception as e:
        # Same message and sysexits code as the toolkits, instead of a traceback.
        _exit_for_exception(e)
    if args.reset_on_launch:
        if isinstance(
            compute_module,
            (
                C0microSDPlusInterface,
                C0SDInterface,
            ),
        ):
            compute_module.reset_core()

    with C0Logger(
        compute_module=compute_module,
        polling_rate=args.polling_rate,
        output_file=args.output_file,
        packet_size=args.packet_size,
        print_hex=args.print_hex,
        print_uint=args.print_uint,
        print_uint_stop_word=args.stop_word,
        no_clear=args.no_clear,
        no_header=args.no_header,
        no_header_styling=args.no_header_styling,
    ) as logger:

        def sigint_handler(signal: int, frame: types.FrameType | None):
            logger.stop()

        signal.signal(signal.SIGINT, sigint_handler)

        if args.one_shot:
            logger.get_log()
        else:
            logger.start_blocking()

    if args.stop_on_exit:
        if isinstance(compute_module, C0microSDPlusInterface):
            compute_module.apply_configure_action("core-stop")
            compute_module.apply_configure_action("sw-led-off")
            compute_module.apply_configure_action("red-led-off")
            compute_module.apply_configure_action("green-led-off")
            compute_module.apply_configure_action("blue-led-off")
        elif isinstance(compute_module, C0SDInterface):
            compute_module.apply_configure_action("core-stop")
            compute_module.apply_configure_action("sw-led-off")
            compute_module.apply_configure_action("green-led-off")
        elif isinstance(compute_module, C0SDInterface):
            compute_module.apply_configure_action("core-stop")


if __name__ == "__main__":
    main()
