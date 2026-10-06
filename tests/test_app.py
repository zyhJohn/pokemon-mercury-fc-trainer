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
    def test_pp_ups_and_gender_are_drafts_with_updated_limits(self):
        from pokemon_data import gender

        self.app.max_pp_ups()
        self.assertEqual([var.get() for var in self.app.pp_up_vars], ["3"] * 4)
        self.app.fill_pp()
        self.assertEqual(
            [var.get() for var in self.app.pp_vars],
            [
                str(self.app.profile["moves"][str(move)]["pp"] * 8 // 5)
                for move in sample().moves
            ],
        )
        self.app.target_gender.set("雌性")
        self.app.preview()
        updated = Pokemon(self.app.prepare_mon()[0][0][2])
        self.assertEqual(gender(updated.pid, 31), "雌性")
        self.assertEqual(updated.pp_ups, (3, 3, 3, 3))
        self.assertIn("上限 16", self.app.pp_max_vars[0].get())
        self.assertEqual(self.mem.writes, 0)
        self.assertNotEqual(self.app.party_form_values(), self.app.party_form_original)
        self.app.move_vars[0].set("33 - 撞击")
        self.assertEqual(self.app.pp_up_vars[0].get(), "0")
        self.app.move_vars[0].set("0 - 无")
        self.assertEqual(self.app.pp_vars[0].get(), "0")

    def test_gender_choices_follow_fixed_species_and_preserve_refresh_draft(self):
        self.app.species.set("201 - 未知图腾")
        self.assertEqual(tuple(self.app.gender_cb["values"]), ("保持当前", "无性别"))
        self.app.species.set("160 - 大力鳄")
        self.app.target_gender.set("雌性")
        self.app.pp_up_vars[0].set("2")
        self.app.run = lambda label, job, done: done(job())
        self.app.refresh(after=lambda: None)
        self.assertEqual(self.app.target_gender.get(), "雌性")
        self.assertEqual(self.app.pp_up_vars[0].get(), "2")
        self.assertEqual(self.mem.writes, 0)

    def test_resizing_basic_form_handles_labels_with_default_empty_wraplength(self):
        self.app.nb.select(self.app.tab_party)
        self.app.party_pages.select(self.app.tab_party_basic)
        form = self.app.tab_party_basic
        self.assertEqual(str(self.app.detail_image.cget("wraplength")), "")
        form._resize(type("Resize", (), {"width": 640})())
        self.root.update_idletasks()
        self.assertEqual(form.canvas.itemcget(form.window_id, "width"), "640")
        self.assertGreaterEqual(self.app.detail_image.winfo_reqheight(), 1)

    def test_empty_move_zero_pp_and_egg_control_are_drafts(self):
        self.app.pp_vars[0].set("50")
        self.app.move_vars[0].set("0 - 无")
        self.assertEqual(self.app.pp_vars[0].get(), "0")
        updated = Pokemon(self.app.prepare_mon()[0][0][2])
        self.assertEqual(updated.pp[0], 0)
        self.assertEqual(updated.moves[0], 0)
        self.assertEqual(self.mem.writes, 0)

    def test_game_value_egg_shortcut_preserves_normal_member_and_creates_backup(self):
        snapshot = self.app.trainer.snapshot()
        egg_patches, _ = self.app.trainer.edit_pokemon(snapshot, 0, egg=True)
        self.mem.put(PARTY, egg_patches[0][2] + sample().raw)
        self.mem.put(PARTY_COUNT, b"\2")
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.app.run = lambda label, job, done: done(job())
        with patch("trainer_app.messagebox.askokcancel", return_value=True):
            self.app.ready_party_eggs()
        egg = Pokemon(self.mem.read(PARTY, 100))
        self.assertTrue(egg.egg)
        self.assertEqual(egg.friendship, 0)
        self.assertEqual(self.mem.read(PARTY + 100, 100), sample().raw)
        self.assertTrue(list(Path(self.temp.name).glob("*.json")))

    def test_connect_automatically_selects_v12_and_keeps_offline_rtc_profile(self):
        from rom_versions import load_profile
        from tests.test_rom_versions import install_profile

        profile = load_profile(crc="4755f497")
        memory = Memory()
        install_profile(memory, profile)
        memory.put(SAVE_POINTER, struct.pack("<I", 0x0202552C))
        memory.put(PARTY_COUNT, b"\1")
        memory.put(PARTY, sample().raw)
        memory.connect = lambda: None
        memory.close = lambda: None
        self.app.run = lambda label, job, done: done(job())
        old_rtc = self.app.rtc_profile
        with patch("trainer_app.MemClient", return_value=memory):
            self.app.connect()
        self.assertEqual(self.app.profile["rom_crc32"], "4755f497")
        self.assertIn("1.2", self.root.title())
        self.assertIs(self.app.rtc_profile, old_rtc)
        self.assertTrue(self.app.icon_request_pending)
        self.assertEqual(memory.writes, 0)

    def test_icons_start_enabled_and_user_disable_survives_refresh(self):
        self.assertTrue(self.app.icons_enabled)
        self.assertEqual(self.app.icon_button.cget("text"), "关闭微缩图")
        self.app.toggle_icons()
        self.assertFalse(self.app.icons_enabled)
        self.assertEqual(self.app.icon_button.cget("text"), "显示微缩图")
        self.app.run = lambda label, job, done: done(job())
        self.app.refresh()
        self.assertFalse(self.app.icon_request_pending)
        self.assertFalse(self.app.icons_enabled)
        self.assertEqual(self.mem.writes, 0)

    def test_egg_state_is_a_preview_and_preserves_iv_draft(self):
        self.app.iv[0].set("31")
        self.app.egg.set(True)
        self.app.preview()
        updated = Pokemon(self.app.prepare_mon()[0][0][2])
        self.assertTrue(updated.egg)
        self.assertEqual(updated.level, 1)
        self.assertEqual(updated.ivs[0], 31)
        self.assertEqual(self.mem.read(PARTY, 100), sample().raw)
        self.assertEqual(self.mem.writes, 0)

    def test_automatic_icons_queue_after_refresh_and_ignore_old_connection(self):
        self.app.run = lambda label, job, done: done(job())
        self.app.refresh()
        self.assertTrue(self.app.icon_request_pending)
        callbacks = []
        self.app.run = lambda label, job, done: callbacks.append(done)
        self.app.load_icons()
        self.app.trainer = None
        callbacks[0](({201: b"not valid PNG"}, None))
        self.assertFalse(self.app.icon_images)
        self.assertEqual(self.mem.writes, 0)

    def test_box_move_selects_destination_and_egg_state_only_previews(self):
        from tests.test_box import packed_box
        from box_data import BoxPokemon

        raw = bytearray(packed_box())
        raw[39:44] = (757).to_bytes(5, "little")
        for sig in self.app.profile["storage"]["signatures"]:
            self.mem.put(sig["address"], bytes.fromhex(sig["hex"]))
        source = self.app.profile["storage"]["box_addresses"][0]
        self.mem.put(source, raw)
        self.app.apply_box_snapshot(self.app.trainer.snapshot_box(0))
        self.app.box_tree.selection_set("0")
        self.app.select_box_mon()

        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        checkbox = next(
            w
            for w in children(self.app.box_editor)
            if w.winfo_class() == "TCheckbutton" and w.cget("text").startswith("蛋状态")
        )
        checkbox.invoke()
        self.assertTrue(self.app.box_editor_values[1].get())
        self.assertEqual(self.mem.writes, 0)
        self.app.box_editor_values[1].set(False)
        self.app.run = lambda label, job, done: done(job())
        self.app.box_move_target.set("2")
        with patch("trainer_app.messagebox.askokcancel", return_value=True):
            self.app.move_box_mon()
        self.assertEqual(self.app.box_snapshot["index"], 1)
        self.assertEqual(self.mem.read(source, 58), b"\0" * 58)
        self.assertEqual(self.app.box_snapshot["pokemon"][0].raw, bytes(raw))
        self.assertEqual(BoxPokemon(self.app.box_snapshot["raw"][:58]).species, 160)

    def test_daily_date_repair_requires_preview_and_preserves_pokemon_draft(self):
        from tests.test_time import put_time

        put_time(self.mem, self.app.profile)
        self.app.run = lambda label, job, done: done(job())
        self.app.read_time()
        self.assertIn("2026-10-04", self.app.daily_detail.get())
        self.assertIn("未来", self.app.daily_detail.get())
        with patch("trainer_app.messagebox.showinfo") as info:
            self.app.write_daily_repair()
            info.assert_called_once()
        self.assertEqual(self.mem.writes, 0)
        self.app.iv[0].set("31")
        self.app.preview_daily_repair()
        self.assertIn("2026-10-01", self.app.daily_detail.get())
        self.app.write_daily_repair()
        self.assertIsNone(self.app.daily_repair_ready)
        self.assertEqual(self.app.trainer.snapshot_time()["daily_date"].day, 1)
        self.assertEqual(self.app.iv[0].get(), "31")

    def test_time_page_reads_weekday_and_writes_separate_playtime(self):
        from tests.test_time import put_time

        put_time(self.mem, self.app.profile)
        self.app.run = lambda label, job, done: done(job())
        self.app.read_time()
        self.assertIn("周五", self.app.time_detail.get())
        self.assertEqual(self.app.play_hours.get(), "26")
        self.app.play_hours.set("12")
        self.app.play_minutes.set("34")
        self.app.play_seconds.set("56")
        self.app.iv[0].set("31")
        self.app.write_playtime()
        self.assertEqual(
            self.app.trainer.snapshot_time()["playtime"], (12, 34, 56, 255)
        )
        self.assertEqual(self.app.iv[0].get(), "31")
        self.assertEqual(self.mem.read(PARTY, 100), sample().raw)

    def test_rtc_draft_preview_and_calibration_never_write(self):
        self.app.rtc_target.set("2026-10-04 00:00:00")
        self.assertIn("周日", self.app.rtc_preview.get())
        self.app.fill_time_now()
        self.assertTrue(self.app.rtc_calibrate)
        self.assertIn("偏移为 0", self.app.rtc_preview.get())
        self.app.rtc_target.set("2026-10-05 00:00:00")
        self.assertFalse(self.app.rtc_calibrate)
        self.assertIn("周一", self.app.rtc_preview.get())
        self.app.rtc_weekday.set("周日")
        self.app.choose_time_weekday()
        self.assertEqual(self.app.rtc_target.get(), "2026-10-11 00:00:00")
        self.assertIn("周日", self.app.rtc_preview.get())
        self.app.rtc_target.set("2026-02-29 00:00:00")
        self.assertIn("日期必须有效", self.app.rtc_preview.get())
        self.assertEqual(self.mem.writes, 0)

    def test_saved_rtc_read_write_restore_without_bridge_connection(self):
        from tests.test_time import make_save
        from clock_data import digest

        with tempfile.TemporaryDirectory(prefix="中文 时间 ") as folder:
            root = Path(folder)
            rom, save = root / "测试.gba", root / "游戏.sav"
            rom.write_bytes(b"test ROM")
            before = make_save()
            save.write_bytes(before)
            self.app.profile = {"rom_sha256": digest(rom.read_bytes())}
            self.app.trainer = None
            self.app.rtc_rom_path.set(str(rom))
            self.app.rtc_save_path.set(str(save))
            self.app.time_backup_dir = lambda: root / "backups"
            self.app.run = lambda label, job, done: done(job())
            with patch("trainer_app.load_profile", return_value=self.app.profile):
                self.app.read_saved_time()
            self.assertIn("周五", self.app.rtc_detail.get())
            self.app.rtc_target.set("2026-10-04 00:00:00")
            self.app.write_saved_time()
            self.assertIn("周日", self.app.rtc_detail.get())
            record = next((root / "backups").glob("*.json"))
            with patch(
                "trainer_app.filedialog.askopenfilename", return_value=str(record)
            ):
                self.app.restore_saved_time()
            self.assertEqual(save.read_bytes(), before)
            self.assertEqual(self.mem.writes, 0)

    def test_values_page_edits_verified_points_and_extended_coins(self):
        for key, value in [
            ("coins", 735),
            ("beauty_points", 5),
            ("bracer_points", 220),
        ]:
            field = self.app.profile["economy"][key]
            self.mem.put(field["address"], value.to_bytes(field["size"], "little"))
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.assertEqual(
            (
                self.app.coins.get(),
                self.app.beauty_points.get(),
                self.app.bracer_points.get(),
            ),
            ("735", "5", "220"),
        )
        self.app.coins.set("100000")
        self.app.beauty_points.set("6")
        self.app.bracer_points.set("221")
        self.app.run = lambda label, job, done: done(job())
        self.app.write_values()
        self.assertEqual(
            (
                self.app.coins.get(),
                self.app.beauty_points.get(),
                self.app.bracer_points.get(),
            ),
            ("100000", "6", "221"),
        )
        self.assertEqual(self.mem.read(PARTY, 100), sample().raw)

    def test_sort_button_writes_current_pocket_preserves_party_draft(self):
        p = self.app.profile["pockets"][0]
        self.mem.put(p["address"], struct.pack("<HHHH", 15, 3, 13, 4))
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.app.iv[0].set("31")
        self.app.run = lambda label, job, done: done(job())
        button = next(b for b in self.app.buttons if b.cget("text") == "按编号排序")
        button.invoke()
        self.assertEqual(
            self.mem.read(p["address"], 8), struct.pack("<HHHH", 13, 4, 15, 3)
        )
        self.assertEqual(self.app.iv[0].get(), "31")
        self.assertEqual(self.mem.read(PARTY, 100), sample().raw)
        self.app.pocket.set("精灵球")
        writes = self.mem.writes
        with patch("trainer_app.messagebox.showerror") as error:
            button.invoke()
            error.assert_called_once()
        self.assertEqual(self.mem.writes, writes)

    def test_pc_ability_preview_tracks_draft_without_writing(self):
        from tests.test_box import packed_box
        from box_data import BoxPokemon

        raw = bytearray(packed_box())
        struct.pack_into("<H", raw, 28, 133)
        raw[39:44] = (757).to_bytes(5, "little")
        raw[57] &= 127
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
        self.app.box_editor_values[16].set("2 - 隐藏特性")
        self.assertNotEqual(
            tuple(v.get() for v in self.app.box_editor_values),
            self.app.box_editor_original,
        )

        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        widgets = list(children(self.app.box_editor))
        next(
            w
            for w in widgets
            if w.winfo_class() == "TButton" and w.cget("text") == "检查与预览"
        ).invoke()
        texts = [
            self.root.getvar(w.cget("textvariable"))
            for w in widgets
            if w.winfo_class() == "TLabel" and w.cget("textvariable")
        ]
        self.assertTrue(
            any("隐藏标志：0 → 1" in value and "PID：" in value for value in texts)
        )
        self.assertEqual(self.mem.writes, 0)

    def test_held_form_party_and_pc_preview_explain_species_changes(self):
        from tests.test_box import packed_box
        from box_data import BoxPokemon

        patches, _ = self.app.trainer.edit_pokemon(self.app.snapshot, 0, species=546)
        self.mem.put(PARTY, patches[0][2])
        self.app.apply_snapshot(self.app.trainer.snapshot())
        self.app.held.set("490")
        patches, _ = self.app.prepare_mon()
        self.assertEqual(Pokemon(patches[0][2]).species, 720)
        self.app.preview()
        self.assertIn("持物形态", self.app.detail_preview.get())
        raw = bytearray(packed_box())
        raw[39:44] = (757).to_bytes(5, "little")
        struct.pack_into("<H", raw, 28, 990)
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
        self.app.box_editor_values[9].set("507 - 格斗存储碟")

        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)

        widgets = list(children(self.app.box_editor))
        button = next(
            widget
            for widget in widgets
            if widget.winfo_class() == "TButton" and widget.cget("text") == "检查与预览"
        )
        button.invoke()
        labels = [
            widget.cget("textvariable")
            for widget in widgets
            if widget.winfo_class() == "TLabel" and widget.cget("textvariable")
        ]
        texts = [self.root.getvar(var) for var in labels]
        self.assertTrue(
            any("持物形态" in value and "银伴战兽" in value for value in texts)
        )
        self.assertEqual(self.mem.writes, 0)

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
