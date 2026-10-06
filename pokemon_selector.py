"""Keyboard-accessible Pokemon cards, bound to slot IDs rather than species."""

from tkinter import ttk


class PokemonSelector(ttk.Frame):
    def __init__(self, parent, capacity, columns, label, width=13, multi_select=False):
        super().__init__(parent)
        self.capacity = capacity
        self.columns = columns
        self.label = label
        self.records = {}
        self.selected = ()
        self.multi_select = multi_select
        self.last_toggle = False
        self.cards = []
        for slot in range(capacity):
            card = ttk.Button(
                self,
                text=f"{slot + 1}\n（空槽）",
                width=width,
                compound="top",
                command=lambda n=slot: self.choose(n),
            )
            card.grid(
                row=slot // columns,
                column=slot % columns,
                sticky="nsew",
                padx=3,
                pady=3,
            )
            card.state(["disabled"])
            if multi_select:
                card.bind("<Button-1>", lambda event, n=slot: self._click(event, n))
                card.bind("<Shift-space>", lambda event, n=slot: self._toggle_key(n))
            for key, step in [
                ("Left", -1),
                ("Right", 1),
                ("Up", -columns),
                ("Down", columns),
            ]:
                card.bind(f"<{key}>", lambda event, n=slot, d=step: self.move(n, d))
            self.cards.append(card)
        for col in range(columns):
            self.columnconfigure(col, weight=1)
        for row in range((capacity + columns - 1) // columns):
            self.rowconfigure(row, weight=1)

    def choose(self, slot):
        if "disabled" in self.cards[slot].state() or str(slot) not in self.records:
            return
        self.selection_set(str(slot))

    def _click(self, event, slot):
        if event.state & 0x0001:
            self.toggle(str(slot))
            self.cards[slot].focus_set()
            return "break"

    def _toggle_key(self, slot):
        self.toggle(str(slot))
        return "break"

    def toggle(self, ident):
        ident = str(ident)
        if ident not in self.records:
            return
        selected = list(self.selected)
        if ident in selected:
            selected.remove(ident)
        else:
            selected.append(ident)
        self.selection_set_many(selected, toggle=True)

    def move(self, slot, delta):
        target = slot + delta
        while 0 <= target < self.capacity:
            if (
                str(target) in self.records
                and "disabled" not in self.cards[target].state()
            ):
                self.choose(target)
                self.cards[target].focus_set()
                break
            target += delta
        return "break"

    def get_children(self):
        return tuple(self.records)

    def delete(self, *items):
        for ident in items:
            self.records.pop(str(ident), None)
            card = self.cards[int(ident)]
            card.configure(text=f"{int(ident) + 1}\n（空槽）", image="")
            card.state(["disabled", "!pressed"])
        self.selected = ()

    def insert(self, parent, position, *, iid, values, image=""):
        slot = int(iid)
        if not 0 <= slot < self.capacity:
            raise ValueError("选择槽超出范围")
        self.records[str(iid)] = {"values": values, "image": image}
        self.cards[slot].configure(text=self.label(values), image=image)
        self.cards[slot].state(["!disabled"])

    def item(self, ident, option=None, **changes):
        record = self.records[str(ident)]
        record.update(changes)
        if changes:
            self.cards[int(ident)].configure(
                text=self.label(record["values"]), image=record["image"]
            )
        return record[option] if option else record.copy()

    def selection(self):
        return self.selected

    def selection_set(self, ident):
        self.selection_set_many((ident,))

    def selection_set_many(self, idents, *, toggle=False, notify=True):
        idents = tuple(dict.fromkeys(str(ident) for ident in idents if str(ident) in self.records))
        if not self.multi_select:
            idents = idents[:1]
        changed = self.selected != idents
        self.last_toggle = toggle
        self.selected = idents
        selected = set(idents)
        for slot, card in enumerate(self.cards):
            card.state(["pressed" if str(slot) in selected else "!pressed"])
        if changed and notify:
            self.event_generate("<<TreeviewSelect>>", when="tail")
