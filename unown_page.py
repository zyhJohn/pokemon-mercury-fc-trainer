"""Explicit, reviewed Unown record edits; workers never access Tk variables."""

import tkinter as tk
from tkinter import messagebox, ttk

import unown_records


GROUP_LABELS = {"captured": "捕获字形", "research": "调查笔记", "final": "最终记录"}
COMPLETION_WARNING = (
    "此操作将准备补齐28种未知图腾字形和6项遗迹研究条件，可能跳过正常调查阶段。\n\n"
    "请先在游戏内保存，并备份完整 .sav 存档，建议保留独立副本。"
    "程序的记录备份不能替代完整 .sav 存档备份。\n\n"
    "此按钮只准备草稿，仍需预览并确认写入。写入后前往阿露福遗迹研究所，"
    "与研究员对话，由游戏正常发奖并完成任务；不重置已完成任务或重复领奖。\n\n"
    "确认已完成存档备份并继续准备草稿？")
RESEARCH_RISK = "风险：以上遗迹研究条件来自已确认的领奖快捷草稿，可能跳过正常调查阶段；请保留完整 .sav 存档备份。"


class UnownPage:
    def __init__(self, app):
        self.app = app
        self.snapshot = None
        self.snapshot_trainer = None
        self.snapshot_generation = None
        self.prepared = None
        self.revision = 0
        self.fields = {}
        self.all_fields = {}
        self.completion_overrides = {}
        self.values = {}
        self.checkbuttons = {}
        self.groups = []
        self.frozen = False
        self.tab = ttk.Frame(app.nb, padding=8)
        app.nb.add(self.tab, text="未知图腾记录")
        actions = ttk.Frame(self.tab)
        actions.pack(fill="x")
        self.read_button = app.button(actions, "读取记录", self.read, side="left")
        self.all_button = app.button(actions, "全选当前组", lambda: self.select_group(True),
                                     side="left", padx=(8, 3))
        self.clear_button = app.button(actions, "清除当前组", lambda: self.select_group(False),
                                       side="left", padx=3)
        self.preview_button = app.button(actions, "预览变更", self.preview, side="left", padx=8)
        self.commit_button = app.button(actions, "确认写入", self.commit, side="left")
        completion_actions = ttk.Frame(self.tab)
        completion_actions.pack(fill="x", pady=(8, 0))
        self.completion_button = app.button(
            completion_actions, "补齐任务领奖条件（草稿）", self.complete_draft, side="left")
        self.info = tk.StringVar(value="连接游戏后读取未知图腾记录。")
        ttk.Label(self.tab, textvariable=self.info, wraplength=950).pack(anchor="w", pady=8)
        ttk.Label(self.tab, text="补齐条件并确认写入后，前往阿露福遗迹研究所与研究员对话，"
                  "由游戏发奖并完成任务。6项遗迹研究记录暂不开放手动编辑；"
                  "最终记录涉及任务完成与奖励判定，仅供查看，已完成记录不重置领奖。",
                  wraplength=950).pack(anchor="w")
        self.pages = ttk.Notebook(self.tab)
        self.pages.pack(fill="both", expand=True, pady=8)
        self.detail = tk.Text(self.tab, height=8, wrap="word", state="disabled")
        self.detail.pack(fill="x")
        self.pages.bind("<<NotebookTabChanged>>", lambda _: self.update_actions())
        self.set_detail("读取后可调整记录，再预览具体变更。")
        self.update_actions()

    def set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def has_changes(self):
        return bool(self.changes())

    def changes(self):
        return {**{key: bool(var.get()) for key, var in self.values.items()
                if self.fields[key].get("editable", True)
                and bool(var.get()) != bool(self.fields[key]["value"])},
                **self.completion_overrides}

    def _research_note(self):
        count = len(self.completion_overrides)
        return (f"已保留经确认的 {count} 项遗迹研究条件草稿，预览与最终确认会逐项列出。"
                if count else "")

    def _valid(self, trainer, generation, revision):
        return (not getattr(self.app, "closed", False)
                and not getattr(self.app, "close_requested", False)
                and self.app.trainer is trainer
                and getattr(trainer, "connection_generation", None) == generation
                and self.revision == revision)

    def _available(self):
        return (self.app.trainer is not None and not getattr(self.app, "busy", False)
                and not self.frozen and not getattr(self.app, "closed", False)
                and not getattr(self.app, "close_requested", False))

    def _snapshot_current(self):
        trainer = self.app.trainer
        return (self.snapshot is not None and trainer is self.snapshot_trainer
                and getattr(trainer, "connection_generation", None) == self.snapshot_generation)

    def update_actions(self):
        available = self._available()
        loaded = available and self._snapshot_current()
        self.read_button.configure(state="normal" if available else "disabled")
        current_group = (self.groups[self.pages.index(self.pages.select())]
                         if self.pages.select() else None)
        editable = loaded and any(field.get("editable", True) and field["group"] == current_group
                                   for field in self.fields.values())
        for button in (self.all_button, self.clear_button):
            button.configure(state="normal" if editable else "disabled")
        self.preview_button.configure(state="normal" if loaded and self.has_changes() else "disabled")
        self.commit_button.configure(state="normal" if loaded and self.prepared else "disabled")
        self.completion_button.configure(state="normal" if loaded else "disabled")
        for key, button in self.checkbuttons.items():
            button.configure(state="normal" if loaded and self.fields[key].get("editable", True)
                             else "disabled")

    def freeze(self):
        self.frozen = True
        self.update_actions()

    def thaw(self):
        self.frozen = False
        self.update_actions()

    def invalidate(self):
        self.revision += 1
        self.snapshot = None
        self.snapshot_trainer = None
        self.snapshot_generation = None
        self.prepared = None
        self.fields.clear()
        self.all_fields.clear()
        self.completion_overrides.clear()
        self.values.clear()
        self.checkbuttons.clear()
        self.groups.clear()
        for tab in self.pages.tabs():
            self.pages.nametowidget(tab).destroy()
        self.info.set("记录未读取。连接或存档变化后请重新读取。")
        self.set_detail("读取后可调整记录，再预览具体变更。")
        self.update_actions()

    def _apply_snapshot(self, snapshot):
        self.invalidate()
        self.snapshot = snapshot
        self.snapshot_trainer = self.app.trainer
        self.snapshot_generation = getattr(self.app.trainer, "connection_generation", None)
        for field in snapshot["fields"]:
            key = field["key"]
            self.all_fields[key] = dict(field)
            if field["group"] == "research":
                continue
            self.fields[key] = dict(field)
            self.values[key] = tk.BooleanVar(value=bool(field["value"]))
        self.groups = list(dict.fromkeys(field["group"] for field in self.fields.values()))
        for group in self.groups:
            frame = ttk.Frame(self.pages, padding=12)
            self.pages.add(frame, text=GROUP_LABELS.get(group, group))
            group_fields = [field for field in self.fields.values() if field["group"] == group]
            for index, field in enumerate(group_fields):
                key = field["key"]
                label = field["label"] + ("（只读）" if not field.get("editable", True) else "")
                button = ttk.Checkbutton(frame, text=label, variable=self.values[key])
                column_count = 4 if group == "captured" else 1
                button.grid(row=index // column_count * 2, column=index % column_count,
                            sticky="w", padx=8, pady=5)
                self.checkbuttons[key] = button
                note = field.get("description") or field.get("note")
                if note:
                    ttk.Label(frame, text=note, wraplength=850).grid(
                        row=index // column_count * 2 + 1, column=index % column_count,
                        sticky="w", padx=8)
                self.values[key].trace_add("write", self._edited)
        self.info.set(f"已读取 {len(self.fields)} 项记录；勾选后先预览，再确认写入。")
        self.update_actions()

    def _edited(self, *_):
        self.revision += 1
        self.prepared = None
        count = len(self.changes())
        self.set_detail(f"待修改 {count} 项；请重新预览具体变更。\n" + self._research_note())
        self.update_actions()

    def select_group(self, value):
        if not self._available() or not self._snapshot_current() or not self.pages.select():
            return
        group = self.groups[self.pages.index(self.pages.select())]
        for key, field in self.fields.items():
            if field["group"] == group and field.get("editable", True):
                self.values[key].set(bool(value))

    def complete_draft(self):
        if not self._available() or not self._snapshot_current():
            return
        trainer = self.app.trainer
        generation = getattr(trainer, "connection_generation", None)
        revision = self.revision
        if not messagebox.askokcancel("领奖条件风险与完整存档备份提醒", COMPLETION_WARNING,
                                     parent=self.app.root, icon="warning"):
            return
        if not self._valid(trainer, generation, revision):
            return
        # Use the visible draft, so an earlier manual clear is also filled back in.
        draft = {**self.snapshot, "fields": [
            {**field, "value": (bool(self.values[field["key"]].get())
                               if field["key"] in self.values else
                               self.completion_overrides.get(field["key"], field["value"]))}
            for field in self.snapshot["fields"]]}
        try:
            changes = unown_records.completion_changes(draft)
        except ValueError as exc:
            self.app.status.set(str(exc))
            self.set_detail(str(exc))
            return
        if any(key not in self.all_fields or not self.all_fields[key].get("editable", True)
               or self.all_fields[key]["group"] not in ("captured", "research")
               for key in changes):
            raise ValueError("领奖条件包含未开放的字段，请重新读取记录。")
        current = {field["key"]: field["value"] for field in draft["fields"]}
        changes = {key: value for key, value in changes.items()
                   if current[key] != value}
        if not changes:
            self.app.status.set("当前草稿已具备任务领奖条件；有修改时请预览并确认写入，再与研究员对话。")
            return
        self.revision += 1
        self.prepared = None
        for key, value in changes.items():
            if key in self.values:
                self.values[key].set(value)
            elif self.all_fields[key]["group"] == "research":
                if value == self.all_fields[key]["value"]:
                    self.completion_overrides.pop(key, None)
                else:
                    self.completion_overrides[key] = value
        self.set_detail(f"已补齐领奖条件草稿；相对游戏记录共修改 {len(self.changes())} 项。\n"
                        + self._research_note() + "\n" + RESEARCH_RISK + "\n"
                        "请预览并确认写入，再前往阿露福遗迹研究所与研究员对话领取奖励、完成任务。")
        self.app.status.set("已补齐任务领奖条件草稿；请预览后确认写入。")
        self.update_actions()

    def read(self):
        if not self._available():
            return
        if self.has_changes() and not messagebox.askokcancel(
                "重新读取未知图腾记录", "重新读取会丢弃尚未写入的修改，是否继续？", parent=self.app.root):
            return
        trainer = self.app.trainer
        generation = getattr(trainer, "connection_generation", None)
        self.invalidate()
        revision = self.revision

        def done(snapshot):
            if self._valid(trainer, generation, revision):
                self._apply_snapshot(snapshot)
                self.app.status.set("已读取未知图腾记录。")

        self.app.run("读取未知图腾记录…", trainer.snapshot_unown_records, done)

    def preview(self):
        if not self._available() or not self._snapshot_current():
            return
        changes = self.changes()
        if not changes:
            self.app.status.set("未知图腾记录没有变更。")
            return
        trainer = self.app.trainer
        generation = getattr(trainer, "connection_generation", None)
        revision = self.revision
        snapshot = self.snapshot
        lines = [f"{GROUP_LABELS.get(self.all_fields[key]['group'], self.all_fields[key]['group'])} · "
                 f"{self.all_fields[key]['label']}："
                 f"{'已记录' if self.all_fields[key]['value'] else '未记录'} → "
                 f"{'已记录' if value else '未记录'}" for key, value in changes.items()]
        self.prepared = None
        self.update_actions()

        def done(prepared):
            if not self._valid(trainer, generation, revision):
                return
            self.prepared = (prepared, trainer, generation, revision, lines)
            self.set_detail("\n".join([f"将修改 {len(changes)} 项：", *lines,
                                       *([RESEARCH_RISK] if self.completion_overrides else []),
                                       "点击“确认写入”查看最终确认。原值会备份并读回核对。 "]))
            self.update_actions()
            self.app.status.set("未知图腾记录预览已准备，请核对具体变更。")

        self.app.run("准备未知图腾记录预览…",
                     lambda: trainer.prepare_unown_records(snapshot, changes), done)

    def commit(self):
        if not self._available() or self.prepared is None:
            return
        prepared, trainer, generation, revision, lines = self.prepared
        if not self._valid(trainer, generation, revision):
            self.invalidate()
            return
        if not messagebox.askokcancel("确认修改未知图腾记录", "\n".join([
                f"将修改以下 {len(lines)} 项：", *lines,
                *([RESEARCH_RISK] if self.completion_overrides else []),
                "确认后写入，原值将备份并读回核对。"]), parent=self.app.root):
            return
        if not self._valid(trainer, generation, revision):
            self.invalidate()
            return
        self.prepared = None
        self.update_actions()

        def job():
            result = trainer.commit_unown_records(prepared)
            return result, trainer.snapshot_unown_records()

        def done(result):
            if self._valid(trainer, generation, revision):
                self._apply_snapshot(result[1])
                self.app.status.set("未知图腾记录已写入并读回核对；原值已备份。")

        self.app.run("写入未知图腾记录并读回核对…", job, done)
