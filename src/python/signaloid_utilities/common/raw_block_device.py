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

"""Cache-bypassing access to Signaloid C0 device nodes.

The C0 compute module device family exposes a register/MMIO interface layered
on top of the standard block-device protocol. Every read must reflect the
device's live state and every write must reach the hardware promptly, so the
operating system's buffer/page cache must never sit between the application and
the device and serve stale data.

This module delivers that guarantee behind a single ``read``/``write`` API. In
most cases you only need :class:`UnifiedBlockDevice`, which inspects the given
path and automatically selects the right strategy for the current platform and
node type. The concrete device classes are also exported for the rare case
where you already know exactly which one you need.

All device classes are safe to share between threads of a single process: each
instance serialises its own reads and writes. This does not coordinate access
between separate processes opening the same device -- that remains the caller's
responsibility.
"""


import fcntl
import mmap
import os
import stat
import struct
import sys
import threading
import types
from abc import ABC, abstractmethod
from contextlib import contextmanager


DEFAULT_BLOCK_SIZE = 512

# macOS (Darwin) disk-driver ioctls, from <sys/disk.h>. Encoded with
# _IOR('d', n, type) = IOC_OUT | sizeof(type) << 16 | 'd' << 8 | n.
_DKIOCGETBLOCKSIZE = 0x40046418   # _IOR('d', 24, uint32_t): logical block size
_DKIOCGETBLOCKCOUNT = 0x40086419  # _IOR('d', 25, uint64_t): number of blocks


@contextmanager
def _handle_device_errors(path: str):
    """Re-raise low-level OS errors with messages aimed at end users.

    Wraps a block of device access so the common failure modes surface with
    actionable text instead of a bare ``errno``.

    :param path: Device path to name in the raised messages.
    """
    try:
        yield
    except PermissionError:
        raise PermissionError(
            "Permission denied: You do not have the necessary "
            f"permissions to access {path}. Try running this "
            "application with root privileges."
        ) from None
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Device not found: The device {path} does not exist."
        ) from None
    except OSError as error:
        raise OSError(
            f"I/O error while accessing {path}: {error.strerror or error}."
        ) from error


def _darwin_capacity_bytes(fd: int) -> int:
    """Return the capacity in bytes of the macOS disk node open on ``fd``.

    Queries the disk driver directly, because on macOS ``lseek(SEEK_END)``
    and ``fstat().st_size`` both report 0 for device nodes. Works on both the
    buffered (``/dev/diskN``) and raw (``/dev/rdiskN``) nodes.

    :raises OSError: if either ioctl is refused (for example, ``fd`` is not a
        disk node).
    """
    block_size = struct.unpack(
        "I", fcntl.ioctl(fd, _DKIOCGETBLOCKSIZE, bytes(4))
    )[0]
    block_count = struct.unpack(
        "Q", fcntl.ioctl(fd, _DKIOCGETBLOCKCOUNT, bytes(8))
    )[0]
    return block_size * block_count


class BlockDevice(ABC):
    """Abstract base for cache-bypassing access to a C0 device node.

    Subclasses implement a platform-specific I/O strategy; this base provides
    the shared, thread-safe ``read``/``write`` API and the context-manager and
    cleanup behaviour. Each instance holds a lock that serialises its own reads
    and writes, so a single instance can be shared safely across threads (for
    example, an application thread alongside a background debug logger).

    Most callers should use :class:`UnifiedBlockDevice` rather than
    instantiating a subclass directly.
    """

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        # Serialises this instance's reads and writes so concurrent threads
        # cannot interleave a single operation (and, for the unbuffered
        # devices, cannot interleave the read-modify-write a partial write
        # performs). Intra-process only; see the module docstring.
        self._lock = threading.Lock()

    @abstractmethod
    def _read(self, offset: int, length: int) -> bytes:
        """Strategy hook: return ``length`` bytes starting at ``offset``."""

    @abstractmethod
    def _write(self, offset: int, data: bytes) -> int:
        """Strategy hook: write ``data`` at ``offset``; return bytes written."""

    @abstractmethod
    def close(self) -> None:
        """Release any resources (such as an open file descriptor)."""

    def read(self, offset: int, length: int) -> bytes:
        """Read and return ``length`` bytes starting at ``offset``.

        ``offset`` and ``length`` may use any alignment; any block alignment
        the underlying device requires is handled transparently.

        :raises FileNotFoundError: if the device node does not exist.
        :raises PermissionError: if access to the device is denied.
        :raises OSError: on any other device-level I/O failure.
        """
        with self._lock, _handle_device_errors(self.path):
            return self._read(offset=offset, length=length)

    def write(self, offset: int, data: bytes) -> int:
        """Write ``data`` at ``offset``; return the number of bytes written.

        ``offset`` and the length of ``data`` may use any alignment. Bytes
        adjacent to the written range within the same block are preserved.

        :raises FileNotFoundError: if the device node does not exist.
        :raises PermissionError: if access to the device is denied.
        :raises OSError: on any other device-level I/O failure.
        """
        with self._lock, _handle_device_errors(self.path):
            return self._write(offset=offset, data=data)

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> bool:
        self.close()
        # Returning False allows any internal exceptions to propagate normally
        return False

    def __del__(self):
        self.close()


class BufferedBlockDevice(BlockDevice):
    """Access strategy for a macOS buffered/block node (``/dev/diskN``).

    This node is served from the buffer cache, which macOS flushes and
    invalidates when the node is closed. Each operation therefore opens the
    node, performs the I/O, and closes it again, so reads always observe fresh
    data and writes are committed promptly. Offsets and lengths of any
    alignment are supported.
    """
    def _read(self, offset: int, length: int) -> bytes:
        with open(self.path, "rb") as device:
            device.seek(offset)
            return device.read(length)

    def _write(self, offset: int, data: bytes) -> int:
        with open(self.path, "r+b") as device:
            device.seek(offset)
            count = device.write(data)
            device.flush()
            return count

    def close(self) -> None:
        pass


class UnbufferedBlockDevice(BlockDevice):
    """Base for uncached nodes accessed with block-aligned I/O.

    These nodes keep a single file descriptor open for the lifetime of the
    instance and require every transfer to be aligned to the device's block
    size. This base handles the alignment bookkeeping -- rounding requests out
    to whole blocks and, for partial writes, reading the touched blocks,
    patching the requested bytes, and writing them back -- so callers may use
    arbitrary offsets and lengths. Subclasses supply the platform-specific
    block-aligned read and write primitives.
    """

    def __init__(self, path: str, block_size: int = DEFAULT_BLOCK_SIZE) -> None:
        super().__init__(path)
        self.bs = block_size
        self.fd: int | None = None
        with _handle_device_errors(self.path):
            self.fd = self.open_fd()

    def open_fd(self) -> int:
        """Open the device node and return its file descriptor."""
        return os.open(self.path, os.O_RDWR)

    def _span(self, offset: int, length: int):
        """Return the block-aligned ``[start, end)`` byte range covering a request."""
        start = (offset // self.bs) * self.bs                       # round down
        end = -(-(offset + length) // self.bs) * self.bs            # round up
        return start, end

    @abstractmethod
    def _aligned_read(self, start: int, count: int) -> bytes:
        """Read ``count`` block-aligned bytes at block-aligned ``start``."""

    @abstractmethod
    def _aligned_write(self, start: int, data: bytes) -> int:
        """Write block-aligned ``data`` at block-aligned ``start``; return bytes written."""

    def _read(self, offset: int, length: int) -> bytes:
        start, end = self._span(offset, length)
        block = self._aligned_read(start, end - start)
        return block[offset - start : offset - start + length]

    def _write(self, offset: int, data: bytes) -> int:
        start, end = self._span(offset, len(data))
        buf = bytearray(self._aligned_read(start, end - start))  # read touched blocks
        buf[offset - start : offset - start + len(data)] = data  # patch in place
        return self._aligned_write(start, buf)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class LinuxUnbufferedBlockDevice(UnbufferedBlockDevice):
    """Access strategy for a Linux block node (``/dev/sdX``).

    The node is opened with ``O_DIRECT`` to bypass the page cache, and its
    logical block size is queried from the kernel. ``O_DIRECT`` requires the
    offset, length, and memory buffer of every transfer to be block-aligned, so
    I/O is performed through a page-aligned buffer. This is the recommended way
    to reach a C0 device on Linux.
    """
    # Linux-only constants; harmless no-ops elsewhere (only used on Linux block nodes).
    _O_DIRECT = getattr(os, "O_DIRECT", 0)  # open flag that bypasses the page cache
    _BLKSSZGET = 0x1268  # ioctl: report a block device's logical block (sector) size

    def __init__(self, path: str, block_size: int = DEFAULT_BLOCK_SIZE) -> None:
        super().__init__(path, block_size)

        if self.fd is None:
            raise RuntimeError("Could not open device.")

        self.bs = self._logical_block_size(self.fd) or block_size

    def open_fd(self) -> int:
        return os.open(self.path, os.O_RDWR | self._O_DIRECT)

    def _logical_block_size(self, fd: int) -> int | None:
        """Return the block device's logical block size in bytes, or None."""
        try:
            buf = bytearray(4)
            fcntl.ioctl(fd, self._BLKSSZGET, buf)
            return struct.unpack("i", buf)[0] or None
        except OSError:
            return None

    def _aligned_read(self, start: int, count: int) -> bytes:
        """Read ``count`` block-aligned bytes at block-aligned ``start``."""
        if self.fd is None:
            raise RuntimeError("File descriptor got closed unexpectedly.")

        buf = mmap.mmap(-1, count)                              # page-aligned
        try:
            # os.preadv lowers to the preadv2 syscall on modern CPython,
            # which snap's seccomp policy does not allow (returns EPERM).
            # lseek + readv use the allow-listed readv syscall and still
            # scatter into the page-aligned buffer O_DIRECT requires.
            os.lseek(self.fd, start, os.SEEK_SET)
            read = os.readv(self.fd, [buf])
            # Guard against a short read: the buffer's untouched tail is zero,
            # so returning it unchecked would silently pad the data.
            if read != count:
                raise OSError(
                    f"Incomplete read at offset {start}: requested "
                    f"{count} bytes, received {read}."
                )
            return bytes(buf)
        finally:
            buf.close()

    def _aligned_write(self, start: int, data: bytes) -> int:
        """Write block-aligned ``data`` at block-aligned ``start``; return bytes written."""
        if self.fd is None:
            raise RuntimeError("File descriptor got closed unexpectedly.")

        buf = mmap.mmap(-1, len(data))                          # page-aligned
        try:
            buf.write(data)
            # os.pwritev lowers to pwritev2, which snap's seccomp blocks;
            # lseek + writev use the allow-listed writev syscall instead.
            os.lseek(self.fd, start, os.SEEK_SET)
            written = os.writev(self.fd, [buf])
            # Guard against a short write so callers are not told that bytes
            # reached the device when only some of them did.
            if written != len(data):
                raise OSError(
                    f"Incomplete write at offset {start}: requested "
                    f"{len(data)} bytes, wrote {written}."
                )
            return written
        finally:
            buf.close()


class MacOSUnbufferedBlockDevice(UnbufferedBlockDevice):
    """Access strategy for a macOS raw/character node (``/dev/rdiskN``).

    The raw node is inherently uncached, so a single descriptor is kept open
    and accessed with block-aligned, positioned ``pread``/``pwrite`` calls.
    This is the recommended way to reach a C0 device on macOS.
    """

    def _aligned_read(self, start: int, count: int) -> bytes:
        """Read ``count`` block-aligned bytes at block-aligned ``start``."""
        if self.fd is None:
            raise RuntimeError("File descriptor got closed unexpectedly.")

        return os.pread(self.fd, count, start)

    def _aligned_write(self, start: int, data: bytes) -> int:
        """Write block-aligned ``data`` at block-aligned ``start``; return bytes written."""
        if self.fd is None:
            raise RuntimeError("File descriptor got closed unexpectedly.")

        return os.pwrite(self.fd, bytes(data), start)


class UnifiedBlockDevice:
    """Cache-bypassing access to a C0 device node, with automatic strategy
    selection.

    This is the recommended entry point. Given a device path, it detects the
    platform and node type and delegates ``read``/``write`` (and ``close``) to
    the appropriate strategy:

      * **Linux block node** (``/dev/sdX``) -> :class:`LinuxUnbufferedBlockDevice`
      * **macOS raw/character node** (``/dev/rdiskN``) -> :class:`MacOSUnbufferedBlockDevice`
      * **macOS buffered/block node** (``/dev/diskN``) -> :class:`BufferedBlockDevice`

    The node type is detected from the path, so the same code works whether it
    is given a real device node or one of the autoconnect daemon's symlinks.
    Use it as a context manager, or call :meth:`close` when finished.

    :param path: Path to the device node (e.g. ``/dev/rdisk4`` or ``/dev/sdb``).
    :param block_size: Fallback block size in bytes; ignored where the node's
        actual block size can be queried from the operating system.
    """

    def __init__(self, path: str, block_size: int = DEFAULT_BLOCK_SIZE):
        self.path = path
        self.bs: int = block_size

        self.device: BlockDevice | None = None

        with _handle_device_errors(self.path):
            is_block = stat.S_ISBLK(os.stat(path).st_mode)

        if sys.platform.startswith("linux") and is_block:
            self.device = LinuxUnbufferedBlockDevice(path, block_size)
        elif is_block:
            self.device = BufferedBlockDevice(path)
        else:
            self.device = MacOSUnbufferedBlockDevice(path, block_size)

        self.read = self.device.read
        self.write = self.device.write

    def capacity_bytes(self) -> int:
        """Size of the underlying block device in bytes.

        Opens its own short-lived descriptor rather than seeking the device's,
        so it cannot disturb an in-flight transfer.

        On Linux the block layer reports the capacity as the node's size, so
        seeking to the end is enough. macOS does not: ``lseek(SEEK_END)`` on a
        ``/dev/diskN`` or ``/dev/rdiskN`` node succeeds and returns 0, so the
        capacity is instead taken from the disk driver as block count times
        block size.

        :raises OSError: if the operating system reports no usable capacity.
        """
        with _handle_device_errors(self.path):
            fd = os.open(self.path, os.O_RDONLY)
            try:
                if sys.platform == "darwin":
                    size = _darwin_capacity_bytes(fd)
                else:
                    size = os.lseek(fd, 0, os.SEEK_END)
            finally:
                os.close(fd)

            # A C0 device is never zero-sized.
            if size <= 0:
                raise OSError(
                    f"Could not determine the capacity of {self.path} "
                    f"(the operating system reported {size} bytes)."
                )
            return size

    def close(self):
        if self.device is not None:
            self.device.close()

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> bool:
        self.close()
        # Returning False allows any internal exceptions to propagate normally
        return False

    def __del__(self):
        self.close()
