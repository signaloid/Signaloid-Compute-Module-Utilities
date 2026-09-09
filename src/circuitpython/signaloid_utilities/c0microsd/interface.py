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

"""CircuitPython port of the C0-microSD device interface.

This module supplies the SD-over-SPI transport.
"""

from busio import SPI
from microcontroller import Pin

from signaloid_utilities.c0microsd.sd_interface import (
    C0microSDSignaloidSoCInterface,
)
from signaloid_utilities.common.sd_over_spi_protocol import SDOverSPI
from signaloid_utilities.common.sd_over_spi_mixin import (
    SDOverSPITransportMixin,
)


class C0microSDSignaloidSoCInterfaceSDSPI(
    SDOverSPITransportMixin,
    C0microSDSignaloidSoCInterface,
):
    """Communication interface for C0-microSD over SPI.

    This class provides basic functionality for interfacing with the
    Signaloid C0-microSD through the SD SPI interface.
    """

    def __init__(
        self,
        spi: SPI,
        cs_pin: Pin,
        timeout: int,
        force_transactions: bool = False,
    ) -> None:
        self.sd: SDOverSPI = SDOverSPI(spi=spi, cs_pin=cs_pin, timeout=timeout)

        super().__init__(
            target_device="",
            force_transactions=force_transactions,
        )
