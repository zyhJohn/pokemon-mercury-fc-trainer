import struct
import tempfile
import tkinter as tk
import unittest
import threading
import time
import json
from pathlib import Path
from unittest.mock import patch
from trainer_app import App
from trainer_core import Trainer, PARTY, PARTY_COUNT, SAVE_POINTER
from pokemon_data import Pokemon
from tests.test_core import Memory
from tests.test_pokemon_data import sample


class AppTests(unittest.TestCase):
    def test_fill_current_player_ot_drafts_in_party_and_pc_without_writes(self):
        from name_codec import encode_name
        from tests.test_box import packed_box
        from box_data import BoxPokemon

        address = 0x2024588
        self.mem.put(
            self.app.profile["trainer"]["pointer_address"], struct.pack("<I", address)
        )
        player = encode_name("小智A12", 8) + b"\x01\xa5" + struct.pack("<HH", 123, 456)
        self.mem.put(address, player)
        self.app.iv[0].set("31")
        immediate = lambda label, job, done: done(job())
        with patch.object(self.app, "run", side_effect=immediate):
            self.app.fill_player_ot()
        self.assertEqual(self.app.ot_name.get(), "小智A12")
        self.assertEqual(self.app.detail_vars["ot_tid"].get(), "123")
        self.assertEqual(self.app.detail_vars["ot_sid"].get(), "456")
        self.assertEqual(self.app.detail_vars["ot_gender"].get(), "1")
        self.assertEqual(self.app.iv[0].get(), "31")
        patches, _ = self.app.prepare_mon()
        self.assertEqual(
            Pokemon(patches[0][2]).shiny, self.app.snapshot["party"][0].shiny
        )

        raw = bytearray(packed_box())
        raw[39:44] = (757).to_bytes(5, "little")
        content = bytes(raw) + b"\0" * 58 * 29
        self.app.apply_box_snapshot(
            {
                "index": 0,
                "address": self.app.profile["storage"]["box_addresses"][0],
                "raw": content,
                "pokemon": tuple(
                    BoxPokemon(content[i : i + 58]) for i in range(0, len(content), 58)
                ),
            }
        )
        self.app.box_tree.selection_set("0")
        self.root.update()

        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        button = next(
            widget
            for widget in children(self.app.box_editor)
            if widget.winfo_class() == "TButton"
            and widget.cget("text").startswith("填入当前玩家")
        )
        with patch.object(self.app, "run", side_effect=immediate):
            button.invoke()
        self.assertEqual(self.app.box_editor_values[6].get(), "小智A12")
        values = [var.get() for var in self.app.box_editor_values]
        self.assertIn("123", values)
        self.assertIn("456", values)
        self.assertEqual(self.mem.read(address, 14), player)
        self.assertEqual(self.mem.writes, 0)

    def test_fill_player_ot_discards_result_if_selected_member_changes(self):
        result = ({"ot_tid": 123, "ot_sid": 456, "ot_gender": 1}, "test")
        self.app.run = lambda label, job, done: setattr(self, "fill_done", done)
        self.app.fill_player_ot()
        self.app.current_slot = None
        self.fill_done(result)
        self.assertIn("选中成员已变化", self.app.status.get())
        self.assertEqual(self.mem.writes, 0)

    def test_nickname_preview_display_and_pending_edit_guard(self):
        self.app.nickname.set("大力鳄小智")
        patches, _ = self.app.prepare_mon()
        updated = Pokemon(patches[0][2])
        self.assertEqual(updated.nickname, "大力鳄小智")
        self.assertNotEqual(self.app.party_form_values(), self.app.party_form_original)
        self.app.preview()
        self.assertIn("大力鳄小智", self.app.detail_preview.get())
        self.mem.put(PARTY, updated.raw)
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.assertIn("大力鳄小智", self.app.party_tree.item("0", "values")[0])
        self.assertEqual(self.mem.writes, 0)

    def test_toxtricity_nature_form_preview_explains_ability_change(self):
        patches, _ = self.app.trainer.edit_pokemon(
            self.app.snapshot, 0, species=1141, nature=0, ability_slot=1
        )
        self.mem.put(PARTY, patches[0][2])
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.app.nature.set("1")
        patches, _ = self.app.prepare_mon()
        self.assertEqual(Pokemon(patches[0][2]).species, 1193)
        self.app.preview()
        detail = self.app.detail_preview.get()
        self.assertIn("高调 → 低调", detail)
        self.assertIn("特性：", detail)
        self.assertEqual(self.mem.writes, 0)

    def test_location_name_selection_keeps_unknown_value_until_changed(self):
        raw = bytearray(sample().raw)
        raw[69] = 222
        self.mem.put(PARTY, raw)
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.assertTrue(self.app.detail_vars["met_location"].get().startswith("222 - "))
        patches, _ = self.app.prepare_mon()
        self.assertEqual(patches[0][1], patches[0][2])
        self.app.detail_vars["met_location"].set("213 - 跨海大桥")
        patches, _ = self.app.prepare_mon()
        self.assertEqual(Pokemon(patches[0][2]).met_location, 213)
        self.app.detail_vars["met_location"].set("223")
        with self.assertRaisesRegex(ValueError, "无效名称指针"):
            self.app.prepare_mon()
        self.assertEqual(self.mem.writes, 0)

    def test_minior_core_form_selection_changes_species_in_preview_only(self):
        patches, _ = self.app.trainer.edit_pokemon(self.app.snapshot, 0, species=1065)
        self.mem.put(PARTY, patches[0][2])
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.assertEqual(self.app.minior_color.get(), "保持当前")
        self.app.minior_color.set("6 - 紫色")
        prepared, _ = self.app.prepare_mon()
        updated = Pokemon(prepared[0][2])
        self.assertEqual((updated.species, updated.pid % 7), (1071, 6))
        self.app.preview()
        self.assertIn("紫色", self.app.report.get("1.0", "end"))
        self.assertEqual(self.mem.writes, 0)

    def test_automatic_refresh_retains_party_draft_when_game_bytes_match(self):
        self.app.level.set("75")
        self.app.ot_name.set("小智")
        original = self.app.party_form_original
        fresh = self.app.trainer.snapshot(2)
        with patch("trainer_app.messagebox.askyesno") as prompt:
            self.assertTrue(self.app.apply_snapshot(fresh, preserve_party=True))
            self.root.update()
            self.assertEqual(self.app.level.get(), "75")
            self.assertEqual(self.app.ot_name.get(), "小智")
            self.assertEqual(self.app.party_form_original, original)
            self.assertEqual(self.app.snapshot["pocket"]["id"], 2)
            prompt.assert_not_called()
        updated = Pokemon(self.app.prepare_mon()[0][0][2])
        self.assertEqual((updated.level, updated.ot_name), (75, "小智"))
        self.assertEqual(self.mem.writes, 0)

    def test_changed_party_refresh_keeps_old_snapshot_until_discard(self):
        self.app.level.set("75")
        original_snapshot = self.app.snapshot
        raw = bytearray(sample().raw)
        raw[41] ^= 1
        self.mem.put(PARTY, raw)
        fresh = self.app.trainer.snapshot(2)
        with patch("trainer_app.messagebox.askyesno", return_value=False):
            self.assertFalse(self.app.apply_snapshot(fresh, preserve_party=True))
        self.assertIs(self.app.snapshot, original_snapshot)
        self.assertEqual(self.app.level.get(), "75")
        patches, _ = self.app.prepare_mon()
        with self.assertRaises(OSError):
            self.app.trainer.commit(self.app.snapshot, patches, "stale draft")
        self.assertEqual(self.mem.writes, 0)
        with patch("trainer_app.messagebox.askyesno", return_value=True):
            self.assertTrue(self.app.apply_snapshot(fresh, preserve_party=True))
        self.assertEqual(self.app.level.get(), str(sample().level))

    def test_trainer_reread_and_reconnect_do_not_discard_draft_on_cancel(self):
        self.app.apply_trainer_snapshot(
            {
                "tid": 12,
                "sid": 34,
                "name": "小智",
                "gender": 0,
                "name_raw": "00",
                "address": 0x2024588,
                "raw": b"\0" * 14,
            }
        )
        self.app.player_name.set("小明")
        with (
            patch("trainer_app.messagebox.askyesno", return_value=False),
            patch.object(self.app, "run") as run,
        ):
            self.app.read_trainer()
            self.app.connect()
            run.assert_not_called()
        self.assertEqual(self.app.player_name.get(), "小明")
        self.assertIsNotNone(self.app.trainer_snapshot)

    def test_spinda_pattern_generation_is_explicit_preview_only(self):
        from pokemon_data import experience_for_level, calculate_stats

        raw = bytearray(sample().raw)
        metadata = self.app.profile["species"]["308"]
        struct.pack_into("<H", raw, 32, 308)
        struct.pack_into(
            "<I",
            raw,
            36,
            experience_for_level(
                raw[84], metadata["growth"], self.app.profile["experience_tables"]
            ),
        )
        mon = Pokemon(bytes(raw))
        struct.pack_into(
            "<6H",
            raw,
            88,
            *calculate_stats(
                metadata["base"], mon.ivs, mon.evs, mon.level, mon.pid % 25
            ),
        )
        struct.pack_into("<H", raw, 86, struct.unpack_from("<H", raw, 88)[0])
        self.mem.put(PARTY, raw)
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.app.shiny.set(not mon.shiny)
        with self.assertRaisesRegex(ValueError, "PID 花纹"):
            self.app.prepare_mon()
        with patch("trainer_app.secrets.randbits", return_value=0x87654321):
            self.app.new_spinda_pattern()
        updated = Pokemon(self.app.prepare_mon()[0][0][2])
        self.assertNotEqual(updated.pid, mon.pid)
        self.assertEqual(updated.shiny, not mon.shiny)
        self.assertEqual(updated.pid % 25, mon.pid % 25)
        self.assertEqual(self.mem.writes, 0)

    def test_card_selection_retains_dirty_member_until_discard(self):
        self.mem.put(PARTY_COUNT, b"\2")
        self.mem.put(PARTY + 100, sample().raw)
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.root.update()
        self.app.level.set("75")
        with patch("trainer_app.messagebox.askyesno", return_value=False) as prompt:
            self.app.party_tree.selection_set("1")
            self.root.update()
            self.assertEqual(self.app.current_slot, 0)
            self.assertEqual(self.app.party_tree.selection(), ("0",))
            self.assertEqual(self.app.level.get(), "75")
            prompt.assert_called_once()
        with patch("trainer_app.messagebox.askyesno", return_value=True):
            self.app.party_tree.selection_set("1")
            self.root.update()
        self.assertEqual(self.app.current_slot, 1)
        self.assertEqual(self.mem.writes, 0)

    def test_pc_cards_and_inline_form_bind_box_slot(self):
        from tests.test_box import packed_box
        from box_data import BoxPokemon

        raw = packed_box() * 2 + b"\0" * 58 * 28
        self.app.apply_box_snapshot(
            {
                "index": 24,
                "address": self.app.profile["storage"]["box_addresses"][24],
                "raw": raw,
                "pokemon": tuple(
                    BoxPokemon(raw[i : i + 58]) for i in range(0, len(raw), 58)
                ),
            }
        )
        self.assertEqual(len(self.app.box_tree.cards), 30)
        self.app.box_tree.selection_set("0")
        self.root.update()
        self.assertEqual(self.app.box_editor_slot, 0)
        self.app.box_editor_values[-1].set("9")
        with patch("trainer_app.messagebox.askyesno", return_value=False):
            self.app.box_tree.selection_set("1")
            self.root.update()
        self.assertEqual(self.app.box_editor_slot, 0)
        self.assertEqual(self.app.box_tree.selection(), ("0",))
        self.assertEqual(self.app.box_editor_values[-1].get(), "9")
        with patch("trainer_app.messagebox.askyesno", return_value=True):
            self.app.box_tree.selection_set("1")
            self.root.update()
        self.assertEqual(self.app.box_editor_slot, 1)
        self.app.box_tree.selection_set("29")
        self.root.update()
        self.assertIsNone(self.app.box_editor)
        self.assertEqual(self.app.box_detail.get(), "空槽")
        self.assertEqual(self.mem.writes, 0)

    def test_details_form_name_ids_and_egg_preview_do_not_write(self):
        self.app.detail_vars["ot_sid"].set("12345")
        self.app.detail_vars["met_location"].set("213")
        self.app.detail_vars["ball"].set("4")
        self.app.ot_name.set("RED")
        self.app.egg.set(True)
        patches, _ = self.app.prepare_mon()
        mon = Pokemon(patches[0][2])
        self.assertTrue(mon.egg)
        self.assertEqual(mon.level, 1)
        self.assertEqual(mon.met_location, 213)
        self.assertEqual(mon.otid >> 16, 12345)
        self.assertEqual(mon.ot_name, "RED")
        self.app.preview()
        self.assertIn("检查通过", self.app.detail_preview.get())
        self.assertEqual(self.mem.writes, 0)

    def test_failed_connection_can_export_diagnostics(self):
        self.app.snapshot = None
        self.app.mem = None
        self.app.last_error = "模拟器没有回应"
        path = Path(self.temp.name) / "connection-report.json"
        with patch("trainer_app.filedialog.asksaveasfilename", return_value=str(path)):
            self.app.export()
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["last_error"], "模拟器没有回应")
        self.assertNotIn("party", report)
        self.assertEqual(report["bridge_capabilities"], [])

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.addCleanup(self.app.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mem = Memory()
        for s in self.app.profile["signatures"]:
            self.mem.put(s["address"], bytes.fromhex(s["hex"]))
        self.mem.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        self.mem.put(PARTY_COUNT, b"\1")
        self.mem.put(PARTY, sample().raw)
        self.app.mem = self.mem
        self.app.trainer = Trainer(self.mem, self.app.profile, self.temp.name)
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.root.update_idletasks()
        # Memory is intentionally disconnected from any emulator.
        self.mem.close = lambda: None

    def test_form_preserves_all_bytes_when_unchanged(self):
        patches, _ = self.app.prepare_mon()
        self.assertEqual(patches[0][1], patches[0][2])

    def test_form_combined_shiny_iv_ev_and_preview(self):
        self.app.shiny.set(True)
        for var in self.app.iv:
            var.set("31")
        for var, n in zip(self.app.ev, [252, 252, 0, 0, 0, 6]):
            var.set(str(n))
        patches, report = self.app.prepare_mon()
        mon = Pokemon(patches[0][2])
        self.assertTrue(mon.shiny)
        self.assertEqual(mon.ivs, (31,) * 6)
        self.assertEqual(sum(mon.evs), 510)
        self.assertTrue(report["stats_match"])
        self.assertEqual(mon.raw[28:32], sample().raw[28:32])
        self.app.preview()
        self.assertIn("性别", self.app.report.get("1.0", "end"))
        self.assertEqual(self.mem.writes, 0)

    def test_important_quantity_hidden_and_preserved(self):
        self.mem.put(0x203C228, struct.pack("<HH", 365, 7))
        self.app.apply_snapshot(self.app.trainer.snapshot(2))
        self.app.bag_tree.selection_set("0")
        self.app.select_item()
        self.assertIn("disabled", self.app.qty_entry.state())
        self.assertEqual(self.app.item_qty.get(), "")
        self.assertEqual(self.app.bag_tree.item("0", "values")[3], "—")
        patch = self.app.trainer.edit_bag(self.app.snapshot, 0, 365, "")[0]
        self.assertEqual(patch[1], patch[2])

    def test_catalog_moves_fill_selected_slot_without_write(self):
        self.app.category.set("moves")
        self.app.filter_catalog()
        ident = next(
            r["id"]
            for r in self.app.catalog["categories"]["moves"]
            if str(r["id"]) in self.app.profile["moves"]
        )
        self.app.catalog_tree.selection_set(str(ident))
        self.app.move_target.set(2)
        old = [var.get() for var in self.app.move_vars]
        self.app.catalog_fill()
        self.assertTrue(self.app.move_vars[2].get().startswith(str(ident) + " - "))
        for i in (0, 1, 3):
            self.assertEqual(old[i], self.app.move_vars[i].get())
        self.assertEqual(self.mem.writes, 0)

    def test_busy_preserves_readonly_and_disabled_states(self):
        self.app.apply_snapshot(self.app.trainer.snapshot(2))
        self.app.freeze_inputs()
        self.assertIn("disabled", self.app.ability_cb.state())
        self.app.thaw_inputs()
        self.assertIn("readonly", self.app.ability_cb.state())
        self.assertNotIn("disabled", self.app.ability_cb.state())
        self.assertIn("disabled", self.app.qty_entry.state())

    def test_worker_result_uses_main_thread_and_close_waits(self):
        release = threading.Event()
        started = threading.Event()
        seen = []
        main_thread = threading.get_ident()

        def work():
            started.set()
            release.wait(2)
            return threading.get_ident()

        def done(worker_id):
            seen.append((worker_id, threading.get_ident()))

        self.app.run("test", work, done)
        self.assertTrue(started.wait(1))
        self.app.close()
        self.assertFalse(self.app.closed)
        self.assertTrue(self.app.close_requested)
        release.set()
        deadline = time.monotonic() + 3
        while not self.app.closed and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertTrue(self.app.closed)
        self.assertEqual(seen[0][1], main_thread)
        self.assertNotEqual(seen[0][0], main_thread)

    def test_catalog_item_fill_survives_queued_selection_event(self):
        self.app.category.set("items")
        self.app.filter_catalog()
        self.app.catalog_tree.selection_set("13")

        def refresh(after=None):
            self.app.apply_snapshot(self.app.trainer.snapshot(self.app.pocket_id()))
            if after:
                after()

        self.app.refresh = refresh
        self.app.catalog_fill()
        self.root.update()
        self.assertEqual(self.app.bag_tree.selection(), ("0",))
        self.assertTrue(self.app.item_id.get().startswith("13 - "))
        self.assertEqual(self.app.item_qty.get(), "1")
        self.assertEqual(self.mem.writes, 0)
        self.app.bag_tree.selection_set("5")
        self.app.select_item()
        self.app.catalog_fill()
        self.root.update()
        self.assertEqual(self.app.bag_tree.selection(), ("5",))
        self.assertTrue(self.app.item_id.get().startswith("13 - "))
        self.assertEqual(self.mem.writes, 0)

    def test_box_dialog_has_separate_member_and_does_not_write_on_open(self):
        from tests.test_box import packed_box
        from box_data import BoxPokemon

        raw = packed_box() + b"\0" * 58 * 29
        snapshot = {
            "index": 24,
            "address": self.app.profile["storage"]["box_addresses"][24],
            "raw": raw,
            "pokemon": tuple(
                BoxPokemon(raw[i : i + 58]) for i in range(0, len(raw), 58)
            ),
        }
        self.app.apply_box_snapshot(snapshot)
        self.app.box_tree.selection_set("0")
        window = self.app.edit_box_dialog()
        window.withdraw()
        self.assertIn("第 25 盒", window.title())
        self.assertIn("第 1 格", window.title())
        self.assertEqual(self.app.current_slot, 0)
        self.assertEqual(self.mem.writes, 0)
        self.app.freeze_inputs()
        window.destroy()
        self.app.thaw_inputs()


if __name__ == "__main__":
    unittest.main()
