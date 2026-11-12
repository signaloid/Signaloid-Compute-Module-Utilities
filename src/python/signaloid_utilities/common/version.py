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

"""The version of this signaloid-utilities copy, for the tool banners.

An installed package reports its metadata version. A source checkout, such as
the Utilities submodule of an app that runs the toolkits straight from the
repo, has no metadata of its own, so it reports ``_version.__version__``,
which ``updatePublic.sh`` sets on every public release. Editable installs
(``pip install -e``, ``poetry install``) count as a checkout here.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

from signaloid_utilities._version import __version__

_PACKAGE_DIR = Path(__file__).resolve().parent.parent


def package_version() -> str:
    """Return the version of the signaloid_utilities package this file is in."""
    try:
        distribution = metadata.distribution("signaloid-utilities")
        installed = Path(str(distribution.locate_file("signaloid_utilities")))
        # A different copy may be installed; only trust metadata that is ours.
        if installed.resolve() == _PACKAGE_DIR:
            return distribution.version
    except metadata.PackageNotFoundError:
        pass
    return __version__
