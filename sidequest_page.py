"""Read-only quest table; all Tk updates stay in the app's main-thread callback."""

import json
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from sidequest_data import filter_quests, read_snapshot


STATUS_LABELS = {
    "complete": ("✓", "完成"),
    "incomplete": ("✗", "未完成"),
    "in_progress": ("✗", "进行中"),
    "unknown": ("—", "未读取"),
}
CATEGORIES = (("all", "全部"), ("complete", "完成"),
              ("incomplete", "未完成"), ("in_progress", "进行中"))


class SidequestPage:
    def __init__(self, app, catalog, layout_path):
        self.app = app
        self.catalog = catalog
        self.layout_path = layout_path
        self.statuses = {}
        self.rows = {row["id"]: row for row in catalog}
        self.tab = ttk.Frame(app.nb, padding=8)
        app.nb.add(self.tab, text="支线任务")
        bar = ttk.Frame(self.tab)
        bar.pack(fill="x")
        app.button(bar, "读取任务进度", self.read, side="left")
        ttk.Label(bar, text="搜索").pack(side="left", padx=(12, 4))
        self.query = tk.StringVar()
        ttk.Entry(bar, textvariable=self.query).pack(side="left", fill="x", expand=True)
        self.count = tk.StringVar()
        ttk.Label(self.tab, textvariable=self.count, padding=(0, 8)).pack(anchor="w")
        self.pages = ttk.Notebook(self.tab)
        self.pages.pack(fill="both", expand=True)
        self.trees = {}
        for category, label in CATEGORIES:
            frame = ttk.Frame(self.pages)
            self.pages.add(frame, text=label)
            tree = ttk.Treeview(frame, columns=("id", "check", "status", "title", "summary"),
                                show="headings", selectmode="browse")
            for key, heading, width in (("id", "编号", 55), ("check", "完成", 50),
                                       ("status", "状态", 80), ("title", "任务", 210),
                                       ("summary", "任务内容", 480)):
                tree.heading(key, text=heading)
                tree.column(key, width=width, minwidth=45,
                            stretch=key in ("title", "summary"),
                            anchor="center" if key in ("id", "check", "status") else "w")
            vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            tree.grid(row=0, column=0, sticky="nsew")
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            tree.bind("<<TreeviewSelect>>", lambda event, t=tree: self.show_detail(t))
            tree.bind("<Double-1>", lambda event, t=tree: self.open_detail(t))
            self.trees[category] = tree
        detail_frame = ttk.Frame(self.tab)
        detail_frame.pack(fill="x", pady=6)
        self.detail = tk.Text(detail_frame, height=7, wrap="word", state="disabled")
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        self.detail.pack(fill="both", expand=True)
        actions = ttk.Frame(self.tab)
        actions.pack(fill="x")
        app.button(actions, "网页详情", self.open_selected, side="left")
        self.read_at = tk.StringVar(value="连接游戏后读取进度。总数为网页收录的任务，未完成包含进行中。")
        ttk.Label(actions, textvariable=self.read_at).pack(side="left", padx=8)
        self.query.trace_add("write", lambda *_: self.render())
        self.render()

    def invalidate(self):
        self.statuses = {}
        self.read_at.set("连接已改变，请重新读取任务进度。未完成包含进行中。")
        self.set_detail("")
        self.render()

    def render(self):
        totals = {status: sum(self.statuses.get(row["id"], "unknown") == status
                              for row in self.catalog) for status in STATUS_LABELS}
        self.count.set(f"支线总数 {len(self.catalog)}　完成 {totals['complete']}　"
                       f"未完成 {totals['incomplete'] + totals['in_progress']}　"
                       f"进行中 {totals['in_progress']}　未读取 {totals['unknown']}")
        for index, (category, label) in enumerate(CATEGORIES):
            tree = self.trees[category]
            selected = tree.selection()
            tree.delete(*tree.get_children())
            matches = filter_quests(self.catalog, self.statuses, category, self.query.get())
            for row in matches:
                mark, status = STATUS_LABELS.get(self.statuses.get(row["id"], "unknown"),
                                                STATUS_LABELS["unknown"])
                tree.insert("", "end", iid=row["id"],
                            values=(row["id"], mark, status, row["title"], row["summary"]))
            self.pages.tab(index, text=f"{label}（{len(matches)}）")
            if selected and tree.exists(selected[0]):
                tree.selection_set(selected[0])
        self.set_detail("")

    def read(self):
        trainer = self.app.trainer
        if trainer is None:
            self.app.status.set("请先连接游戏，再读取支线任务进度。")
            return

        def job():
            trainer.verify()
            layout = json.loads(Path(self.layout_path).read_text(encoding="utf-8"))
            return read_snapshot(trainer.g, trainer.profile, layout=layout)

        def done(statuses):
            if self.app.trainer is not trainer:
                return
            self.statuses = statuses
            self.render()
            if not any(status != "unknown" for status in statuses.values()):
                self.read_at.set("未读取到可靠的任务进度，请检查连接与 ROM 版本后重读。")
                self.app.status.set("任务进度未能核实，已保留为未读取。")
                return
            self.read_at.set(f"读取时间 {datetime.now().strftime('%H:%M:%S')}；"
                             "未完成包含进行中。游戏推进后请重新读取。")
            self.app.status.set("已读取支线任务进度。")

        self.app.run("读取支线任务进度…", job, done)

    def set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def show_detail(self, tree):
        selected = tree.selection()
        if not selected:
            self.set_detail("")
            return
        row = self.rows[selected[0]]
        lines = [f"{row['id']}　{row['title']}", row["summary"]]
        if row.get("objective"):
            lines.append(f"目标：{row['objective']}")
        for place in row.get("locations", []):
            text = "；".join(str(place[key]) for key in ("place", "trigger", "scenes")
                             if place.get(key))
            lines.append(f"地点：{text}")
        for stage in row.get("stages", []):
            title = f"{stage.get('number', '')}. {stage.get('title', '')}"
            if stage.get("optional"):
                title += "（可选）"
            lines.append(title)
            for key, label in (("unlock", "条件"), ("journal", "任务日志")):
                if stage.get(key):
                    lines.append(f"  {label}：{stage[key]}")
        for reward in row.get("rewards", []):
            items = "、".join(item["name"] for item in reward.get("items", []))
            condition = reward.get("condition", "")
            lines.append(f"奖励：{condition}{'；' if condition and items else ''}{items}")
        lines.extend(f"备注：{note}" for note in row.get("notes", []))
        for scene in row.get("story", []):
            lines.extend((scene.get("title", ""), scene.get("text", "")))
        self.set_detail("\n".join(lines))

    def open_detail(self, tree):
        selected = tree.selection()
        if selected:
            webbrowser.open(self.rows[selected[0]]["source_url"])

    def open_selected(self):
        index = self.pages.index(self.pages.select())
        self.open_detail(self.trees[CATEGORIES[index][0]])
