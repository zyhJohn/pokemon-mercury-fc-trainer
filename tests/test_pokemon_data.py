import unittest
import struct
import random
import json
from pathlib import Path
from pokemon_data import (
    Pokemon,
    shiny_value,
    change_shiny_pid,
    calculate_stats,
    experience_for_level,
    unown_form,
)

TABLES = json.loads(Path("rom_profile.json").read_text(encoding="utf-8"))[
    "experience_tables"
]


def sample(pid=0x43DEB981, flags=0x80000000, species=160):
    raw = bytearray(100)
    struct.pack_into("<II", raw, 0, pid, 0x40F88D36)
    raw[19] = 2 | (4 if flags & 0x40000000 else 0)
    raw[28:32] = bytes.fromhex("00000ca5")
    struct.pack_into("<HHI", raw, 32, species, 0, 103278)
    struct.pack_into("<4H4B", raw, 44, 757, 242, 8, 700, 10, 15, 10, 10)
    raw[56:62] = bytes([92, 102, 73, 134, 68, 41])
    ivs = [23, 10, 16, 1, 25, 23]
    word = flags | sum(v << (5 * k) for k, v in enumerate(ivs))
    struct.pack_into("<I", raw, 72, word)
    raw[84] = 48
    stats = calculate_stats([85, 105, 100, 78, 79, 83], ivs, raw[56:62], 48, pid % 25)
    struct.pack_into("<H6H", raw, 86, stats[0] - 10, *stats)
    return Pokemon(bytes(raw))


class PokemonTests(unittest.TestCase):
    def test_rom_specific_experience_boundaries(self):
        self.assertEqual(experience_for_level(70, 1, TABLES), 276915)
        self.assertEqual(experience_for_level(5, 2, TABLES), 64)
        self.assertEqual(experience_for_level(5, 3, TABLES), 134)
        with self.assertRaises(ValueError):
            experience_for_level(50, 3, None)

    def test_fixed_layout_independent_of_pid(self):
        for pid in range(24):
            m = sample(pid)
            self.assertEqual(m.species, 160)
            self.assertEqual(m.experience, 103278)
            self.assertEqual(m.moves, (757, 242, 8, 700))

    def test_preserve_flags_and_unrelated_bytes(self):
        for flags in [0, 0x40000000, 0x80000000, 0xC0000000]:
            m = sample(flags=flags)
            edited, report = m.edit(
                ivs=[31] * 6,
                evs=[252, 252, 0, 0, 0, 6],
                base=[85, 105, 100, 78, 79, 83],
            )
            self.assertTrue(report["structural_ok"])
            self.assertEqual(edited.u32(72) & 0xC0000000, flags)
            self.assertEqual(edited.raw[:56], m.raw[:56])
            self.assertEqual(edited.raw[62:72], m.raw[62:72])
            self.assertEqual(edited.raw[76:86], m.raw[76:86])
            self.assertEqual(edited.stats[0] - edited.hp, 10)

    def test_fainted_hp_stays_zero(self):
        raw = bytearray(sample().raw)
        raw[86:88] = b"\0\0"
        m = Pokemon(bytes(raw))
        edited, _ = m.edit(ivs=[31] * 6, base=[85, 105, 100, 78, 79, 83])
        self.assertEqual(edited.hp, 0)

    def test_reject_invalid_stats_without_truncation(self):
        for changes in [
            dict(ivs=[32] * 6),
            dict(ivs=[-1] * 6),
            dict(ivs=[1] * 5),
            dict(evs=[252] * 6),
            dict(evs=[253, 0, 0, 0, 0, 0]),
            dict(evs=["a"] * 6),
        ]:
            with self.assertRaises(ValueError):
                sample().edit(base=[85, 105, 100, 78, 79, 83], **changes)

    def test_shiny_preserves_nature_gender_ability_and_unown(self):
        rng = random.Random(4886)
        for species in [160, 201]:
            for _ in range(60):
                pid, ot = rng.getrandbits(32), rng.getrandbits(32)
                shiny = change_shiny_pid(pid, ot, True, species)
                self.assertLess(shiny_value(shiny, ot), 8)
                self.assertEqual(shiny % 25, pid % 25)
                self.assertEqual(shiny & 255, pid & 255)
                if species == 201:
                    self.assertEqual(unown_form(shiny), unown_form(pid))
                normal = change_shiny_pid(shiny, ot, False, species)
                self.assertGreaterEqual(shiny_value(normal, ot), 8)
                self.assertEqual(normal % 25, pid % 25)
                self.assertEqual(normal & 255, pid & 255)

    def test_spinda_refuses_pattern_change(self):
        m = sample(species=308)
        with self.assertRaises(ValueError):
            m.edit(shiny=not m.shiny)

    def test_shiny_edit_only_touches_pid(self):
        m = sample()
        edited, _ = m.edit(shiny=True)
        self.assertTrue(edited.shiny)
        self.assertEqual(edited.raw[4:], m.raw[4:])

    def test_growth_curves(self):
        for growth, maximum in enumerate(
            [1000000, 600000, 1640000, 1059860, 800000, 1250000]
        ):
            self.assertEqual(experience_for_level(1, growth, TABLES), 1)
            self.assertEqual(experience_for_level(100, growth, TABLES), maximum)
            values = [experience_for_level(n, growth, TABLES) for n in range(1, 101)]
            self.assertEqual(values, sorted(values))

    def test_level_change_updates_exp_stats_and_preserves_header(self):
        m = sample()
        edited, _ = m.edit(
            level=50,
            growth=3,
            experience_tables=TABLES,
            base=[85, 105, 100, 78, 79, 83],
        )
        self.assertEqual(edited.level, 50)
        self.assertEqual(edited.experience, 117360)
        self.assertEqual(edited.raw[:32], m.raw[:32])
        self.assertEqual(edited.moves, m.moves)


if __name__ == "__main__":
    unittest.main()
