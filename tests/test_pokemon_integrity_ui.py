"""Real hidden-Tk regression coverage for damaged-record isolation."""

import struct
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from box_data import BoxPokemon
from tests.test_box import packed_box
from tests.test_core import Memory
from tests.test_pokemon_data import sample
from trainer_app import App
from trainer_core import PARTY, PARTY_COUNT, SAVE_POINTER, Trainer


def healthy_box():
    raw = bytearray(packed_box())
    raw[39:44] = (757).to_bytes(5, "little")
    return bytes(raw)


class PokemonIntegrityUiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root)
        self.addCleanup(self.app.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mem = Memory()
        self.mem.close = lambda: None
        for signature in self.app.profile["signatures"] + self.app.profile["storage"]["signatures"]:
            self.mem.put(signature["address"], bytes.fromhex(signature["hex"]))
        self.mem.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        self.mem.put(PARTY_COUNT, b"\1")
        self.mem.put(PARTY, sample().raw)
        self.app.mem = self.mem
        self.app.trainer = Trainer(self.mem, self.app.profile, self.temp.name)
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.app.icon_request_pending = False
        self.address = self.app.profile["storage"]["box_addresses"][0]

    def put_party(self, records):
        self.mem.put(PARTY_COUNT, bytes([len(records)]))
        self.mem.put(PARTY, b"".join(records))
        self.app.apply_snapshot(self.app.trainer.snapshot())

    def put_box(self, records):
        self.mem.put(self.address, b"".join(records) + b"\0" * 58 * (30 - len(records)))
        self.app.apply_box_snapshot(self.app.trainer.snapshot_box(0))

    def choose_box(self, slot):
        self.app.box_tree.selection_set(str(slot))
        self.app.select_box_mon()

    def test_party_unknown_species_stays_in_slot_and_never_loads_image_or_formula(self):
        broken = bytearray(sample().raw)
        struct.pack_into("<H", broken, 32, 65535)
        with patch("trainer_app.icon_species", side_effect=AssertionError("image lookup")), patch.object(self.app.trainer, "validate_pokemon", side_effect=AssertionError("formula")):
            self.put_party([bytes(broken), bytes(broken)])
        self.assertEqual(self.app.party_tree.get_children(), ("0", "1"))
        self.assertIn("异常（只读）", self.app.party_tree.item("0", "values")[0])
        self.assertIn("65535", self.app.identity.get())
        self.assertIn("未验证", self.app.report.get("1.0", "end"))
        self.assertIn("disabled", self.app.ability_cb.state())
        self.assertIn("disabled", self.app.party_preview_button.state())
        self.assertIn("disabled", self.app.party_write_button.state())
        self.assertNotIn("disabled", self.app.party_delete_abnormal_button.state())
        with patch.object(self.app.trainer, "edit_pokemon") as edit:
            with self.assertRaisesRegex(ValueError, "异常成员"):
                self.app.prepare_mon()
            self.app.fill_pp()
            self.app.max_pp_ups()
            self.assertEqual(self.app.species.get(), "")
            edit.assert_not_called()

    def test_bad_egg_has_explicit_label_and_healthy_member_can_still_edit(self):
        broken = bytearray(sample().raw)
        broken[19] |= 1
        self.put_party([bytes(broken), sample().raw])
        self.assertIn("坏蛋（只读）", self.app.party_tree.item("0", "values")[0])
        self.app.party_tree.selection_set("1")
        self.app.select_mon()
        self.assertEqual(self.app.current_slot, 1)
        self.assertNotIn("disabled", self.app.ability_cb.state())
        self.assertNotIn("disabled", self.app.party_preview_button.state())
        self.assertNotIn("disabled", self.app.party_write_button.state())
        self.assertIn("disabled", self.app.party_delete_abnormal_button.state())
        self.assertTrue(self.app.prepare_mon()[0])

    def test_nonzero_species_zero_box_is_not_empty_and_keeps_original_slot(self):
        broken = bytearray(healthy_box())
        broken[28:30] = b"\0\0"
        self.put_box([bytes(broken), healthy_box()])
        self.assertIn("2 / 30", self.app.box_detail.get())
        self.choose_box(0)
        self.assertIn("异常（只读）", self.app.box_tree.item("0", "values")[1])
        self.assertIn("原始可读字段", self.app.box_detail.get())
        self.assertEqual(self.app.box_editor_values, [])
        self.assertEqual(self.app.box_selected_refs, {})
        self.assertNotIn("disabled", self.app.box_delete_abnormal_button.state())
        self.assertIsNone(self.app.edit_box_dialog())
        self.assertEqual(self.mem.read(self.address, 58), bytes(broken))

    def test_unknown_move_box_does_not_call_describe_and_rejects_batch(self):
        with patch.object(BoxPokemon, "describe", side_effect=AssertionError("derived fields")):
            self.put_box([packed_box()])
            self.choose_box(0)
        self.assertIn("1023", self.app.box_detail.get())
        with patch.object(self.app.trainer, "prepare_box_batch") as prepare, patch("trainer_app.messagebox.showerror") as error:
            self.app.run_box_batch("move")
            self.app.stage_box_selection()
            prepare.assert_not_called()
            self.assertEqual(error.call_count, 2)
        menu = self.app.make_box_batch_menu(())
        self.addCleanup(menu.destroy)
        self.assertEqual(menu.entrycget(0, "state"), "disabled")
        self.assertEqual(menu.entrycget(4, "state"), "normal")
        self.assertEqual(menu.entrycget(5, "state"), "normal")

    def test_image_loader_only_receives_healthy_records(self):
        unknown = bytearray(healthy_box())
        unknown[28:30] = b"\xff\xff"
        self.put_box([bytes(unknown), healthy_box()])
        self.app.run = lambda label, job, done: done(job())
        with patch("trainer_app.read_icons", return_value={}) as read:
            self.app.load_icons()
        pokemon = read.call_args.args[2]
        self.assertTrue(pokemon)
        self.assertTrue(all(self.app.record_diagnosis(mon)["editable"] for mon in pokemon))
        self.assertFalse(any(mon.species == 65535 for mon in pokemon))

    def test_cancel_prepares_then_confirms_and_never_commits(self):
        self.put_box([packed_box()])
        self.choose_box(0)
        self.app.run = lambda label, job, done: done(job())
        with patch.object(self.app.trainer, "prepare_abnormal_deletion", wraps=self.app.trainer.prepare_abnormal_deletion) as prepare, patch.object(self.app.trainer, "commit_abnormal_deletion") as commit, patch("trainer_app.messagebox.askokcancel", return_value=False) as confirm:
            self.app.delete_abnormal("box")
        prepare.assert_called_once_with("box", 0, box_index=0)
        commit.assert_not_called()
        self.assertIn("第 1 盒第 1 格", confirm.call_args.args[1])
        self.assertIn("完整 .sav", confirm.call_args.args[1])
        self.assertIn("永久删除", confirm.call_args.args[1])
        self.assertEqual(self.mem.writes, 0)

    def test_delete_uses_workers_confirmation_on_main_thread_and_refreshes_target_box(self):
        self.put_box([packed_box()])
        self.choose_box(0)
        main_thread = threading.get_ident()
        seen = []
        diagnosis = self.app.record_diagnosis(BoxPokemon(packed_box()), "box")
        def prepare(kind, slot, box_index=None):
            seen.append(("prepare", threading.get_ident()))
            return {"diagnosis": diagnosis, "summary": "清空异常槽", "kind": kind, "slot": slot, "box_index": box_index}
        def confirm(*args, **kwargs):
            seen.append(("confirm", threading.get_ident()))
            return True
        def commit(prepared):
            seen.append(("commit", threading.get_ident()))
            self.mem.put(self.address, b"\0" * 58)
            return {"changed": True, "backup": "transaction.json"}
        with patch.object(self.app.trainer, "prepare_abnormal_deletion", side_effect=prepare), patch.object(self.app.trainer, "commit_abnormal_deletion", side_effect=commit), patch("trainer_app.messagebox.askokcancel", side_effect=confirm), patch.object(self.app, "request_icons"):
            self.app.delete_abnormal("box")
            deadline = time.monotonic() + 5
            while (self.app.busy or len(seen) < 3) and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.005)
        self.assertFalse(self.app.busy)
        self.assertEqual([name for name, _ in seen], ["prepare", "confirm", "commit"])
        self.assertNotEqual(seen[0][1], main_thread)
        self.assertEqual(seen[1][1], main_thread)
        self.assertNotEqual(seen[2][1], main_thread)
        self.assertEqual(self.app.box_snapshot["index"], 0)
        self.assertEqual(self.app.box_snapshot["pokemon"][0].raw, b"\0" * 58)

    def test_diagnosis_buttons_are_busy_protected_and_thaw_reapplies_state(self):
        self.put_box([packed_box()])
        self.choose_box(0)
        self.app.busy = True
        self.app.freeze_inputs()
        self.app.update_integrity_actions()
        self.assertIn("disabled", self.app.box_delete_abnormal_button.state())
        with patch.object(self.app.trainer, "prepare_abnormal_deletion") as prepare:
            self.app.delete_abnormal("box")
            self.assertIsNone(self.app.show_record_diagnosis("box"))
            prepare.assert_not_called()
        self.app.busy = False
        self.app.thaw_inputs()
        self.app.update_integrity_actions()
        self.assertNotIn("disabled", self.app.box_delete_abnormal_button.state())
        self.choose_box(1)
        self.assertIn("disabled", self.app.box_delete_abnormal_button.state())

    def test_rejected_deletion_shows_error_and_refreshes_target_without_clearing_record(self):
        self.put_box([packed_box()])
        self.choose_box(0)
        self.app.run = lambda label, job, done: done(job())
        with patch.object(self.app.trainer, "commit_abnormal_deletion", side_effect=ValueError("原槽已经变化")), patch.object(self.app.trainer, "snapshot_box", wraps=self.app.trainer.snapshot_box) as read, patch("trainer_app.messagebox.askokcancel", return_value=True), patch("trainer_app.messagebox.showerror") as error:
            self.app.delete_abnormal("box")
        self.assertGreaterEqual(read.call_count, 2)
        self.assertEqual(read.call_args.args, (0,))
        error.assert_called_once()
        self.assertIn("原槽已经变化", self.app.status.get())
        self.assertEqual(self.app.box_snapshot["pokemon"][0].raw, packed_box())
        self.assertEqual(self.mem.writes, 0)

    def assert_double_failure_retires_target(self, kind):
        if kind == "party":
            raw = bytearray(sample().raw)
            raw[19] |= 1
            self.put_party([bytes(raw), sample().raw])
            reader = "snapshot"
            diagnostic_button = self.app.party_diagnosis_button
            deletion_button = self.app.party_delete_abnormal_button
        else:
            self.put_box([packed_box()])
            self.choose_box(0)
            reader = "snapshot_box"
            diagnostic_button = self.app.box_diagnosis_button
            deletion_button = self.app.box_delete_abnormal_button
        prepared = {"diagnosis": self.app.selected_abnormal(kind)[2], "summary": "删除异常目标"}
        self.app.run = lambda label, job, done: done(job())
        with patch.object(self.app.trainer, "prepare_abnormal_deletion", return_value=prepared) as prepare, patch.object(self.app.trainer, "commit_abnormal_deletion", side_effect=RuntimeError("write outcome unknown; backup=private-backup.json")) as commit, patch.object(self.app.trainer, reader, side_effect=ConnectionError("refresh disconnected")), patch("trainer_app.messagebox.askokcancel", return_value=True), patch("trainer_app.messagebox.showerror") as error, patch.object(self.app, "apply_snapshot") as apply_party, patch.object(self.app, "apply_box_snapshot") as apply_box:
            self.app.delete_abnormal(kind)
            commit.assert_called_once_with(prepared)
            apply_party.assert_not_called()
            apply_box.assert_not_called()
            error.assert_called_once()
            for value in ("write outcome unknown", "private-backup.json", "refresh disconnected"):
                self.assertIn(value, error.call_args.args[1])
                self.assertIn(value, self.app.last_error)
                self.assertIn(value, self.app.status.get())
            self.assertIn("disabled", diagnostic_button.state())
            self.assertIn("disabled", deletion_button.state())
            self.app.delete_abnormal(kind)
            prepare.assert_called_once()
            commit.assert_called_once()
        self.assertEqual(self.mem.writes, 0)

    def test_box_double_failure_preserves_commit_error_and_backup_and_retires_target(self):
        self.assert_double_failure_retires_target("box")
        self.assertIsNone(self.app.box_snapshot)
        self.assertEqual(self.app.box_selected_refs, {})
        self.assertEqual(self.app.box_staged_refs, {})
        self.assertIn("disabled", self.app.box_tree.cards[0].state())
        self.put_box([healthy_box()])
        self.choose_box(0)
        self.assertNotIn("disabled", self.app.box_tree.cards[0].state())
        self.assertTrue(self.app.box_editor_values)

    def test_party_double_failure_preserves_commit_error_and_backup_and_retires_target(self):
        self.assert_double_failure_retires_target("party")
        self.assertIsNone(self.app.snapshot)
        self.assertIsNone(self.app.current_slot)
        self.assertIn("disabled", self.app.party_preview_button.state())
        self.assertIn("disabled", self.app.party_write_button.state())
        self.assertIn("disabled", self.app.party_tree.cards[0].state())
        with self.assertRaises(ValueError):
            self.app.prepare_mon()
        self.put_party([sample().raw])
        self.assertNotIn("disabled", self.app.party_preview_button.state())
        self.assertNotIn("disabled", self.app.party_write_button.state())
        self.assertTrue(self.app.prepare_mon()[0])

    def test_successful_commit_with_failed_refresh_retains_returned_backup_path(self):
        self.put_box([packed_box()])
        self.choose_box(0)
        prepared = {"diagnosis": self.app.selected_abnormal("box")[2], "summary": "删除异常目标"}
        self.app.run = lambda label, job, done: done(job())
        with patch.object(self.app.trainer, "prepare_abnormal_deletion", return_value=prepared), patch.object(self.app.trainer, "commit_abnormal_deletion", return_value={"changed": True, "backup": "committed-backup.json"}) as commit, patch.object(self.app.trainer, "snapshot_box", side_effect=ConnectionError("refresh disconnected")), patch("trainer_app.messagebox.askokcancel", return_value=True), patch("trainer_app.messagebox.showerror") as error:
            self.app.delete_abnormal("box")
        commit.assert_called_once()
        self.assertIn("committed-backup.json", error.call_args.args[1])
        self.assertIn("refresh disconnected", error.call_args.args[1])
        self.assertIsNone(self.app.box_snapshot)
        self.assertIn("disabled", self.app.box_delete_abnormal_button.state())


if __name__ == "__main__":
    unittest.main()
