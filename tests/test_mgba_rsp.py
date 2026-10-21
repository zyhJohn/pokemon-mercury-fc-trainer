"""Fake loopback peers exercise RSP framing without ROMs or a real emulator."""

import socket
import threading
import time
import unittest

from tools.mgba_rsp import (
    GdbRemote,
    RspError,
    RspNAKError,
    RspProtocolError,
    RspRemoteError,
    RspTimeoutError,
    RspUnsupportedError,
)


def frame(payload):
    return b"$" + payload + b"#" + f"{sum(payload) & 255:02x}".encode("ascii")


def receive(sock, count):
    data = b""
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise AssertionError("client disconnected unexpectedly")
        data += part
    return data


def request(sock):
    if receive(sock, 1) != b"$":
        raise AssertionError("missing packet start")
    payload = b""
    while True:
        value = receive(sock, 1)
        if value == b"#":
            break
        payload += value
    checksum = receive(sock, 2)
    if int(checksum, 16) != sum(payload) & 255:
        raise AssertionError("incorrect client checksum")
    return payload


def answer(sock, payload):
    sock.sendall(b"+" + frame(payload))
    if receive(sock, 1) != b"+":
        raise AssertionError("client did not ACK the reply")


class Peer:
    def __init__(self, handler):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(3)
        self.port = self.listener.getsockname()[1]
        self.errors = []
        self.done = threading.Event()

        def run():
            try:
                with self.listener.accept()[0] as connection:
                    connection.settimeout(3)
                    handler(connection)
            except Exception as exc:  # noqa: BLE001 - propagate peer failures to the test thread
                self.errors.append(exc)
            finally:
                self.done.set()

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def finish(self):
        self.thread.join(4)
        self.listener.close()
        if self.thread.is_alive():
            raise AssertionError("fake peer failed to finish")
        if self.errors:
            raise self.errors[0]


class GdbRemoteTests(unittest.TestCase):
    def connect(self, handler, timeout=0.5):
        peer = Peer(handler)
        self.addCleanup(peer.finish)
        client = GdbRemote(port=peer.port, timeout=timeout)
        self.addCleanup(client.close)
        return client

    def test_fragmented_response_and_checksums(self):
        def handler(sock):
            self.assertEqual(request(sock), b"m2000000,4")
            for byte in b"+" + frame(b"deadBEEF"):
                sock.sendall(bytes((byte,)))
            self.assertEqual(receive(sock, 1), b"+")

        with self.connect(handler) as client:
            self.assertEqual(
                client.read_memory(0x02000000, 4), bytes.fromhex("deadbeef")
            )
        client.close()

    def test_read_and_write_chunk_sizes_and_addresses(self):
        data = bytes(range(256)) * 4 + b"last"

        def handler(sock):
            for offset in range(0, len(data), 512):
                part = data[offset : offset + 512]
                self.assertEqual(
                    request(sock), f"m{0x2000000 + offset:x},{len(part):x}".encode()
                )
                answer(sock, part.hex().encode())
            for offset in range(0, len(data), 512):
                part = data[offset : offset + 512]
                self.assertEqual(
                    request(sock),
                    f"M{0x2000000 + offset:x},{len(part):x}:{part.hex()}".encode(),
                )
                answer(sock, b"OK")

        client = self.connect(handler)
        self.assertEqual(client.read_memory(0x2000000, len(data)), data)
        client.write_memory(0x2000000, data)

    def test_registers_and_breakpoint_commands(self):
        raw = bytes(range(80))
        commands = [
            (b"g", raw.hex().encode()),
            (b"G" + raw.hex().encode(), b"OK"),
            (b"Z0,8000100,2", b"OK"),
            (b"z0,8000100,2", b"OK"),
            (b"Z0,8000104,4", b"OK"),
            (b"z0,8000104,4", b"OK"),
        ]

        def handler(sock):
            for expected, reply in commands:
                self.assertEqual(request(sock), expected)
                answer(sock, reply)

        client = self.connect(handler)
        self.assertEqual(client.read_registers(), raw)
        client.write_registers(raw)
        client.breakpoint(0x8000100)
        client.remove_breakpoint(0x8000100)
        client.breakpoint(0x8000104, thumb=False)
        client.remove_breakpoint(0x8000104, thumb=False)

    def test_nak_never_replays_mutation(self):
        def handler(sock):
            self.assertEqual(request(sock), b"M2000000,1:01")
            sock.sendall(b"-")
            self.assertEqual(sock.recv(4096), b"")

        client = self.connect(handler)
        with self.assertRaises(RspNAKError):
            client.write_memory(0x2000000, b"\x01")

    def test_bad_checksum_sends_nak_without_replaying_write(self):
        def handler(sock):
            self.assertEqual(request(sock), b"M2000000,1:01")
            sock.sendall(b"+$OK#00")
            self.assertEqual(receive(sock, 1), b"-")
            self.assertEqual(sock.recv(4096), b"")

        client = self.connect(handler)
        with self.assertRaisesRegex(RspProtocolError, "checksum mismatch"):
            client.write_memory(0x2000000, b"\x01")

    def test_malformed_checksum_digits(self):
        def handler(sock):
            request(sock)
            sock.sendall(b"+$OK#zz")
            self.assertEqual(receive(sock, 1), b"-")

        with self.assertRaisesRegex(RspProtocolError, "checksum digits"):
            self.connect(handler).command("?")

    def test_unsupported_and_remote_error_are_distinct(self):
        def handler(sock):
            request(sock)
            answer(sock, b"")
            request(sock)
            answer(sock, b"E01")
            request(sock)
            answer(sock, b"E.problem")
            request(sock)
            answer(sock, b"EF12")

        client = self.connect(handler)
        with self.assertRaises(RspUnsupportedError):
            client.command("qUnsupported")
        for expected in (b"E01", b"E.problem"):
            with self.assertRaises(RspRemoteError) as caught:
                client.command("g")
            self.assertEqual(caught.exception.reply, expected)
        self.assertEqual(client.read_memory(0, 2), b"\xef\x12")

    def test_write_requires_exact_ok(self):
        def handler(sock):
            request(sock)
            answer(sock, b"success")

        with self.assertRaisesRegex(RspProtocolError, "did not return OK"):
            self.connect(handler).write_memory(0, b"a")

    def test_partial_write_stops_at_first_failed_chunk(self):
        def handler(sock):
            self.assertEqual(request(sock), b"M0,200:" + b"00" * 512)
            answer(sock, b"OK")
            self.assertEqual(request(sock), b"M200,200:" + b"00" * 512)
            answer(sock, b"E02")
            self.assertEqual(sock.recv(4096), b"")

        client = self.connect(handler)
        with self.assertRaises(RspRemoteError):
            client.write_memory(0, b"\0" * 1025)
        client.close()

    def test_unsupported_mutations_raise_instead_of_succeeding(self):
        def handler(sock):
            for expected in (b"Z0,8000000,2", b"G00"):
                self.assertEqual(request(sock), expected)
                answer(sock, b"")

        client = self.connect(handler)
        with self.assertRaises(RspUnsupportedError):
            client.breakpoint(0x8000000)
        with self.assertRaises(RspUnsupportedError):
            client.write_registers(b"\0")

    def test_memory_rejects_short_long_odd_and_nonhex_replies(self):
        for reply in (b"00", b"000000", b"000", b"00gg", b"00 0", b"xxxx"):
            with self.subTest(reply=reply):

                def handler(sock, reply=reply):
                    request(sock)
                    answer(sock, reply)

                with self.assertRaises(RspProtocolError):
                    self.connect(handler).read_memory(0, 2)

    def test_register_unavailable_markers_fail_explicitly(self):
        def handler(sock):
            request(sock)
            answer(sock, b"xxxxxxxx")

        with self.assertRaises(RspProtocolError):
            self.connect(handler).read_registers()

    def test_run_length_and_escaped_response(self):
        escaped = b"}\x03}\x04}]}\x0a"

        def handler(sock):
            request(sock)
            answer(sock, b"0* ")
            self.assertEqual(request(sock), b"qData:" + escaped)
            answer(sock, b"a" + escaped)

        client = self.connect(handler)
        self.assertEqual(client.read_memory(0, 2), b"\0\0")
        self.assertEqual(client.command(b"qData:#$}*"), b"a#$}*")

    def test_malformed_run_length_and_escape(self):
        for reply in (b"* ", b"0*", b"0*\x1e", b"a}"):
            with self.subTest(reply=reply):

                def handler(sock, reply=reply):
                    request(sock)
                    answer(sock, reply)

                with self.assertRaises(RspProtocolError):
                    self.connect(handler).command("g")

    def test_console_packets_before_response(self):
        def handler(sock):
            request(sock)
            sock.sendall(b"+" + frame(b"O6869"))
            self.assertEqual(receive(sock, 1), b"+")
            sock.sendall(frame(b"OK"))
            self.assertEqual(receive(sock, 1), b"+")

        self.assertEqual(self.connect(handler).command("QTest"), b"OK")

    def test_resume_returns_after_ack_then_interrupt(self):
        def handler(sock):
            self.assertEqual(request(sock), b"c")
            sock.sendall(b"+")
            self.assertEqual(receive(sock, 1), b"\x03")
            sock.sendall(frame(b"T02thread:1;"))
            self.assertEqual(receive(sock, 1), b"+")
            self.assertEqual(request(sock), b"g")
            answer(sock, b"0011")

        client = self.connect(handler)
        client.resume()
        with self.assertRaisesRegex(RspError, "Target is running"):
            client.command("g")
        with self.assertRaisesRegex(RspError, "already running"):
            client.resume()
        self.assertEqual(client.interrupt(), b"T02thread:1;")
        self.assertEqual(client.read_registers(), b"\0\x11")

    def test_immediate_stop_after_resume_ack_is_preserved(self):
        def handler(sock):
            self.assertEqual(request(sock), b"c")
            sock.sendall(b"+" + frame(b"S05"))
            self.assertEqual(receive(sock, 1), b"+")

        client = self.connect(handler)
        client.resume()
        self.assertEqual(client.wait_stop(), b"S05")

    def test_stop_rejects_non_stop_and_accepts_exit(self):
        for reply, succeeds in (
            (b"OK", False),
            (b"S0g", False),
            (b"S0500", False),
            (b"W00", True),
        ):
            with self.subTest(reply=reply):

                def handler(sock, reply=reply):
                    sock.sendall(frame(reply))
                    self.assertEqual(receive(sock, 1), b"+")

                client = self.connect(handler)
                if succeeds:
                    self.assertEqual(client.wait_stop(), reply)
                else:
                    with self.assertRaises(RspProtocolError):
                        client.wait_stop()

    def test_ack_timeout_closes_without_replay(self):
        def handler(sock):
            self.assertEqual(request(sock), b"G00")
            self.assertEqual(sock.recv(4096), b"")

        client = self.connect(handler, timeout=0.08)
        started = time.monotonic()
        with self.assertRaises(RspTimeoutError):
            client.write_registers(b"\0")
        self.assertLess(time.monotonic() - started, 1)

    def test_reply_timeout_and_wait_stop_override(self):
        def handler(sock):
            request(sock)
            sock.sendall(b"+")
            self.assertEqual(sock.recv(4096), b"")

        with self.assertRaises(RspTimeoutError):
            self.connect(handler, timeout=0.08).command("g")

        def waiting(sock):
            self.assertEqual(sock.recv(4096), b"")

        client = self.connect(waiting, timeout=2)
        started = time.monotonic()
        with self.assertRaises(RspTimeoutError):
            client.wait_stop(timeout=0.08)
        self.assertLess(time.monotonic() - started, 1)

    def test_deadline_covers_whole_reply_not_each_byte(self):
        def handler(sock):
            request(sock)
            sock.sendall(b"+$")
            for _ in range(20):
                threading.Event().wait(0.035)
                try:
                    sock.sendall(b"0")
                except OSError:
                    return

        started = time.monotonic()
        with self.assertRaises(RspTimeoutError):
            self.connect(handler, timeout=0.11).command("g")
        self.assertLess(time.monotonic() - started, 0.5)

    def test_eof_inside_packet_and_missing_ack(self):
        for reply in (b"+$OK#", b"$OK#9a"):
            with self.subTest(reply=reply):

                def handler(sock, reply=reply):
                    request(sock)
                    sock.sendall(reply)

                with self.assertRaises(RspError):
                    self.connect(handler).command("g")

    def test_oversized_response(self):
        def handler(sock):
            request(sock)
            sock.sendall(b"+$" + b"0" * (GdbRemote.MAX_PACKET + 1))

        with self.assertRaisesRegex(RspProtocolError, "oversized"):
            self.connect(handler).command("g")

    def test_run_length_expansion_is_bounded(self):
        def handler(sock):
            request(sock)
            answer(sock, b"0" + b"*~" * 170)

        with self.assertRaisesRegex(RspProtocolError, "oversized"):
            self.connect(handler).read_registers()

    def test_invalid_arguments_do_not_send_commands(self):
        def handler(sock):
            self.assertEqual(sock.recv(4096), b"")

        client = self.connect(handler)
        for payload in (b"", b"a" * (client.MAX_PACKET + 1), b"#" * client.MAX_PACKET):
            with self.assertRaises(ValueError):
                client.command(payload)
        with self.assertRaises(TypeError):
            client.command(12)
        for address, size in ((-1, 1), (0, -1), (0xFFFFFFFF, 2), (True, 1), (0, 0.5)):
            with self.assertRaises(ValueError):
                client.read_memory(address, size)
        with self.assertRaises(TypeError):
            client.write_memory(0, bytearray(b"x"))
        with self.assertRaises(ValueError):
            client.write_registers(b"")
        with self.assertRaises(TypeError):
            client.breakpoint(0, thumb=1)
        self.assertEqual(client.read_memory(0, 0), b"")
        client.write_memory(0, b"")

    def test_nonloopback_and_unbounded_timeouts_rejected(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            GdbRemote(host="192.0.2.1")
        for timeout in (None, 0, -1, float("inf"), float("nan"), True):
            with self.assertRaises((ValueError, TypeError)):
                GdbRemote(timeout=timeout)


if __name__ == "__main__":
    unittest.main()
