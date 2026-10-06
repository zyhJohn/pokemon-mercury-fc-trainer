import base64
import json
import queue
import secrets
import struct
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from box_data import BoxPokemon
from box_preferences import BoxPreferences
from clock_data import (
    WEEKDAYS,
    calendar_text,
    local_epoch,
    parse_calendar,
    read_save,
    restore_calendar,
    write_calendar,
)
from held_forms import held_form_family
from memory_client import MemClient
from pokemon_data import (
    MINIOR_COLORS,
    MINIOR_SPECIES,
    TOXTRICITY_SPECIES,
    STAT_NAMES,
    Pokemon,
    gender,
    gender_choices,
    maximum_pp,
    toxtricity_species,
    unown_form,
)
from pokemon_selector import PokemonSelector
from scrolling_form import ScrollingForm
from spinda_images import read_spinda_assets, spinda_png
from sprite_images import icon_species, read_icons
from trainer_core import Trainer
from rom_versions import load_profile
from version import APP_VERSION
from wiki_catalog import load_catalog, search_rows


def resource_path(name):
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


class App:
    def __init__(self, root):
        self.root = root
        root.title(f"水银 FC 修改器 · {APP_VERSION}")
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
        self.time_snapshot = None
        self.rtc_snapshot = None
        self.rtc_profile = self.profile
        self.rtc_calibrate = False
        self.daily_repair_ready = None
        self.icon_images = {}
        self.icons_enabled = True
        self.icon_request_pending = False
        self.spinda_assets = None
        self.spinda_photos = []
        self.party_form_original = None
        self.box_editor = None
        self.box_editor_values = []
        self.box_editor_original = ()
        self.box_editor_slot = None
        self.current_bag_slot = None
        self.status = tk.StringVar(
            value="未连接。请在 mGBA 加载本项目新版 mercury_bridge.lua"
        )
        self.pocket = tk.StringVar(value="道具")
        self.buttons = []
        self.frozen_widgets = []
        base = (
            Path(sys.executable).parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
        self.box_preferences = BoxPreferences(base / "box-locks.json")
        self.box_save_path = tk.StringVar()
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
            ("关闭微缩图", self.toggle_icons),
        ]:
            widget = self.button(top, text, cmd, side="left", padx=3)
            if cmd == self.toggle_icons:
                self.icon_button = widget
        ttk.Label(self.root, textvariable=self.status, wraplength=900, padding=8).pack(
            fill="x"
        )
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)
        self.tab_values = ttk.Frame(self.nb, padding=12)
        self.nb.add(self.tab_values, text="游戏数值")
        self.money = tk.StringVar()
        self.coins = tk.StringVar()
        self.beauty_points = tk.StringVar()
        self.bracer_points = tk.StringVar()
        for label, var in [
            ("金钱", self.money),
            ("代币", self.coins),
            ("BeautyPoints", self.beauty_points),
            ("BracerPoints", self.bracer_points),
        ]:
            row = ttk.Frame(self.tab_values)
            row.pack(anchor="w", pady=6)
            ttk.Label(row, text=label, width=16).pack(side="left")
            ttk.Entry(row, textvariable=var, width=18).pack(side="left")
        self.button(
            self.tab_values, "写入数值修改", self.write_values, anchor="w", pady=12
        )
        ttk.Label(
            self.tab_values,
            text="金钱：0～9,999,999；代币：0～999,999,999。\n两类点数：0～65,535（存储范围）；写入后请在游戏内保存。",
            wraplength=650,
        ).pack(anchor="w", pady=6)
        ttk.Label(
            self.tab_values,
            text="每次写入前保存原始数据备份，并检查游戏数据是否变化。\n玩家姓名与 ID 在“训练师”页编辑；支持中文姓名；主角性别联动待核验。",
        ).pack(anchor="w")
        shortcuts = ttk.LabelFrame(self.tab_values, text="一键操作 / 金手指", padding=8)
        shortcuts.pack(fill="x", pady=12)
        self.button(
            shortcuts,
            "队伍已有蛋：亲密度／周期归零",
            self.ready_party_eggs,
            anchor="w",
            pady=4,
        )
        self.button(
            shortcuts,
            "选中队伍成员：填满 PP（草稿）",
            self.shortcut_fill_pp,
            anchor="w",
            pady=4,
        )
        ttk.Button(
            shortcuts, text="培育屋生成待领取蛋（待核验）", state="disabled"
        ).pack(anchor="w", pady=4)
        ttk.Button(shortcuts, text="喷雾剩余步数（待核验）", state="disabled").pack(
            anchor="w", pady=4
        )
        ttk.Button(shortcuts, text="持续金手指（待核验）", state="disabled").pack(
            anchor="w", pady=4
        )
        ttk.Label(
            shortcuts,
            text="归零只作用于已有蛋，保留蛋状态；普通精灵亲密度不变。灰色功能尚未开放。",
            wraplength=650,
        ).pack(anchor="w", pady=4)
        self._build_time()
        self.tab_party = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.tab_party, text="队伍")
        self.party_tree = PokemonSelector(
            self.tab_party,
            6,
            2,
            lambda values: f"{values[0]}\n等级 {values[1]}",
        )
        self.party_tree.pack(side="left", fill="y", padx=(0, 10))
        self.party_tree.bind("<<TreeviewSelect>>", self.select_mon)
        self.party_pages = ttk.Notebook(self.tab_party)
        self.party_pages.pack(side="left", fill="both", expand=True)
        basic_scroll = ScrollingForm(self.party_pages, padding=6)
        form = basic_scroll.body
        self.tab_party_basic = basic_scroll
        self.party_pages.add(basic_scroll, text="基本 / 能力")
        self.detail_vars = {"friendship": tk.StringVar()}
        self.egg = tk.BooleanVar()
        ttk.Checkbutton(
            form, text="蛋状态（转换后先检查预览）", variable=self.egg
        ).pack(anchor="w", pady=4)
        friend_row = ttk.Frame(form)
        friend_row.pack(anchor="w", pady=4)
        ttk.Label(friend_row, text="亲密度 / 孵化周期（0～255）").pack(side="left")
        ttk.Entry(
            friend_row, textvariable=self.detail_vars["friendship"], width=8
        ).pack(side="left", padx=6)
        self.detail_image = ttk.Label(form)
        self.detail_image.pack(anchor="w")
        self.species = tk.StringVar()
        self.level = tk.StringVar()
        self.hp = tk.StringVar()
        self.held = tk.StringVar()
        self.identity = tk.StringVar()
        self.shiny = tk.BooleanVar()
        self.nature = tk.StringVar()
        self.target_gender = tk.StringVar(value="保持当前")
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
        ttk.Label(fields, text="宝可梦性别").grid(row=7, column=0, sticky="w")
        self.gender_cb = ttk.Combobox(
            fields,
            textvariable=self.target_gender,
            state="readonly",
            width=30,
            values=["保持当前"],
        )
        self.gender_cb.grid(row=7, column=1, columnspan=3, sticky="w", pady=3)
        self.species.trace_add("write", lambda *_: self.update_gender_choices())
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
        basic_scroll.enable_navigation()
        self.tab_moves = ttk.Frame(self.party_pages, padding=12)
        self.party_pages.add(self.tab_moves, text="招式 / PP")
        ttk.Label(self.tab_moves, textvariable=self.identity, wraplength=900).pack(
            anchor="w", pady=6
        )
        ttk.Label(
            self.tab_moves,
            text="先在“宝可梦编辑”选择队伍成员。更换招式会清除此槽的 PP 提升次数。\n预览提供等级学习表和学习器来源提示；遗传、教学与进化前等来源尚未核对。",
        ).pack(anchor="w", pady=5)
        self.move_vars = [tk.StringVar() for _ in range(4)]
        self.pp_vars = [tk.StringVar() for _ in range(4)]
        self.pp_up_vars = [tk.StringVar(value="0") for _ in range(4)]
        self.pp_max_vars = [tk.StringVar(value="上限 —") for _ in range(4)]
        for var in self.pp_up_vars:
            var.trace_add("write", lambda *_: self.update_pp_limits())
        for i, var in enumerate(self.move_vars):
            var.trace_add("write", lambda *_, slot=i: self.normalize_empty_move(slot))
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
            ttk.Label(row, text="提升", padding=(8, 0)).pack(side="left")
            ttk.Combobox(
                row,
                textvariable=self.pp_up_vars[i],
                values=(0, 1, 2, 3),
                state="readonly",
                width=3,
            ).pack(side="left")
            ttk.Label(row, textvariable=self.pp_max_vars[i], padding=(8, 0)).pack(
                side="left"
            )
        self.button(
            self.tab_moves,
            "已有招式满提升（草稿）",
            self.max_pp_ups,
            anchor="w",
            pady=6,
        )
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
        self.button(bar, "按编号排序", self.sort_items, side="left", padx=8)
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
        self.nb.insert(0, self.tab_party)
        self.nb.insert(1, self.tab_boxes)
        self.nb.insert(2, self.tab_trainer)
        self.nb.select(self.tab_party)

    def _build_details(self):
        scroller = ScrollingForm(self.party_pages)
        self.party_pages.add(scroller, text="来源 / 原训练师")
        tab = scroller.body
        ttk.Label(tab, textvariable=self.identity, wraplength=900).pack(
            anchor="w", pady=6
        )
        ttk.Label(
            tab,
            text="先选择队伍成员。地点可按中文名搜索选择，原有未知编号保持不变；捕获球按本改版编号填写。\n修改原训练师 ID 时保留当前选择的闪光状态；字段有效不代表遭遇来源已认证。\n转为蛋时同步两处标志、设为1级及默认周期；取消蛋标记不等于执行自然孵化。\n颤弦蝾螈修改性格时同步高调/低调形态，预览会显示相应特性变化。",
            wraplength=900,
        ).pack(anchor="w", pady=6)
        for key, label in [
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
            if key == "met_location":
                self.location_entry(row, var, 28).pack(side="left")
            else:
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
        row = ttk.Frame(tab)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="小陨星核心颜色（含闪光时的原核心）", width=38).pack(
            side="left"
        )
        self.minior_color = tk.StringVar(value="不适用")
        ttk.Combobox(
            row,
            textvariable=self.minior_color,
            state="readonly",
            values=["保持当前"]
            + [f"{i} - {name}" for i, name in enumerate(MINIOR_COLORS)],
            width=18,
        ).pack(side="left")
        row = ttk.Frame(tab)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="原训练师姓名（最多3汉字或7字节）", width=38).pack(
            side="left"
        )
        self.ot_name = tk.StringVar()
        self.original_ot_name = ""
        ttk.Entry(row, textvariable=self.ot_name, width=18).pack(side="left")
        row = ttk.Frame(tab)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="宝可梦昵称（最多5汉字或10字节）", width=38).pack(
            side="left"
        )
        self.nickname = tk.StringVar()
        self.original_nickname = ""
        ttk.Entry(row, textvariable=self.nickname, width=20).pack(side="left")
        self.button(
            tab,
            "填入当前玩家的原训练师资料（待预览）",
            self.fill_player_ot,
            anchor="w",
            pady=4,
        )
        self.spinda_seed = tk.StringVar()
        self.button(
            tab,
            "重新生成晃晃斑花纹（待预览）",
            self.new_spinda_pattern,
            anchor="w",
            pady=4,
        )
        self.spinda_caption = tk.StringVar(
            value="晃晃斑花纹需先加载游戏图像；重新生成后检查预览再写入。"
        )
        ttk.Label(tab, textvariable=self.spinda_caption, wraplength=600).pack(
            anchor="w", pady=4
        )
        images = ttk.Frame(tab)
        images.pack(anchor="w")
        self.spinda_before = ttk.Label(images)
        self.spinda_before.pack(side="left", padx=8)
        self.spinda_after = ttk.Label(images)
        self.spinda_after.pack(side="left", padx=8)
        self.button(tab, "检查与预览", self.preview, anchor="w", pady=8)
        self.button(tab, "写入当前宝可梦", self.write_mon, anchor="w", pady=8)
        self.detail_preview = tk.StringVar()
        ttk.Label(tab, textvariable=self.detail_preview, wraplength=900).pack(
            anchor="w", pady=8
        )
        scroller.enable_navigation()

    def location_label(self, ident):
        return f"{ident} - {self.profile.get('met_locations', {}).get(str(ident), '未核实地点，保留原值')}"

    def fill_player_ot(self):
        if self.trainer is None or self.current_slot is None or self.busy:
            return
        trainer, slot = self.trainer, self.current_slot
        original = self.snapshot["party"][slot].raw

        def done(result):
            if (
                self.trainer is not trainer
                or self.current_slot != slot
                or self.snapshot is None
                or slot >= len(self.snapshot["party"])
                or self.snapshot["party"][slot].raw != original
            ):
                self.status.set("选中成员已变化，未填入玩家资料。")
                return
            values, note = result
            for key in ("ot_tid", "ot_sid", "ot_gender"):
                self.detail_vars[key].set(str(values[key]))
            if "ot_name" in values:
                self.ot_name.set(values["ot_name"])
            self.detail_preview.set(note)
            self.status.set(note)

        self.run("读取当前玩家资料…", trainer.read_player_ot, done)

    def nature_form_summary(self, original, updated):
        if updated.species not in TOXTRICITY_SPECIES:
            return ""

        def form(mon):
            return (
                "高调"
                if mon.species == 1141
                else "低调"
                if mon.species == 1193
                else str(mon.species)
            )

        def ability(mon):
            slots = self.profile["species"][str(mon.species)]["abilities"]
            slot = (
                2 if mon.ability_flag and slots[2] else mon.pid & 1 if slots[1] else 0
            )
            ident = slots[slot]
            return self.names["specs"].get(str(ident), str(ident))

        decision = (
            "由所选性格决定"
            if updated.species == toxtricity_species(updated.pid % 25)
            else "原有形态与性格不匹配，保持原值"
        )
        return (
            f"\n颤弦蝾螈形态：{form(original)} → {form(updated)}（{decision}）；"
            f"特性：{ability(original)} → {ability(updated)}。"
        )

    def location_entry(self, parent, var, width):
        choices = [
            self.location_label(ident)
            for ident in sorted(map(int, self.profile.get("met_locations", {})))
        ]
        entry = ttk.Combobox(parent, textvariable=var, values=choices, width=width)
        entry.bind(
            "<KeyRelease>",
            lambda _: entry.configure(
                values=[
                    value
                    for value in choices
                    if var.get().casefold() in value.casefold()
                ]
            ),
        )
        return entry

    def held_item_entry(self, parent, var):
        choices = ["0 - 无"] + [
            f"{ident} - {self.item_name(ident)}"
            for ident in sorted(map(int, self.profile["items"]))
            if ident and self.profile["items"][str(ident)]["pocket"] not in (2, 4)
        ]
        entry = ttk.Combobox(parent, textvariable=var, values=choices, width=28)
        entry.bind(
            "<KeyRelease>",
            lambda _: entry.configure(
                values=[
                    value
                    for value in choices
                    if var.get().casefold() in value.casefold()
                ]
            ),
        )
        return entry

    def held_form_summary(self, original, updated):
        if held_form_family(updated.species) is None:
            return ""

        def name(mon):
            return self.names["breeds"].get(str(mon.species), str(mon.species))

        def ability(mon):
            slots = self.profile["species"][str(mon.species)]["abilities"]
            slot = (
                2 if mon.ability_flag and slots[2] else mon.pid & 1 if slots[1] else 0
            )
            return self.names["specs"].get(str(slots[slot]), str(slots[slot]))

        return (
            f"\n持物形态：{name(original)} → {name(updated)}；"
            f"携带道具：{self.item_name(original.held)} → {self.item_name(updated.held)}；"
            f"特性：{ability(original)} → {ability(updated)}。"
        )

    def _build_time(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="时间 / 星期")
        self.time_form = ScrollingForm(tab, padding=0)
        self.time_form.pack(fill="both", expand=True)
        live = ttk.LabelFrame(self.time_form.body, text="正在运行的游戏", padding=8)
        live.pack(fill="x", pady=4)
        self.time_detail = tk.StringVar(
            value="连接后读取：RTC 日历、星期、累计游玩时长。"
        )
        ttk.Label(live, textvariable=self.time_detail, wraplength=850).pack(anchor="w")
        row = ttk.Frame(live)
        row.pack(anchor="w", pady=6)
        self.play_hours = tk.StringVar()
        self.play_minutes = tk.StringVar()
        self.play_seconds = tk.StringVar()
        for label, var in [
            ("累计小时", self.play_hours),
            ("分", self.play_minutes),
            ("秒", self.play_seconds),
        ]:
            ttk.Label(row, text=label).pack(side="left", padx=3)
            ttk.Entry(row, textvariable=var, width=7).pack(side="left")
        self.button(row, "读取游戏时间", self.read_time, side="left", padx=8)
        self.button(row, "写入累计时长", self.write_playtime, side="left")
        ttk.Label(
            live,
            text="累计时长：0～999 小时、0～59 分/秒；按填写的绝对时长应用。保持 mGBA 运行，写入后在游戏内保存。",
            wraplength=850,
        ).pack(anchor="w")
        self.daily_detail = tk.StringVar(value="引擎每日刷新记录：尚未读取。")
        ttk.Label(live, textvariable=self.daily_detail, wraplength=850).pack(
            anchor="w", pady=4
        )
        row = ttk.Frame(live)
        row.pack(anchor="w", pady=3)
        self.button(
            row, "预览每日日期修复", self.preview_daily_repair, side="left", padx=3
        )
        self.button(
            row, "写入每日日期修复", self.write_daily_repair, side="left", padx=3
        )
        saved = ttk.LabelFrame(
            self.time_form.body, text=".sav 中的持久 RTC（先关闭该游戏）", padding=8
        )
        saved.pack(fill="x", pady=8)
        self.rtc_rom_path = tk.StringVar()
        self.rtc_save_path = tk.StringVar()
        for label, var, callback in [
            ("本地 ROM", self.rtc_rom_path, self.choose_time_rom),
            ("游戏存档", self.rtc_save_path, self.choose_time_save),
        ]:
            row = ttk.Frame(saved)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=10).pack(side="left")
            ttk.Entry(row, textvariable=var, state="readonly").pack(
                side="left", fill="x", expand=True
            )
            self.button(row, "选择", callback, side="left", padx=5)
        self.rtc_detail = tk.StringVar(
            value="选择匹配本版的 ROM 与 .sav，再读取。无需连接桥接。"
        )
        ttk.Label(saved, textvariable=self.rtc_detail, wraplength=850).pack(
            anchor="w", pady=6
        )
        row = ttk.Frame(saved)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="目标本地时间").pack(side="left", padx=3)
        self.rtc_target = tk.StringVar()
        ttk.Entry(row, textvariable=self.rtc_target, width=25).pack(side="left")
        self.button(
            row, "校准到电脑时间（填入预览）", self.fill_time_now, side="left", padx=8
        )
        row = ttk.Frame(saved)
        row.pack(anchor="w", pady=3)
        ttk.Label(row, text="切换至同一周的").pack(side="left", padx=3)
        self.rtc_weekday = tk.StringVar()
        weekday = ttk.Combobox(
            row,
            textvariable=self.rtc_weekday,
            values=WEEKDAYS,
            width=8,
            state="readonly",
        )
        weekday.pack(side="left")
        weekday.bind("<<ComboboxSelected>>", self.choose_time_weekday)
        self.rtc_preview = tk.StringVar(
            value="格式：YYYY-MM-DD HH:MM:SS；星期由日期自动计算。"
        )
        ttk.Label(saved, textvariable=self.rtc_preview, wraplength=850).pack(
            anchor="w", pady=4
        )
        self.rtc_target.trace_add("write", self.update_time_preview)
        row = ttk.Frame(saved)
        row.pack(anchor="w", pady=4)
        self.button(row, "读取 .sav 时间", self.read_saved_time, side="left", padx=3)
        self.button(row, "写入 RTC 修改", self.write_saved_time, side="left", padx=3)
        self.button(row, "恢复 RTC 备份", self.restore_saved_time, side="left", padx=3)
        ttk.Label(
            saved,
            text="仅支持 mGBA 0.10.5 带 RTC 尾部的 .sav；年份 2000～2099。写入前保留完整存档备份。\n"
            "写入后重新打开 ROM，从游戏内存档继续；旧即时存档可能覆盖时钟状态。关闭 mGBA 的自定义 RTC 覆盖，\n"
            "使用相同的电脑时区。时间回拨可能使每日事件等待原日期；本功能保留每日事件历史记录。",
            wraplength=850,
        ).pack(anchor="w", pady=5)
        self.time_form.enable_navigation()

    def apply_time_snapshot(self, snap):
        self.time_snapshot = snap
        self.daily_repair_ready = None
        daily = snap["daily_date"]
        daily_text = "引擎每日刷新记录（Var5009/500A）："
        if snap["daily_error"]:
            daily_text += snap["daily_error"]
        elif daily is None:
            daily_text += "尚未设置"
        else:
            daily_text += calendar_text(daily)
            if daily.date() > snap["clock"].date():
                daily_text += (
                    "；处于未来，可能阻止当天刷新。可先预览修复。"
                    if self.profile["time"]["daily_event"].get(
                        "future_blocks_refresh", True
                    )
                    else "；处于未来。V1.2 的跨日判断已改变，可按需预览日期修复。"
                )
            else:
                daily_text += "；没有未来日期异常。"
        self.daily_detail.set(daily_text)
        for var, value in zip(
            (self.play_hours, self.play_minutes, self.play_seconds),
            snap["playtime"][:3],
        ):
            var.set(str(value))
        note = "（游戏已开启上午/下午对调）" if snap["invert_ampm"] else ""
        if snap.get("virtual_clock"):
            note += "；当前为 V1.2 游戏内虚拟时钟，修改 RTC 文件不会覆盖它"
        if snap["weekday_mismatch"]:
            note += "；游戏星期缓存与日期不一致"
        if snap["rtc_error"]:
            note += f"；RTC 错误标志 0x{snap['rtc_error']:04X}"
        self.time_detail.set(
            "游戏日历："
            + calendar_text(snap["clock"])
            + note
            + "\n"
            + "读取时间："
            + datetime.now().strftime("%H:%M:%S")
            + "；日历缓存只读，持久修改在下方操作。"
        )

    def read_time(self):
        if self.trainer is None or self.busy:
            return
        trainer = self.trainer
        self.run(
            "读取游戏 RTC 与累计时长…", trainer.snapshot_time, self.apply_time_snapshot
        )

    def preview_daily_repair(self):
        if self.busy or self.time_snapshot is None or self.trainer is None:
            return
        try:
            before, target = self.trainer.daily_repair_preview(self.time_snapshot)
        except ValueError as exc:
            self.daily_repair_ready = None
            messagebox.showinfo("每日日期修复", str(exc))
            return
        self.daily_repair_ready = self.time_snapshot
        self.daily_detail.set(
            "修复预览："
            + calendar_text(before)
            + " → "
            + calendar_text(target.replace(second=0))
            + "\n仅校准引擎每日刷新记录到昨天，让游戏重新执行当天刷新；其他领取标志保持原样。"
        )

    def write_daily_repair(self):
        if self.busy or self.trainer is None:
            return
        if self.daily_repair_ready is None:
            messagebox.showinfo("先预览", "请先读取游戏时间并预览每日日期修复。")
            return
        trainer, snapshot = self.trainer, self.daily_repair_ready
        self.daily_repair_ready = None

        def job():
            result = trainer.commit_daily_repair(snapshot)
            return result, trainer.snapshot_time()

        def done(result):
            self.apply_time_snapshot(result[1])
            self.status.set(
                "每日刷新日期已核对；请在游戏内保存后重新进入地图或重开游戏。备份："
                + str(result[0]["backup"])
            )

        self.run("检查日期、备份并校准每日刷新记录…", job, done)

    def write_playtime(self):
        if self.time_snapshot is None or self.trainer is None or self.busy:
            return
        trainer, snap = self.trainer, self.time_snapshot
        values = (
            self.play_hours.get(),
            self.play_minutes.get(),
            self.play_seconds.get(),
        )

        def job():
            result = trainer.commit_playtime(snap, *values)
            return result, trainer.snapshot_time()

        def done(result):
            self.apply_time_snapshot(result[1])
            self.status.set(
                "累计时长已读回核对；请在游戏内保存。备份：" + str(result[0]["backup"])
            )

        self.run("检查并写入累计游玩时长…", job, done)

    def choose_time_rom(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(
            title="选择已验证的水银 FC ROM", filetypes=[("GBA ROM", "*.gba")]
        )
        if path:
            self.rtc_rom_path.set(path)
            self.rtc_snapshot = None
            self.rtc_detail.set("ROM 已改变，请重新读取 .sav。")

    def choose_time_save(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(
            title="选择游戏内保存的 .sav（不选择 .ss1）",
            filetypes=[("游戏存档", "*.sav")],
        )
        if path:
            self.rtc_save_path.set(path)
            self.rtc_snapshot = None
            self.rtc_detail.set("存档已改变，请重新读取。")

    def time_backup_dir(self):
        base = (
            Path(sys.executable).parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
        return base / "backups"

    def apply_saved_time(self, snap):
        self.rtc_snapshot = snap
        hours, minutes, seconds, _ = snap["playtime"]
        text = (
            "RTC 保存记录："
            + calendar_text(snap["saved"])
            + "\n"
            + "按当前电脑时区推算："
            + calendar_text(snap["current"])
            + f"；时钟偏移（电脑减游戏）{snap['offset']:+d} 秒\n"
            + f"存档累计时长：{hours:03d}:{minutes:02d}:{seconds:02d}；存档次数 {snap['counter']}"
        )
        if snap["weekday_mismatch"]:
            text += "；RTC 星期字节异常，写入时会按日期校正"
        if "virtual_clock" in self.rtc_profile.get("time", {}):
            text += "\nV1.2 若启用游戏内虚拟时钟，RTC 文件校准不会覆盖该时钟设置。"
        self.rtc_detail.set(text)
        self.rtc_target.set(snap["current"].strftime("%Y-%m-%d %H:%M:%S"))

    def read_saved_time(self):
        if self.busy:
            return
        path, rom = self.rtc_save_path.get(), self.rtc_rom_path.get()
        if not path or not rom:
            messagebox.showinfo("选择文件", "请先选择本地 ROM 和 .sav。")
            return
        self.rtc_snapshot = None

        def job():
            profile = load_profile(
                Path(rom).read_bytes(), root=resource_path("rom_profile.json").parent
            )
            return read_save(path, rom, profile), profile

        def done(result):
            self.rtc_profile = result[1]
            self.apply_saved_time(result[0])

        self.run(
            "核对 ROM 并读取存档 RTC…",
            job,
            done,
        )

    def update_time_preview(self, *_):
        self.rtc_calibrate = False
        try:
            target = parse_calendar(self.rtc_target.get())
            self.rtc_weekday.set(WEEKDAYS[target.weekday()])
            offset = int(datetime.now().timestamp()) - local_epoch(target)
            self.rtc_preview.set(
                "指定时间预览："
                + calendar_text(target)
                + f"；预计偏移 {offset:+d} 秒。星期随日期同步。"
            )
        except ValueError as exc:
            self.rtc_weekday.set("")
            self.rtc_preview.set(str(exc))

    def choose_time_weekday(self, event=None):
        if self.busy:
            return
        try:
            target = parse_calendar(self.rtc_target.get())
            selected = WEEKDAYS.index(self.rtc_weekday.get())
            target += timedelta(days=selected - target.weekday())
            self.rtc_target.set(target.strftime("%Y-%m-%d %H:%M:%S"))
        except ValueError as exc:
            messagebox.showerror("未修改", str(exc))

    def fill_time_now(self):
        if self.busy:
            return
        value = datetime.now().replace(microsecond=0)
        self.rtc_target.set(value.strftime("%Y-%m-%d %H:%M:%S"))
        self.rtc_calibrate = True
        self.rtc_preview.set(
            "校准预览：" + calendar_text(value) + "；写入时采用电脑当前时间，偏移为 0。"
        )

    def write_saved_time(self):
        if self.busy or self.rtc_snapshot is None:
            return
        snapshot, rom, profile = (
            self.rtc_snapshot,
            self.rtc_rom_path.get(),
            self.rtc_profile,
        )
        target, calibrate = self.rtc_target.get(), self.rtc_calibrate
        folder = self.time_backup_dir()

        def job():
            result = write_calendar(snapshot, target, rom, profile, folder, calibrate)
            return result, read_save(snapshot["path"], rom, profile)

        def done(result):
            self.apply_saved_time(result[1])
            self.status.set(
                "RTC 修改已读回核对；请重新打开游戏并从游戏内存档继续。备份："
                + str(result[0]["backup"])
            )

        self.run("独占检查、备份并写入 RTC 尾部…", job, done)

    def restore_saved_time(self):
        if self.busy or self.rtc_snapshot is None:
            return
        folder = self.time_backup_dir()
        path = filedialog.askopenfilename(
            title="选择已完成的 RTC 备份记录",
            initialdir=str(folder),
            filetypes=[("RTC 备份记录", "*-rtc-*.json")],
        )
        if not path:
            return
        snapshot, rom, profile = (
            self.rtc_snapshot,
            self.rtc_rom_path.get(),
            self.rtc_profile,
        )

        def job():
            result = restore_calendar(snapshot, path, rom, profile, folder)
            return result, read_save(snapshot["path"], rom, profile)

        def done(result):
            self.apply_saved_time(result[1])
            self.status.set(
                "RTC 原偏移已条件恢复并读回；恢复动作也已备份。请重新打开游戏。"
            )

        self.run("核对存档与 RTC 备份后恢复…", job, done)

    def _build_trainer(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.tab_trainer = tab
        self.nb.add(tab, text="训练师")
        self.player_tid = tk.StringVar()
        self.player_sid = tk.StringVar()
        self.player_name = tk.StringVar()
        self.original_player_name = ""
        self.player_detail = tk.StringVar(
            value="连接后点击读取。支持中文/中英混合姓名；主角性别目前只读。"
        )
        ttk.Label(tab, textvariable=self.player_detail, wraplength=900).pack(
            anchor="w", pady=8
        )
        for label, var in [
            ("姓名（最多3汉字或7字节）", self.player_name),
            ("玩家 TID（0～65535）", self.player_tid),
            ("玩家 SID（0～65535）", self.player_sid),
        ]:
            row = ttk.Frame(tab)
            row.pack(anchor="w", pady=6)
            ttk.Label(row, text=label, width=30).pack(side="left")
            ttk.Entry(row, textvariable=var, width=18).pack(side="left")
        ttk.Label(
            tab,
            text="修改玩家姓名 / ID，不自动改变队伍或 PC 的原训练师资料。\n现有宝可梦可能因此被视为外来宝可梦；修改前后请核对训练师卡。",
            wraplength=900,
        ).pack(anchor="w", pady=8)
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
            f"完整 ID：{snap['sid'] * 65536 + snap['tid']:08X}；主角性别原始值：{snap['gender']}\n姓名原始编码：{snap['name_raw']}；汉字占2字节，英文/数字占1字节，总计最多7字节。"
        )

    def read_trainer(self):
        if (
            not self.busy
            and self.trainer is not None
            and self.discard_trainer_changes()
        ):
            self.run(
                "读取训练师资料…",
                self.trainer.snapshot_trainer,
                self.apply_trainer_snapshot,
            )

    def discard_trainer_changes(self):
        snap = self.trainer_snapshot
        return (
            snap is None
            or (self.player_name.get(), self.player_tid.get(), self.player_sid.get())
            == (self.original_player_name, str(snap["tid"]), str(snap["sid"]))
            or messagebox.askyesno(
                "尚未写入",
                "训练师资料的修改尚未写入。放弃修改并继续？",
                parent=self.root,
            )
        )

    def toggle_icons(self):
        if self.busy:
            return
        self.icons_enabled = not self.icons_enabled
        self.icon_button.configure(
            text="关闭微缩图" if self.icons_enabled else "显示微缩图"
        )
        if self.icons_enabled:
            self.load_icons()
        else:
            self.icon_images.clear()
            self.spinda_assets = None
            self.spinda_photos.clear()
            self.detail_image.configure(image="")
            for tree in (self.party_tree, self.box_tree):
                for item in tree.get_children():
                    tree.item(item, image="")
            for label in [
                self.spinda_before,
                self.spinda_after,
                *getattr(self, "box_pattern_labels", []),
            ]:
                if label.winfo_exists():
                    label.configure(image="")
            self.status.set("微缩图已关闭；本次会话刷新后保持关闭。")

    def request_icons(self):
        if self.icons_enabled:
            self.icon_request_pending = True

    def load_icons(self):
        if (
            self.busy
            or not self.icons_enabled
            or self.trainer is None
            or self.snapshot is None
        ):
            return
        self.icon_request_pending = False
        trainer = self.trainer
        profile = trainer.profile
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
            result = read_icons(trainer.g, profile, pokemon)
            assets = (
                read_spinda_assets(trainer.g, profile)
                if any(mon.species == 308 for mon in pokemon)
                else None
            )
            trainer.verify()
            return result, assets

        def done(result):
            if trainer is not self.trainer or not self.icons_enabled:
                return
            if result[1] is not None:
                self.spinda_assets = result[1]
            for ident, png in result[0].items():
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
                self.show_spinda_patterns(preview_mon)
            self.status.set(f"{profile['name']}；已加载微缩图。")

        self.run("读取游戏微缩图…", job, done)

    def mon_image(self, mon):
        if not self.icons_enabled:
            return ""
        return self.icon_images.get(icon_species(mon, self.profile), "")

    def new_spinda_pattern(self):
        if self.busy or self.current_slot is None or self.snapshot is None:
            return
        if self.snapshot["party"][self.current_slot].species != 308:
            self.status.set("花纹重新生成仅适用于晃晃斑。")
            return
        self.spinda_seed.set(str(secrets.randbits(32)))
        self.preview()

    def show_spinda_patterns(self, updated):
        self.spinda_before.configure(image="")
        self.spinda_after.configure(image="")
        self.spinda_photos = []
        if self.snapshot is None or self.current_slot is None:
            return
        original = self.snapshot["party"][self.current_slot]
        if (
            original.species != 308
            or updated.species != 308
            or self.spinda_assets is None
        ):
            self.spinda_caption.set(
                "晃晃斑花纹需先加载游戏图像；重新生成后检查预览再写入。"
            )
            return
        for mon, label in [
            (original, self.spinda_before),
            (updated, self.spinda_after),
        ]:
            photo = tk.PhotoImage(
                master=self.root,
                data=base64.b64encode(
                    spinda_png(self.spinda_assets, mon.pid, mon.shiny)
                ),
            ).zoom(2)
            self.spinda_photos.append(photo)
            label.configure(image=photo)
        self.spinda_caption.set(
            f"左：当前 {original.pid:08X}；右：预览 {updated.pid:08X}。{'花纹将改变，尚未写入。' if original.pid != updated.pid else '花纹保持。'}"
        )

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
        self.nb.add(tab, text="盒子编辑")
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
        ttk.Label(row, text="移到第").pack(side="left", padx=(10, 2))
        self.box_move_target = tk.StringVar(value="2")
        ttk.Combobox(
            row,
            textvariable=self.box_move_target,
            values=list(range(1, 26)),
            state="readonly",
            width=5,
        ).pack(side="left")
        self.button(row, "移动", self.move_box_mon, side="left", padx=6)
        lock_row = ttk.Frame(tab)
        lock_row.pack(fill="x", pady=6)
        self.button(
            lock_row, "关联存档（本地盒锁）", self.choose_box_save, side="left", padx=3
        )
        self.box_lock = tk.BooleanVar()
        ttk.Checkbutton(
            lock_row,
            text="锁定当前盒",
            variable=self.box_lock,
            command=self.toggle_box_lock,
        ).pack(side="left", padx=6)
        self.button(
            lock_row,
            "全部未锁盒按内部编号排序",
            self.sort_all_boxes,
            side="left",
            padx=3,
        )
        self.box_egg_ready_button = self.button(
            lock_row, "快速生蛋（仅已有蛋）", self.ready_box_egg, side="left", padx=3
        )
        self.box_egg_ready_button.configure(state="disabled")
        self.box_lock_detail = tk.StringVar(
            value="盒锁保存在本地配置；请关联当前游戏的 .sav，不修改存档文件。"
        )
        ttk.Label(tab, textvariable=self.box_lock_detail, wraplength=850).pack(
            anchor="w"
        )
        ttk.Label(
            tab,
            text="选择左侧格子，在右侧编辑能力、形态和来源资料。盒内能力值由游戏在取出时计算。",
        ).pack(anchor="w", pady=8)
        box = ttk.Frame(tab)
        box.pack(fill="both", expand=True)
        self.box_tree = PokemonSelector(
            box,
            30,
            6,
            lambda values: (
                f"{values[0]}. {values[1]}{' ★' if values[3] == '是' else ''}\n等级 {values[2]}"
            ),
            width=10,
        )
        self.box_tree.pack(side="left", fill="both", expand=True)
        self.box_editor_host = ttk.Frame(box, padding=(12, 0, 0, 0))
        self.box_editor_host.pack(side="left", fill="both", expand=True)
        self.box_tree.bind("<<TreeviewSelect>>", self.select_box_mon)
        for card in self.box_tree.cards:
            card.bind("<Button-3>", self.box_context_menu)
        self.box_detail = tk.StringVar(value="连接后选择盒子并点击读取。")
        ttk.Label(tab, textvariable=self.box_detail, wraplength=900).pack(
            anchor="w", pady=10
        )

    def choose_box_save(self):
        if self.busy or self.trainer is None:
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择当前游戏存档：只关联本地盒锁，不读取或修改存档",
            filetypes=[("游戏存档", "*.sav")],
        )
        if not path:
            return
        try:
            locked = self.box_preferences.load(self.profile["rom_sha256"], path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("未关联盒锁", str(exc), parent=self.root)
            return
        if not self.discard_box_changes():
            return
        self.box_save_path.set(path)
        self.trainer.locked_boxes = locked
        self.update_box_lock()

    def update_box_lock(self):
        locked = self.trainer.locked_boxes if self.trainer else set()
        index = (
            self.box_snapshot["index"]
            if self.box_snapshot
            else int(self.box_number.get()) - 1
        )
        self.box_lock.set(index in locked)
        boxes = "、".join(str(i + 1) for i in sorted(locked)) or "无"
        path = self.box_save_path.get()
        self.box_lock_detail.set(
            f"关联存档：{Path(path).name if path else '尚未选择'}；锁定盒：{boxes}。本地盒锁不影响游戏自身操作。"
        )
        self.update_box_egg_button()

    def update_box_egg_button(self):
        selected = self.box_tree.selection()
        mon = (
            self.box_snapshot["pokemon"][int(selected[0])]
            if self.box_snapshot and selected
            else None
        )
        enabled = (
            mon
            and mon.species
            and mon.egg
            and self.trainer
            and self.box_snapshot["index"] not in self.trainer.locked_boxes
            and not self.busy
        )
        self.box_egg_ready_button.configure(state="normal" if enabled else "disabled")

    def toggle_box_lock(self):
        if self.busy or self.trainer is None or self.box_snapshot is None:
            self.update_box_lock()
            return
        if not self.box_save_path.get():
            desired = self.box_lock.get()
            self.choose_box_save()
            if not self.box_save_path.get():
                self.update_box_lock()
                return
            self.box_lock.set(desired)
        if not self.discard_box_changes():
            self.update_box_lock()
            return
        locked = set(self.trainer.locked_boxes)
        index = self.box_snapshot["index"]
        if self.box_lock.get():
            locked.add(index)
        else:
            locked.discard(index)
        try:
            self.box_preferences.save(
                self.profile["rom_sha256"], self.box_save_path.get(), locked
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("盒锁未保存", str(exc), parent=self.root)
        else:
            self.trainer.locked_boxes = locked
        self.update_box_lock()

    def sort_all_boxes(self):
        if self.busy or self.trainer is None or not self.discard_box_changes():
            return
        trainer = self.trainer
        locked = "、".join(str(i + 1) for i in sorted(trainer.locked_boxes)) or "无"
        if not messagebox.askokcancel(
            "全部盒子排序",
            f"此排序为全部盒子排序：按内部编号汇总排序，依次放回未锁定盒子，精灵可能跨盒移动。\n\n如有不想移动的盒子，请先锁定。\n当前锁定盒：{locked}\n\n是否继续读取排序预览？",
            parent=self.root,
        ):
            return

        def preview(prepared):
            changed = sum(before != after for _, before, after in prepared["patches"])
            if not changed:
                self.status.set("全部未锁盒已经按内部编号排序，无需写入。")
                return
            if not messagebox.askokcancel(
                "确认全盒排序",
                f"参与 {len(prepared['indices'])} 盒，共 {prepared['count']} 只；将改变 {changed} 盒。\n同编号保持原顺序，空槽在末尾，完整个体记录保留。\n\n确认备份并写入？",
                parent=self.root,
            ):
                return
            index = self.box_snapshot["index"] if self.box_snapshot else 0

            def job():
                result = trainer.commit_box_sort(prepared)
                return result, trainer.snapshot_box(index)

            def done(result):
                self.apply_box_snapshot(result[1])
                self.status.set(
                    "全部未锁盒排序已备份、写入及读回。请在游戏内保存；恢复备份前先解锁相关盒子。"
                )

            self.run("备份并统一比较、写入全部未锁盒…", job, done)

        self.run("读取全部未锁盒并生成排序预览…", trainer.prepare_box_sort, preview)

    def box_context_menu(self, event):
        if self.busy or self.box_snapshot is None:
            return "break"
        slot = self.box_tree.cards.index(event.widget)
        self.box_tree.selection_set(str(slot))
        self.select_box_mon()
        if self.box_tree.selection() != (str(slot),):
            return "break"
        mon = self.box_snapshot["pokemon"][slot]
        locked = self.box_snapshot["index"] in self.trainer.locked_boxes
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(
            label="编辑",
            command=self.edit_box_dialog,
            state="normal" if mon.species and not locked else "disabled",
        )
        menu.add_command(
            label="移动",
            command=self.move_box_mon,
            state="normal" if mon.species and not locked else "disabled",
        )
        menu.add_command(
            label="快速生蛋",
            command=self.ready_box_egg,
            state="normal" if mon.species and mon.egg and not locked else "disabled",
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def ready_box_egg(self):
        if (
            self.busy
            or self.trainer is None
            or self.box_snapshot is None
            or not self.box_tree.selection()
        ):
            return
        trainer, snap = self.trainer, self.box_snapshot
        slot = int(self.box_tree.selection()[0])
        try:
            patches, _ = trainer.prepare_box_egg_ready(snap, slot)
        except ValueError as exc:
            messagebox.showinfo("快速生蛋", str(exc), parent=self.root)
            return
        if not self.discard_box_changes() or not messagebox.askokcancel(
            "快速生蛋",
            "仅将这个已有蛋的亲密度／孵化周期设为 0，保留蛋状态。\n取出后由游戏走路触发孵化；不会创建新蛋。\n\n确认备份并写入？",
            parent=self.root,
        ):
            return

        def job():
            result = trainer.commit_box(patches, "PC 已有蛋周期归零")
            return result, trainer.snapshot_box(snap["index"])

        def done(result):
            self.apply_box_snapshot(result[1])
            self.status.set(
                "已有蛋周期已设为 0 并读回；蛋状态保留。请取出后在游戏中触发孵化。"
            )

        self.run("备份并写入已有蛋周期…", job, done)

    def shortcut_fill_pp(self):
        if self.busy:
            return
        self.fill_pp()
        self.nb.select(self.tab_party)
        self.party_pages.select(self.tab_moves)

    def ready_party_eggs(self):
        if self.busy or self.trainer is None or self.snapshot is None:
            return
        trainer, snapshot = self.trainer, self.snapshot
        eggs = [i for i, mon in enumerate(snapshot["party"]) if mon.egg]
        if not eggs:
            messagebox.showinfo(
                "已有蛋周期归零",
                "当前队伍没有蛋；普通精灵不会被修改。",
                parent=self.root,
            )
            return
        if not self.discard_party_changes() or not messagebox.askokcancel(
            "已有蛋周期归零",
            f"将队伍中 {len(eggs)} 只已有蛋的亲密度／周期设为 0。保留蛋状态及其他精灵亲密度。\n\n确认备份并写入？",
            parent=self.root,
        ):
            return
        pocket = self.pocket_id()

        def job():
            patches = []
            for slot in eggs:
                patches.extend(trainer.edit_pokemon(snapshot, slot, friendship=0)[0])
            result = trainer.commit(snapshot, patches, "队伍已有蛋周期归零")
            return result, trainer.snapshot(pocket)

        def done(result):
            self.apply_snapshot(result[1])
            self.status.set("队伍已有蛋周期已设为 0 并读回；请在游戏内走路触发孵化。")

        self.run("备份并写入队伍已有蛋周期…", job, done)

    def read_box(self):
        if self.trainer is None:
            return
        if not self.discard_box_changes():
            self.box_number.set(str(self.box_snapshot["index"] + 1))
            return
        index = int(self.box_number.get()) - 1

        def done(snapshot):
            self.apply_box_snapshot(snapshot)
            self.request_icons()

        self.run(
            "读取 PC 盒子…",
            lambda: self.trainer.snapshot_box(index),
            done,
        )

    def move_box_mon(self):
        selected = self.box_tree.selection()
        if (
            self.busy
            or self.trainer is None
            or self.box_snapshot is None
            or not selected
        ):
            return
        if not self.discard_box_changes():
            return
        trainer, source, slot = self.trainer, self.box_snapshot, int(selected[0])
        target_index = int(self.box_move_target.get()) - 1

        def job():
            target = trainer.snapshot_box(target_index)
            result = trainer.commit_box_move(source, slot, target)
            return result, trainer.snapshot_box(target_index)

        def done(result):
            self.apply_box_snapshot(result[1])
            self.box_tree.selection_set(str(result[0]["target_slot"]))
            self.select_box_mon()
            self.request_icons()
            self.status.set(
                f"已移动至第{target_index + 1}盒第{result[0]['target_slot'] + 1}格，两槽已备份并读回。请在游戏内保存。"
            )

        self.run("检查源/目标槽、备份并移动宝可梦…", job, done)

    def apply_box_snapshot(self, snapshot):
        self.clear_box_editor()
        self.box_snapshot = snapshot
        self.update_box_lock()
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
            if mon.species and mon.nickname and mon.nickname.strip():
                name = mon.nickname
            count += bool(mon.species)
            self.box_tree.insert(
                "",
                "end",
                iid=str(i),
                image=self.mon_image(mon),
                values=(
                    i + 1,
                    name + ("（蛋）" if mon.egg else ""),
                    info["level"] or "—",
                    ("是" if mon.shiny else "否") if mon.species else "—",
                ),
            )
        self.box_detail.set(
            f"第 {snapshot['index'] + 1} 盒：{count} / 30。选择成员查看详情。"
        )
        self.status.set(f"已读取第 {snapshot['index'] + 1} 盒，尚未写入。")
        self.update_box_egg_button()

    def clear_box_editor(self):
        if self.box_editor is not None and self.box_editor.winfo_exists():
            self.box_editor.destroy()
        self.box_editor = None
        self.box_editor_values = []
        self.box_editor_original = ()
        self.box_editor_slot = None

    def discard_box_changes(self):
        values = tuple(var.get() for var in self.box_editor_values)
        return values == self.box_editor_original or messagebox.askyesno(
            "尚未写入",
            "当前盒内宝可梦的修改尚未写入。放弃修改并继续？",
            parent=self.root,
        )

    def edit_box_dialog(self, parent=None):
        selected = self.box_tree.selection()
        if not selected or self.box_snapshot is None or self.trainer is None:
            return
        snapshot = self.box_snapshot
        slot = int(selected[0])
        mon = snapshot["pokemon"][slot]
        if not mon.species:
            return
        trainer = self.trainer
        window = None
        if parent is None:
            window = tk.Toplevel(self.root)
            window.title(f"第 {snapshot['index'] + 1} 盒 · 第 {slot + 1} 格")
            window.geometry("650x580")
        frame = ttk.Frame(parent if parent is not None else window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text=self.names["breeds"].get(str(mon.species), str(mon.species))
        ).pack(anchor="w")
        editor_tabs = ttk.Notebook(frame)
        editor_tabs.pack(fill="both", expand=True, pady=6)
        basic_scroll = ScrollingForm(editor_tabs, padding=6)
        source_scroll = ScrollingForm(editor_tabs, padding=6)
        basic, sources = basic_scroll.body, source_scroll.body
        editor_tabs.add(basic_scroll, text="基本 / 能力")
        egg = tk.BooleanVar(value=mon.egg)
        ttk.Checkbutton(basic, text="蛋状态（转换后先检查预览）", variable=egg).pack(
            anchor="w", pady=4
        )
        editor_tabs.add(source_scroll, text="来源 / 原训练师")
        letter = tk.StringVar(value=str(unown_form(mon.pid)))
        core_color = tk.StringVar(value="保持当前")
        pattern_seed = tk.StringVar()
        pattern_photos = []
        pattern_labels = []
        if parent is not None:
            self.box_pattern_labels = pattern_labels
        if mon.species in TOXTRICITY_SPECIES:
            ttk.Label(
                basic,
                text="颤弦蝾螈的高调/低调形态由所选性格决定；预览会显示形态与特性变化。",
                wraplength=520,
            ).pack(anchor="w", pady=(8, 0))
        if mon.species == 201:
            ttk.Label(basic, text="未知图腾字形").pack(anchor="w", pady=(8, 0))
            ttk.Combobox(
                basic,
                textvariable=letter,
                state="readonly",
                values=[
                    f"{i} - {name}"
                    for i, name in enumerate(self.profile["unown_letters"])
                ],
                width=20,
            ).pack(anchor="w")
        if mon.species in MINIOR_SPECIES:
            ttk.Label(
                basic, text=f"小陨星当前PID核心：{MINIOR_COLORS[mon.pid % 7]}"
            ).pack(anchor="w", pady=(8, 0))
            ttk.Combobox(
                basic,
                textvariable=core_color,
                state="readonly",
                values=["保持当前"]
                + [f"{i} - {name}" for i, name in enumerate(MINIOR_COLORS)],
                width=20,
            ).pack(anchor="w")
        shiny = tk.BooleanVar(value=mon.shiny)
        nature = tk.StringVar(
            value=f"{mon.pid % 25} - {self.names['pers'].get(str(mon.pid % 25), str(mon.pid % 25))}"
        )
        ttk.Checkbutton(basic, text="闪光", variable=shiny).pack(anchor="w", pady=5)
        ttk.Combobox(
            basic,
            textvariable=nature,
            state="readonly",
            values=[
                f"{i} - {self.names['pers'].get(str(i), str(i))}" for i in range(25)
            ],
        ).pack(anchor="w")
        metadata = self.profile["species"].get(str(mon.species), {})
        target_gender = tk.StringVar(value="保持当前")
        ttk.Label(
            basic,
            text=f"宝可梦性别（当前{gender(mon.pid, metadata.get('gender_ratio', 255))}）",
        ).pack(anchor="w", pady=(6, 0))
        ttk.Combobox(
            basic,
            textvariable=target_gender,
            state="readonly",
            width=24,
            values=["保持当前", *gender_choices(metadata.get("gender_ratio", 255))],
        ).pack(anchor="w")
        moves_page = ttk.Frame(editor_tabs, padding=6)
        editor_tabs.insert(1, moves_page, text="招式 / PP上限")
        ttk.Label(
            moves_page,
            text="盒内取出时按上限补满PP；这里仅修改提升次数，不更换招式。",
            wraplength=500,
        ).pack(anchor="w", pady=5)
        pp_ups = [
            tk.StringVar(value=str(ups if move else 0))
            for move, ups in zip(mon.moves, mon.pp_ups)
        ]
        pp_limits = [tk.StringVar() for _ in range(4)]

        def update_box_pp_limits(*_):
            for move, ups, label in zip(mon.moves, pp_ups, pp_limits):
                try:
                    label.set(
                        f"上限 {maximum_pp(move, ups.get(), self.profile['moves'])}"
                    )
                except ValueError:
                    label.set("上限 —")

        for i, move in enumerate(mon.moves):
            row = ttk.Frame(moves_page)
            row.pack(fill="x", pady=6)
            ttk.Label(
                row,
                text=f"{i + 1}. {move} - {self.names['skills'].get(str(move), '无' if not move else '未收录')}",
                width=30,
            ).pack(side="left")
            ttk.Combobox(
                row,
                textvariable=pp_ups[i],
                values=(0, 1, 2, 3) if move else (0,),
                state="readonly" if move else "disabled",
                width=3,
            ).pack(side="left")
            ttk.Label(row, textvariable=pp_limits[i], padding=(8, 0)).pack(side="left")
            pp_ups[i].trace_add("write", update_box_pp_limits)
        update_box_pp_limits()
        ttk.Button(
            moves_page,
            text="已有招式满提升（草稿）",
            command=lambda: [
                var.set("3" if move else "0") for var, move in zip(pp_ups, mon.moves)
            ],
        ).pack(anchor="w", pady=6)
        if parent is not None:
            self.box_pp_up_vars = pp_ups
            self.box_gender_var = target_gender
        slots = metadata.get("abilities", [0, 0, 0])
        current_ability = (
            2 if mon.ability_flag and slots[2] else mon.pid & 1 if slots[1] else 0
        )
        ability = tk.StringVar(
            value=f"{current_ability} - {self.names['specs'].get(str(slots[current_ability]), str(slots[current_ability]))}"
        )
        original_ability = ability.get()
        ttk.Label(basic, text="特性槽位（按目标形态检查）").pack(
            anchor="w", pady=(6, 0)
        )
        ttk.Combobox(
            basic,
            textvariable=ability,
            state="readonly",
            values=[
                f"{slot} - {self.names['specs'].get(str(ident), str(ident))}"
                for slot, ident in enumerate(slots)
                if ident
            ],
            width=28,
        ).pack(anchor="w")
        grid = ttk.Frame(basic)
        grid.pack(anchor="w", pady=10)
        iv = [tk.StringVar(value=str(n)) for n in mon.ivs]
        ev = [tk.StringVar(value=str(n)) for n in mon.evs]
        for i, name in enumerate(STAT_NAMES):
            ttk.Label(grid, text=name, width=5).grid(row=0, column=i + 1)
        for row, label, variables in [(1, "IV", iv), (2, "EV", ev)]:
            ttk.Label(grid, text=label).grid(row=row, column=0)
            for i, var in enumerate(variables):
                ttk.Entry(grid, textvariable=var, width=4).grid(
                    row=row, column=i + 1, padx=2, pady=5
                )
        source_values = {}
        for key, label, value in [
            ("friendship", "亲密度 / 孵化周期", mon.friendship),
            ("held", "携带道具", mon.held),
            ("ball", "捕获球编号", mon.ball),
            ("met_location", "相遇地点编号", mon.met_location),
            ("met_level", "相遇等级", mon.met_level),
            ("ot_tid", "原训练师 TID", mon.otid & 65535),
            ("ot_sid", "原训练师 SID", mon.otid >> 16),
            ("ot_gender", "原训练师性别（0男/1女）", mon.ot_gender),
        ]:
            row = ttk.Frame(basic if key == "friendship" else sources)
            row.pack(anchor="w", pady=3)
            ttk.Label(row, text=label, width=25).pack(side="left")
            var = tk.StringVar(value=str(value))
            source_values[key] = var
            if key == "met_location":
                var.set(self.location_label(value))
                self.location_entry(row, var, 28).pack(side="left")
            elif key == "held":
                var.set(f"{value} - {self.item_name(value)}")
                self.held_item_entry(row, var).pack(side="left")
            else:
                ttk.Entry(row, textvariable=var, width=12).pack(side="left")
        original_sources = {key: var.get() for key, var in source_values.items()}
        original_name = (
            mon.ot_name if mon.ot_name is not None else "（未知编码，原样保留）"
        )
        ot_name = tk.StringVar(value=original_name)
        ttk.Label(sources, text="原训练师姓名（最多3汉字或7字节）").pack(
            anchor="w", pady=3
        )
        ttk.Entry(sources, textvariable=ot_name, width=24).pack(anchor="w")
        original_nickname = (
            mon.nickname if mon.nickname is not None else "（未知编码，原样保留）"
        )
        nickname = tk.StringVar(value=original_nickname)
        ttk.Label(sources, text="宝可梦昵称（最多5汉字或10字节）").pack(
            anchor="w", pady=3
        )
        ttk.Entry(sources, textvariable=nickname, width=24).pack(anchor="w")

        def fill_player_ot():
            if self.busy:
                return

            def done(result):
                if self.trainer is not trainer or not frame.winfo_exists():
                    return
                values, note = result
                for key in ("ot_tid", "ot_sid", "ot_gender"):
                    source_values[key].set(str(values[key]))
                if "ot_name" in values:
                    ot_name.set(values["ot_name"])
                detail.set(note)

            self.run("读取当前玩家资料…", trainer.read_player_ot, done)

        ttk.Button(
            sources, text="填入当前玩家的原训练师资料（待预览）", command=fill_player_ot
        ).pack(anchor="w", pady=6)
        ttk.Label(
            sources,
            text="转为蛋同步两处标志、1级及默认周期；取消标志不等于自然孵化。",
            wraplength=350,
        ).pack(anchor="w")
        if parent is not None:
            self.box_editor = frame
            self.box_editor_slot = slot
            self.box_editor_values = [
                shiny,
                egg,
                nature,
                letter,
                core_color,
                pattern_seed,
                ot_name,
                nickname,
                *source_values.values(),
                ability,
                target_gender,
                *pp_ups,
                *iv,
                *ev,
            ]
            self.box_editor_original = tuple(
                var.get() for var in self.box_editor_values
            )
        detail = tk.StringVar(
            value="IV 0～31；EV 单项 0～252、总和 ≤510。结构检查不等于完整来源合法化。"
        )
        detail_label = ttk.Label(frame, textvariable=detail, wraplength=400)
        detail_label.pack(anchor="w", pady=6, fill="x")
        frame.bind(
            "<Configure>",
            lambda event: detail_label.configure(wraplength=max(160, event.width - 24)),
        )

        def prepare():
            if self.trainer is not trainer:
                raise ValueError("连接已经改变，请重新打开此编辑窗口")
            details = {
                key: var.get().split(" - ", 1)[0]
                if key in ("met_location", "held")
                else var.get()
                for key, var in source_values.items()
                if var.get() != original_sources[key]
            }
            if ot_name.get() != original_name:
                details["ot_name"] = ot_name.get()
            if nickname.get() != original_nickname:
                details["nickname"] = nickname.get()
            if pattern_seed.get():
                details["spinda_seed"] = pattern_seed.get()
            if egg.get() != mon.egg:
                details["egg"] = egg.get()
            if core_color.get() != "保持当前":
                details["minior_color"] = core_color.get().split(" - ", 1)[0]
            if ability.get() != original_ability:
                details["ability_slot"] = ability.get().split(" - ", 1)[0]
            if target_gender.get() != "保持当前":
                details["target_gender"] = target_gender.get()
            if [var.get() for var in pp_ups] != list(map(str, mon.pp_ups)):
                details["pp_ups"] = [var.get() for var in pp_ups]
            return trainer.edit_box(
                snapshot,
                slot,
                ivs=[v.get() for v in iv],
                evs=[v.get() for v in ev],
                nature=nature.get().split(" - ", 1)[0],
                shiny=shiny.get(),
                **details,
                **(
                    {"unown_letter": letter.get().split(" - ", 1)[0]}
                    if mon.species == 201
                    else {}
                ),
            )

        def preview():
            try:
                patches, report = prepare()
                updated = BoxPokemon(patches[0][2])
                detail.set(
                    f"检查通过；闪光：{'是' if report['shiny'] else '否'}，EV 总和：{sum(report['evs'])}。\n蛋：{'是' if updated.egg else '否'}；等级：{report['level']}；亲密度/周期：{updated.friendship}。\n特性：{self.names['specs'].get(str(report['ability']), report['ability'])}；隐藏标志：{mon.ability_flag} → {updated.ability_flag}；PID：{mon.pid:08X} → {updated.pid:08X}。\n取出时由游戏计算能力值。来源合法性未完整验证。"
                )
                if mon.species in MINIOR_SPECIES:
                    detail.set(
                        detail.get()
                        + f"\n核心：{MINIOR_COLORS[updated.pid % 7]}；形态编号 {mon.species} → {updated.species}。闪光颜色以游戏实际显示为准。"
                    )
                detail.set(detail.get() + self.nature_form_summary(mon, updated))
                detail.set(detail.get() + self.held_form_summary(mon, updated))
                detail.set(
                    detail.get()
                    + f"\n宝可梦性别：{report.get('gender', '未知')}；PP提升：{mon.pp_ups} → {updated.pp_ups}；取出PP上限：{report.get('maximum_pp', [])}。"
                )
                if mon.species == 308 and self.spinda_assets is not None:
                    pattern_photos.clear()
                    for displayed, label in zip([mon, updated], pattern_labels):
                        photo = tk.PhotoImage(
                            master=self.root,
                            data=base64.b64encode(
                                spinda_png(
                                    self.spinda_assets, displayed.pid, displayed.shiny
                                )
                            ),
                        ).zoom(2)
                        pattern_photos.append(photo)
                        label.configure(image=photo)
                    detail.set(
                        detail.get()
                        + f"\n花纹 PID：{mon.pid:08X} → {updated.pid:08X}；左当前、右预览。"
                    )
                elif mon.species == 308:
                    detail.set(
                        detail.get()
                        + "\n点击顶部加载游戏微缩图，再检查预览可查看花纹图像。"
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
                if window is not None and window.winfo_exists():
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

        if mon.species == 308:

            def regenerate():
                pattern_seed.set(str(secrets.randbits(32)))
                preview()

            ttk.Button(basic, text="重新生成花纹（待预览）", command=regenerate).pack(
                anchor="w", pady=6
            )
            image_row = ttk.Frame(basic)
            image_row.pack(anchor="w")
            for _ in range(2):
                label = ttk.Label(image_row)
                label.pack(side="left", padx=6)
                pattern_labels.append(label)
        basic_scroll.enable_navigation()
        source_scroll.enable_navigation()
        return window

    def select_box_mon(self, event=None):
        self.update_box_egg_button()
        selected = self.box_tree.selection()
        if self.busy or not selected or self.box_snapshot is None:
            return
        slot = int(selected[0])
        if self.box_editor_slot == slot:
            return
        if not self.discard_box_changes():
            self.box_tree.selection_set(str(self.box_editor_slot))
            return
        self.clear_box_editor()
        mon = self.box_snapshot["pokemon"][int(selected[0])]
        info = mon.describe(self.profile)
        if not mon.species:
            self.box_detail.set("空槽")
            return
        self.edit_box_dialog(self.box_editor_host)
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
            self.party_pages.select(self.tab_party_basic)
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
            self.nb.select(self.tab_party)
            self.party_pages.select(self.tab_moves)
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
            self.party_pages.select(self.tab_party_basic)
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
        self.update_box_egg_button()
        if self.icon_request_pending and not self.busy and self.trainer is not None:
            self.icon_request_pending = False
            self.load_icons()
        self.timer = self.root.after(50, self._poll)

    def pocket_id(self):
        return next(
            p["id"] for p in self.profile["pockets"] if p["name"] == self.pocket.get()
        )

    def connect(self):
        if self.busy:
            return
        if (
            not self.discard_party_changes()
            or not self.discard_box_changes()
            or not self.discard_trainer_changes()
        ):
            return
        old_mem = self.mem
        self.mem = None
        self.trainer = None
        self.time_snapshot = None
        self.time_detail.set("连接已改变，请重新读取游戏时间。")
        self.daily_repair_ready = None
        self.daily_detail.set("连接已改变，请重新读取每日刷新日期。")
        self.snapshot = None
        self.snapshot_at = None
        self.current_slot = None
        self.party_form_original = None
        self.clear_box_editor()
        self.trainer_snapshot = None
        self.player_tid.set("")
        self.player_sid.set("")
        self.player_name.set("")
        self.player_detail.set("连接已改变，请重新读取训练师资料。")
        self.box_snapshot = None
        self.icon_images.clear()
        self.spinda_assets = None
        self.icon_request_pending = False
        self.detail_image.configure(image="")
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
                if "ROMCRC" not in mem.capabilities:
                    raise ValueError(
                        "请重新加载 mercury_bridge.lua，以完整 ROM CRC32 识别版本"
                    )
                profile = load_profile(
                    crc=mem.command("ROMCRC").decode("ascii").lower(),
                    root=resource_path("rom_profile.json").parent,
                )
                trainer = Trainer(mem, profile, base / "backups")
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
            self.profile = self.trainer.profile
            if self.box_save_path.get():
                try:
                    self.trainer.locked_boxes = self.box_preferences.load(
                        self.profile["rom_sha256"], self.box_save_path.get()
                    )
                except (ValueError, OSError) as exc:
                    self.box_save_path.set("")
                    messagebox.showerror("未加载盒锁", str(exc), parent=self.root)
            self.update_box_lock()
            self.root.title(
                f"水银 FC 修改器 · {APP_VERSION} · {self.profile['name'].split(' / ')[0]}"
            )
            self.pocket.set("道具")
            self.apply_snapshot(snap)
            self.request_icons()

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
        if after is None and not self.discard_party_changes():
            return
        pocket = self.pocket_id()

        def done(snap):
            applied = self.apply_snapshot(snap, preserve_party=after is not None)
            if after and applied:
                after()
            if applied:
                self.request_icons()

        self.run("读取游戏数据…", lambda: self.trainer.snapshot(pocket), done)

    def apply_snapshot(self, snap, preserve_party=False):
        keep_form = (
            preserve_party
            and self.party_form_original is not None
            and self.party_form_values() != self.party_form_original
        )
        if keep_form:
            previous = self.snapshot
            unchanged = (
                previous is not None
                and previous["saveblock"] == snap["saveblock"]
                and tuple(mon.raw for mon in previous["party"])
                == tuple(mon.raw for mon in snap["party"])
            )
            if not unchanged:
                if not self.discard_party_changes():
                    self.pocket.set(previous["pocket"]["name"])
                    self.status.set(
                        "队伍数据已变化，未覆盖当前编辑。请核对后重新读取。"
                    )
                    return False
                keep_form = False
        self.snapshot = snap
        self.snapshot_at = datetime.now().astimezone().isoformat()
        self.money.set(str(snap["money"]))
        self.coins.set(str(snap["coins"]))
        self.beauty_points.set(str(snap["beauty_points"]))
        self.bracer_points.set(str(snap["bracer_points"]))
        self.party_tree.delete(*self.party_tree.get_children())
        for i, mon in enumerate(snap["party"]):
            name = (
                mon.nickname
                if mon.nickname and mon.nickname.strip()
                else self.names["breeds"].get(str(mon.species), f"未收录#{mon.species}")
            )
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
            if not keep_form:
                self.select_mon(force=True)
        else:
            self.current_slot = None
            self.party_form_original = None
            for var in [
                self.species,
                self.level,
                self.hp,
                self.held,
                self.nature,
                *self.iv,
                *self.ev,
                *self.detail_vars.values(),
                self.ot_name,
                self.nickname,
                self.unown_letter,
                self.minior_color,
                self.spinda_seed,
            ]:
                var.set("")
            self.identity.set("队伍为空")
            self.egg.set(False)
            self.shiny.set(False)
            self.detail_image.configure(image="")
            self.detail_preview.set("")
            self.spinda_before.configure(image="")
            self.spinda_after.configure(image="")
            self.set_report("")
            self.ability.set("")
            for var in [*self.move_vars, *self.pp_vars, *self.pp_up_vars]:
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
            + (" · 已保留队伍成员尚未写入的修改" if keep_form else "")
        )
        return True

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

    def party_form_values(self):
        return tuple(
            var.get()
            for var in [
                self.species,
                self.level,
                self.hp,
                self.held,
                self.nature,
                self.target_gender,
                self.ability,
                self.shiny,
                self.egg,
                self.ot_name,
                self.nickname,
                self.unown_letter,
                self.minior_color,
                self.spinda_seed,
                *self.iv,
                *self.ev,
                *self.move_vars,
                *self.pp_vars,
                *self.pp_up_vars,
                *self.detail_vars.values(),
            ]
        )

    def discard_party_changes(self):
        return (
            self.party_form_original is None
            or self.party_form_values() == self.party_form_original
            or messagebox.askyesno(
                "尚未写入",
                "当前队伍成员的修改尚未写入。放弃修改并继续？",
                parent=self.root,
            )
        )

    def select_mon(self, event=None, force=False):
        if self.busy:
            return
        selection = self.party_tree.selection()
        if not selection or self.snapshot is None:
            return
        i = int(selection[0])
        if not force and i == self.current_slot:
            return
        if not force and not self.discard_party_changes():
            self.party_tree.selection_set(str(self.current_slot))
            return
        self.current_slot = i
        self.target_gender.set("保持当前")
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
            var.set(self.location_label(value) if key == "met_location" else str(value))
        self.detail_preview.set("")
        self.detail_image.configure(image=self.mon_image(mon))
        self.spinda_seed.set("")
        self.show_spinda_patterns(mon)
        self.original_ot_name = (
            mon.ot_name if mon.ot_name is not None else "（未知编码，原样保留）"
        )
        self.ot_name.set(self.original_ot_name)
        self.original_nickname = (
            mon.nickname if mon.nickname is not None else "（未知编码，原样保留）"
        )
        self.nickname.set(self.original_nickname)
        self.unown_letter.set(
            f"{unown_form(mon.pid)} - 当前字形" if mon.species == 201 else "不适用"
        )
        self.minior_color.set("保持当前" if mon.species in MINIOR_SPECIES else "不适用")
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
            self.pp_vars[i].set(str(mon.pp[i] if mon.moves[i] else 0))
            self.pp_up_vars[i].set(str(mon.pp_ups[i] if mon.moves[i] else 0))
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
        self.party_form_original = self.party_form_values()

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
            value = var.get().split(" - ", 1)[0] if key == "met_location" else var.get()
            if value != str(before):
                changes[key] = value
        if self.egg.get() != mon.egg:
            changes["egg"] = self.egg.get()
        if self.ot_name.get() != self.original_ot_name:
            changes["ot_name"] = self.ot_name.get()
        if self.nickname.get() != self.original_nickname:
            changes["nickname"] = self.nickname.get()
        if self.spinda_seed.get():
            changes["spinda_seed"] = self.spinda_seed.get()
        if changes["species"] == "201" and self.unown_letter.get() not in (
            "保持当前",
            "不适用",
        ):
            changes["unown_letter"] = self.unown_letter.get().split(" - ", 1)[0]
        if self.minior_color.get() not in ("保持当前", "不适用"):
            changes["minior_color"] = self.minior_color.get().split(" - ", 1)[0]
        # Untouched HP follows automatic damage-preserving recalculation.
        if self.hp.get() != str(mon.hp):
            changes["hp"] = self.hp.get()
        if self.ability.get() != self.original_ability:
            changes["ability_slot"] = self.ability.get().split(" - ", 1)[0]
        if self.target_gender.get() != "保持当前":
            changes["target_gender"] = self.target_gender.get()
        moves = [v.get().split(" - ", 1)[0] for v in self.move_vars]
        pp = [v.get() for v in self.pp_vars]
        if moves != list(map(str, mon.moves)) or pp != list(map(str, mon.pp)):
            changes["moves"] = moves
            changes["pp"] = pp
        if [var.get() for var in self.pp_up_vars] != list(map(str, mon.pp_ups)):
            changes["pp_ups"] = [var.get() for var in self.pp_up_vars]
            changes["pp"] = pp
        return self.trainer.edit_pokemon(self.snapshot, self.current_slot, **changes)

    def normalize_empty_move(self, slot):
        move = self.move_vars[slot].get().split(" - ", 1)[0].strip()
        if move == "0":
            self.pp_vars[slot].set("0")
        mon = (
            self.snapshot["party"][self.current_slot]
            if self.snapshot is not None and self.current_slot is not None
            else None
        )
        if move == "0" or (mon is not None and move != str(mon.moves[slot])):
            self.pp_up_vars[slot].set("0")
        self.update_pp_limits()

    def update_gender_choices(self):
        metadata = self.profile["species"].get(self.species.get().split(" - ", 1)[0])
        choices = (
            ["保持当前", *gender_choices(metadata["gender_ratio"])]
            if metadata
            else ["保持当前"]
        )
        self.gender_cb.configure(values=choices)
        if self.target_gender.get() not in choices:
            self.target_gender.set("保持当前")

    def update_pp_limits(self):
        for move, ups, label in zip(self.move_vars, self.pp_up_vars, self.pp_max_vars):
            try:
                label.set(
                    f"上限 {maximum_pp(int(move.get().split(' - ', 1)[0]), ups.get(), self.profile['moves'])}"
                )
            except ValueError:
                label.set("上限 —")

    def max_pp_ups(self):
        if self.snapshot is None or self.current_slot is None:
            return
        mon = self.snapshot["party"][self.current_slot]
        for i, var in enumerate(self.move_vars):
            move = var.get().split(" - ", 1)[0]
            self.pp_up_vars[i].set(
                "3" if move != "0" and move == str(mon.moves[i]) else "0"
            )

    def fill_pp(self):
        if self.snapshot is None or self.current_slot is None:
            return
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
                values.append(
                    maximum_pp(move, self.pp_up_vars[i].get(), self.profile["moves"])
                )
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
                f"\n原训练师姓名：{original.ot_name or '未知编码'} → {updated.ot_name or '未知编码'}"
                f"\n昵称：{original.nickname or '未知编码'} → {updated.nickname or '未知编码'}"
                f"\n蛋：{original.egg} → {updated.egg}；等级：{original.level} → {updated.level}。"
                + self.nature_form_summary(original, updated)
                + self.held_form_summary(original, updated)
            )
            self.detail_image.configure(image=self.mon_image(updated))
            self.show_spinda_patterns(updated)
            self.set_report(
                "结构与数值检查通过（不是官方合法性认证）\n"
                + f"PID：{original.pid:08X} → {updated.pid:08X}；闪光：{'是' if updated.shiny else '否'}\n"
                + f"性格：{nature}；性别：{gender(updated.pid, ratio)}；EV 总和：{sum(updated.evs)}\n"
                + "能力值："
                + " / ".join(changes)
                + f"\n当前 HP：{original.hp} → {updated.hp}\n"
                + f"PP提升：{original.pp_ups} → {updated.pp_ups}；当前PP：{original.pp} → {updated.pp}\n"
                + "PP上限："
                + " / ".join(
                    str(maximum_pp(move, ups, self.profile["moves"]))
                    for move, ups in zip(updated.moves, updated.pp_ups)
                )
                + "\n"
                + self.source_report(report)
                + "\n"
                + "\n".join(report["notes"])
            )
        except Exception as exc:
            self.detail_preview.set("未通过：" + str(exc))
            self.set_report("未通过：" + str(exc))
        self.nb.select(self.tab_party)

    def commit(self, patches, label, preserve_party=True):
        snap = self.snapshot
        pocket = self.pocket_id()

        def job():
            result = self.trainer.commit(snap, patches, label)
            return result, self.trainer.snapshot(pocket)

        def done(result):
            record, snap = result
            applied = self.apply_snapshot(snap, preserve_party=preserve_party)
            self.status.set(
                "本次操作已完成；队伍数据已变化，当前编辑已保留，请重新读取。"
                if not applied
                else "写入完成，已读回核对；原数据已备份。"
                if record["changed"]
                else "没有变化，无需写入。"
            )

        self.run("校验并写入…", job, done)

    def write_mon(self):
        try:
            patches, _ = self.prepare_mon()
            self.commit(patches, "宝可梦编辑", preserve_party=False)
        except Exception as exc:
            messagebox.showerror("未写入", str(exc))

    def write_values(self):
        if self.snapshot is None:
            return
        try:
            self.commit(
                self.trainer.edit_values(
                    self.snapshot,
                    self.money.get(),
                    self.coins.get(),
                    self.beauty_points.get(),
                    self.bracer_points.get(),
                ),
                "金钱、代币与点数",
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

    def sort_items(self):
        if self.snapshot is None or self.busy:
            return
        try:
            if self.snapshot["pocket"]["id"] != self.pocket_id():
                raise ValueError("口袋尚未刷新，请稍后重试")
            patches = self.trainer.sort_bag(self.snapshot)
            self.commit(patches, self.snapshot["pocket"]["name"] + "按编号排序")
        except Exception as exc:
            messagebox.showerror("未写入", str(exc))

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
        if self.time_snapshot is not None:
            data["time"] = {
                key: value.hex()
                if isinstance(value, bytes)
                else value.isoformat()
                if isinstance(value, datetime)
                else value
                for key, value in self.time_snapshot.items()
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
        if (
            not self.discard_party_changes()
            or not self.discard_box_changes()
            or not self.discard_trainer_changes()
        ):
            return
        pocket = self.pocket_id()
        box_index = (
            self.box_snapshot["index"] if self.box_snapshot is not None else None
        )
        had_trainer = self.trainer_snapshot is not None
        had_time = self.time_snapshot is not None

        def job():
            result = self.trainer.restore(path)
            box = (
                self.trainer.snapshot_box(box_index) if box_index is not None else None
            )
            trainer_snap = self.trainer.snapshot_trainer() if had_trainer else None
            time_snap = self.trainer.snapshot_time() if had_time else None
            return result, self.trainer.snapshot(pocket), box, trainer_snap, time_snap

        def done(result):
            self.apply_snapshot(result[1])
            if result[2] is not None:
                self.apply_box_snapshot(result[2])
            if result[3] is not None:
                self.apply_trainer_snapshot(result[3])
            if result[4] is not None:
                self.apply_time_snapshot(result[4])
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
