import socket
import threading
import unittest
from unittest.mock import patch
from memory_client import MemClient


class Server:
    def __init__(self, handler):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.handler = handler
        self.error = None
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            conn, _ = self.listener.accept()
            with conn:
                conn.settimeout(4)
                reader = conn.makefile("rb")
                while True:
                    line = reader.readline()
                    if not line:
                        break
                    reply = self.handler(line.strip())
                    if reply is None:
                        return
                    for chunk in reply:
                        conn.sendall(chunk)
        except Exception as exc:
            self.error = exc
        finally:
            self.listener.close()

    def join(self):
        self.thread.join(5)
        if self.thread.is_alive():
            raise AssertionError("server did not terminate")
        if self.error:
            raise self.error


class TransportTests(unittest.TestCase):
    def connection(self, handler):
        server = Server(handler)
        mem = MemClient(port=server.port)
        self.addCleanup(server.join)
        self.addCleanup(mem.close)
        mem.connect(scan=1)
        return mem

    def test_fragmented_responses(self):
        def handler(line):
            if line == b"PING":
                return [b"PO", b"NG\n"]
            if line == b"CAPS":
                return [b"MERCURY/2 ", b"BATCH\n"]
            if line.startswith(b"READ"):
                return [b"00", b"0a", b"ff\n"]
            if line.startswith(b"BATCH"):
                return [b"O", b"K\n"]

        mem = self.connection(handler)
        self.assertEqual(mem.read(0x2000000, 3), bytes([0, 10, 255]))
        mem.batch([(0x2000000, b"\0", b"\1")])

    def test_old_bridge_is_read_only(self):
        def handler(line):
            return [b"PONG\n"] if line == b"PING" else [b"ERR unknown cmd\n"]

        mem = self.connection(handler)
        with self.assertRaises(IOError):
            mem.batch([(0x2000000, b"\0", b"\1")])

    def test_eof_does_not_loop_forever(self):
        def handler(line):
            if line == b"PING":
                return [b"PONG\n"]
            if line == b"CAPS":
                return [b"MERCURY/2 BATCH\n"]
            return None

        mem = self.connection(handler)
        with self.assertRaises(IOError):
            mem.read(0x2000000, 1)
        self.assertIsNone(mem.s)

    def test_short_read_rejected(self):
        def handler(line):
            if line == b"PING":
                return [b"PONG\n"]
            if line == b"CAPS":
                return [b"MERCURY/2 BATCH\n"]
            return [b"00\n"]

        mem = self.connection(handler)
        with self.assertRaises(IOError):
            mem.read(0x2000000, 2)

    def test_scan_prefers_new_bridge_over_old_fallback(self):
        def old(line):
            return [b"PONG\n"] if line == b"PING" else [b"ERR unknown\n"]

        def new(line):
            return (
                [b"PONG\n"]
                if line == b"PING"
                else [b"MERCURY/3 BATCH ROMCRC CRCBATCH\n"]
            )

        servers = [Server(old), Server(new)]
        for server in servers:
            self.addCleanup(server.join)
        connect = socket.create_connection

        def route(address, timeout):
            return connect(("127.0.0.1", servers[address[1] - 20000].port), timeout)

        mem = MemClient(port=20000)
        self.addCleanup(mem.close)
        with patch("memory_client.socket.create_connection", side_effect=route):
            mem.connect(scan=2)
        self.assertEqual(mem.port, 20001)
        self.assertIn("BATCH", mem.capabilities)


if __name__ == "__main__":
    unittest.main()
