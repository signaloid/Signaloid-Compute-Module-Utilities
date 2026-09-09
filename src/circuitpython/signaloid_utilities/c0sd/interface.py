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

"""CircuitPython ports of the C0-microSD+ and C0-SD device interfaces.

``sd_interface`` (the interface logic) and ``regmaps`` (the register map
definitions) are shared verbatim with the host library. This module supplies
the SD-over-SPI transport, and the choice of register map.
"""

import sys

if sys.implementation.name != "circuitpython":
    from typing import Any, Optional


from busio import SPI
from microcontroller import Pin

from signaloid_utilities.c0sd.sd_interface import (
    C0microSDPlusInterface,
    C0SDInterface,
)
from signaloid_utilities.common.sd_over_spi_protocol import SDOverSPI
from signaloid_utilities.common.sd_over_spi_mixin import (
    SDOverSPITransportMixin,
)

from signaloid_utilities.c0sd.regmaps.c0microsdplus import (
    Top as C0microSDPlusRegmap,
)
from signaloid_utilities.c0sd.regmaps.c0sd import Top as C0SDRegmap


class C0microSDPlusInterfaceSDSPI(
    SDOverSPITransportMixin,
    C0microSDPlusInterface,
):
    """Communication interface for the C0-microSD+ over SPI.

    This class provides basic functionality for interfacing with the
    Signaloid C0-microSD+ through the SD SPI interface.
    """

    DEFAULT_REGMAP = C0microSDPlusRegmap

    def __init__(
        self,
        spi: SPI,
        cs_pin: Pin,
        timeout: int,
        regmap: Optional[Any] = None,
    ) -> None:
        """Initializes the C0-microSD+ interface.

        :param spi:                 The SPI bus the compute module is on.
        :param cs_pin:              The chip-select pin of the compute module.
        :param timeout:             SD transaction timeout, in retries.
        :param regmap:              Register map namespace of the FPGA design
                                    the board is running. Defaults to
                                    `DEFAULT_REGMAP`.
        """
        self.sd: SDOverSPI = SDOverSPI(
            spi=spi,
            cs_pin=cs_pin,
            timeout=timeout,
        )

        super().__init__(
            target_device="",
            regmap=self.DEFAULT_REGMAP if regmap is None else regmap,
        )


class C0SDInterfaceSDSPI(
    SDOverSPITransportMixin,
    C0SDInterface,
):
    """Communication interface for the C0-SD over SPI.

    This class provides basic functionality for interfacing with the
    Signaloid C0-SD through the SD SPI interface.
    """

    DEFAULT_REGMAP = C0SDRegmap

    def __init__(
        self,
        spi: SPI,
        cs_pin: Pin,
        timeout: int,
        regmap: Optional[Any] = None,
    ) -> None:
        """Initializes the C0-SD interface.

        :param spi:                 The SPI bus the compute module is on.
        :param cs_pin:              The chip-select pin of the compute module.
        :param timeout:             SD transaction timeout, in retries.
        :param regmap:              Register map namespace of the FPGA design
                                    the board is running. Defaults to
                                    `DEFAULT_REGMAP`.
        """
        self.sd: SDOverSPI = SDOverSPI(
            spi=spi,
            cs_pin=cs_pin,
            timeout=timeout,
        )

        super().__init__(
            target_device="",
            regmap=self.DEFAULT_REGMAP if regmap is None else regmap,
        )
