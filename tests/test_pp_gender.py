import struct
import tempfile
import unittest

from box_data import BoxPokemon
from pokemon_data import Pokemon, change_gender_pid, gender, maximum_pp, unown_form
from trainer_core import Trainer, PARTY, PARTY_COUNT, SAVE_POINTER
from tests.test_box import packed_box
from tests.test_core import Memory
from tests.test_pokemon_data import sample
from tests.test_rom_versions import install_profile
from rom_versions import load_profile


class PPGenderTests(unittest.TestCase):
    def test_party_pp_lower_limit_clamps_untouched_value_and_rejects_new_overflow(self):
        profile = load_profile(crc="b4af11c8")
        source = sample()
        filled, _ = source.edit(
            pp_ups=[3] * 4, pp=[16, 24, 16, 16], move_data=profile["moves"]
        )
        lowered, _ = filled.edit(pp_ups=[0, 3, 3, 3], move_data=profile["moves"])
        self.assertEqual(lowered.pp, (10, 24, 16, 16))
        self.assertEqual(lowered.pp_ups, (0, 3, 3, 3))
        self.assertEqual(lowered.raw[41:52], filled.raw[41:52])
        self.assertEqual(lowered.raw[56:], filled.raw[56:])
        with self.assertRaises(ValueError):
            filled.edit(
                pp_ups=[0, 3, 3, 3], pp=[17, 24, 16, 16], move_data=profile["moves"]
            )

    def test_empty_and_replaced_moves_cannot_inherit_pp_ups(self):
        profile = load_profile(crc="b4af11c8")
        source, _ = sample().edit(pp_ups=[3] * 4, move_data=profile["moves"])
        cleared, _ = source.edit(
            moves=[0, 242, 8, 700], pp_ups=[3] * 4, move_data=profile["moves"]
        )
        self.assertEqual((cleared.pp[0], cleared.pp_ups[0]), (0, 0))
        self.assertEqual(cleared.pp_ups[1:], (3, 3, 3))
        with self.assertRaisesRegex(ValueError, "更换招式"):
            source.edit(
                moves=[33, 242, 8, 700], pp_ups=[3] * 4, move_data=profile["moves"]
            )
        with self.assertRaises(ValueError):
            source.edit(pp_ups=[4] * 4, move_data=profile["moves"])

    def test_pc_pp_ups_change_one_byte_and_keep_move_packing(self):
        for crc in ("b4af11c8", "4755f497"):
            profile = load_profile(crc=crc)
            raw = bytearray(packed_box())
            raw[39:44] = sum(
                move << (10 * i) for i, move in enumerate((757, 242, 8, 700))
            ).to_bytes(5, "little")
            source = BoxPokemon(bytes(raw))
            updated, report = source.edit(profile, pp_ups=[3, 2, 1, 0])
            self.assertEqual(updated.pp_ups, (3, 2, 1, 0))
            self.assertEqual(updated.raw[:36], source.raw[:36])
            self.assertEqual(updated.raw[37:], source.raw[37:])
            self.assertEqual(
                report["maximum_pp"],
                [
                    maximum_pp(m, u, profile["moves"])
                    for m, u in zip(source.moves, (3, 2, 1, 0))
                ],
            )
            with self.assertRaises(ValueError):
                source.edit(profile, pp_ups=[3] * 3)

    def test_gender_pid_preserves_nature_shiny_ability_and_special_form(self):
        for shiny in (False, True):
            for species, ratio in ((160, 31), (1, 31), (1065, 127), (201, 127)):
                pid = 0x43DEB981
                otid = 0x40F88D36 if not shiny else (pid >> 16) ^ (pid & 65535)
                result = change_gender_pid(pid, otid, "雌性", species, ratio, True)
                self.assertEqual(gender(result, ratio), "雌性")
                self.assertEqual(result % 25, pid % 25)
                self.assertEqual(result & 1, pid & 1)
                self.assertEqual(
                    ((result >> 16) ^ (result & 65535) ^ (otid >> 16) ^ (otid & 65535))
                    < 8,
                    shiny,
                )
                if species == 1065:
                    self.assertEqual(result % 7, pid % 7)
                if species == 201:
                    self.assertEqual(unown_form(result), unown_form(pid))

    def test_fixed_gender_impossible_parity_and_spinda_requirements(self):
        for ratio, target in ((0, "雄性"), (254, "雌性"), (255, "无性别")):
            self.assertEqual(change_gender_pid(123, 456, target, 1, ratio), 123)
            with self.assertRaisesRegex(ValueError, "不存在"):
                change_gender_pid(
                    123, 456, "雌性" if target != "雌性" else "雄性", 1, ratio
                )
        with self.assertRaisesRegex(ValueError, "无法同时"):
            change_gender_pid(123, 456, "雌性", 1, 1, True)
        with self.assertRaisesRegex(ValueError, "花纹"):
            change_gender_pid(0x43DEB981, 456, "雌性", 308, 127)
        result = change_gender_pid(0x43DEB981, 456, "雌性", 308, 127, allow_spinda=True)
        self.assertGreaterEqual(result & 255, 16)

    def test_both_profiles_gender_pp_write_restore_and_stale_protection(self):
        for crc in ("b4af11c8", "4755f497"):
            profile = load_profile(crc=crc)
            memory = Memory()
            install_profile(memory, profile)
            memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
            memory.put(PARTY_COUNT, b"\1")
            memory.put(PARTY, sample(flags=0).raw)
            with tempfile.TemporaryDirectory() as directory:
                trainer = Trainer(memory, profile, directory)
                snapshot = trainer.snapshot()
                patches, report = trainer.edit_pokemon(
                    snapshot, 0, target_gender="雌性", pp_ups=[3] * 4
                )
                updated = Pokemon(patches[0][2])
                self.assertEqual(
                    gender(updated.pid, profile["species"]["160"]["gender_ratio"]),
                    "雌性",
                )
                self.assertEqual(updated.ivs, sample().ivs)
                self.assertEqual(updated.pp, sample().pp)
                result = trainer.commit(snapshot, patches, "gender and PP")
                trainer.restore(result["backup"])
                self.assertEqual(memory.read(PARTY, 100), sample(flags=0).raw)
                memory.put(PARTY + 1, b"\xa5")
                before = dict(memory.data)
                with self.assertRaises(OSError):
                    trainer.commit(snapshot, patches, "stale")
                self.assertEqual(memory.data, before)


if __name__ == "__main__":
    unittest.main()
