"""Synthetic instruction and preimage tests; no private ROM is required."""

import struct
import unittest
from unittest.mock import Mock, patch

from tools import builtin_image_guard as guard

try:
    from unicorn import UC_ARCH_ARM, UC_HOOK_MEM_READ, UC_MODE_THUMB, Uc
    from unicorn.arm_const import (
        UC_ARM_REG_LR,
        UC_ARM_REG_R0,
        UC_ARM_REG_R1,
        UC_ARM_REG_R2,
        UC_ARM_REG_R3,
        UC_ARM_REG_R4,
        UC_ARM_REG_R5,
        UC_ARM_REG_R6,
        UC_ARM_REG_R7,
        UC_ARM_REG_R8,
        UC_ARM_REG_R9,
        UC_ARM_REG_R10,
        UC_ARM_REG_R11,
        UC_ARM_REG_R12,
        UC_ARM_REG_SP,
    )
except ModuleNotFoundError:
    Uc = None


def synthetic_base():
    image = bytearray(0x02000000)
    for site, _ in guard.SITES:
        image[site - guard.BASE:site - guard.BASE + 4] = guard.SITE_PREIMAGE
    image[guard.CAVE - guard.BASE:guard.CAVE - guard.BASE + guard.CAVE_SIZE] = b"\xff" * guard.CAVE_SIZE
    for n, slot in enumerate((0x08A7FA98, 0x08000130, 0x08000134)):
        table, resource = 0x08001000 + n * 0x100, 0x08002000 + n * 0x100
        struct.pack_into("<I", image, slot - guard.BASE, table)
        struct.pack_into("<I", image, table - guard.BASE, resource)
        struct.pack_into("<I", image, resource - guard.BASE, (0x800 if not n else 0x20) << 8 | 0x10)
    return bytes(image)


class ImageGuardPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = synthetic_base()

    def pinned_digest(self):
        return patch.object(guard.hashlib, "sha256", return_value=Mock(
            hexdigest=lambda: next(iter(guard.BASE_SHA256))))

    def test_rejects_unknown_identity_and_mutable_input(self):
        with self.assertRaises(ValueError):
            guard.image_patches(self.image)
        with self.assertRaises(ValueError):
            guard.image_patches(b"\0" * 4096)
        with self.assertRaises(TypeError):
            guard.image_patches(bytearray())

    def test_each_site_and_entire_cave_preimage_are_required(self):
        for address in (guard.SITES[0][0], guard.SITES[1][0], guard.CAVE,
                        guard.CAVE + guard.CAVE_SIZE - 1):
            damaged = bytearray(self.image)
            damaged[address - guard.BASE] ^= 1
            with self.pinned_digest(), self.assertRaisesRegex(ValueError, "preimage"):
                guard.image_patches(bytes(damaged))

    def test_fallback_zero_resources_must_have_correct_lz77_header(self):
        for n in range(3):
            damaged = bytearray(self.image)
            damaged[0x2000 + 0x100 * n] = 0
            with self.pinned_digest(), self.assertRaisesRegex(ValueError, "fallback"):
                guard.image_patches(bytes(damaged))

    def test_plan_owns_only_two_sites_and_full_128_byte_cave(self):
        with self.pinned_digest():
            plan = guard.image_patches(self.image)
            hardened = guard.harden_images(self.image)
        self.assertEqual(len(plan), 3)
        self.assertEqual((plan[-1][0], len(plan[-1][1])), (guard.CAVE, 128))
        expected = bytearray(self.image)
        occupied = set()
        for address, before, after in plan:
            self.assertEqual(len(before), len(after))
            region = set(range(address, address + len(before)))
            self.assertFalse(occupied & region)
            occupied |= region
            self.assertEqual(self.image[address - guard.BASE:address - guard.BASE + len(before)], before)
            expected[address - guard.BASE:address - guard.BASE + len(after)] = after
        self.assertEqual(hardened, bytes(expected))
        self.assertEqual(self.image[guard.CAVE - guard.BASE:guard.CAVE - guard.BASE + 128], b"\xff" * 128)


@unittest.skipIf(Uc is None, "optional Unicorn research dependency unavailable")
class ImageGuardInstructionTests(unittest.TestCase):
    def fixture(self, index):
        cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        cpu.mem_map(0x08A7D000, 0x3000)
        cpu.mem_map(0x02000000, 0x40000)
        cpu.mem_map(0x03000000, 0x8000)
        cpu.mem_write(guard.CAVE, guard.helper_bytes())
        for site, target in guard.SITES:
            cpu.mem_write(site, guard.thumb_bl(site, target))
        context = bytearray(0x80)
        struct.pack_into("<H", context, 0x2C, index)
        cpu.mem_write(0x02001000, bytes(context))
        for table in (0x02004000, 0x02008000):
            for number in (0, 1, 1553):
                cpu.mem_write(table + number * 8, struct.pack("<I", 0x08001000 + number * 4))
        registers = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                     UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                     UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
                     UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR)
        values = {reg: 0x12340000 + n * 0x100 for n, reg in enumerate(registers)}
        values.update({UC_ARM_REG_R3: 0x02001000, UC_ARM_REG_R5: 0x02004000,
                       UC_ARM_REG_R6: 0x02008000, UC_ARM_REG_SP: 0x03007E00,
                       UC_ARM_REG_LR: 0x02000101})
        for reg, value in values.items():
            cpu.reg_write(reg, value)
        return cpu, values, bytes(context)

    def test_both_entries_preserve_registers_stack_lr_context_and_bound_before_read(self):
        for entry in (guard.CAVE, guard.CAVE + 6):
            for index in (0, 1, 1294, 1553, 1554, 65535):
                with self.subTest(entry=entry, index=index):
                    cpu, values, context = self.fixture(index)
                    reads = []
                    cpu.hook_add(UC_HOOK_MEM_READ, lambda uc, a, address, s, v, d, reads=reads: reads.append(address),
                                 begin=0x02004000, end=0x0200BFFF)
                    cpu.emu_start(entry | 1, 0x02000100, count=100)
                    for reg, value in values.items():
                        if reg != UC_ARM_REG_R4:
                            self.assertEqual(cpu.reg_read(reg), value)
                    self.assertEqual(cpu.reg_read(UC_ARM_REG_R4), index * 8 if index in (0, 1, 1553) else 0)
                    self.assertEqual(bytes(cpu.mem_read(0x02001000, len(context))), context)
                    table = values[UC_ARM_REG_R5 if entry == guard.CAVE else UC_ARM_REG_R6]
                    self.assertEqual(reads, [table + 8 * index] if index <= 1553 else [])

    def test_both_replacement_bl_pairs_return_to_correct_continuation(self):
        for site, _ in guard.SITES:
            cpu, values, context = self.fixture(1553)
            cpu.emu_start(site | 1, site + 4, count=100)
            self.assertEqual(cpu.reg_read(UC_ARM_REG_LR), site + 5)
            self.assertEqual(cpu.reg_read(UC_ARM_REG_SP), values[UC_ARM_REG_SP])
            self.assertEqual(cpu.reg_read(UC_ARM_REG_R3), values[UC_ARM_REG_R3])
            self.assertEqual(cpu.reg_read(UC_ARM_REG_R4), 1553 * 8)
            self.assertEqual(bytes(cpu.mem_read(0x02001000, len(context))), context)

    def test_bl_rejects_unaligned_and_out_of_range_targets(self):
        for site, target in ((0, 1), (1, 0), (0, 0x800000)):
            with self.assertRaises(ValueError):
                guard.thumb_bl(site, target)


if __name__ == "__main__":
    unittest.main()
