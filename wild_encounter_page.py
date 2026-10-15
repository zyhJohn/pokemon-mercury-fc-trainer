"""Explicit temporary future-encounter controls; all Tk work stays on the UI thread."""

import tkinter as tk
from tkinter import messagebox, ttk


SCOPE = "后续普通草地、水面、碎岩及钓鱼野生遭遇；不含训练师战斗和剧情特例"


class WildEncounterPage:
    def __init__(self, app):
        self.app = app
        self.revision = 0
        self.frozen = False
        self.bound_trainer = None
        self.bound_controller = None
        self.bound_generation = None
        self.profile = None
        self.species_by_label = {}
        self.active_state = None
        self.tab = ttk.Frame(app.nb, padding=12)
        app.nb.add(self.tab, text="野生遭遇")
        ttk.Label(self.tab, text="指定后续野生遭遇的物种和等级", font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(self.tab, text=SCOPE + "。", wraplength=920).pack(anchor="w", pady=(6, 12))
        form = ttk.LabelFrame(self.tab, text="待开启参数（修改参数后须再次点击开启）", padding=12)
        form.pack(fill="x")
        self.search = tk.StringVar()
        self.species = tk.StringVar()
        self.level = tk.StringVar(value="5")
        ttk.Label(form, text="搜索名称 / 内部编号").grid(row=0, column=0, sticky="w", pady=4)
        self.search_entry = ttk.Entry(form, textvariable=self.search, width=28)
        self.search_entry.grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        ttk.Label(form, text="物种 / 形态").grid(row=1, column=0, sticky="w", pady=4)
        self.species_combo = ttk.Combobox(form, textvariable=self.species, state="readonly", width=45)
        self.species_combo.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        ttk.Label(form, text="等级（1～100）").grid(row=2, column=0, sticky="w", pady=4)
        self.level_entry = ttk.Entry(form, textvariable=self.level, width=10)
        self.level_entry.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="编号为当前 ROM 内部物种编号，不是全国图鉴编号。", wraplength=880).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        actions = ttk.Frame(self.tab)
        actions.pack(fill="x", pady=12)
        self.arm_button = app.button(actions, "开启指定遭遇", self.arm, side="left")
        self.stop_button = app.button(actions, "关闭指定遭遇", self.stop, side="left", padx=8)
        self.read_button = app.button(actions, "读取状态", self.read, side="left")
        self.active = tk.StringVar(value="实际状态：未读取")
        self.info = tk.StringVar(value="连接游戏后读取状态。")
        ttk.Label(self.tab, textvariable=self.active, wraplength=920).pack(anchor="w", pady=6)
        ttk.Label(self.tab, textvariable=self.info, wraplength=920).pack(anchor="w", pady=6)
        ttk.Label(self.tab, text="喷雾仍按游戏原本抽签的等级判断，可能阻止遭遇。特殊形态仍受游戏原生规则影响。\n"
                  "此开关为临时控制；断线、切换 ROM 或关闭工具后需重新开启。\n"
                  "卸载桥接脚本、读取即时存档前请先关闭此功能；恢复异常时请重新载入 ROM。保存和重载场景仍待实机核验。",
                  wraplength=920).pack(anchor="w", pady=12)
        self.search.trace_add("write", self._filter_species)
        self.refresh_profile()

    def _current_context(self):
        trainer = self.app.trainer
        return trainer, getattr(self.app, "wild_control", None), getattr(trainer, "connection_generation", None)

    def _bound_current(self):
        trainer, controller, generation = self._current_context()
        return (trainer is not None and controller is not None and trainer is self.bound_trainer
                and controller is self.bound_controller and generation == self.bound_generation)

    def _available(self):
        return (self._bound_current() and not self.frozen and not getattr(self.app, "busy", False)
                and not getattr(self.app, "closed", False) and not getattr(self.app, "close_requested", False))

    def _valid(self, trainer, controller, generation, revision):
        current_trainer, current_controller, current_generation = self._current_context()
        return (current_trainer is trainer and current_controller is controller
                and current_generation == generation and self.revision == revision
                and not getattr(self.app, "closed", False) and not getattr(self.app, "close_requested", False))

    def refresh_profile(self):
        """Call after connection/profile selection; unchanged contexts keep their draft."""
        trainer, controller, generation = self._current_context()
        profile = getattr(trainer, "profile", None) if trainer is not None else getattr(self.app, "profile", None)
        if (trainer is self.bound_trainer and controller is self.bound_controller
                and generation == self.bound_generation and profile is self.profile):
            self.update_actions()
            return
        self.invalidate()
        self.bound_trainer, self.bound_controller, self.bound_generation = trainer, controller, generation
        self.profile = profile
        names = getattr(self.app, "names", {}).get("breeds", {})
        self.species_by_label = {
            f"{names.get(str(ident), '未收录名称')}（内部编号 {ident}）": ident
            for ident in sorted(int(key) for key in (profile or {}).get("species", {}) if str(key).isdigit())
            if ident > 0
        }
        self.search.set("")
        self.species.set("")
        self._filter_species()
        self.update_actions()

    def _filter_species(self, *_):
        term = self.search.get().strip().casefold()
        values = tuple(label for label in self.species_by_label if term in label.casefold())
        self.species_combo.configure(values=values)
        # Keep a valid chosen draft while filtering; search never silently changes it.

    def update_actions(self):
        available = self._available()
        for button in (self.arm_button, self.stop_button, self.read_button):
            button.configure(state="normal" if available else "disabled")
        for entry in (self.search_entry, self.level_entry):
            entry.configure(state="normal" if available else "disabled")
        self.species_combo.configure(state="readonly" if available else "disabled")

    def freeze(self):
        self.frozen = True
        self.update_actions()

    def thaw(self):
        self.frozen = False
        self.update_actions()

    def invalidate(self):
        self.revision += 1
        self.bound_trainer = self.bound_controller = self.bound_generation = None
        self.profile = None
        self.species_by_label.clear()
        self.species_combo.configure(values=())
        self.species.set("")
        self.active_state = None
        self.active.set("实际状态：未读取")
        self.info.set("连接或 ROM 已变化，请重新读取状态。")
        self.update_actions()

    def apply_status(self, state):
        """Apply a current controller result on the Tk thread (heartbeat caller guards its context)."""
        if not self._bound_current() or getattr(self.app, "closed", False) or getattr(self.app, "close_requested", False):
            return
        self.active_state = dict(state)
        status = state.get("state")
        if status == "active":
            ident = state.get("species")
            name = getattr(self.app, "names", {}).get("breeds", {}).get(str(ident), "未收录名称")
            self.active.set(f"实际状态：已开启 · {name}（内部编号 {ident}）· 等级 {state.get('level')}")
        elif status == "inactive":
            self.active.set("实际状态：已关闭")
        else:
            self.active.set("实际状态：异常，当前是否生效尚未确认；请读取状态或关闭")
        self.info.set(str(state.get("reason") or ("参数仅在点击开启后生效。" if status == "active" else "可选择参数后开启。")))
        self.update_actions()

    def _submit(self, label, action):
        trainer, controller, generation = self._current_context()
        revision = self.revision
        self.info.set(label)

        def job():
            # Context checks and controller operations use only ordinary Python data.
            if not self._valid(trainer, controller, generation, revision):
                return None
            try:
                return action(controller)
            except Exception as exc:
                return {"state": "error", "species": None, "level": None, "reason": str(exc) or type(exc).__name__}

        def done(state):
            if state is not None and self._valid(trainer, controller, generation, revision):
                self.apply_status(state)
                self.app.status.set("野生遭遇状态：" + self.active.get().removeprefix("实际状态："))

        self.app.run(label, job, done)

    def arm(self):
        if not self._available():
            return
        label = self.species.get()
        species = self.species_by_label.get(label)
        level_text = self.level.get().strip()
        if species is None or not level_text.isascii() or not level_text.isdigit() or not 1 <= int(level_text) <= 100:
            messagebox.showerror("参数无效", "请选择当前 ROM 已核验的物种，并填写 1～100 的整数等级。", parent=self.app.root)
            return
        level = int(level_text)
        context = self._current_context()
        revision = self.revision
        if not messagebox.askokcancel("确认开启指定野生遭遇", f"物种：{label}\n等级：{level}\n范围：{SCOPE}。\n"
                                     "喷雾仍按原抽签等级判断；修改参数不会自动生效。\n确认开启？", parent=self.app.root):
            return
        if not self._available() or not self._valid(*context, revision):
            return
        self._submit("开启指定野生遭遇…", lambda controller: controller.arm(species, level))

    def stop(self):
        if self._available():
            self._submit("关闭指定野生遭遇…", lambda controller: controller.stop())

    def read(self):
        if self._available():
            self._submit("读取野生遭遇状态…", lambda controller: controller.status())
