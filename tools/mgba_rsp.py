"""Bounded, all-stop GDB RSP transport for an isolated local mGBA instance.

Use ``with GdbRemote() as remote:``; memory transfers use at most 512 bytes per
packet. Register methods preserve target byte order and layout without interpreting
register positions. ``resume()`` waits only for the command ACK; use ``wait_stop``
or ``interrupt`` to receive the eventual stop reply. No command is retried, even
after a NAK. A timeout or malformed frame closes the connection: a failed write
may already have changed the target and must be checked independently.

Protocol references (GDB manual):
https://sourceware.org/gdb/current/onlinedocs/gdb.html/Overview.html
https://sourceware.org/gdb/current/onlinedocs/gdb.html/Packets.html
This is an ACK-mode, single-caller client; no non-stop/no-ACK negotiation.
"""

import ipaddress
import math
import socket
import time


class RspError(OSError):
    """Remote transport or protocol failure."""


class RspProtocolError(RspError):
    """Malformed or unexpected RSP data."""


class RspTimeoutError(RspError, TimeoutError):
    """An operation exceeded its finite total time limit."""


class RspNAKError(RspError):
    """The target rejected a command; the client did not replay it."""


class RspUnsupportedError(RspError):
    """The target returned the empty unsupported-command reply."""


class RspRemoteError(RspError):
    """The target reported an E error reply."""

    def __init__(self, reply):
        self.reply = reply
        super().__init__(f"GDB remote error: {reply!r}")


class GdbRemote:
    """A loopback-only RSP connection with finite operation deadlines."""

    MAX_PACKET = 16384
    MEMORY_CHUNK = 512
    _HEX = frozenset(b"0123456789abcdefABCDEF")

    def __init__(self, host: str = "127.0.0.1", port: int = 2345, timeout: float = 5):
        self.timeout = self._finite_timeout(timeout)
        # Resolve once and connect to the checked numeric address, avoiding a
        # second DNS lookup between the loopback check and the connection.
        candidates = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not candidates or any(
            not ipaddress.ip_address(entry[4][0]).is_loopback for entry in candidates
        ):
            raise ValueError("GDB transport requires a loopback address")
        self._socket = socket.create_connection(
            (candidates[0][4][0], port), timeout=self.timeout
        )
        self._running = False
        self._closed = False

    @staticmethod
    def _finite_timeout(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("timeout must be a finite positive number")
        if not math.isfinite(value) or value <= 0:
            raise ValueError("timeout must be a finite positive number")
        return float(value)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self) -> None:
        """Close the socket without issuing detach, resume, or other mutations."""
        if not self._closed:
            self._closed = True
            self._socket.close()

    def _deadline(self, timeout=None):
        value = self.timeout if timeout is None else self._finite_timeout(timeout)
        return time.monotonic() + value

    def _remaining(self, deadline):
        if self._closed:
            raise RspError("GDB connection is closed")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self.close()
            raise RspTimeoutError("GDB operation timed out; command was not retried")
        self._socket.settimeout(remaining)

    def _send(self, data, deadline):
        self._remaining(deadline)
        try:
            self._socket.sendall(data)
        except OSError as exc:
            self.close()
            if isinstance(exc, TimeoutError):
                raise RspTimeoutError(
                    "GDB send timed out; outcome may be unknown"
                ) from exc
            raise RspError("GDB send failed; outcome may be unknown") from exc

    def _recv(self, size, deadline):
        data = bytearray()
        while len(data) < size:
            self._remaining(deadline)
            try:
                part = self._socket.recv(size - len(data))
            except OSError as exc:
                self.close()
                if isinstance(exc, TimeoutError):
                    raise RspTimeoutError(
                        "GDB receive timed out; command was not retried"
                    ) from exc
                raise RspError("GDB receive failed") from exc
            if not part:
                self.close()
                raise RspError("GDB connection closed before a complete reply")
            data.extend(part)
        return bytes(data)

    def _fail(self, message):
        self.close()
        raise RspProtocolError(message)

    def _send_command(self, payload, deadline):
        if isinstance(payload, str):
            payload = payload.encode("ascii")
        if not isinstance(payload, bytes):
            raise TypeError("RSP command must be str or bytes")
        if not payload or len(payload) > self.MAX_PACKET:
            raise ValueError("RSP command length must be between 1 and MAX_PACKET")
        encoded = bytearray()
        for value in payload:
            if value in b"#$}*":
                encoded.extend((ord("}"), value ^ 0x20))
            else:
                encoded.append(value)
        if len(encoded) > self.MAX_PACKET:
            raise ValueError("escaped RSP command exceeds MAX_PACKET")
        packet = b"$" + encoded + b"#" + f"{sum(encoded) & 255:02x}".encode("ascii")
        self._send(packet, deadline)
        ack = self._recv(1, deadline)
        if ack == b"-":
            self.close()
            raise RspNAKError("GDB rejected command checksum; command was not retried")
        if ack != b"+":
            self._fail(f"Expected GDB command ACK, received {ack!r}")

    def _packet(self, deadline):
        if self._recv(1, deadline) != b"$":
            self._fail("Expected start of GDB reply packet")
        encoded = bytearray()
        while True:
            value = self._recv(1, deadline)
            if value == b"#":
                break
            if value == b"$" or len(encoded) >= self.MAX_PACKET:
                self._fail("Malformed or oversized GDB reply")
            encoded.extend(value)
        checksum = self._recv(2, deadline)
        if any(value not in self._HEX for value in checksum):
            self._send(b"-", deadline)
            self._fail("Invalid GDB checksum digits")
        if int(checksum, 16) != (sum(encoded) & 255):
            self._send(b"-", deadline)
            self._fail("GDB response checksum mismatch")
        self._send(b"+", deadline)
        decoded = bytearray()
        index = 0
        while index < len(encoded):
            value = encoded[index]
            index += 1
            if value == ord("}"):
                if index >= len(encoded):
                    self._fail("Truncated GDB escape")
                value = encoded[index] ^ 0x20
                index += 1
            elif value == ord("*"):
                if not decoded or index >= len(encoded):
                    self._fail("Invalid GDB run-length sequence")
                count = encoded[index] - 29
                index += 1
                if not 3 <= count <= 97 or len(decoded) + count > self.MAX_PACKET:
                    self._fail("Invalid or oversized GDB run-length sequence")
                decoded.extend(bytes((decoded[-1],)) * count)
                continue
            if len(decoded) >= self.MAX_PACKET:
                self._fail("Decoded GDB packet exceeds MAX_PACKET")
            decoded.append(value)
        return bytes(decoded)

    @classmethod
    def _check_reply(cls, reply):
        if not reply:
            raise RspUnsupportedError("GDB command is unsupported (empty reply)")
        if reply.startswith(b"E.") or (
            len(reply) == 3
            and reply.startswith(b"E")
            and all(value in cls._HEX for value in reply[1:])
        ):
            raise RspRemoteError(reply)
        return reply

    def _reply(self, deadline):
        # Console-output packets may precede the actual response. Their total
        # processing time is bounded by the same operation deadline.
        while True:
            reply = self._check_reply(self._packet(deadline))
            if reply.startswith(b"O") and reply != b"OK":
                self._unhex(reply[1:])
                continue
            return reply

    def command(self, payload: str | bytes) -> bytes:
        """Send once and return decoded packet bytes, or raise an explicit error."""
        if self._running:
            raise RspError("Target is running; interrupt or wait for its stop first")
        deadline = self._deadline()
        self._send_command(payload, deadline)
        return self._reply(deadline)

    def _unhex(self, payload, expected=None):
        if len(payload) % 2 or any(value not in self._HEX for value in payload):
            self._fail("GDB reply is not a complete hexadecimal byte sequence")
        result = bytes.fromhex(payload.decode("ascii"))
        if expected is not None and len(result) != expected:
            self._fail(f"GDB memory reply has {len(result)} bytes; expected {expected}")
        return result

    @staticmethod
    def _memory_range(address, size):
        if (
            isinstance(address, bool)
            or not isinstance(address, int)
            or not 0 <= address <= 0xFFFFFFFF
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or address + size > 0x100000000
        ):
            raise ValueError("memory range must fit in the 32-bit target address space")

    def read_memory(self, address: int, size: int) -> bytes:
        """Read exactly size bytes, in chunks of at most 512 bytes."""
        self._memory_range(address, size)
        result = bytearray()
        for offset in range(0, size, self.MEMORY_CHUNK):
            count = min(self.MEMORY_CHUNK, size - offset)
            result.extend(
                self._unhex(self.command(f"m{address + offset:x},{count:x}"), count)
            )
        return bytes(result)

    def _require_ok(self, payload):
        if self.command(payload) != b"OK":
            self._fail("GDB mutation did not return OK; outcome may be unknown")

    def write_memory(self, address: int, data: bytes) -> None:
        """Issue explicit M writes once each; a later failure can leave a partial write."""
        if not isinstance(data, bytes):
            raise TypeError("memory data must be bytes")
        self._memory_range(address, len(data))
        for offset in range(0, len(data), self.MEMORY_CHUNK):
            chunk = data[offset : offset + self.MEMORY_CHUNK]
            self._require_ok(f"M{address + offset:x},{len(chunk):x}:{chunk.hex()}")

    def read_registers(self) -> bytes:
        """Return g register bytes without assuming a target register layout."""
        return self._unhex(self.command("g"))

    def write_registers(self, raw: bytes) -> None:
        """Write the caller-supplied raw register layout through G."""
        if not isinstance(raw, bytes) or not raw:
            raise ValueError("raw registers must be nonempty bytes")
        self._require_ok(b"G" + raw.hex().encode("ascii"))

    def _breakpoint(self, action, address, thumb):
        self._memory_range(address, 1)
        if not isinstance(thumb, bool):
            raise TypeError("thumb must be a bool")
        self._require_ok(f"{action}0,{address:x},{2 if thumb else 4}")

    def breakpoint(self, address: int, thumb: bool = True) -> None:
        """Insert a Z0 software breakpoint (Thumb kind 2, ARM kind 4)."""
        self._breakpoint("Z", address, thumb)

    def remove_breakpoint(self, address: int, thumb: bool = True) -> None:
        """Remove a z0 breakpoint with the same address and instruction kind."""
        self._breakpoint("z", address, thumb)

    def resume(self) -> None:
        """Send c once and wait for ACK only; a stop reply remains unread."""
        if self._running:
            raise RspError("Target is already running")
        self._send_command(b"c", self._deadline())
        self._running = True

    def _stop(self, deadline):
        reply = self._reply(deadline)
        if (
            len(reply) < 3
            or reply[:1] not in (b"S", b"T", b"W", b"X")
            or any(value not in self._HEX for value in reply[1:3])
            or (reply.startswith(b"S") and len(reply) != 3)
        ):
            self._fail(f"Expected GDB stop/exit reply, received {reply!r}")
        self._running = False
        return reply

    def interrupt(self) -> bytes:
        """Send raw Ctrl-C and wait for a stop reply within the default timeout."""
        deadline = self._deadline()
        self._send(b"\x03", deadline)
        return self._stop(deadline)

    def wait_stop(self, timeout: float | None = None) -> bytes:
        """Wait for a stop/exit packet; None uses the finite connection timeout."""
        return self._stop(self._deadline(timeout))
