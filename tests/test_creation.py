import unittest

from pokemon_creation import box_to_party_pokemon, create_box_pokemon
from rom_versions import load_profile


class CreationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles = [load_profile(crc=crc) for crc in ("b4af11c8", "4755f497")]

    def make(self, profile, **changes):
        values = dict(
            species=25, level=5, pid=0x12345678, otid=0x87654321,
            nickname="皮卡丘", ot_name="小智", moves=(33, 0, 0, 0),
        )
        values.update(changes)
        return create_box_pokemon(profile, **values)

    def test_full_record_fields_on_both_versions(self):
        for profile in self.profiles:
            with self.subTest(rom=profile["rom_crc32"]):
                mon = self.make(
                    profile, pp_ups=(3, 0, 0, 0),
                    ivs=(31, 30, 29, 28, 27, 26),
                    evs=(100, 80, 60, 40, 20, 0),
                    met_location=255, met_level=4, ot_gender=1,
                )
                self.assertEqual(len(mon.raw), 58)
                self.assertEqual(mon.raw[18], 2)
                self.assertEqual(mon.raw[19] & 7, 2)
                self.assertEqual(mon.species, 25)
                self.assertEqual(mon.nickname, "皮卡丘")
                self.assertEqual(mon.ot_name, "小智")
                self.assertEqual(mon.moves, (33, 0, 0, 0))
                self.assertEqual(mon.pp_ups, (3, 0, 0, 0))
                self.assertEqual(mon.ivs, (31, 30, 29, 28, 27, 26))
                self.assertEqual(mon.evs, (100, 80, 60, 40, 20, 0))
                self.assertEqual(mon.met_location, 255)
                self.assertEqual(mon.met_level, 4)
                self.assertEqual(mon.ot_gender, 1)
                self.assertEqual(mon.describe(profile)["errors"], [])

    def test_new_egg_has_both_flags_and_one_level(self):
        for profile in self.profiles:
            with self.subTest(rom=profile["rom_crc32"]):
                mon = self.make(
                    profile, level=1, nickname="Egg", moves=(0, 0, 0, 0), egg=True,
                )
                self.assertTrue(mon.egg)
                self.assertTrue(mon.raw[19] & 4)
                self.assertEqual(mon.met_level, 0)
                self.assertEqual(mon.friendship, profile["species"]["25"]["egg_cycles"])
                self.assertEqual(mon.describe(profile)["errors"], [])
                party = box_to_party_pokemon(profile, mon)
                self.assertEqual(len(party.raw), 100)
                self.assertTrue(party.egg)
                self.assertEqual(party.level, 1)
                self.assertEqual(party.moves, (0, 0, 0, 0))

    def test_party_withdrawal_derives_stats_and_pp(self):
        for profile in self.profiles:
            with self.subTest(rom=profile["rom_crc32"]):
                pc = self.make(profile, pp_ups=(3, 0, 0, 0))
                party = box_to_party_pokemon(profile, pc)
                self.assertEqual(party.species, pc.species)
                self.assertEqual(party.pid, pc.pid)
                self.assertEqual(party.otid, pc.otid)
                self.assertEqual(party.pp[0], profile["moves"]["33"]["pp"] * 8 // 5)
                self.assertEqual(party.pp[1:], (35, 35, 35))
                self.assertEqual(party.hp, party.stats[0])
                self.assertEqual(party.level, 5)
                self.assertEqual(party.validate(profile["species"]["25"]["base"])["errors"], [])

    def test_invalid_inputs_fail_closed(self):
        profile = self.profiles[0]
        invalid = (
            {"species": 65535},
            {"moves": (0, 0, 0, 0)},
            {"moves": (33, 33, 0, 0)},
            {"moves": (1023, 0, 0, 0)},
            {"pp_ups": (0, 1, 0, 0)},
            {"egg": True, "level": 5},
            {"nickname": "12345678901"},
            {"ivs": (0, 0, 0, 0, 0, 32)},
            {"evs": (252, 252, 7, 0, 0, 0)},
            {"species": 1141, "pid": 1},
            {"species": 1193, "pid": 0},
            {"species": 718, "held": 0},
            {"species": 919, "held": 0},
            {"species": 1066, "pid": 0},
            {"ball": 255},
            {"met_location": 222},
        )
        for case in invalid:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.make(profile, **case)
        forged = dict(profile)
        forged["rom_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.make(forged)
        with self.assertRaises(ValueError):
            box_to_party_pokemon(profile, bytes(58))


if __name__ == "__main__":
    unittest.main()
