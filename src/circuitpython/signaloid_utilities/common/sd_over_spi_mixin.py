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


class SDOverSPITransportMixin:
    """SD-over-SPI transport for the C0-SD device family.

    Carries register I/O over SD block transfers. Must be listed before the
    shared interface class in the bases.
    """

    def _open_device(self, target_device: str) -> None:
        """Return no block-device handle; the transport is SD-over-SPI."""
        return None

    def _read(self, offset: int, size: int) -> bytes:
        """Reads data from the compute module.

        :param offset: The byte offset to read from.
        :param size: The number of bytes to read.
        :return: The read buffer
        """
        if size % 512 == 0:
            num_blocks = size // 512
        else:
            num_blocks = size // 512 + 1
        data = self.sd.read_blocks(offset, num_blocks)

        # Slicing copies, so the buffer is returned as it is when the read
        # already covers exactly the requested size.
        return data if len(data) == size else data[0:size]

    def _write(
        self,
        offset: int,
        data: list[int] | bytes | bytearray,
    ) -> int:
        """Writes data to the compute module.

        :param offset: The byte offset to write to.
        :param data: The data buffer to write.
        :return: Number of bytes written.
        """
        # Pad the data to 512 bytes
        if len(data) % 512:
            remaining_bytes = 512 - (len(data) % 512)
            data += bytes(remaining_bytes)

        return self.sd.write_blocks(offset, data)
