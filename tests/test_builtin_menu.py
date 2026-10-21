"""Synthetic ARM/Thumb fixture checks; no ROM, save or emulator required."""
import struct
import unittest

try:
    import unicorn
except ImportError:
    unicorn = None


@unittest.skipUnless(unicorn, "optional Unicorn research dependency unavailable")
class BuiltinCPUFixtureTests(unittest.TestCase):
    def cpu(self, code):
        from tools.verify_builtin_menu import BuiltinCPU
        rom = code + bytes(0x1000 - len(code))
        return BuiltinCPU(rom, bytes(0x61000))

    def test_absolute_thumb_call_and_return(self):
        cpu = self.cpu(bytes.fromhex("01307047"))  # adds r0,1; bx lr
        self.assertEqual(cpu.call(0x08000000, 41), 42)
        self.assertEqual(cpu.call(0x08000001, 3), 4)
        self.assertFalse(cpu.stub_calls)
        self.assertEqual(cpu.visits[0x08000000], 2)

    def test_documented_mgba_memory_region_offsets(self):
        from tools.verify_builtin_menu import BuiltinCPU
        state = bytearray(0x61000)
        samples = ((0x400, 0x04000000), (0x800, 0x05000000),
                   (0xC00, 0x07000000), (0x1000, 0x06000000),
                   (0x19000, 0x03000000), (0x21000, 0x02000000))
        for index, (offset, address) in enumerate(samples, 1):
            state[offset:offset + 4] = bytes((index,)) * 4
        cpu = BuiltinCPU(bytes(0x1000), bytes(state))
        for index, (offset, address) in enumerate(samples, 1):
            self.assertEqual(cpu.read(address, 4), bytes((index,)) * 4)

    def test_explicit_stub_logged(self):
        cpu = self.cpu(bytes.fromhex("7047"))
        cpu.install_stub(0x08000000, "synthetic", lambda fixture, args: args[0] + 7)
        self.assertEqual(cpu.call(0x08000000, 5), 12)
        self.assertEqual(cpu.stub_calls[0]["name"], "synthetic")
        self.assertFalse(cpu.native_calls)

    def test_bios_cpuset_and_fastset_fill(self):
        cpu = self.cpu(bytes.fromhex("0bdf7047"))
        cpu.write(0x02001000, b"abcdef")
        cpu.call(0x08000000, 0x02001000, 0x02002000, 3)
        self.assertEqual(cpu.read(0x02002000, 6), b"abcdef")
        self.assertEqual(cpu.bios_calls[0]["swi"], 11)
        cpu = self.cpu(bytes.fromhex("0cdf7047"))
        cpu.write(0x02001000, b"wxyz")
        cpu.call(0x08000000, 0x02001000, 0x02002000, (1 << 24) | 8)
        self.assertEqual(cpu.read(0x02002000, 32), b"wxyz" * 8)

    def test_bios_lz77_and_division(self):
        cpu = self.cpu(bytes.fromhex("11df7047"))
        cpu.write(0x02001000, bytes.fromhex("10060000106162630002"))
        # flags 0x10: three literals, then a three-byte distance-three copy.
        cpu.call(0x08000000, 0x02001000, 0x02002000)
        self.assertEqual(cpu.read(0x02002000, 6), b"abcabc")
        cpu = self.cpu(bytes.fromhex("06df7047"))
        self.assertEqual(cpu.call(0x08000000, -17, 5), 0xFFFFFFFD)
        self.assertEqual(cpu.registers()["r1"], 0xFFFFFFFE)

    def test_immediate_dma_real_store_instruction(self):
        cpu = self.cpu(bytes.fromhex("10607047"))  # str r0,[r2]; bx lr
        cpu.write(0x02001000, b"abcdefghijklmnop")
        cpu.write(0x040000D4, struct.pack("<II", 0x02001000, 0x02002000))
        cpu.call(0x08000000, 0x84000004, 0, 0x040000DC)
        self.assertEqual(cpu.read(0x02002000, 16), b"abcdefghijklmnop")
        self.assertEqual(len(cpu.dma_calls), 1)
        self.assertEqual(cpu.u32(0x040000DC), 0x04000004)

    def test_fault_and_loop_preserve_stop_evidence(self):
        cpu = self.cpu(bytes.fromhex("0047"))  # bx r0
        with self.assertRaises(RuntimeError):
            cpu.call(0x08000000, 1)
        self.assertEqual(cpu.stop["pc"], 0)
        self.assertEqual(cpu.stop["r0"], 1)
        cpu = self.cpu(bytes.fromhex("fee7"))
        with self.assertRaises(RuntimeError):
            cpu.call(0x08000000, max_instructions=100)
        self.assertEqual(cpu.stop["reason"], "instruction limit")

    def test_api_rejects_rom_writes(self):
        cpu = self.cpu(bytes.fromhex("7047"))
        before = cpu.read(0x08000000, 4)
        with self.assertRaises(ValueError):
            cpu.write(0x08000000, b"x")
        with self.assertRaises(ValueError):
            cpu.w32(0x08000000, 1)
        self.assertEqual(cpu.read(0x08000000, 4), before)

    def test_lifecycle_rejects_zero_negative_and_unbounded_cycles(self):
        from tools.verify_builtin_menu import verify_lifecycle
        for count in (0, -1, 101, True, 1.0):
            with self.subTest(count=count), self.assertRaises(ValueError):
                verify_lifecycle(bytes(0x1000), bytes(0x61000), count)


if __name__ == "__main__":
    unittest.main()
