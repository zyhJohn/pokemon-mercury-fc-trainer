"""Synthetic safety-plan and Thumb ABI checks; no private ROM/state required."""
import hashlib
import struct
import unittest
from unittest.mock import patch

from tools.builtin_bag_guard import (
    CALL_POINTER,
    CAVE,
    CAVE_SIZE,
    GUARD_CODE,
    bag_patches,
    harden_bag,
)

try:
    from unicorn.arm_const import (
        UC_ARM_REG_R4,
        UC_ARM_REG_R5,
        UC_ARM_REG_R6,
        UC_ARM_REG_R7,
        UC_ARM_REG_SP,
    )

    from tools.verify_builtin_actions import case_summary
    from tools.verify_builtin_menu import BuiltinCPU
except ImportError:
    BuiltinCPU = None


class BagPatchPlanTests(unittest.TestCase):
    def test_strict_identity_and_complete_cave_guard(self):
        image = bytearray(b"\xff" * 0x02000000)
        offset = CALL_POINTER - 0x08000000
        image[offset:offset+4] = struct.pack("<I", 0x0809A085)
        image = bytes(image)
        with self.assertRaises(ValueError):
            bag_patches(image)
        with patch("tools.builtin_bag_guard.BASE_SHA256", {hashlib.sha256(image).hexdigest()}):
            plan = bag_patches(image)
            self.assertEqual(len(plan), 2)
            self.assertEqual(plan[1][0], CAVE)
            self.assertEqual(len(plan[1][1]), CAVE_SIZE)
            self.assertTrue(all(len(old) == len(new) for _, old, new in plan))
            hardened = harden_bag(image)
            self.assertEqual(len(hardened), len(image))
            self.assertEqual(hardened[CAVE-0x08000000:CAVE-0x08000000+len(GUARD_CODE)], GUARD_CODE)
            with self.assertRaises(ValueError):
                bag_patches(hardened)
        for address in (CALL_POINTER, CAVE, CAVE+CAVE_SIZE-1):
            altered = bytearray(image)
            altered[address-0x08000000] ^= 1
            altered = bytes(altered)
            with patch("tools.builtin_bag_guard.BASE_SHA256", {hashlib.sha256(altered).hexdigest()}), self.assertRaises(ValueError):
                bag_patches(altered)

    @unittest.skipIf(BuiltinCPU is None, "Optional Unicorn research dependency unavailable")
    def test_mixed_status_summary(self):
        rows = [{"case": "ordinary", "status": "passed"},
                {"case": "xor8", "status": "known_issue", "finding": "boundary"},
                {"case": "egg", "status": "observation", "finding": "create"},
                {"case": "full", "status": "blocked", "error": "write", "stop": {"pc": 123}}]
        result = case_summary("synthetic", rows)
        self.assertEqual(sum(result["counts"].values()), 4)
        self.assertEqual(result["failures"], [{"case": "full", "error": "write", "pc": 123}])


@unittest.skipIf(BuiltinCPU is None, "Optional Unicorn research dependency unavailable")
class BagGuardThumbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image = bytearray(0x02000000)
        offset = CAVE-0x08000000
        image[offset:offset+len(GUARD_CODE)] = GUARD_CODE
        cls.image = bytes(image)

    def engine(self, slots=(1, 2, 3), capacity=3, pointer=0x02028000, pocket=1):
        cpu = BuiltinCPU(self.image, bytes(0x61000))
        calls = []
        def sanitize(c, args):
            calls.append(("sanitize", args[:2]))
            return args[0] if args[0] <= 749 else 0
        def get_pocket(c, args):
            calls.append(("pocket", args[:2]))
            return pocket
        def add(c, args):
            calls.append(("add", args[:2]))
            return 7  # Preserve the native function's exact return value.
        cpu.install_stub(0x0809A8A4, "synthetic native sanitizer", sanitize)
        cpu.install_stub(0x0809A9D8, "synthetic native pocket getter", get_pocket)
        cpu.install_stub(0x0809A084, "synthetic native add ABI sentinel", add)
        for index in range(5):
            cpu.write(0x0203988C+8*index, struct.pack("<IHH", pointer, capacity, 0))
        cpu.write(0x02028000, b"".join(struct.pack("<HH", item, 1) for item in slots))
        return cpu, calls

    def run_guard(self, cpu, item, qty):
        registers = (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7)
        for index, reg in enumerate(registers):
            cpu.uc.reg_write(reg, 0xA0B00000+index)
        result = cpu.call(CAVE, item, qty)
        self.assertEqual([cpu.uc.reg_read(reg) for reg in registers], [0xA0B00000+i for i in range(4)])
        self.assertEqual(cpu.uc.reg_read(UC_ARM_REG_SP), 0x03007E00)
        return result

    def test_full_unmatched_fails_without_add_or_data_writes(self):
        cpu, calls = self.engine()
        before = cpu.read(0x02000000, 0x40000)
        self.assertEqual(self.run_guard(cpu, 13, 1), 0)
        self.assertNotIn("add", [call[0] for call in calls])
        self.assertEqual(cpu.read(0x02000000, 0x40000), before)

    def test_same_id_or_empty_delegates_with_unchanged_arguments(self):
        for slots in ((1, 13, 3), (1, 0, 3), (13, 13, 13)):
            with self.subTest(slots=slots):
                cpu, calls = self.engine(slots)
                self.assertEqual(self.run_guard(cpu, 13, 99), 7)
                self.assertEqual(calls[-1], ("add", [13, 99]))

    def test_input_and_descriptor_rejections(self):
        for item, qty in ((0, 1), (750, 1), (65535, 1), (13, 0), (13, 100), (13, 65535)):
            with self.subTest(item=item, qty=qty):
                cpu, calls = self.engine()
                self.assertEqual(self.run_guard(cpu, item, qty), 0)
                self.assertNotIn("add", [call[0] for call in calls])
        for changes in ({"capacity": 0}, {"capacity": 451}, {"pointer": 0x02028001},
                        {"pointer": 0x01000000}, {"pointer": 0x0203FFFC},
                        {"pointer": 0xFFFFFFFC, "capacity": 1},
                        {"pocket": 0}, {"pocket": 6}):
            with self.subTest(changes=changes):
                cpu, calls = self.engine(**changes)
                before = cpu.read(0x02000000, 0x40000)
                self.assertEqual(self.run_guard(cpu, 13, 1), 0)
                self.assertNotIn("add", [call[0] for call in calls])
                self.assertEqual(cpu.read(0x02000000, 0x40000), before)


if __name__ == "__main__":
    unittest.main()
