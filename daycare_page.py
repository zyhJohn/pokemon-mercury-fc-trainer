"""Live daycare eligibility and pending-egg transaction window."""

import tkinter as tk
from tkinter import messagebox, ttk


def _field(record, key, default=None):
    return record.get(key, default) if isinstance(record, dict) else getattr(record, key, default)


class DaycareWindow:
    REQUIRED_CAPABILITIES = frozenset({"BATCH", "ROMCRC", "CRCBATCH", "BATCHVERIFY"})

    def __init__(self, app):
        self.app = app
        self.window = None
        self.snapshot = None
        self.epoch = 0
        self.detail = tk.StringVar(master=app.root, value="连接后读取培育屋状态。")
        self.action_button = None

    def _alive(self):
        return self.window is not None and self.window.winfo_exists()

    def open(self):
        if self._alive():
            self.window.lift()
            return
        window = tk.Toplevel(self.app.root)
        self.epoch += 1
        window.title("培育屋待领取蛋")
        window.transient(self.app.root)
        window.resizable(False, False)
        self.window = window
        body = ttk.Frame(window, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, textvariable=self.detail, wraplength=500,
                  justify="left").pack(anchor="w", pady=6)
        ttk.Label(body, text="仅加速符合条件的现有父母组合。蛋由游戏培育屋 NPC 原生领取并决定继承；此操作不直接创建个体。",
                  wraplength=500, justify="left").pack(anchor="w", pady=8)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="重新读取", command=self.read).pack(side="left", padx=3)
        self.action_button = ttk.Button(actions, text="预览设为待领取", command=self.preview,
                                        state="disabled")
        self.action_button.pack(side="left", padx=3)
        ttk.Button(actions, text="关闭", command=window.destroy).pack(side="right", padx=3)
        if self.app.trainer is not None and not self.app.busy:
            self.read()

    def invalidate(self):
        self.epoch += 1
        self.snapshot = None
        self.detail.set("连接已变化，请重新读取培育屋。")
        self._update_action()

    def _update_action(self):
        if self.action_button is not None and self.action_button.winfo_exists():
            capabilities = getattr(getattr(self.app.trainer, "g", None), "capabilities", ())
            ready = (self._alive() and not self.app.busy and self.app.trainer is not None
                     and self.snapshot is not None and self.snapshot.get("eligible") is True
                     and self.REQUIRED_CAPABILITIES.issubset(capabilities))
            self.action_button.configure(state="normal" if ready else "disabled")

    def _current(self, trainer, window, epoch):
        return (self.app.trainer is trainer and self.window is window
                and self.epoch == epoch and window.winfo_exists())

    def _describe_parent(self, parent, index):
        species = _field(parent, "species", 0)
        if not species:
            return f"父母 {index}：未寄放"
        name = _field(parent, "nickname")
        species_name = self.app.names.get("breeds", {}).get(str(species), f"物种 {species}")
        gender = _field(parent, "gender")
        ot = _field(parent, "ot_name")
        otid = _field(parent, "otid")
        steps = _field(parent, "steps")
        parts = [f"父母 {index}：{name or '昵称未核验'}（{species_name} · ID {species}）",
                 f"性别 {gender or '未核验'}", f"OT {ot or '姓名未核验'}"]
        if otid is not None:
            parts.append(f"OTID {otid}")
        if steps is not None:
            parts.append(f"寄放步数 {steps}")
        if _field(parent, "egg", _field(parent, "is_egg")):
            parts.append("当前记录是蛋")
        return " · ".join(parts)

    def _render(self, snap):
        parents = snap.get("parents", ())
        lines = [self._describe_parent(parent, i + 1) for i, parent in enumerate(parents)]
        lines += [f"兼容分：{snap.get('compatibility_score', '未读取')}",
                  f"已有待领蛋：{'是' if snap.get('pending') else '否'}",
                  f"旧后代 token：{snap.get('offspring_token', '未读取')}"]
        if snap.get("eligible") is True:
            capabilities = getattr(getattr(self.app.trainer, "g", None), "capabilities", ())
            lines.append("已通过后端资格检查，可预览加速。" if
                         self.REQUIRED_CAPABILITIES.issubset(capabilities) else
                         "资格已通过，但桥接缺少安全写入能力，请重载本项目桥接脚本。")
        else:
            lines.append("不可执行：" + (snap.get("reason") or "资格未通过，原因未返回"))
        self.detail.set("\n".join(lines))
        self._update_action()

    def read(self):
        trainer = self.app.trainer
        if trainer is None or self.app.busy or not self._alive():
            return
        self.epoch += 1
        epoch, window = self.epoch, self.window
        self.snapshot = None
        self._update_action()

        def job():
            try:
                return trainer.snapshot_daycare(), None
            except Exception as exc:
                return None, str(exc) or type(exc).__name__

        def done(result):
            if not self._current(trainer, window, epoch):
                return
            snap, error = result
            if error:
                self.snapshot = None
                self.detail.set("培育屋读取失败，写入不可用：" + error)
                self._update_action()
                return
            self.snapshot = snap
            self._render(snap)

        self.app.run("读取培育屋父母与待领状态…", job, done)

    def preview(self):
        trainer, original = self.app.trainer, self.snapshot
        if (trainer is None or self.app.busy or original is None
                or original.get("eligible") is not True or not self._alive()
                or not self.REQUIRED_CAPABILITIES.issubset(
                    getattr(getattr(trainer, "g", None), "capabilities", ()))):
            return
        epoch, window = self.epoch, self.window
        self.action_button.configure(state="disabled")

        def job():
            fresh = trainer.snapshot_daycare()
            if fresh != original:
                raise ValueError("父母、计步、旗标或连接数据已变化，请重新读取")
            return trainer.prepare_daycare_egg()

        def prepared_done(prepared):
            if (not self._current(trainer, window, epoch)
                    or self.snapshot is not original):
                return
            parent_text = "\n".join(self._describe_parent(p, i + 1)
                                    for i, p in enumerate(original.get("parents", ())))
            message = (f"{parent_text}\n兼容分：{original['compatibility_score']}\n"
                       "待领标志：未设置 → 已设置\n"
                       "确认后将备份、单次写入并读回。请到游戏培育屋 NPC 原生领取；继承结果由游戏生成。\n"
                       "仅同一连接的最近已确认操作可条件恢复；重连、后续培育屋写入或恢复尝试后，该恢复凭据失效。")
            if not messagebox.askokcancel("培育屋加速预览", message, parent=self.window):
                self._update_action()
                return
            self.snapshot = None
            self.epoch += 1
            commit_epoch = self.epoch
            self._update_action()

            def commit():
                result = trainer.commit_daycare_egg(prepared)
                return result, trainer.snapshot_daycare()

            def done(result):
                if not self._current(trainer, window, commit_epoch):
                    return
                self.snapshot = result[1]
                self._render(result[1])
                backup = result[0].get("backup")
                if backup:
                    self.detail.set(self.detail.get() + f"\n已读回核对；备份：{backup}\n"
                                    "仅同一连接最近一次已确认操作可条件恢复；重连、后续培育屋写入或恢复尝试后，该恢复凭据失效。")
                    self.app.status.set(f"培育屋已设待领取并读回；备份：{backup}")
                else:
                    self.app.status.set("培育屋状态没有变化。")

            self.app.run("设置培育屋待领标志并读回…", commit, done)

        self.app.run("核对培育屋资格并准备预览…", job, prepared_done)
