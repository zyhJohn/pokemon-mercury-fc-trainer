import unittest
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None
from tests.test_core import Memory


@unittest.skipIf(LuaRuntime is None, "install lupa to test the actual Lua bridge")
class LuaTests(unittest.TestCase):
    def test_large_box_command_rejects_other_write_sizes_and_reads_back(self):
        prefix = b"BOXBATCHCRC " + self.mem.rom_crc32.encode() + b" "
        self.assertEqual(self.handle(prefix + b"2000010:00:01"), b"ERR length")
        body = b"2001000:" + b"00" * 1740 + b":" + b"01" * 1740
        self.assertEqual(self.handle(prefix + body + b";2005000:ff:ff"), b"ERR stale")
        self.assertEqual(self.mem.writes, 0)
        self.assertEqual(self.handle(prefix + body), b"OK")
        self.assertEqual(self.mem.read(0x2001000, 1740), b"\1" * 1740)

    def test_batch_verify_confirms_readback_and_reports_failed_write(self):
        self.assertEqual(
            self.raw_handle(
                b"BATCHVERIFYCRC " + self.mem.rom_crc32.encode() + b" 2000010:00:01"
            ),
            b"OK",
        )
        self.assertEqual(self.mem.read(0x2000010, 1), b"\1")
        self.lua.globals()[b"emu"][b"write8"] = lambda *_: None
        self.assertEqual(
            self.raw_handle(
                b"BATCHVERIFYCRC " + self.mem.rom_crc32.encode() + b" 2000010:01:02"
            ),
            b"ERR readback",
        )
        self.assertEqual(self.mem.read(0x2000010, 1), b"\1")

    def setUp(self):
        self.lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
        self.mem = Memory()
        emu = self.lua.table()
        emu[b"readRange"] = lambda _, a, n: self.mem.read(a, n)

        def write(_, a, v):
            self.mem.put(a, bytes([v]))
            self.mem.writes += 1

        emu[b"write8"] = write
        emu[b"checksum"] = lambda _: bytes.fromhex(self.mem.rom_crc32)
        self.lua.globals()[b"emu"] = emu
        self.lua.globals()[b"MERCURY_BRIDGE_TEST"] = True
        self.raw_handle = self.lua.execute(Path("mercury_bridge.lua").read_bytes())
        self.handle = lambda line: self.raw_handle(
            b"BATCHCRC " + self.mem.rom_crc32.encode() + b" " + line[6:]
            if line.startswith(b"BATCH ")
            else line
        )

    def test_rom_swap_blocks_batch_and_legacy_command_is_disabled(self):
        self.assertEqual(
            self.raw_handle(b"BATCHCRC 00000000 2000010:00:01"), b"ERR rom"
        )
        self.assertEqual(self.raw_handle(b"BATCH 2000010:00:01"), b"ERR unsupported")
        self.assertEqual(self.mem.writes, 0)

    def test_stale_second_patch_does_not_write_first(self):
        result = self.handle(b"BATCH 2000010:0000:0102;2000020:ff:01")
        self.assertEqual(result, b"ERR stale")
        self.assertEqual(self.mem.writes, 0)

    def test_matching_batch_writes_and_binary_reads(self):
        result = self.handle(b"BATCH 2000010:0000:0aff;3005008:00:00")
        self.assertEqual(result, b"OK")
        self.assertEqual(self.handle(b"READ 2000010 2"), b"0aff")
        self.assertEqual(self.mem.writes, 2)

    def test_reject_malformed_out_of_range_and_overlap(self):
        for value in [
            b"BATCH 2000000:0:1",
            b"BATCH 2000000:00:0000",
            b"BATCH 8000000:00:ff",
            b"BATCH 2000000:0000:1111;2000001:00:11",
            b"BATCH 2000000:00:11;",
            b"BATCH 2000000:00:11;;2000002:00:11",
            b"WRITE8 2000000 1",
            b"READ 2000000 1001",
        ]:
            self.assertTrue(self.handle(value).startswith(b"ERR"), value)
        self.assertEqual(self.mem.writes, 0)

    def test_restore_sized_batch_and_patch_limit(self):
        parts = [f"{0x2000010 + i:x}:00:01".encode() for i in range(34)]
        self.assertEqual(self.handle(b"BATCH " + b";".join(parts)), b"OK")
        self.assertEqual(self.mem.writes, 34)
        parts = [f"{0x2001000 + i:x}:00:01".encode() for i in range(65)]
        self.assertEqual(self.handle(b"BATCH " + b";".join(parts)), b"ERR limit")
        self.assertEqual(self.mem.writes, 34)

    def test_large_atomic_batch_and_byte_limit(self):
        parts = [
            b"2001000:" + b"00" * 4096 + b":" + b"01" * 4096,
            b"2003000:" + b"00" * 4096 + b":" + b"02" * 4096,
        ]
        command = b"BATCH " + b";".join(parts)
        self.assertEqual(self.handle(command + b";2005000:00:03"), b"ERR limit")
        self.assertEqual(self.mem.writes, 0)
        self.assertEqual(self.handle(command), b"OK")
        self.assertEqual(self.mem.writes, 8192)


if __name__ == "__main__":
    unittest.main()
