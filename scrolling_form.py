"""A resizable numeric form that keeps keyboard-focused fields in view."""

import tkinter as tk
from tkinter import ttk


class ScrollingForm(ttk.Frame):
    def __init__(self, parent, padding=8):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas, padding=padding)
        self.window_id = self.canvas.create_window(
            (0, 0), anchor="nw", window=self.body
        )
        self.body.bind("<Configure>", self._region)
        self.canvas.bind("<Configure>", self._resize)

    def _region(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

        def wrap_labels(widget):
            if (
                isinstance(widget, ttk.Label)
                and widget.cget("wraplength") not in ("", 0, "0")
                and widget.winfo_pixels(widget.cget("wraplength")) > 0
            ):
                widget.configure(wraplength=max(100, event.width - 32))
            for child in widget.winfo_children():
                wrap_labels(child)

        wrap_labels(self.body)

    def enable_navigation(self):
        def visit(widget):
            widget.bind("<MouseWheel>", self._wheel, add="+")
            widget.bind("<FocusIn>", self._focus, add="+")
            for child in widget.winfo_children():
                visit(child)

        visit(self.body)
        self.canvas.bind("<MouseWheel>", self._wheel)

    def _wheel(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def _focus(self, event):
        widget = event.widget
        self.canvas.update_idletasks()
        top = widget.winfo_rooty() - self.body.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible_top = self.canvas.canvasy(0)
        visible_bottom = visible_top + self.canvas.winfo_height()
        height = max(1, self.body.winfo_height())
        if top < visible_top:
            self.canvas.yview_moveto(max(0, top - 8) / height)
        elif bottom > visible_bottom:
            self.canvas.yview_moveto(
                max(0, bottom - self.canvas.winfo_height() + 8) / height
            )
