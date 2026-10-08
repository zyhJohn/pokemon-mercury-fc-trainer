"""Small live field editors; all emulator work goes through App.run."""

import tkinter as tk
from tkinter import messagebox, ttk


class RepelWindow:
    def __init__(self, app):
        self.app = app
        self.window = None
        self.snapshot = None
        self.steps = tk.StringVar(master=app.root)
        self.detail = tk.StringVar(master=app.root, value="连接后读取喷雾剩余步数。")
        self.steps.trace_add("write", lambda *_: self._input_changed())

    def open(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return
        win = tk.Toplevel(self.app.root)
        win.title("喷雾剩余步数")
        win.transient(self.app.root)
        win.resizable(False, False)
        self.window = win
        body = ttk.Frame(win, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, textvariable=self.detail, wraplength=440).pack(anchor="w", pady=6)
        row = ttk.Frame(body)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="剩余步数（0～250）").pack(side="left")
        ttk.Entry(row, textvariable=self.steps, width=8).pack(side="left", padx=8)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="读取", command=self.read).pack(side="left", padx=3)
        ttk.Button(actions, text="预览修改", command=self.preview).pack(side="left", padx=3)
        ttk.Button(actions, text="预览归零", command=lambda: self.preview(0)).pack(side="left", padx=3)
        ttk.Button(actions, text="关闭", command=win.destroy).pack(side="right", padx=3)
        if self.app.trainer is not None and not self.app.busy:
            self.read()

    def invalidate(self):
        self.snapshot = None
        self.steps.set("")
        self.detail.set("连接已改变，请重新读取喷雾剩余步数。")

    def _input_changed(self):
        # A read snapshot is still useful, but any pending preview becomes stale.
        if self.snapshot is not None:
            self.detail.set(f"已读取 {self.snapshot['steps']} 步；输入变化后需重新预览。")

    def read(self):
        trainer = self.app.trainer
        if trainer is None or self.app.busy:
            return

        def done(snap):
            if self.app.trainer is not trainer:
                return
            self.snapshot = snap
            self.steps.set(str(snap["steps"]))
            self.detail.set(f"当前剩余 {snap['steps']} 步。写入只修改计步变量，不改变背包道具。")

        self.app.run("读取喷雾剩余步数…", trainer.snapshot_repel, done)

    def preview(self, zero=None):
        trainer = self.app.trainer
        if trainer is None or self.app.busy or self.snapshot is None:
            return
        value = str(zero) if zero is not None else self.steps.get()
        try:
            number = int(value)
            if value.strip() != str(number) or not 0 <= number <= 250:
                raise ValueError
        except ValueError:
            messagebox.showerror("未预览", "剩余步数须为 0～250 的整数。", parent=self.window)
            return
        if zero is not None:
            self.steps.set(value)
        original = self.snapshot

        def prepared_done(prepared):
            if (self.app.trainer is not trainer or self.snapshot is not original
                    or self.steps.get() != value or self.window is None
                    or not self.window.winfo_exists()):
                self.detail.set("连接或输入已变化，请重新读取和预览。")
                return
            before = prepared["steps"]
            after = prepared["new_steps"]
            message = f"喷雾剩余步数：{before} → {after}\n确认后写入并读回核对，原值会备份。"
            if not messagebox.askokcancel("喷雾步数预览", message, parent=self.window):
                return

            def commit():
                result = trainer.commit_repel_steps(prepared)
                return result, trainer.snapshot_repel()

            def done(result):
                if self.app.trainer is not trainer:
                    return
                self.snapshot = result[1]
                self.steps.set(str(result[1]["steps"]))
                backup = result[0].get("backup")
                self.detail.set(f"已读回 {result[1]['steps']} 步。" +
                                (f"\n备份：{backup}" if backup else "没有变化，无需写入。"))
                self.app.status.set(self.detail.get())

            self.app.run("写入喷雾步数并读回核对…", commit, done)

        self.app.run("准备喷雾步数预览…", lambda: trainer.prepare_repel_steps(number), prepared_done)
