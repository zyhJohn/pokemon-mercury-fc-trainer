"""Private-ROM-free checks of the isolated execution research harness."""

import struct
import unittest

try:
    from tools.verify_builtin_relocations import engine, name_cases, probe_image, result
except ModuleNotFoundError as exc:
    if exc.name != "unicorn":
        raise
    engine = None


@unittest.skipIf(engine is None, "optional Unicorn research dependency unavailable")
class BuiltinRelocationHarnessTests(unittest.TestCase):
    def test_synthetic_native_entry_and_name_read(self):
        image = bytearray(0x1000)
        # ldr r0,[pc]; bx lr; .word 08000010
        image[:8] = bytes.fromhex("00487047") + struct.pack("<I", 0x08000010)
        image[16:18] = bytes.fromhex("bbff")  # Mercury Latin A, terminator.
        image = bytes(image)
        cpu = engine(image)
        self.assertEqual(cpu.addresses, {})
        actual = result(cpu, 0x08000000, 999, 888)
        self.assertEqual(actual["pointer"], "08000010")
        self.assertEqual(actual["name"], "A")
        self.assertEqual(image[16:18], bytes.fromhex("bbff"))

    def test_profile_cases_include_every_slot_and_all_129_species(self):
        cases, slots = name_cases({"species": {
            "7": {"abilities": [66, 0, 44]}, "8": {"abilities": [67, 68, 0]}}})
        self.assertEqual(slots, [2, 1, 1])
        self.assertTrue({(66, 7), (44, 7), (67, 8), (68, 8)}.issubset(cases))
        self.assertTrue(all((129, i) in cases for i in range(1554)))
        self.assertEqual(len(cases), len(set(cases)))

    @staticmethod
    def image(code):
        image = bytearray(0xA7E000)
        image[0xA7D568:0xA7D568 + len(code)] = code
        return bytes(image)

    def test_low_memory_reads_are_reported_without_synthetic_bios(self):
        # movs r0,0; ldrb r0,[r0]; bx lr
        actual = probe_image(self.image(bytes.fromhex("002000787047")), 65535)
        self.assertEqual(actual["invalid_accesses"][0]["address"], "00000000")
        self.assertEqual(actual["invalid_accesses"][0]["pc"], "08a7d56a")
        self.assertFalse(actual["bios_wrapper_boundary"])
        self.assertIsNotNone(actual["error"])

    def test_boundary_stops_before_bios_wrapper_instruction(self):
        code = bytes.fromhex("004b1847") + struct.pack("<I", 0x08A7A061)
        actual = probe_image(self.image(code), 1)
        self.assertEqual(actual["bios_wrapper_boundary"][0]["entry"], "08a7a060")
        self.assertIsNone(actual["error"])
        self.assertFalse(actual["invalid_accesses"])
        self.assertFalse(actual["completed_render"])


if __name__ == "__main__":
    unittest.main()
