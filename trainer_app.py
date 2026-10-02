import json
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, filedialog
from memory_client import MemClient
from trainer_core import Trainer
from pokemon_data import Pokemon, STAT_NAMES, gender, unown_form
from wiki_catalog import load_catalog, search_rows
import webbrowser
import struct
from datetime import datetime
import base64
from sprite_images import icon_species, read_icons


def resource_path(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


class App:
    def __init__(self, root):
        self.root = root
        root.title("水银 FC 修改器 · 闪光与能力编辑")
        self.names = json.loads(resource_path("names.json").read_text(encoding="utf-8"))
        self.profile = json.loads(
            resource_path("rom_profile.json").read_text(encoding="utf-8")
        )
        self.catalog = load_catalog(resource_path("catalog.json"))
        self.snapshot = None
        self.mem = None
        self.trainer = None
        self.busy = False
        self.closed = False
        self.close_requested = False
        self.last_error = ""
        self.snapshot_at = None
        self.results = queue.Queue()
        self.current_slot = None
        self.box_snapshot = None
        self.trainer_snapshot = None
        self.icon_images = {}
        self.current_bag_slot = None
        self.status = tk.StringVar(
            value="未连接。请在 mGBA 加载本项目新版 mercury_bridge.lua"
        )
        self.pocket = tk.StringVar(value="道具")
        self.buttons = []
        self.frozen_widgets = []
        self._build()
        self.timer = root.after(50, self._poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def button(self, parent, text, command, **pack):
        widget = ttk.Button(parent, text=text, command=command)
        widget.pack(**pack)
        self.buttons.append(widget)
        return widget

    def _build(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        for text, cmd in [
            ("连接", self.connect),
            ("刷新", self.refresh),
            ("导出诊断", self.export),
            ("恢复备份", self.restore),
            ("加载游戏微缩图", self.load_icons),
        ]:
            self.button(top, text, cmd, side="left", padx=3)
        ttk.Label(self.root, textvariable=self.status, wraplength=900, padding=8).pack(
            fill="x"
        )
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)
        self.tab_values = ttk.Frame(self.nb, padding=12)
        self.nb.add(self.tab_values, text="数值")
        self.money = tk.StringVar()
        self.coins = tk.StringVar()
        for label, var in [("金钱", self.money), ("代币", self.coins)]:
            row = ttk.Frame(self.tab_values)
            row.pack(anchor="w", pady=6)
            ttk.Label(row, text=label, width=12).pack(side="left")
            ttk.Entry(row, textvariable=var, width=18).pack(side="left")
        self.button(
            self.tab_values, "写入数值修改", self.write_values, anchor="w", pady=12
        )
        ttk.Label(
            self.tab_values,
            text="每次写入前保存原始数据备份，并检查游戏数据是否变化。\n玩家 ID 在“训练师”页编辑；姓名与主角性别等待完整核验。",
        ).pack(anchor="w")
        self.tab_party = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.tab_party, text="宝可梦编辑")
        self.party_tree = ttk.Treeview(
            self.tab_party,
            columns=("name", "level"),
            show="tree headings",
            style="MercuryParty.Treeview",
            height=6,
            selectmode="browse",
        )
        ttk.Style(self.root).configure("MercuryParty.Treeview", rowheight=66)
        self.party_tree.heading("#0", text="形象")
        self.party_tree.column("#0", width=72, stretch=False)
        self.party_tree.heading("name", text="队伍")
        self.party_tree.heading("level", text="等级")
        self.party_tree.column("name", width=170)
        self.party_tree.column("level", width=45)
        self.party_tree.pack(side="left", fill="y", padx=(0, 10))
        self.party_tree.bind("<<TreeviewSelect>>", self.select_mon)
        form = ttk.Frame(self.tab_party)
        form.pack(side="left", fill="both", expand=True)
        self.species = tk.StringVar()
        self.level = tk.StringVar()
        self.hp = tk.StringVar()
        self.held = tk.StringVar()
        self.identity = tk.StringVar()
        self.shiny = tk.BooleanVar()
        self.nature = tk.StringVar()
        self.ability = tk.StringVar()
        self.original_ability = ""
        labels = [
            f"{i} - {self.names['breeds'].get(str(i), '未收录')}"
            for i in sorted(map(int, self.profile["species"]))
        ]
        fields = ttk.Frame(form)
        fields.pack(fill="x")
        ttk.Label(fields, text="物种").grid(row=0, column=0, sticky="w")
        species_cb = ttk.Combobox(
            fields, textvariable=self.species, values=labels, width=30
        )
        species_cb.grid(row=0, column=1, columnspan=3, sticky="w", pady=3)
        species_cb.bind(
            "<KeyRelease>",
            lambda _: species_cb.configure(
                values=[
                    v for v in labels if self.species.get().casefold() in v.casefold()
                ]
            ),
        )
        held_choices = ["0 - 无"] + [
            f"{i} - {self.item_name(i)}"
            for i in sorted(map(int, self.profile["items"]))
            if self.profile["items"][str(i)]["pocket"] not in (2, 4)
        ]
        for row, (label, var) in enumerate(
            [("等级", self.level), ("当前 HP", self.hp), ("携带道具", self.held)], 1
        ):
            ttk.Label(fields, text=label).grid(row=row, column=0, sticky="w")
            if var is self.held:
                cb = ttk.Combobox(
                    fields, textvariable=var, values=held_choices, width=30
                )
                cb.grid(row=row, column=1, columnspan=3, sticky="w", pady=3)
                cb.bind(
                    "<KeyRelease>",
                    lambda _, w=cb: w.configure(
                        values=[
                            v
                            for v in held_choices
                            if w.get().casefold() in v.casefold()
                        ]
                    ),
                )
            else:
                ttk.Entry(fields, textvariable=var, width=12).grid(
                    row=row, column=1, sticky="w", pady=3
                )
        ttk.Checkbutton(
            fields, text="闪光（保留性格和性别）", variable=self.shiny
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=5)
        ttk.Label(fields, text="特性槽位").grid(row=5, column=0, sticky="w")
        self.ability_cb = ttk.Combobox(
            fields, textvariable=self.ability, state="readonly", width=30
        )
        self.ability_cb.grid(row=5, column=1, columnspan=3, sticky="w")
        ttk.Label(fields, text="性格").grid(row=6, column=0, sticky="w")
        ttk.Combobox(
            fields,
            textvariable=self.nature,
            state="readonly",
            width=30,
            values=[
                f"{i} - {self.names.get('pers', {}).get(str(i), str(i))}"
                for i in range(25)
            ],
        ).grid(row=6, column=1, columnspan=3, sticky="w", pady=3)
        self.species.trace_add("write", lambda *_: self.ability_options())
        ttk.Label(form, textvariable=self.identity, wraplength=600).pack(
            anchor="w", pady=4
        )
        stats = ttk.Frame(form)
        stats.pack(anchor="w", pady=6)
        self.iv = [tk.StringVar() for _ in range(6)]
        self.ev = [tk.StringVar() for _ in range(6)]
        self.stat_preview = tk.StringVar()
        for i, name in enumerate(STAT_NAMES):
            ttk.Label(stats, text=name, width=7, anchor="center").grid(
                row=0, column=i + 1
            )
        for row, label, values in [(1, "IV", self.iv), (2, "EV", self.ev)]:
            ttk.Label(stats, text=label, width=5).grid(row=row, column=0)
            for i, var in enumerate(values):
                ttk.Entry(stats, textvariable=var, width=6).grid(
                    row=row, column=i + 1, padx=2, pady=4
                )
        ttk.Label(form, text="IV：0～31；EV：单项 0～252，总和不超过 510。").pack(
            anchor="w"
        )
        ttk.Label(form, textvariable=self.stat_preview, wraplength=600).pack(
            anchor="w", pady=5
        )
        actions = ttk.Frame(form)
        actions.pack(fill="x", pady=5)
        self.button(actions, "检查与预览", self.preview, side="left", padx=3)
        self.button(actions, "写入当前宝可梦", self.write_mon, side="left", padx=3)
        report_frame = ttk.Frame(form)
        report_frame.pack(fill="both", expand=True)
        self.report = tk.Text(
            report_frame, height=8, width=65, wrap="word", state="disabled"
        )
        report_scroll = ttk.Scrollbar(
            report_frame, orient="vertical", command=self.report.yview
        )
        self.report.configure(yscrollcommand=report_scroll.set)
        report_scroll.pack(side="right", fill="y")
        self.report.pack(fill="both", expand=True)
        self.tab_moves = ttk.Frame(self.nb, padding=12)
        self.nb.add(self.tab_moves, text="招式 / PP")
        ttk.Label(self.tab_moves, textvariable=self.identity, wraplength=900).pack(
            anchor="w", pady=6
        )
        ttk.Label(
            self.tab_moves,
            text="先在“宝可梦编辑”选择队伍成员。更换招式会清除此槽的 PP 提升次数。\n预览提供等级学习表和学习器来源提示；遗传、教学与进化前等来源尚未核对。",
        ).pack(anchor="w", pady=5)
        self.move_vars = [tk.StringVar() for _ in range(4)]
        self.pp_vars = [tk.StringVar() for _ in range(4)]
        self.move_target = tk.IntVar(value=0)
        move_options = ["0 - 无"] + [
            f"{i} - {self.names['skills'].get(str(i), '未收录')}"
            for i in sorted(map(int, self.profile.get("moves", {})))
        ]
        for i in range(4):
            row = ttk.Frame(self.tab_moves)
            row.pack(anchor="w", pady=8)
            ttk.Radiobutton(
                row, text=f"招式 {i + 1}", variable=self.move_target, value=i
            ).pack(side="left", padx=(0, 10))
            cb = ttk.Combobox(
                row, textvariable=self.move_vars[i], values=move_options, width=32
            )
            cb.pack(side="left")
            cb.bind(
                "<KeyRelease>",
                lambda _, widget=cb: widget.configure(
                    values=[
                        v
                        for v in move_options
                        if widget.get().casefold() in v.casefold()
                    ]
                ),
            )
            ttk.Label(row, text="PP", width=5, anchor="center").pack(side="left")
            ttk.Entry(row, textvariable=self.pp_vars[i], width=6).pack(side="left")
        self.button(
            self.tab_moves, "按当前招式填满 PP", self.fill_pp, anchor="w", pady=6
        )
        self.button(
            self.tab_moves,
            "查看本物种等级招式表",
            self.show_learnset,
            anchor="w",
            pady=6,
        )
        self.button(self.tab_moves, "检查与预览", self.preview, anchor="w", pady=6)
        self.button(
            self.tab_moves, "写入当前宝可梦", self.write_mon, anchor="w", pady=6
        )
        self.tab_bag = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.tab_bag, text="背包 / 学习器")
        bar = ttk.Frame(self.tab_bag)
        bar.pack(fill="x")
        cb = ttk.Combobox(
            bar,
            textvariable=self.pocket,
            values=[p["name"] for p in self.profile["pockets"]],
            state="readonly",
            width=20,
        )
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        ttk.Label(bar, text="重要道具保留内部数量，界面不显示数量。").pack(
            side="left", padx=10
        )
        box = ttk.Frame(self.tab_bag)
        box.pack(fill="both", expand=True, pady=8)
        self.bag_tree = ttk.Treeview(
            box,
            columns=("slot", "id", "name", "qty"),
            show="headings",
            selectmode="browse",
        )
        for c, label, width in [
            ("slot", "位置", 55),
            ("id", "编号", 65),
            ("name", "道具", 320),
            ("qty", "数量", 65),
        ]:
            self.bag_tree.heading(c, text=label)
            self.bag_tree.column(c, width=width)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.bag_tree.yview)
        self.bag_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.bag_tree.pack(fill="both", expand=True)
        self.bag_tree.bind("<<TreeviewSelect>>", self.select_item)
        ed = ttk.Frame(self.tab_bag)
        ed.pack(fill="x")
        self.item_id = tk.StringVar()
        self.item_qty = tk.StringVar()
        ttk.Label(ed, text="道具").pack(side="left")
        self.item_choices = []
        self.item_cb = ttk.Combobox(ed, textvariable=self.item_id, width=34)
        self.item_cb.pack(side="left", padx=5)
        self.item_cb.bind(
            "<KeyRelease>",
            lambda _: self.item_cb.configure(
                values=[
                    v
                    for v in self.item_choices
                    if self.item_id.get().casefold() in v.casefold()
                ]
            ),
        )
        ttk.Label(ed, text="数量").pack(side="left")
        self.qty_entry = ttk.Entry(ed, textvariable=self.item_qty, width=10)
        self.qty_entry.pack(side="left", padx=5)
        self.button(ed, "写入选中", self.write_item, side="left", padx=5)
        self.button(ed, "清空选中", lambda: self.write_item(True), side="left", padx=5)
        self._build_boxes()
        self._build_catalog()
        self._build_details()
        self._build_trainer()

    def _build_details(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="来源 / 原训练师")
        ttk.Label(tab, textvariable=self.identity, wraplength=900).pack(
            anchor="w", pady=6
        )
        ttk.Label(
            tab,
            text="先选择队伍成员。地点暂显示原始编号；捕获球按本改版编号填写。\n修改原训练师 ID 时保留当前选择的闪光状态；字段有效不代表遭遇来源已认证。\n转为蛋时同步两处标志、设为1级及默认周期；取消蛋标记不等于执行自然孵化。",
            wraplength=900,
        ).pack(anchor="w", pady=6)
        self.detail_vars = {}
        self.egg = tk.BooleanVar()
        ttk.Checkbutton(tab, text="蛋（转换时请先检查预览）", variable=self.egg).pack(
            anchor="w", pady=4
        )
        for key, label in [
            ("friendship", "亲密度 / 孵化周期（0～255）"),
            ("met_location", "相遇地点编号（0～255）"),
            ("met_level", "相遇等级原始值（0～127）"),
            ("ball", "捕获球编号"),
            ("ot_tid", "原训练师 TID"),
            ("ot_sid", "原训练师 SID"),
            ("ot_gender", "原训练师性别（0男 / 1女）"),
        ]:
            row = ttk.Frame(tab)
            row.pack(anchor="w", pady=4)
            ttk.Label(row, text=label, width=38).pack(side="left")
            var = tk.StringVar()
            self.detail_vars[key] = var
            ttk.Entry(row, textvariable=var, width=18).pack(side="left")
        row = ttk.Frame(tab)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="未知图腾字形", width=38).pack(side="left")
        self.unown_letter = tk.StringVar(value="不适用")
        ttk.Combobox(
            row,
            textvariable=self.unown_letter,
            state="readonly",
            values=["保持当前"]
            + [
                f"{i} - {letter}"
                for i, letter in enumerate(self.profile["unown_letters"])
            ],
            width=18,
        ).pack(side="left")
        self.button(tab, "检查与预览", self.preview, anchor="w", pady=8)
        row = ttk.Frame(tab)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="原训练师姓名（英文/数字，最多7字）", width=38).pack(
            side="left"
        )
        self.ot_name = tk.StringVar()
        self.original_ot_name = ""
        ttk.Entry(row, textvariable=self.ot_name, width=18).pack(side="left")
        self.button(tab, "写入当前宝可梦", self.write_mon, anchor="w", pady=8)
        self.detail_preview = tk.StringVar()
        ttk.Label(tab, textvariable=self.detail_preview, wraplength=900).pack(
            anchor="w", pady=8
        )

    def _build_trainer(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.tab_trainer = tab
        self.nb.add(tab, text="训练师")
        self.player_tid = tk.StringVar()
        self.player_sid = tk.StringVar()
        self.player_name = tk.StringVar()
        self.original_player_name = ""
        self.player_detail = tk.StringVar(
            value="连接后点击读取。主角性别目前只读；中文姓名编码待核验。"
        )
        ttk.Label(tab, textvariable=self.player_detail, wraplength=900).pack(
            anchor="w", pady=8
        )
        for label, var in [
            ("姓名（英文/数字，最多7字）", self.player_name),
            ("玩家 TID（0～65535）", self.player_tid),
            ("玩家 SID（0～65535）", self.player_sid),
        ]:
            row = ttk.Frame(tab)
            row.pack(anchor="w", pady=6)
            ttk.Label(row, text=label, width=30).pack(side="left")
            ttk.Entry(row, textvariable=var, width=18).pack(side="left")
        ttk.Label(
            tab,
            text="仅修改玩家 ID，不自动改变队伍或 PC 的原训练师资料。\n现有宝可梦可能因此被视为外来宝可梦；修改前后请核对训练师卡。",
            wraplength=900,
        ).pack(anchor="w", pady=8)
        self.detail_image = ttk.Label(tab)
        self.detail_image.pack(anchor="w")
        self.button(tab, "读取训练师资料", self.read_trainer, anchor="w", pady=6)
        self.button(tab, "写入训练师资料", self.write_trainer_ids, anchor="w", pady=6)

    def apply_trainer_snapshot(self, snap):
        self.trainer_snapshot = snap
        self.player_tid.set(str(snap["tid"]))
        self.player_sid.set(str(snap["sid"]))
        self.original_player_name = (
            snap["name"] if snap["name"] is not None else "（未知编码，原样保留）"
        )
        self.player_name.set(self.original_player_name)
        self.player_detail.set(
            f"完整 ID：{snap['sid'] * 65536 + snap['tid']:08X}；主角性别原始值：{snap['gender']}\n姓名原始编码：{snap['name_raw']}；中文编码未核验，不支持自动转换。"
        )

    def read_trainer(self):
        if self.trainer is not None:
            self.run(
                "读取训练师资料…",
                self.trainer.snapshot_trainer,
                self.apply_trainer_snapshot,
            )

    def load_icons(self):
        if self.busy or self.trainer is None or self.snapshot is None:
            return
        trainer = self.trainer
        pokemon = list(self.snapshot["party"])
        try:
            patches, _ = self.prepare_mon()
            preview_mon = Pokemon(patches[0][2])
            pokemon.append(preview_mon)
        except ValueError:
            preview_mon = None
        if self.box_snapshot is not None:
            pokemon += list(self.box_snapshot["pokemon"])

        def job():
            trainer.verify()
            result = read_icons(trainer.g, self.profile, pokemon)
            trainer.verify()
            return result

        def done(result):
            for ident, png in result.items():
                self.icon_images[ident] = tk.PhotoImage(
                    master=self.root, data=base64.b64encode(png)
                )
            for slot, mon in enumerate(self.snapshot["party"]):
                self.party_tree.item(str(slot), image=self.mon_image(mon))
            if self.box_snapshot is not None:
                for slot, mon in enumerate(self.box_snapshot["pokemon"]):
                    self.box_tree.item(str(slot), image=self.mon_image(mon))
            if preview_mon is not None:
                self.detail_image.configure(image=self.mon_image(preview_mon))
            self.status.set("已从当前 ROM 加载微缩图；图像保存在本次会话，不写入游戏。")

        self.run("读取游戏微缩图…", job, done)

    def mon_image(self, mon):
        return self.icon_images.get(icon_species(mon, self.profile), "")

    def write_trainer_ids(self):
        if self.busy or self.trainer is None or self.trainer_snapshot is None:
            return
        trainer = self.trainer
        snap = self.trainer_snapshot
        tid, sid = self.player_tid.get(), self.player_sid.get()
        name = (
            None
            if self.player_name.get() == self.original_player_name
            else self.player_name.get()
        )

        def job():
            result = trainer.commit_trainer_profile(snap, tid, sid, name)
            return result, trainer.snapshot_trainer()

        def done(result):
            self.apply_trainer_snapshot(result[1])
            self.status.set(
                "训练师资料已写入并读回核对，原值已备份。"
                if result[0]["changed"]
                else "没有变化，无需写入。"
            )

        self.run("校验并写入训练师资料…", job, done)

    def _build_boxes(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="PC 盒子")
        self.tab_boxes = tab
        row = ttk.Frame(tab)
        row.pack(fill="x")
        self.box_number = tk.StringVar(value="1")
        ttk.Combobox(
            row,
            textvariable=self.box_number,
            values=list(range(1, 26)),
            state="readonly",
            width=6,
        ).pack(side="left")
        self.button(row, "读取盒子", self.read_box, side="left", padx=8)
        self.button(row, "编辑选中宝可梦", self.edit_box_dialog, side="left", padx=8)
        ttk.Label(
            tab,
            text="支持闪光、性格与 IV/EV 编辑；其他字段仅查看。盒内不存储能力值，取出时由游戏计算。",
        ).pack(anchor="w", pady=8)
        box = ttk.Frame(tab)
        box.pack(fill="both", expand=True)
        self.box_tree = ttk.Treeview(
            box,
            columns=("slot", "species", "level", "shiny"),
            show="tree headings",
            style="MercuryBox.Treeview",
            selectmode="browse",
            height=10,
        )
        ttk.Style(self.root).configure("MercuryBox.Treeview", rowheight=38)
        self.box_tree.heading("#0", text="形象")
        self.box_tree.column("#0", width=46, stretch=False)
        for key, label, width in [
            ("slot", "位置", 50),
            ("species", "宝可梦", 300),
            ("level", "等级（由经验推算）", 150),
            ("shiny", "闪光", 70),
        ]:
            self.box_tree.heading(key, text=label)
            self.box_tree.column(key, width=width)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.box_tree.yview)
        self.box_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.box_tree.pack(fill="both", expand=True)
        self.box_tree.bind("<<TreeviewSelect>>", self.select_box_mon)
        self.box_detail = tk.StringVar(value="连接后选择盒子并点击读取。")
        ttk.Label(tab, textvariable=self.box_detail, wraplength=900).pack(
            anchor="w", pady=10
        )

    def read_box(self):
        if self.trainer is None:
            return
        index = int(self.box_number.get()) - 1
        self.run(
            "读取 PC 盒子…",
            lambda: self.trainer.snapshot_box(index),
            self.apply_box_snapshot,
        )

    def apply_box_snapshot(self, snapshot):
        self.box_snapshot = snapshot
        self.box_number.set(str(snapshot["index"] + 1))
        self.box_tree.delete(*self.box_tree.get_children())
        count = 0
        for i, mon in enumerate(snapshot["pokemon"]):
            info = mon.describe(self.profile)
            name = (
                self.names["breeds"].get(str(mon.species), f"未收录 #{mon.species}")
                if mon.species
                else "（空槽）"
            )
            count += bool(mon.species)
            self.box_tree.insert(
                "",
                "end",
                iid=str(i),
                image=self.mon_image(mon),
                values=(
                    i + 1,
                    name,
                    info["level"] or "—",
                    ("是" if mon.shiny else "否") if mon.species else "—",
                ),
            )
        self.box_detail.set(
            f"第 {snapshot['index'] + 1} 盒：{count} / 30。选择成员查看详情。"
        )
        self.status.set(f"已读取第 {snapshot['index'] + 1} 盒，尚未写入。")

    def edit_box_dialog(self):
        selected = self.box_tree.selection()
        if not selected or self.box_snapshot is None or self.trainer is None:
            return
        snapshot = self.box_snapshot
        slot = int(selected[0])
        mon = snapshot["pokemon"][slot]
        if not mon.species:
            return
        trainer = self.trainer
        window = tk.Toplevel(self.root)
        window.title(f"第 {snapshot['index'] + 1} 盒 · 第 {slot + 1} 格")
        window.geometry("650x430")
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text=self.names["breeds"].get(str(mon.species), str(mon.species))
        ).pack(anchor="w")
        letter = tk.StringVar(value=str(unown_form(mon.pid)))
        if mon.species == 201:
            ttk.Label(frame, text="未知图腾字形").pack(anchor="w", pady=(8, 0))
            ttk.Combobox(
                frame,
                textvariable=letter,
                state="readonly",
                values=[
                    f"{i} - {name}"
                    for i, name in enumerate(self.profile["unown_letters"])
                ],
                width=20,
            ).pack(anchor="w")
        shiny = tk.BooleanVar(value=mon.shiny)
        nature = tk.StringVar(
            value=f"{mon.pid % 25} - {self.names['pers'].get(str(mon.pid % 25), str(mon.pid % 25))}"
        )
        ttk.Checkbutton(frame, text="闪光", variable=shiny).pack(anchor="w", pady=5)
        ttk.Combobox(
            frame,
            textvariable=nature,
            state="readonly",
            values=[
                f"{i} - {self.names['pers'].get(str(i), str(i))}" for i in range(25)
            ],
        ).pack(anchor="w")
        grid = ttk.Frame(frame)
        grid.pack(anchor="w", pady=10)
        iv = [tk.StringVar(value=str(n)) for n in mon.ivs]
        ev = [tk.StringVar(value=str(n)) for n in mon.evs]
        for i, name in enumerate(STAT_NAMES):
            ttk.Label(grid, text=name, width=7).grid(row=0, column=i + 1)
        for row, label, variables in [(1, "IV", iv), (2, "EV", ev)]:
            ttk.Label(grid, text=label).grid(row=row, column=0)
            for i, var in enumerate(variables):
                ttk.Entry(grid, textvariable=var, width=6).grid(
                    row=row, column=i + 1, padx=2, pady=5
                )
        detail = tk.StringVar(
            value="IV 0～31；EV 单项 0～252、总和 ≤510。结构检查不等于完整来源合法化。"
        )
        ttk.Label(frame, textvariable=detail, wraplength=600).pack(anchor="w", pady=6)

        def prepare():
            if self.trainer is not trainer:
                raise ValueError("连接已经改变，请重新打开此编辑窗口")
            return trainer.edit_box(
                snapshot,
                slot,
                ivs=[v.get() for v in iv],
                evs=[v.get() for v in ev],
                nature=nature.get().split(" - ", 1)[0],
                shiny=shiny.get(),
                **(
                    {"unown_letter": letter.get().split(" - ", 1)[0]}
                    if mon.species == 201
                    else {}
                ),
            )

        def preview():
            try:
                _, report = prepare()
                detail.set(
                    f"检查通过；闪光：{'是' if report['shiny'] else '否'}，EV 总和：{sum(report['evs'])}。\n性别与特性标志保留；取出时由游戏计算能力值。来源合法性未完整验证。"
                )
            except Exception as exc:
                detail.set("未通过：" + str(exc))

        def write():
            if self.busy:
                return
            try:
                patches, _ = prepare()
            except Exception as exc:
                detail.set("未写入：" + str(exc))
                return

            def job():
                result = trainer.commit_box(
                    patches, f"PC 第 {snapshot['index'] + 1} 盒 第 {slot + 1} 格"
                )
                return result, trainer.snapshot_box(snapshot["index"])

            def done(result):
                self.apply_box_snapshot(result[1])
                self.status.set(
                    "盒内宝可梦已写入并读回核对，原数据已备份。"
                    if result[0]["changed"]
                    else "没有变化，无需写入。"
                )
                if window.winfo_exists():
                    window.destroy()

            self.run("校验并写入盒内宝可梦…", job, done)

        actions = ttk.Frame(frame)
        actions.pack(anchor="w", pady=8)
        ttk.Button(actions, text="检查与预览", command=preview).pack(
            side="left", padx=4
        )
        ttk.Button(actions, text="写入此宝可梦", command=write).pack(
            side="left", padx=4
        )
        return window

    def select_box_mon(self, event=None):
        selected = self.box_tree.selection()
        if self.busy or not selected or self.box_snapshot is None:
            return
        mon = self.box_snapshot["pokemon"][int(selected[0])]
        info = mon.describe(self.profile)
        if not mon.species:
            self.box_detail.set("空槽")
            return
        nature = self.names.get("pers", {}).get(str(mon.pid % 25), str(mon.pid % 25))
        moves = " / ".join(
            self.names["skills"].get(str(m), str(m)) for m in mon.moves if m
        )
        ability = self.names["specs"].get(str(info.get("ability")), "未验证")
        self.box_detail.set(
            f"性格 {nature} · {info.get('gender', '未验证')} · 特性 {ability} · 经验 {mon.experience}\n"
            + "IV："
            + " / ".join(map(str, mon.ivs))
            + "\nEV："
            + " / ".join(map(str, mon.evs))
            + "\n招式："
            + moves
            + "\n"
            + ("；".join(info["errors"]) or "已解码主要字段；完整来源合法性未验证。")
        )

    def _build_catalog(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="水银资料库")
        self.category = tk.StringVar(value="pokemon")
        self.query = tk.StringVar()
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        cb = ttk.Combobox(
            bar,
            textvariable=self.category,
            values=list(self.catalog["categories"]),
            width=12,
            state="readonly",
        )
        cb.pack(side="left")
        ttk.Entry(bar, textvariable=self.query).pack(
            side="left", fill="x", expand=True, padx=8
        )
        self.catalog_count = tk.StringVar()
        ttk.Label(bar, textvariable=self.catalog_count).pack(side="right")
        ttk.Label(
            tab,
            text="pokemon 宝可梦 / abilities 特性 / items 道具 / moves 招式；资料 ID 与全国图鉴号不同。\n填入招式前，在“招式 / PP”勾选目标招式槽。特性仅可填入该物种具备的槽位。",
        ).pack(anchor="w", pady=6)
        box = ttk.Frame(tab)
        box.pack(fill="both", expand=True)
        self.catalog_tree = ttk.Treeview(
            box,
            columns=("id", "dex", "name", "fields"),
            show="headings",
            selectmode="browse",
        )
        for c, label, width in [
            ("id", "资料 ID", 65),
            ("dex", "图鉴号", 60),
            ("name", "名称", 180),
            ("fields", "资料摘要", 500),
        ]:
            self.catalog_tree.heading(c, text=label)
            self.catalog_tree.column(c, width=width)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.catalog_tree.yview)
        self.catalog_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.catalog_tree.pack(fill="both", expand=True)
        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=6)
        self.button(actions, "填入编辑区（不写入）", self.catalog_fill, side="left")
        self.button(actions, "网页详情", self.catalog_open, side="left", padx=6)
        self.query.trace_add("write", lambda *_: self.filter_catalog())
        cb.bind("<<ComboboxSelected>>", lambda _: self.filter_catalog())
        self.filter_catalog()

    def ability_options(self):
        try:
            species = int(self.species.get().split(" - ", 1)[0])
        except ValueError:
            return
        metadata = self.profile["species"].get(str(species))
        if not metadata:
            return
        labels = ["普通 1", "普通 2", "隐藏"]
        self.ability_cb.configure(
            values=[
                f"{i} - {labels[i]}：{self.names['specs'].get(str(a), str(a))}"
                for i, a in enumerate(metadata["abilities"])
                if a
            ]
        )

    def filter_catalog(self):
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        rows = self.catalog["categories"][self.category.get()]
        matches = search_rows(rows, self.query.get())
        self.catalog_rows = {str(r["id"]): r for r in matches}
        for r in matches:
            self.catalog_tree.insert(
                "",
                "end",
                iid=str(r["id"]),
                values=(r["id"], r["dex"] or "—", r["name"], " / ".join(r["fields"])),
            )
        self.catalog_count.set(f"{len(matches)} / {len(rows)}")

    def selected_catalog(self):
        selected = self.catalog_tree.selection()
        return self.catalog_rows.get(selected[0]) if selected else None

    def catalog_open(self):
        row = self.selected_catalog()
        if row and row["url"].startswith("https://sum-light.github.io/azoth-wiki/"):
            webbrowser.open(row["url"])

    def catalog_fill(self):
        row = self.selected_catalog()
        if not row:
            return
        if self.category.get() == "pokemon" and self.current_slot is not None:
            self.species.set(f"{row['id']} - {row['name']}")
            self.nb.select(self.tab_party)
        elif self.category.get() == "items":
            if self.snapshot is None:
                messagebox.showinfo("提示", "请先连接游戏并刷新背包。")
                return
            metadata = self.profile["items"].get(str(row["id"]))
            if not metadata:
                return
            wanted = self.profile["pockets"][metadata["pocket"] - 1]["name"]
            selected = self.bag_tree.selection()
            preferred = (
                int(selected[0])
                if selected and self.snapshot["pocket"]["id"] == metadata["pocket"]
                else None
            )
            self.pocket.set(wanted)
            self.nb.select(self.tab_bag)

            def fill():
                slot = preferred
                if slot is None:
                    slot = next(
                        (
                            i
                            for i, (ident, _) in enumerate(
                                struct.iter_unpack("<HH", self.snapshot["bag"])
                            )
                            if ident == 0
                        ),
                        None,
                    )
                if slot is None:
                    messagebox.showinfo(
                        "提示", "当前口袋已满。请先选择要替换的位置，再回资料库填入。"
                    )
                    return
                self.bag_tree.selection_set(str(slot))
                self.bag_tree.see(str(slot))
                self.select_item()
                self.item_id.set(f"{row['id']} - {self.item_name(row['id'])}")
                if self.snapshot["pocket"]["quantity_visible"]:
                    self.item_qty.set("1")
                self.status.set(f"已填入第 {slot + 1} 格的编辑区，尚未写入游戏。")

            self.refresh(after=fill)
        elif self.category.get() == "moves" and self.current_slot is not None:
            metadata = self.profile.get("moves", {}).get(str(row["id"]))
            if not metadata:
                messagebox.showinfo("提示", "此招式尚无可用的本地 ROM PP 数据。")
                return
            slot = self.move_target.get()
            self.move_vars[slot].set(f"{row['id']} - {row['name']}")
            self.pp_vars[slot].set(str(metadata["pp"]))
            self.nb.select(self.tab_moves)
        elif self.category.get() == "abilities" and self.current_slot is not None:
            try:
                species = int(self.species.get().split(" - ", 1)[0])
            except ValueError:
                return
            metadata = self.profile["species"].get(str(species), {})
            slots = [
                i for i, a in enumerate(metadata.get("abilities", [])) if a == row["id"]
            ]
            if not slots:
                messagebox.showinfo(
                    "提示", "该物种没有此特性，请选择其已有的特性槽位。"
                )
                return
            slot = slots[0]
            self.ability.set(
                next(v for v in self.ability_cb["values"] if v.startswith(f"{slot} - "))
            )
            self.nb.select(self.tab_party)
        else:
            messagebox.showinfo("提示", "请先连接游戏并选择队伍成员。")

    def freeze_inputs(self):
        self.frozen_widgets = []

        def visit(parent):
            for widget in parent.winfo_children():
                if isinstance(
                    widget,
                    (
                        ttk.Entry,
                        ttk.Combobox,
                        ttk.Checkbutton,
                        ttk.Radiobutton,
                        ttk.Treeview,
                        ttk.Button,
                    ),
                ):
                    self.frozen_widgets.append((widget, widget.state()))
                    widget.state(["disabled"])
                visit(widget)

        visit(self.root)

    def thaw_inputs(self):
        for widget, state in self.frozen_widgets:
            if widget.winfo_exists():
                widget.state(["!disabled", "!readonly", *state])
        self.frozen_widgets = []

    def run(self, label, job, done):
        if self.busy:
            return
        self.busy = True
        self.status.set(label)
        self.freeze_inputs()
        for b in self.buttons:
            b.configure(state="disabled")

        def worker():
            try:
                self.results.put((done, job(), None))
            except Exception as exc:
                self.results.put((done, None, str(exc) or type(exc).__name__))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        if self.closed:
            return
        try:
            done, result, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.thaw_inputs()
            for b in self.buttons:
                b.configure(state="normal")
            if error:
                self.last_error = error
                self.status.set("操作未完成：" + error.splitlines()[0])
                messagebox.showerror("操作未完成", error)
            else:
                self.last_error = ""
                try:
                    done(result)
                except Exception as exc:
                    self.last_error = str(exc) or type(exc).__name__
                    self.status.set("显示结果时遇到错误：" + str(exc))
                    messagebox.showerror("显示失败", str(exc))
        if self.close_requested and not self.busy:
            self.close()
            return
        self.timer = self.root.after(50, self._poll)

    def pocket_id(self):
        return next(
            p["id"] for p in self.profile["pockets"] if p["name"] == self.pocket.get()
        )

    def connect(self):
        if self.busy:
            return
        old_mem = self.mem
        self.mem = None
        self.trainer = None
        self.snapshot = None
        self.snapshot_at = None
        self.current_slot = None
        self.trainer_snapshot = None
        self.player_tid.set("")
        self.player_sid.set("")
        self.player_name.set("")
        self.player_detail.set("连接已改变，请重新读取训练师资料。")
        self.box_snapshot = None
        self.box_tree.delete(*self.box_tree.get_children())
        self.box_detail.set("请连接后重新读取盒子。")

        def job():
            if old_mem:
                old_mem.close()
            mem = MemClient()
            try:
                mem.connect()
                base = (
                    Path(sys.executable).parent
                    if getattr(sys, "frozen", False)
                    else Path(__file__).resolve().parent
                )
                trainer = Trainer(mem, self.profile, base / "backups")
                trainer.verify()
                snap = trainer.snapshot()
                if self.closed:
                    mem.close()
                    raise IOError("窗口已关闭")
                return mem, trainer, snap
            except Exception:
                mem.close()
                raise

        def done(result):
            self.mem, self.trainer, snap = result
            self.pocket.set("道具")
            self.apply_snapshot(snap)

        self.run("连接并检查 ROM 布局…", job, done)

    def refresh(self, after=None):
        if self.trainer is None:
            return
        if after is None and self.nb.select() == str(self.tab_boxes):
            self.read_box()
            return
        if after is None and self.nb.select() == str(self.tab_trainer):
            self.read_trainer()
            return
        pocket = self.pocket_id()

        def done(snap):
            self.apply_snapshot(snap)
            if after:
                after()

        self.run("读取游戏数据…", lambda: self.trainer.snapshot(pocket), done)

    def apply_snapshot(self, snap):
        self.snapshot = snap
        self.snapshot_at = datetime.now().astimezone().isoformat()
        self.money.set(str(snap["money"]))
        self.coins.set(str(snap["coins"]))
        self.party_tree.delete(*self.party_tree.get_children())
        for i, mon in enumerate(snap["party"]):
            name = self.names["breeds"].get(str(mon.species), f"未收录#{mon.species}")
            self.party_tree.insert(
                "",
                "end",
                iid=str(i),
                image=self.mon_image(mon),
                values=(
                    f"{i + 1}. {name}{' ★' if mon.shiny else ''}{'（蛋）' if mon.egg else ''}",
                    mon.level,
                ),
            )
        if snap["party"]:
            index = min(self.current_slot or 0, len(snap["party"]) - 1)
            self.party_tree.selection_set(str(index))
            self.select_mon()
        else:
            self.current_slot = None
            for var in [
                self.species,
                self.level,
                self.hp,
                self.held,
                self.nature,
                *self.iv,
                *self.ev,
                *self.detail_vars.values(),
            ]:
                var.set("")
            self.identity.set("队伍为空")
            self.set_report("")
            self.ability.set("")
            for var in [*self.move_vars, *self.pp_vars]:
                var.set("")
        self.bag_tree.delete(*self.bag_tree.get_children())
        self.current_bag_slot = None
        pocket = snap["pocket"]
        self.pocket.set(pocket["name"])
        self.item_choices = [
            f"{i} - {self.item_name(i)}"
            for i in sorted(map(int, self.profile["items"]))
            if self.profile["items"][str(i)]["pocket"] == pocket["id"]
        ]
        self.item_cb.configure(values=self.item_choices)
        for i in range(pocket["capacity"]):
            item, qty = struct.unpack_from("<HH", snap["bag"], i * 4)
            name = self.item_name(item)
            self.bag_tree.insert(
                "",
                "end",
                iid=str(i),
                values=(i + 1, item, name, qty if pocket["quantity_visible"] else "—"),
            )
        self.qty_entry.configure(
            state="normal" if pocket["quantity_visible"] else "disabled"
        )
        self.item_id.set("")
        self.item_qty.set("")
        self.status.set(
            f"已刷新 · {len(snap['party'])} 只宝可梦 · {pocket['name']} {pocket['capacity']} 格"
            + (" · 战斗中：宝可梦仅可查看" if snap["in_battle"] else "")
            + (" · 旧桥接仅可读" if "CRCBATCH" not in self.mem.capabilities else "")
        )

    def item_name(self, item):
        if not item:
            return "（空槽）"
        metadata = self.profile["items"].get(str(item), {})
        if "tm_index" in metadata:
            i = metadata["tm_index"]
            total = self.profile.get("tm_count", 120)
            label = f"TM{i + 1:03d}" if i < total else f"HM{i - total + 1:02d}"
            return (
                label
                + " "
                + self.names["skills"].get(str(metadata["move"]), str(metadata["move"]))
            )
        return self.names["items"].get(str(item), f"未收录#{item}")

    def select_mon(self, event=None):
        if self.busy:
            return
        selection = self.party_tree.selection()
        if not selection or self.snapshot is None:
            return
        i = int(selection[0])
        self.current_slot = i
        mon = self.snapshot["party"][i]
        self.species.set(
            f"{mon.species} - {self.names['breeds'].get(str(mon.species), '未收录')}"
        )
        self.level.set(str(mon.level))
        self.hp.set(str(mon.hp))
        self.held.set(f"{mon.held} - {self.item_name(mon.held) if mon.held else '无'}")
        self.shiny.set(mon.shiny)
        self.egg.set(mon.egg)
        for key, var in self.detail_vars.items():
            value = (
                mon.otid & 65535
                if key == "ot_tid"
                else mon.otid >> 16
                if key == "ot_sid"
                else getattr(mon, key)
            )
            var.set(str(value))
        self.detail_preview.set("")
        self.detail_image.configure(image=self.mon_image(mon))
        self.original_ot_name = (
            mon.ot_name if mon.ot_name is not None else "（未知编码，原样保留）"
        )
        self.ot_name.set(self.original_ot_name)
        self.unown_letter.set(
            f"{unown_form(mon.pid)} - 当前字形" if mon.species == 201 else "不适用"
        )
        metadata = self.profile["species"].get(str(mon.species))
        if metadata:
            abilities = metadata["abilities"]
            slot = (
                2
                if mon.ability_flag and abilities[2]
                else (mon.pid & 1 if abilities[1] else 0)
            )
            self.ability.set(
                next(
                    (
                        v
                        for v in self.ability_cb["values"]
                        if v.startswith(str(slot) + " - ")
                    ),
                    "",
                )
            )
        else:
            self.ability.set("")
        self.original_ability = self.ability.get()
        for i, move in enumerate(mon.moves):
            self.move_vars[i].set(
                f"{move} - {self.names['skills'].get(str(move), '未收录')}"
            )
            self.pp_vars[i].set(str(mon.pp[i]))
        for i in range(6):
            self.iv[i].set(str(mon.ivs[i]))
            self.ev[i].set(str(mon.evs[i]))
        nature = self.names.get("pers", {}).get(str(mon.pid % 25), str(mon.pid % 25))
        self.nature.set(f"{mon.pid % 25} - {nature}")
        self.identity.set(
            f"PID {mon.pid:08X} · 性格 {nature} · EV 合计 {sum(mon.evs)} · 经验 {mon.experience}"
        )
        moves = " / ".join(
            self.names["skills"].get(str(m), str(m)) for m in mon.moves if m
        )
        self.stat_preview.set(
            "能力值：" + " / ".join(map(str, mon.stats)) + "\n招式：" + moves
        )
        report = self.trainer.validate_pokemon(mon)
        self.set_report("\n".join(report["errors"] + report["notes"]))

    def set_report(self, text):
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", text)
        self.report.configure(state="disabled")

    def prepare_mon(self):
        if self.snapshot is None or self.current_slot is None:
            raise ValueError("请先连接并选中队伍成员")
        mon = self.snapshot["party"][self.current_slot]
        changes = {
            "ivs": [v.get() for v in self.iv],
            "evs": [v.get() for v in self.ev],
            "shiny": self.shiny.get(),
            "species": self.species.get().split(" - ", 1)[0],
            "level": self.level.get(),
            "held": self.held.get().split(" - ", 1)[0],
            "nature": self.nature.get().split(" - ", 1)[0],
        }
        for key, var in self.detail_vars.items():
            before = (
                mon.otid & 65535
                if key == "ot_tid"
                else mon.otid >> 16
                if key == "ot_sid"
                else getattr(mon, key)
            )
            if var.get() != str(before):
                changes[key] = var.get()
        if self.egg.get() != mon.egg:
            changes["egg"] = self.egg.get()
        if self.ot_name.get() != self.original_ot_name:
            changes["ot_name"] = self.ot_name.get()
        if changes["species"] == "201" and self.unown_letter.get() not in (
            "保持当前",
            "不适用",
        ):
            changes["unown_letter"] = self.unown_letter.get().split(" - ", 1)[0]
        # Untouched HP follows automatic damage-preserving recalculation.
        if self.hp.get() != str(mon.hp):
            changes["hp"] = self.hp.get()
        if self.ability.get() != self.original_ability:
            changes["ability_slot"] = self.ability.get().split(" - ", 1)[0]
        moves = [v.get().split(" - ", 1)[0] for v in self.move_vars]
        pp = [v.get() for v in self.pp_vars]
        if moves != list(map(str, mon.moves)) or pp != list(map(str, mon.pp)):
            changes["moves"] = moves
            changes["pp"] = pp
        return self.trainer.edit_pokemon(self.snapshot, self.current_slot, **changes)

    def fill_pp(self):
        if self.snapshot is None or self.current_slot is None:
            return
        mon = self.snapshot["party"][self.current_slot]
        try:
            values = []
            for i, var in enumerate(self.move_vars):
                move = int(var.get().split(" - ", 1)[0])
                if move == 0:
                    values.append(0)
                    continue
                metadata = self.profile.get("moves", {}).get(str(move))
                if not metadata:
                    raise ValueError(f"未知招式 {move}")
                bonus = (mon.raw[40] >> (2 * i)) & 3 if move == mon.moves[i] else 0
                values.append(metadata["pp"] * (5 + bonus) // 5)
            for var, value in zip(self.pp_vars, values):
                var.set(str(value))
        except ValueError as exc:
            messagebox.showerror("填写失败", str(exc))

    def show_learnset(self):
        if self.current_slot is None:
            return
        try:
            species = int(self.species.get().split(" - ", 1)[0])
        except ValueError:
            return
        entries = self.profile.get("level_up_learnsets", {}).get(str(species))
        if entries is None:
            messagebox.showinfo("提示", "此物种暂无已核对等级招式表。")
            return
        window = tk.Toplevel(self.root)
        window.title("本物种等级招式表 · 仅供来源参考")
        window.geometry("480x520")
        ttk.Label(window, text="0 表示进化招式。此表不涵盖所有学习途径。").pack(pady=8)
        frame = ttk.Frame(window)
        frame.pack(fill="both", expand=True, padx=8)
        tree = ttk.Treeview(
            frame, columns=("level", "id", "move"), show="headings", selectmode="browse"
        )
        for column, label, width in [
            ("level", "等级", 60),
            ("id", "编号", 70),
            ("move", "招式", 260),
        ]:
            tree.heading(column, text=label)
            tree.column(column, width=width)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        for i, (move, level) in enumerate(entries):
            tree.insert(
                "",
                "end",
                iid=str(i),
                values=(level, move, self.names["skills"].get(str(move), str(move))),
            )

        def fill():
            selected = tree.selection()
            if not selected or self.busy:
                return
            move = entries[int(selected[0])][0]
            metadata = self.profile["moves"].get(str(move))
            if not metadata:
                return
            slot = self.move_target.get()
            self.move_vars[slot].set(
                f"{move} - {self.names['skills'].get(str(move), str(move))}"
            )
            self.pp_vars[slot].set(str(metadata["pp"]))
            window.destroy()

        ttk.Button(window, text="填入已勾选的招式槽（不写入）", command=fill).pack(
            pady=10
        )

    def source_report(self, report):
        return "\n".join(
            f"招式 {r['slot']} {self.names['skills'].get(str(r['move']), r['move'])}：{r['text']}"
            for r in report.get("move_sources", [])
        )

    def preview(self):
        try:
            patches, report = self.prepare_mon()
            updated = Pokemon(patches[0][2])
            original = self.snapshot["party"][self.current_slot]
            ratio = self.profile["species"][str(updated.species)]["gender_ratio"]
            nature = self.names.get("pers", {}).get(
                str(updated.pid % 25), str(updated.pid % 25)
            )
            changes = [
                f"{name} {old}→{new}"
                for name, old, new in zip(STAT_NAMES, original.stats, updated.stats)
            ]
            self.detail_preview.set(
                f"检查通过；OT ID：{original.otid:08X} → {updated.otid:08X}；闪光：{'是' if updated.shiny else '否'}\n"
                f"亲密度/周期：{original.friendship} → {updated.friendship}；地点：{original.met_location} → {updated.met_location}；相遇等级：{original.met_level} → {updated.met_level}\n"
                f"捕获球：{original.ball} → {updated.ball}；原训练师性别：{original.ot_gender} → {updated.ot_gender}。来源合法性未完整验证。"
                f"\n蛋：{original.egg} → {updated.egg}；等级：{original.level} → {updated.level}。"
            )
            self.detail_image.configure(image=self.mon_image(updated))
            self.set_report(
                "结构与数值检查通过（不是官方合法性认证）\n"
                + f"PID：{original.pid:08X} → {updated.pid:08X}；闪光：{'是' if updated.shiny else '否'}\n"
                + f"性格：{nature}；性别：{gender(updated.pid, ratio)}；EV 总和：{sum(updated.evs)}\n"
                + "能力值："
                + " / ".join(changes)
                + f"\n当前 HP：{original.hp} → {updated.hp}\n"
                + self.source_report(report)
                + "\n"
                + "\n".join(report["notes"])
            )
        except Exception as exc:
            self.detail_preview.set("未通过：" + str(exc))
            self.set_report("未通过：" + str(exc))
        self.nb.select(self.tab_party)

    def commit(self, patches, label):
        snap = self.snapshot
        pocket = self.pocket_id()

        def job():
            result = self.trainer.commit(snap, patches, label)
            return result, self.trainer.snapshot(pocket)

        def done(result):
            record, snap = result
            self.apply_snapshot(snap)
            self.status.set(
                "写入完成，已读回核对；原数据已备份。"
                if record["changed"]
                else "没有变化，无需写入。"
            )

        self.run("校验并写入…", job, done)

    def write_mon(self):
        try:
            patches, _ = self.prepare_mon()
            self.commit(patches, "宝可梦编辑")
        except Exception as exc:
            messagebox.showerror("未写入", str(exc))

    def write_values(self):
        if self.snapshot is None:
            return
        try:
            self.commit(
                self.trainer.edit_money(
                    self.snapshot, self.money.get(), self.coins.get()
                ),
                "金钱与代币",
            )
        except Exception as exc:
            messagebox.showerror("未写入", str(exc))

    def select_item(self, event=None):
        if self.busy:
            return
        selected = self.bag_tree.selection()
        if not selected:
            return
        if self.current_bag_slot == selected[0]:
            return
        self.current_bag_slot = selected[0]
        values = self.bag_tree.item(selected[0], "values")
        self.item_id.set(f"{values[1]} - {values[2]}")
        self.item_qty.set(values[3] if values[3] != "—" else "")

    def write_item(self, delete=False):
        selected = self.bag_tree.selection()
        if not selected or self.snapshot is None:
            return
        try:
            if self.snapshot["pocket"]["id"] != self.pocket_id():
                raise ValueError("口袋尚未刷新，请稍后重试")
            patches = self.trainer.edit_bag(
                self.snapshot,
                int(selected[0]),
                self.item_id.get().split(" - ", 1)[0],
                self.item_qty.get(),
                delete=delete,
            )
            self.commit(patches, "背包编辑")
        except Exception as exc:
            messagebox.showerror("未写入", str(exc))

    def export(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile="mercury-diagnostic.json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        snap = self.snapshot
        data = {
            "schema": 1,
            "exported_at": datetime.now().astimezone().isoformat(),
            "snapshot_at": self.snapshot_at,
            "last_error": self.last_error,
            "status": self.status.get(),
            "bridge_capabilities": sorted(self.mem.capabilities) if self.mem else [],
            "connection_port": getattr(self.mem, "port", None),
            "rom_profile": self.profile["name"],
            "layout": self.profile["layout"],
        }
        if snap is not None:
            data.update(
                {
                    "party": [
                        {
                            "raw": m.raw.hex(),
                            "species": m.species,
                            "pid": m.pid,
                            "shiny": m.shiny,
                            "ivs": m.ivs,
                            "evs": m.evs,
                        }
                        for m in snap["party"]
                    ],
                    "pocket": snap["pocket"],
                    "bag_hex": snap["bag"].hex(),
                }
            )
        if self.box_snapshot is not None:
            data["box"] = {
                "index": self.box_snapshot["index"],
                "raw_hex": self.box_snapshot["raw"].hex(),
            }
        if self.trainer_snapshot is not None:
            data["trainer"] = {
                **self.trainer_snapshot,
                "raw": self.trainer_snapshot["raw"].hex(),
            }
        try:
            Path(path).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.status.set("已导出连接信息与最近读取的诊断快照")
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))

    def close(self):
        if self.closed:
            return
        if self.busy:
            self.close_requested = True
            self.status.set("正在完成当前操作与备份记录，完成后关闭…")
            return
        self.closed = True
        self.root.after_cancel(self.timer)
        if self.mem:
            self.mem.close()
        self.root.destroy()

    def restore(self):
        if self.trainer is None:
            return
        path = filedialog.askopenfilename(
            title="恢复一次已完成修改之前的数据",
            initialdir=str(self.trainer.backup_dir),
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        pocket = self.pocket_id()
        box_index = (
            self.box_snapshot["index"] if self.box_snapshot is not None else None
        )
        had_trainer = self.trainer_snapshot is not None

        def job():
            result = self.trainer.restore(path)
            box = (
                self.trainer.snapshot_box(box_index) if box_index is not None else None
            )
            trainer_snap = self.trainer.snapshot_trainer() if had_trainer else None
            return result, self.trainer.snapshot(pocket), box, trainer_snap

        def done(result):
            self.apply_snapshot(result[1])
            if result[2] is not None:
                self.apply_box_snapshot(result[2])
            if result[3] is not None:
                self.apply_trainer_snapshot(result[3])
            self.status.set("已恢复并读回核对；恢复动作也已保存备份。")

        self.run("检查备份与当前游戏数据…", job, done)


def main():
    root = tk.Tk()
    root.geometry("1040x740")
    root.minsize(940, 700)
    smoke = "--self-test" in sys.argv
    if smoke:
        root.withdraw()
    app = App(root)
    if smoke:
        root.update_idletasks()
        app.close()
    else:
        root.mainloop()
