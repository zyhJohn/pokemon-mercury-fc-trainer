"""UI for creating a verified Mercury FC Pokemon in a PC or party slot."""

import tkinter as tk
from tkinter import messagebox, ttk


STAT_NAMES = ("HP", "攻击", "防御", "速度", "特攻", "特防")
MODES = ("盒子空槽", "队伍空位", "顶替队伍成员")


class CreationPage:
    def __init__(self, app, notebook):
        self.app = app
        self.notebook = notebook
        self.prepared = None
        self.preview_trainer = None
        self.preview_key = None
        self._selected_target = None
        self._frozen = False
        self._tracked = []
        self._inputs = []
        self.tab = ttk.Frame(notebook, padding=8)
        notebook.add(self.tab, text="自定义创建")
        self.detail = tk.StringVar(value="填写草稿并预览；写入前会核对当前 ROM 和目标槽位。")

        self.mode = tk.StringVar(value=MODES[0])
        self.box = tk.StringVar(value="1")
        self.replacement = tk.StringVar(value="")
        self.species = tk.StringVar(value="1")
        self.level = tk.StringVar(value="5")
        self.nickname = tk.StringVar(value="MON")
        self.ot_name = tk.StringVar(value="OT")
        self.pid = tk.StringVar(value="0")
        self.tid = tk.StringVar(value="0")
        self.sid = tk.StringVar(value="0")
        self.ot_gender = tk.StringVar(value="0 - 男")
        self.moves = [tk.StringVar(value="1" if i == 0 else "0") for i in range(4)]
        self.pp_ups = [tk.StringVar(value="0") for _ in range(4)]
        self.ivs = [tk.StringVar(value="0") for _ in range(6)]
        self.evs = [tk.StringVar(value="0") for _ in range(6)]
        self.held = tk.StringVar(value="0 - 无")
        self.friendship = tk.StringVar(value="自动")
        self.ball = tk.StringVar(value="4")
        self.met_location = tk.StringVar(value="0")
        self.met_level = tk.StringVar(value="自动")
        self.egg = tk.BooleanVar(value=False)
        self.ability_slot = tk.StringVar(value="自动")
        self.experience = tk.StringVar(value="自动")

        target = ttk.LabelFrame(self.tab, text="目标", padding=6)
        target.pack(fill="x")
        self._choice(target, "投放到", self.mode, MODES, 0, 0, readonly=True)
        self.box_widget = self._choice(target, "目标盒", self.box,
                                       [str(i) for i in range(1, 26)], 0, 2, readonly=True)
        self.replacement_widget = self._choice(
            target, "顶替槽", self.replacement, [str(i) for i in range(1, 7)],
            0, 4, readonly=True)
        self.replacement_widget.configure(state="disabled")

        scroll_frame = ttk.Frame(self.tab)
        scroll_frame.pack(fill="both", expand=True, pady=7)
        canvas = tk.Canvas(scroll_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        form = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        form.columnconfigure(0, weight=1, uniform="creation-form")
        form.columnconfigure(1, weight=1, uniform="creation-form")
        left = ttk.LabelFrame(form, text="基本与来源", padding=6)
        right = ttk.LabelFrame(form, text="招式与能力", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        self.species_widget = self._choice(left, "物种", self.species, [], 0, 0)
        self._entry(left, "等级", self.level, 1, 0)
        self._entry(left, "昵称", self.nickname, 2, 0)
        self._entry(left, "原训练师", self.ot_name, 3, 0)
        self._entry(left, "PID（十进制或0x）", self.pid, 4, 0)
        self._entry(left, "OT TID", self.tid, 5, 0)
        self._entry(left, "OT SID", self.sid, 6, 0)
        self._choice(left, "OT性别", self.ot_gender, ["0 - 男", "1 - 女"], 7, 0, readonly=True)
        self.held_widget = self._choice(left, "携带道具", self.held, [], 8, 0)
        self.ball_widget = self._choice(left, "捕获球", self.ball, [], 9, 0)
        self.location_widget = self._choice(left, "相遇地点", self.met_location, [], 10, 0)
        self._entry(left, "相遇等级", self.met_level, 11, 0)
        self._entry(left, "亲密度/孵化周期", self.friendship, 12, 0)
        self._entry(left, "经验值", self.experience, 13, 0)
        egg_box = ttk.Checkbutton(left, text="蛋（等级须为1）", variable=self.egg)
        egg_box.grid(row=14, column=0, columnspan=2, sticky="w", pady=3)
        self._inputs.append(egg_box)
        self._tracked.append(self.egg)

        self.move_widgets = []
        for i in range(4):
            widget = self._choice(right, f"招式 {i + 1}", self.moves[i], [], i, 0, width=12)
            self.move_widgets.append(widget)
            self._choice(right, "PP提升", self.pp_ups[i], ["0", "1", "2", "3"],
                         i, 2, readonly=True, width=3)
        for i, name in enumerate(STAT_NAMES):
            self._entry(right, f"{name} IV", self.ivs[i], i + 4, 0, width=5)
            self._entry(right, f"{name} EV", self.evs[i], i + 4, 2, width=5)
        self.ability_widget = self._choice(
            right, "特性槽", self.ability_slot, ["自动", "0", "1", "2"],
            10, 0, readonly=True, width=12)
        ttk.Label(right, text="物种、招式、道具和地点可输入编号或从名称列表选择。",
                  wraplength=330).grid(row=11, column=0, columnspan=4, sticky="w", pady=8)

        actions = ttk.Frame(self.tab)
        actions.pack(fill="x")
        self.player_button = ttk.Button(actions, text="读取玩家OT填入草稿", command=self.fill_player_ot)
        self.player_button.pack(side="left")
        self.preview_button = ttk.Button(actions, text="检查并预览", command=self.preview)
        self.preview_button.pack(side="left", padx=8)
        self.commit_button = ttk.Button(actions, text="确认创建并写入", command=self.commit)
        self.commit_button.pack(side="left")
        ttk.Label(self.tab, textvariable=self.detail, wraplength=1050,
                  justify="left").pack(fill="x", anchor="w", pady=8)
        self._refresh_choices()
        for var in self._tracked:
            var.trace_add("write", lambda *_: self.invalidate())
        for var in (self.mode, self.box, self.replacement):
            var.trace_add("write", lambda *_: self._clear_selected_target())
        self.mode.trace_add("write", lambda *_: self._target_state())
        self.species.trace_add("write", lambda *_: self._ability_choices())
        self._target_state()
        self._actions()

    def _entry(self, parent, label, var, row, column, width=17):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=3, pady=2)
        widget = ttk.Entry(parent, textvariable=var, width=width)
        widget.grid(row=row, column=column + 1, sticky="ew", padx=3, pady=2)
        self._tracked.append(var)
        self._inputs.append(widget)
        return widget

    def _choice(self, parent, label, var, values, row, column, readonly=False, width=18):
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=3, pady=2)
        widget = ttk.Combobox(parent, textvariable=var, values=values, width=width,
                              state="readonly" if readonly else "normal")
        widget.grid(row=row, column=column + 1, sticky="ew", padx=3, pady=2)
        self._tracked.append(var)
        self._inputs.append(widget)
        return widget

    def _refresh_choices(self):
        trainer = self.app.trainer
        if trainer is None:
            return
        profile = trainer.profile
        names = self.app.names
        self.species_widget.configure(values=self._labels(profile["species"], names["breeds"]))
        moves = {"0": None, **profile["moves"]}
        labels = self._labels(moves, names["skills"], zero="无")
        for widget in self.move_widgets:
            widget.configure(values=labels)
        items = {"0": None, **{k: v for k, v in profile["items"].items()
                               if v["pocket"] not in (2, 4)}}
        self.held_widget.configure(values=self._labels(items, names["items"], zero="无"))
        balls = {k: v for k, v in profile["items"].items() if v["pocket"] == 3}
        self.ball_widget.configure(values=self._labels(balls, names["items"]))
        locations = {"0": "未设置", **{str(i): label for i, label in profile.get("met_locations", {}).items()
                     if int(i) not in profile.get("invalid_location_ids", ())}
                     }
        self.location_widget.configure(values=self._labels(locations, locations))
        self._ability_choices()

    @staticmethod
    def _labels(records, names, zero=None):
        return [f"{i} - {zero if i == 0 and zero else names.get(str(i), '未收录')}"
                for i in sorted(map(int, records))]

    def _ability_choices(self):
        trainer = self.app.trainer
        if trainer is None:
            return
        try:
            species = self._lookup(self.species, trainer.profile["species"],
                                   self.app.names["breeds"], "物种")
            abilities = trainer.profile["species"][str(species)]["abilities"]
        except ValueError:
            return
        values = ["自动"] + [f"{i} - {self.app.names['specs'].get(str(a), a)}"
                               for i, a in enumerate(abilities) if a]
        self.ability_widget.configure(values=values)
        current = self.ability_slot.get().strip()
        if current != "自动" and current.split(" - ", 1)[0] not in {str(i) for i, a in enumerate(abilities) if a}:
            self.ability_slot.set("自动")

    @staticmethod
    def _number(text, label):
        value = text.strip()
        try:
            return int(value, 16 if value.lower().startswith("0x") else 10)
        except ValueError as exc:
            raise ValueError(f"{label}须填写整数") from exc

    def _lookup(self, variable, records, names, label):
        value = variable.get().strip()
        prefix = value.split(" - ", 1)[0]
        try:
            number = self._number(prefix, label)
        except ValueError:
            matches = [int(k) for k in records if str(names.get(str(k), "")).casefold() == value.casefold()]
            if len(matches) != 1:
                raise ValueError(f"{label}请选择已核验名称或填写编号")
            number = matches[0]
        if str(number) not in records:
            raise ValueError(f"{label}编号不在当前ROM已核验列表")
        return number

    def _optional(self, var, label):
        value = var.get().strip()
        return None if value in ("", "自动") else self._number(value, label)

    def _trainer_id(self, var, label):
        value = self._number(var.get(), label)
        if not 0 <= value <= 65535:
            raise ValueError(f"{label}须在0～65535之间")
        return value

    def draft(self):
        trainer = self.app.trainer
        if trainer is None:
            raise ValueError("请先连接游戏")
        profile = trainer.profile
        names = self.app.names
        species = self._lookup(self.species, profile["species"], names["breeds"], "物种")
        moves = {"0": None, **profile["moves"]}
        items = {"0": None, **profile["items"]}
        locations = {"0": "未设置", **{str(k): v for k, v in profile.get("met_locations", {}).items()
                     if int(k) not in profile.get("invalid_location_ids", ())}}
        ability = self.ability_slot.get().split(" - ", 1)[0]
        return {
            "species": species, "level": self._number(self.level.get(), "等级"),
            "nickname": self.nickname.get(), "ot_name": self.ot_name.get(),
            "pid": self._number(self.pid.get(), "PID"),
            "otid": self._trainer_id(self.tid, "OT TID") |
                    (self._trainer_id(self.sid, "OT SID") << 16),
            "ot_gender": self._number(self.ot_gender.get().split(" - ", 1)[0], "OT性别"),
            "moves": [self._lookup(v, moves, names["skills"], f"招式{i + 1}")
                      for i, v in enumerate(self.moves)],
            "pp_ups": [self._number(v.get(), "PP提升") for v in self.pp_ups],
            "ivs": [self._number(v.get(), "IV") for v in self.ivs],
            "evs": [self._number(v.get(), "EV") for v in self.evs],
            "held": self._lookup(self.held, items, names["items"], "携带道具"),
            "friendship": self._optional(self.friendship, "亲密度"),
            "ball": self._lookup(self.ball, items, names["items"], "捕获球"),
            "met_location": self._lookup(self.met_location, locations, locations, "相遇地点"),
            "met_level": self._optional(self.met_level, "相遇等级"),
            "egg": self.egg.get(),
            "ability_slot": None if ability == "自动" else self._number(ability, "特性槽"),
            "experience": self._optional(self.experience, "经验值"),
        }

    def _target(self):
        mode = self.mode.get()
        if mode not in MODES:
            raise ValueError("请选择创建目标")
        if mode == MODES[0]:
            box = self._number(self.box.get(), "目标盒") - 1
            slot = (self._selected_target[2] if self._selected_target is not None
                    and self._selected_target[:2] == ("pc_empty", box) else None)
            return mode, box, None, slot
        if mode == MODES[2]:
            if not self.replacement.get():
                raise ValueError("请明确选择要顶替的队伍槽位")
            return mode, None, self._number(self.replacement.get(), "顶替槽") - 1, None
        slot = (self._selected_target[2] if self._selected_target is not None
                and self._selected_target[0] == "party_empty" else None)
        return mode, None, None, slot

    def _clear_selected_target(self):
        self._selected_target = None

    def select_target(self, kind, *, slot, box=None):
        """Pin a zero-based empty PC slot or a zero-based party append slot."""
        if self.app.trainer is None or self.app.busy:
            raise ValueError("请先连接游戏并等待当前操作完成")
        if type(slot) is not int or not 0 <= slot < (30 if kind == "pc_empty" else 6):
            raise ValueError("创建目标槽位超出范围")
        if kind == "pc_empty":
            if type(box) is not int or not 0 <= box < 25:
                raise ValueError("目标盒子超出范围")
            self.invalidate()
            self.mode.set(MODES[0])
            self.box.set(str(box + 1))
            self.replacement.set("")
            self._selected_target = (kind, box, slot)
            self.detail.set(f"已选盒子 {box + 1} 第 {slot + 1} 格；请检查草稿并预览。")
        elif kind == "party_empty":
            if box is not None:
                raise ValueError("队伍空槽不应指定盒子")
            self.invalidate()
            self.mode.set(MODES[1])
            self.replacement.set("")
            self._selected_target = (kind, None, slot)
            self.detail.set(f"已选队伍第 {slot + 1} 位；只允许向当前队伍末尾空位追加。")
        else:
            raise ValueError("创建目标类型无效")
        self._actions()

    def open_for_target(self, kind, *, slot, box=None):
        self.select_target(kind, slot=slot, box=box)
        self.notebook.select(self.tab)

    def _target_state(self):
        mode = self.mode.get()
        self.box_widget.configure(state="disabled" if mode != MODES[0] or self._frozen else "readonly")
        self.replacement_widget.configure(state="readonly" if mode == MODES[2] and not self._frozen else "disabled")
        if mode != MODES[2] and self.replacement.get():
            self.replacement.set("")

    def _key(self):
        return self._target(), self.draft()

    def invalidate(self):
        self.prepared = None
        self.preview_trainer = None
        self.preview_key = None
        self.detail.set("草稿或目标已变化，请重新检查并预览。")
        self._actions()

    def refresh(self):
        self._refresh_choices()
        self._actions()

    def _actions(self):
        ready = self.app.trainer is not None and not self.app.busy and not self._frozen
        self.preview_button.configure(state="normal" if ready else "disabled")
        self.player_button.configure(state="normal" if ready else "disabled")
        self.commit_button.configure(state="normal" if ready and self.prepared is not None else "disabled")

    def freeze(self):
        if self._frozen:
            return
        self._frozen = True
        for widget in self._inputs:
            widget.configure(state="disabled")
        self._actions()

    def thaw(self):
        self._frozen = False
        for widget in self._inputs:
            widget.configure(state="readonly" if isinstance(widget, ttk.Combobox)
                             and widget in (self.ability_widget, self.replacement_widget,
                                            self.box_widget) else "normal")
        self.mode_widget_state()
        self._target_state()
        self._refresh_choices()
        self._actions()

    def mode_widget_state(self):
        # The target mode and fixed numeric choices must stay selection-only.
        for widget in self._inputs:
            if isinstance(widget, ttk.Combobox) and widget.cget("textvariable") in {
                str(self.mode), str(self.ot_gender),
                *(str(v) for v in self.pp_ups),
            }:
                widget.configure(state="readonly")

    def fill_player_ot(self):
        trainer = self.app.trainer
        if trainer is None or self.app.busy:
            return

        def done(result):
            if self.app.trainer is not trainer:
                self.invalidate()
                return
            values, note = result
            self.tid.set(str(values["ot_tid"]))
            self.sid.set(str(values["ot_sid"]))
            self.ot_gender.set(f"{values['ot_gender']} - {'女' if values['ot_gender'] else '男'}")
            if "ot_name" in values:
                self.ot_name.set(values["ot_name"])
            self.app.status.set(note)

        self.app.run("读取玩家OT资料…", trainer.read_player_ot, done)

    def preview(self):
        trainer = self.app.trainer
        if trainer is None or self.app.busy:
            return
        try:
            target, draft = self._key()
        except ValueError as exc:
            self.app.status.set(str(exc))
            return
        if not (self.app.discard_box_changes() if target[0] == MODES[0]
                else self.app.discard_party_changes()):
            return
        self.invalidate()
        generation = getattr(trainer, "connection_generation", None)

        def job():
            if target[0] == MODES[0]:
                if target[3] is None:
                    return trainer.prepare_create_box(target[1], draft)
                prepared = trainer.prepare_create_box(target[1], draft, slot=target[3])
            else:
                prepared = trainer.prepare_create_party(draft, replacement_slot=target[2])
            if target[3] is not None and not self._prepared_matches_target(prepared, target):
                raise ValueError("所选空槽已变化，创建目标不一致，请重新选择")
            return prepared

        def done(prepared):
            if (self.app.trainer is not trainer
                    or getattr(trainer, "connection_generation", None) != generation):
                self.invalidate()
                return
            try:
                if self._key() != (target, draft):
                    raise ValueError("创建草稿或目标已变化，请重新预览")
                if not self._prepared_matches_target(prepared, target):
                    raise ValueError("创建目标与所选槽位不一致，请重新预览")
            except ValueError as exc:
                self.invalidate()
                self.app.status.set(str(exc))
                return
            self.prepared = prepared
            self.preview_trainer = trainer
            self.preview_key = (target, draft)
            self.detail.set(self._describe(prepared))
            self._actions()

        self.app.run("准备自定义创建预览…", job, done)

    @staticmethod
    def _prepared_matches_target(prepared, target):
        expected = (("party", target[3]) if target[0] != MODES[0] and target[3] is not None
                    else (target[1], target[3]) if target[0] == MODES[0] and target[3] is not None
                    else None)
        return expected is None or tuple(prepared["destination"]) == expected

    def _describe(self, prepared):
        mon = prepared["pokemon"]
        info = mon.describe(self.app.trainer.profile)
        names = self.app.names
        destination = prepared["destination"]
        target = (f"队伍第 {destination[1] + 1} 位" if destination[0] == "party"
                  else f"盒子 {destination[0] + 1}，位置 {destination[1] + 1}（全零空槽）")
        moves = " / ".join(names["skills"].get(str(m), str(m)) for m in mon.moves if m)
        ability = names["specs"].get(str(info.get("ability")), str(info.get("ability")))
        lines = [f"目标：{target}",
                 f"宝可梦：{names['breeds'].get(str(mon.species), mon.species)} Lv.{info['level']}；"
                 f"昵称：{mon.nickname}；PID {mon.pid:08X}",
                 f"性格：{names.get('pers', {}).get(str(mon.pid % 25), mon.pid % 25)}；"
                 f"性别：{info.get('gender', '未知')}；特性：{ability}；闪光：{'是' if mon.shiny else '否'}",
                 f"招式：{moves or '无'}；PP提升：{mon.pp_ups}",
                 f"IV：{mon.ivs}；EV：{mon.evs}（合计 {sum(mon.evs)}）；经验：{mon.experience}",
                 f"OT：{mon.ot_name}；TID {mon.otid & 65535} / SID {mon.otid >> 16}；"
                 f"蛋：{'是' if mon.egg else '否'}",
                 f"携带道具：{names['items'].get(str(mon.held), mon.held)}；"
                 f"捕获球：{names['items'].get(str(mon.ball), mon.ball)}；"
                 f"地点：{self.app.trainer.profile.get('met_locations', {}).get(str(mon.met_location), mon.met_location)}；"
                 f"相遇等级：{mon.met_level}"]
        replaced = prepared.get("replaced")
        if replaced is not None:
            species_name = names["breeds"].get(str(replaced.species), str(replaced.species))
            lines.append(f"将顶替：{replaced.nickname or species_name}（{species_name}）"
                         f" Lv.{replaced.level}；PID {replaced.pid:08X}；"
                         f"OT {replaced.ot_name or '未知编码'}；"
                         f"TID {replaced.otid & 65535} / SID {replaced.otid >> 16}")
        lines.append("确认后写前备份、比较旧值并读回核对；来源合法性未完整验证。")
        return "\n".join(lines)

    def commit(self):
        trainer = self.app.trainer
        prepared = self.prepared
        if (trainer is None or prepared is None or self.app.busy
                or trainer is not self.preview_trainer
                or prepared.get("connection_generation") != getattr(trainer, "connection_generation", None)):
            self.invalidate()
            return
        try:
            if self._key() != self.preview_key:
                raise ValueError("草稿或目标已变化，请重新预览")
            if not self._prepared_matches_target(prepared, self.preview_key[0]):
                raise ValueError("创建目标与所选槽位不一致，请重新预览")
        except ValueError as exc:
            self.invalidate()
            self.app.status.set(str(exc))
            return
        if not messagebox.askokcancel("自定义创建确认", self.detail.get(), parent=self.app.root):
            return
        if trainer is not self.app.trainer or self._key() != self.preview_key:
            self.invalidate()
            return
        party = prepared["destination"][0] == "party"
        box = None if party else prepared["destination"][0]
        self.invalidate()

        def job():
            result = (trainer.commit_create_party(prepared) if party
                      else trainer.commit_create_box(prepared))
            return result, trainer.snapshot() if party else trainer.snapshot_box(box)

        def done(result):
            if self.app.trainer is not trainer:
                return
            if party:
                self.app.apply_snapshot(result[1])
            else:
                self.app.apply_box_snapshot(result[1])
            self.app.status.set("自定义创建已写入并读回核对；旧值已备份。")

        self.app.run("写入自定义宝可梦并读回核对…", job, done)
