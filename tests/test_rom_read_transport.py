"""Exercise bounded ROM reads over TCP and the real Lua command/callback code."""

import unittest
from pathlib import Path
from unittest.mock import Mock

from memory_client import MemClient
from tests.test_bridge import LuaRuntime
from tests.test_transport import Server
from tests import test_bridge_lifecycle


class RomReadTransportTests(unittest.TestCase):
    def connection(self, capabilities, reads):
        def handler(line):
            if line == b"PING":
                return [b"PONG\n"]
            if line == b"CAPS":
                return [capabilities + b"\n"]
            cmd, address, count = line.split()
            address, count = int(address, 16), int(count, 16)
            reads.append((cmd, address, count))
            data = bytes((address + i) % 256 for i in range(count))
            response = data.hex().encode() + b"\n"
            return [response[i:i + 997] for i in range(0, len(response), 997)]

        server = Server(handler)
        client = MemClient(port=server.port)
        self.addCleanup(server.join)
        self.addCleanup(client.close)
        client.connect(scan=1)
        return client

    def test_maximum_fragmented_response_and_multiple_chunks_over_tcp(self):
        reads = []
        client = self.connection(b"MERCURY/3 CRCBATCH ROMREAD", reads)
        self.assertEqual(
            client.read_rom(0x08000000, 65539), bytes(range(256)) * 256 + b"\0\1\2"
        )
        self.assertEqual(reads, [(b"READROM", 0x08000000, 65536), (b"READROM", 0x08010000, 3)])
        self.assertEqual(client.command("PING"), b"PONG")

    def test_old_bridge_and_other_capabilities_use_small_read_fallback(self):
        for capabilities in (b"ERR unsupported", b"MERCURY/3 BATCH CRCBATCH BATCH8192"):
            with self.subTest(capabilities=capabilities):
                reads = []
                client = self.connection(capabilities, reads)
                self.assertEqual(client.read_rom(0x08000000, 8193), bytes(range(256)) * 32 + b"\0")
                self.assertEqual(reads, [(b"READ", 0x08000000, 4096), (b"READ", 0x08001000, 4096), (b"READ", 0x08002000, 1)])

    def test_bounds_and_types_rejected_before_any_command(self):
        client = MemClient()
        client.command = Mock()
        for address, count in (
            (0x02000000, 1), (0x08000000 - 1, 1), (0x0A000000, 1),
            (0x09FFFFFF, 2), (0x08000000, 0), (0x08000000, -1),
            (0x08000000, 0x02000001), (0x08000000, 1.5),
            (0x08000000, True), (True, 1), ("8000000", 1),
        ):
            with self.subTest(address=address, count=count), self.assertRaises(ValueError):
                client.read_rom(address, count)
        client.command.assert_not_called()

    def test_rom_end_and_entire_32_mib_range_are_bounded_chunks(self):
        client = MemClient()
        client.capabilities = {"ROMREAD"}
        client.command = Mock(side_effect=lambda command: b"00" * int(command.split()[2], 16))
        self.assertEqual(client.read_rom(0x09FFFFFF, 1), b"\0")
        client.command.reset_mock()
        self.assertEqual(len(client.read_rom(0x08000000, 0x02000000)), 0x02000000)
        self.assertEqual(client.command.call_count, 512)
        self.assertEqual(client.command.call_args.args[0], "READROM 9ff0000 10000")

    def test_short_long_invalid_hex_and_whitespace_rejected_for_both_protocols(self):
        for capabilities in (set(), {"ROMREAD"}):
            client = MemClient()
            client.capabilities = capabilities
            for response in (b"00", b"000000", b"gggg", b"\xff000", b"00 00", b"00  "):
                client.command = Mock(return_value=response)
                with self.subTest(capabilities=capabilities, response=response), self.assertRaises(IOError):
                    client.read_rom(0x08000000, 2)

    def test_large_response_allowance_is_command_and_capability_specific(self):
        for capabilities, command in ((set(), "READROM 8000000 10000"), ({"ROMREAD"}, "READ 8000000 1000"), ({"ROMREAD"}, "PING")):
            client = MemClient()
            client.capabilities = capabilities
            sock = Mock()
            sock.recv.return_value = b"0" * 32769
            client.s = sock
            with self.subTest(capabilities=capabilities, command=command), self.assertRaisesRegex(IOError, "长度限制"):
                client.command(command)
            self.assertIsNone(client.s)

    def test_romread_does_not_expand_python_read_or_write_capacity(self):
        client = MemClient()
        client.capabilities = {"ROMREAD", "BATCH", "BATCH8192"}
        client.command = Mock(return_value=b"OK")
        with self.assertRaises(ValueError):
            client.read(0x02000000, 4097)
        with self.assertRaises(ValueError):
            client.batch([(0x02000000, b"\0" * 8193, b"\1" * 8193)])
        client.command.assert_not_called()


@unittest.skipIf(LuaRuntime is None, "lupa is required")
class RomReadLuaTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
        self.lua.execute(b"""
reads={}; writes=0
emu={readRange=function(_,a,n) reads[#reads+1]={a,n};return string.rep(string.char(0xab),n) end,
     checksum=function() return string.char(1,2,3,4) end,
     write8=function() writes=writes+1 end}
MERCURY_BRIDGE_TEST=true
""")
        self.handle = self.lua.execute(Path("mercury_bridge.lua").read_bytes())

    def test_maximum_rom_read_and_upper_boundary_are_readonly(self):
        self.assertIn(b"ROMREAD", self.handle(b"CAPS").split())
        self.assertEqual(self.handle(b"READROM 8000000 10000"), b"ab" * 65536)
        self.assertEqual(self.handle(b"READROM 9ffffff 1"), b"ab")
        self.assertEqual(self.lua.globals()[b"reads"][1][2], 65536)
        self.assertEqual(self.lua.globals()[b"writes"], 0)

    def test_invalid_sizes_ram_ranges_and_formats_do_not_read_or_write(self):
        for command in (b"READROM 8000000 0", b"READROM 8000000 10001", b"READROM 9ffffff 2", b"READROM a000000 1", b"READROM 7ffffff 1", b"READROM 2000000 1", b"READROM 3000000 1", b"READROM 8000000 -1", b"READROM 8000000 1 extra", b"READROM 8000000", b"READ 8000000 1001"):
            with self.subTest(command=command):
                self.assertTrue(self.handle(command).startswith(b"ERR"))
        self.assertEqual(len(self.lua.globals()[b"reads"]), 0)
        self.assertEqual(self.lua.globals()[b"writes"], 0)

    def test_large_rom_compare_and_ram_write_are_still_rejected(self):
        for address in (b"8000000", b"2000000"):
            command = b"BATCHCRC 01020304 " + address + b":" + b"ab" * 4097 + b":" + b"ab" * 4097
            self.assertEqual(self.handle(command), b"ERR range")
        self.assertEqual(self.handle(b"BATCHCRC 01020304 8000000:ab:00"), b"ERR range")
        self.assertEqual(self.lua.globals()[b"writes"], 0)

    def test_large_output_flush_retries_after_backpressure(self):
        fixture = test_bridge_lifecycle.LifecycleTests()
        fixture.setUp()
        client = fixture.g.connect()
        client[b"blocked"] = True
        fixture.g.feed(client, b"READROM 8000000 10000\n")
        self.assertFalse(client[b"closed"])
        self.assertEqual(client[b"sent"], b"")
        client[b"blocked"] = False
        client[b"limit"] = 997
        fixture.g.events[b"frame"]()
        self.assertEqual(client[b"sent"], b"00" * 65536 + b"\n")
        fixture.g.feed(client, b"PING\n")
        self.assertFalse(client[b"closed"])
        self.assertTrue(client[b"sent"].endswith(b"\nPONG\n"))

    def test_pending_output_and_normal_input_limits_remain_bounded(self):
        fixture = test_bridge_lifecycle.LifecycleTests()
        fixture.setUp()
        client = fixture.g.connect()
        client[b"blocked"] = True
        fixture.g.feed(client, b"READROM 8000000 10000\nPING\n")
        self.assertTrue(client[b"closed"])
        client = fixture.g.connect()
        client[b"blocked"] = True
        fixture.g.feed(client, b"PING\n" * 7000)
        self.assertTrue(client[b"closed"])
        client = fixture.g.connect()
        fixture.g.feed(client, b"READROM " + b"0" * 40001)
        self.assertTrue(client[b"closed"])


if __name__ == "__main__":
    unittest.main()
