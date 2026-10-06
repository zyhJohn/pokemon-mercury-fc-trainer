"""Browse sourced gifts and preview a single empty-slot PC transaction."""

import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from box_data import BoxPokemon


COMPATIBILITY = {"verified": "可用", "adaptable": "可适配，待核验",
                 "pending": "待核验", "unsupported": "不适用"}


class GiftPage:
    def __init__(self, app, catalog):
        self.app = app
        self.eligible = False
        self.catalog = catalog
        self.rows = {row["id"]: row for row in catalog["rows"]}
        self.tab = ttk.Frame(app.nb, padding=8)
        app.nb.add(self.tab, text="神秘礼物")
        bar = ttk.Frame(self.tab)
        bar.pack(fill="x")
        self.source = tk.StringVar(value="全部")
        groups = list(dict.fromkeys(row["source_group"] for row in self.rows.values()))
        source = ttk.Combobox(bar, textvariable=self.source, values=["全部", *groups],
                              state="readonly", width=22)
        source.pack(side="left")
        source.bind("<<ComboboxSelected>>", lambda _: self.render())
        self.query = tk.StringVar()
        ttk.Entry(bar, textvariable=self.query).pack(side="left", fill="x", expand=True, padx=8)
        self.count = tk.StringVar()
        ttk.Label(bar, textvariable=self.count).pack(side="right")
        frame = ttk.Frame(self.tab)
        frame.pack(fill="both", expand=True, pady=8)
        self.tree = ttk.Treeview(frame, columns=("title", "source", "compatible"),
                                 show="headings", selectmode="browse")
        for key, label, width in (("title", "礼物", 360), ("source", "来源", 220),
                                  ("compatible", "本改版兼容情况", 180)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=80)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_detail())
        detail_frame = ttk.Frame(self.tab)
        detail_frame.pack(fill="x")
        self.detail = tk.Text(detail_frame, height=8, wrap="word", state="disabled")
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        self.detail.pack(fill="both", expand=True)
        actions = ttk.Frame(self.tab)
        actions.pack(fill="x", pady=8)
        app.button(actions, "配信来源", self.open_source, side="left")
        app.button(actions, "复制游戏领取密钥", self.copy_key, side="left", padx=6)
        self.destination = tk.StringVar(value="盒子空槽")
        destination = ttk.Combobox(actions, textvariable=self.destination,
                                  values=("盒子空槽", "队伍空位", "顶替队伍成员"),
                                  state="readonly", width=14)
        destination.pack(side="left", padx=6)
        ttk.Label(actions, text="目标盒").pack(side="left", padx=(12, 4))
        self.box = tk.StringVar(value="1")
        ttk.Combobox(actions, textvariable=self.box, values=list(range(1, 26)),
                     state="readonly", width=5).pack(side="left")
        ttk.Label(actions, text="顶替槽").pack(side="left", padx=(8, 3))
        self.replacement = tk.StringVar()
        self.replacement_cb = ttk.Combobox(actions, textvariable=self.replacement,
                                           values=list(range(1, 7)), state="disabled", width=4)
        self.replacement_cb.pack(side="left")
        destination.bind("<<ComboboxSelected>>", lambda _: self.change_destination())
        self.deposit_button = app.button(actions, "预览并投放空槽", self.deposit, side="left", padx=8)
        ttk.Label(self.tab, text="投放使用当前盒的首个全零空槽；满盒、锁盒会拒绝。来源与适配说明见上方详情。",
                  wraplength=950).pack(anchor="w")
        self.query.trace_add("write", lambda *_: self.render())
        self.render()

    def change_destination(self):
        self.replacement.set("")
        self.replacement_cb.configure(state="readonly" if self.destination.get() == "顶替队伍成员"
                                      else "disabled")

    def selected(self):
        selection = self.tree.selection()
        return self.rows.get(selection[0]) if selection else None

    def render(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().strip().casefold()
        matches = [row for row in self.rows.values()
                   if (self.source.get() == "全部" or row["source_group"] == self.source.get())
                   and query in f"{row['title']} {row['source_group']} {row['reason']}".casefold()]
        for row in matches:
            self.tree.insert("", "end", iid=row["id"], values=(row["title"], row["source_group"],
                               COMPATIBILITY[row["compatibility"]]))
        self.count.set(f"{len(matches)} / {len(self.rows)}")
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self.show_detail()

    def set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def show_detail(self):
        row = self.selected()
        if row is None:
            self.eligible = False
            self.set_detail("选择配信查看内容、原始来源和兼容情况。")
            self.deposit_button.configure(state="disabled")
            return
        lines = [row["title"], f"来源：{row['source_group']}", row["reason"]]
        fields = row.get("source_fields", {})
        for key, label in (("species_name", "宝可梦"), ("level", "等级"), ("ot", "原训练师"),
                            ("moves_text", "招式"), ("metadata_text", "配信资料")):
            if fields.get(key):
                value = fields[key]
                lines.append(f"{label}：{'、'.join(map(str, value)) if isinstance(value, list) else value}")
        if row.get("distribution_date"):
            lines.append(f"配信日期：{row['distribution_date']}")
        lines.append(f"网页：{row['source_url']}")
        trainer = self.app.trainer
        usable = row["compatibility"] == "verified" and bool(row.get("template"))
        template = row.get("template") or {}
        if trainer is not None and template.get("native_pc_hex"):
            mon = BoxPokemon(bytes.fromhex(template["native_pc_hex"]))
            info = mon.describe(trainer.profile)
            usable = usable and not info["errors"] and bool(mon.species)
            if usable:
                lines.extend(self.describe_mon(mon, trainer.profile))
            else:
                lines.append("当前 ROM 检查未通过：" + "；".join(info["errors"]))
        self.eligible = usable and trainer is not None
        self.update_actions()
        self.set_detail("\n".join(lines))

    def update_actions(self):
        self.deposit_button.configure(state="normal" if self.eligible and self.app.trainer is not None
                                      and not self.app.busy else "disabled")

    def describe_mon(self, mon, profile):
        info = mon.describe(profile)
        names = self.app.names
        return [f"宝可梦：{names['breeds'].get(str(mon.species), mon.species)}　等级 {info['level']}",
                f"招式：{' / '.join(names['skills'].get(str(move), str(move)) for move in mon.moves)}",
                f"原训练师：{mon.ot_name or '未知编码'}　TID {mon.otid & 65535} / SID {mon.otid >> 16}",
                f"闪光：{'是' if mon.shiny else '否'}　性别：{info.get('gender', '未知')}　"
                f"IV：{' / '.join(map(str, mon.ivs))}"]

    def copy_key(self):
        row = self.selected()
        key = (row or {}).get("source_fields", {}).get("manual_key")
        if not key:
            self.app.status.set("此配信没有本游戏的领取密钥。")
            return
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(key)
        self.app.status.set("已复制本游戏的配信领取密钥。")

    def open_source(self):
        row = self.selected()
        if row:
            webbrowser.open(row["source_url"])

    def deposit(self):
        row = self.selected()
        trainer = self.app.trainer
        if trainer is None or row is None or row["compatibility"] != "verified":
            return
        mode = self.destination.get()
        party = mode != "盒子空槽"
        replacement = self.replacement.get() if mode == "顶替队伍成员" else ""
        if mode == "顶替队伍成员" and not replacement:
            self.app.status.set("请明确选择要顶替的队伍槽位。")
            return
        if not (self.app.discard_party_changes() if party else self.app.discard_box_changes()):
            return
        ident = row["id"]
        box = int(self.box.get()) - 1

        def confirm(prepared):
            if (self.app.trainer is not trainer or self.selected() is not row
                    or int(self.box.get()) - 1 != box or self.destination.get() != mode
                    or (mode == "顶替队伍成员" and self.replacement.get() != replacement)):
                self.app.status.set("礼物选择或连接已变化，请重新预览。")
                return
            target_box, target_slot = prepared["destination"]
            target = (f"队伍第 {target_slot + 1} 位" if party else
                      f"盒子 {target_box + 1}，位置 {target_slot + 1}（空槽）")
            lines = [row["title"], *self.describe_mon(prepared["pokemon"], trainer.profile),
                     f"投放：{target}"]
            replaced = prepared.get("replaced")
            if replaced is not None:
                name = self.app.names["breeds"].get(str(replaced.species), str(replaced.species))
                lines.append(f"将顶替：{replaced.nickname or name}（{name}） Lv.{replaced.level}　"
                             f"PID {replaced.pid:08X}　OT {replaced.ot_name or '未知编码'}　"
                             f"TID {replaced.otid & 65535} / SID {replaced.otid >> 16}")
            lines.append("确认后写入，原槽完整记录会备份并读回核对。")
            if not messagebox.askokcancel("神秘礼物投放预览", "\n".join(lines), parent=self.app.root):
                return

            def commit():
                result = (trainer.commit_gift_party(prepared) if party else
                          trainer.commit_gift_box(prepared))
                return result, trainer.snapshot() if party else trainer.snapshot_box(target_box)

            def done(result):
                if self.app.trainer is trainer:
                    if party:
                        self.app.apply_snapshot(result[1])
                    else:
                        self.app.apply_box_snapshot(result[1])
                    self.app.status.set("礼物已投放并读回核对；原值已备份。")

            self.app.run("写入神秘礼物并读回核对…", commit, done)

        def prepare():
            if party:
                return trainer.prepare_gift_party(ident,
                    replacement_slot=int(replacement) - 1 if replacement else None)
            return trainer.prepare_gift_box(ident, box)

        self.app.run("准备神秘礼物投放预览…", prepare, confirm)
