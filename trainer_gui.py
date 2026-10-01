# -*- coding: utf-8 -*-
"""
宝可梦水银FC 修改器（mGBA 版，图形界面）
======================================
类似 PokemonMemHack 的图形化修改器，通过内存桥接读写游戏内存。

两种连接方式（自动尝试，优先 TCP 桥接）：
  1. TCP 桥接（推荐，无需 GDB/无需 cmd）：在 mGBA 里加载 mercury_bridge.lua 脚本
     （mGBA 菜单 Tools -> Scripting -> File -> Load Script），脚本会在本地监听端口。
  2. GDB 桩（旧方式）：mGBA.exe -g "游戏.gba"，端口 2345。

用法：
  1. 正常启动 mGBA 并进入游戏（双击 mGBA.exe 即可，无需任何参数）。
  2. 在 mGBA 里加载一次 mercury_bridge.lua（Tools -> Scripting）。
  3. 双击运行本程序（或 python trainer_gui.py），点「连接」。

适配：火红(BPRE)改版《宝可梦水银FC~致150年后的你》
说明：本改版把存档结构 SaveBlock1 前移了 0x7C，金钱真实地址 0x020257BC。
"""

import os
import sys
import json
import socket
import struct
import tkinter as tk
from tkinter import ttk, messagebox


def resource_path(rel):
    """兼容 PyInstaller 打包：优先取解包目录，否则取脚本目录。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)

# ============ 常量 ============
GDB_HOST = "127.0.0.1"
GDB_PORT = 2345
TCP_HOST = "127.0.0.1"
TCP_PORT = 8888          # mercury_bridge.lua 的默认端口（被占用会 +1）

ADDR = {
    "saveBlock1Ptr": 0x03005008,   # gSaveBlock1Ptr
    "moneyOff": 0x290,             # 金钱偏移
    "coinsOff": 0x294,             # 代币偏移
    "party": 0x02024284,           # 队伍
    "partyCount": 0x02024029,      # 队伍数量
    "bag": 0x0203BB20,             # 背包道具口袋（实测定位，[ID 2B][数量 2B] 明文）
    "bagSlots": 64,                # 道具口袋槽位上限
}

ORDER = ["GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA",
         "AGEM", "AGME", "AEGM", "AEMG", "AMGE", "AMEG",
         "EGAM", "EGMA", "EAGM", "EAMG", "EMGA", "EMAG",
         "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG"]


# ============ GDB 客户端 ============
class GDBClient:
    def __init__(self):
        self.s = None

    def connect(self):
        self.s = socket.socket()
        self.s.settimeout(6)
        self.s.connect((GDB_HOST, GDB_PORT))

    def close(self):
        if self.s:
            try:
                self.s.close()
            except Exception:
                pass
            self.s = None

    def _read_packet(self):
        d = b""
        while b"$" not in d:
            b = self.s.recv(1)
            if not b:
                return None
            d += b
        while b"#" not in d:
            b = self.s.recv(1)
            if not b:
                return None
            d += b
        d += self.s.recv(2)
        return d

    def _cmd(self, payload):
        ck = sum(payload.encode("latin1")) & 0xFF
        self.s.sendall(b"$" + payload.encode("latin1") + b"#" + f"{ck:02x}".encode())
        b = self.s.recv(1)
        if b == b"$":
            d = b
            while b"#" not in d:
                d += self.s.recv(1)
            d += self.s.recv(2)
            return d
        if b != b"+":
            return None
        return self._read_packet()

    def read(self, addr, n):
        r = self._cmd(f"m{addr:x},{n:x}")
        if r is None:
            raise IOError("GDB 读取超时")
        body = r[1:r.rfind(b"#")]
        if body.startswith(b"E") or body == b"":
            raise IOError("GDB 读取错误")
        return bytes.fromhex(body.decode())

    def write(self, addr, data):
        r = self._cmd(f"M{addr:x},{len(data)}:{data.hex()}")
        if r is None:
            raise IOError("GDB 写入超时")
        body = r[1:r.rfind(b"#")]
        if body != b"OK":
            raise IOError("GDB 写入错误: " + body.decode(errors="ignore"))
        return True

    def r8(self, a): return self.read(a, 1)[0]
    def r16(self, a): return struct.unpack("<H", self.read(a, 2))[0]
    def r32(self, a): return struct.unpack("<I", self.read(a, 4))[0]
    def w16(self, a, v): self.write(a, struct.pack("<H", v & 0xFFFF))
    def w32(self, a, v): self.write(a, struct.pack("<I", v & 0xFFFFFFFF))
    def w8(self, a, v): self.write(a, struct.pack("<B", v & 0xFF))


# ============ TCP 内存桥接客户端（mercury_bridge.lua） ============
class MemClient:
    """连接 mGBA 内 mercury_bridge.lua 提供的 TCP 服务，替代 GDB 桩。

    接口与 GDBClient 保持一致：r8/r16/r32/w8/w16/w32/read/write。
    """

    def __init__(self, host=TCP_HOST, port=TCP_PORT):
        self.host = host
        self.port = port
        self.s = None

    def connect(self):
        # 尝试从默认端口开始，向上扫描（脚本被占用时会 +1 递增）
        last_err = None
        for port in range(self.port, self.port + 50):
            try:
                s = socket.socket()
                s.settimeout(3)
                s.connect((self.host, port))
                # 握手
                s.sendall(b"PING\n")
                data = b""
                while b"\n" not in data:
                    chunk = s.recv(64)
                    if not chunk:
                        break
                    data += chunk
                if data.strip() == b"PONG":
                    self.s = s
                    self.port = port
                    return
                s.close()
            except Exception as e:
                last_err = e
                continue
        raise IOError(f"未找到 mGBA 内存桥接服务（端口 {self.port}+）。"
                      f"请确认已在 mGBA 中加载 mercury_bridge.lua。错误: {last_err}")

    def close(self):
        if self.s:
            try:
                self.s.close()
            except Exception:
                pass
            self.s = None

    def _roundtrip(self, cmd):
        self.s.sendall(cmd.encode("ascii") + b"\n")
        data = b""
        while b"\n" not in data:
            chunk = self.s.recv(4096)
            if not chunk:
                raise IOError("连接被 mGBA 关闭")
            data += chunk
        return data.strip()

    def read(self, addr, n):
        resp = self._roundtrip(f"READ {addr:x} {n:x}")
        if resp.startswith(b"ERR"):
            raise IOError("读取失败: " + resp.decode(errors="ignore"))
        return bytes.fromhex(resp.decode())

    def write(self, addr, data):
        resp = self._roundtrip(f"WRITE {addr:x} {data.hex()}")
        if resp != b"OK":
            raise IOError("写入失败: " + resp.decode(errors="ignore"))
        return True

    def r8(self, a):
        resp = self._roundtrip(f"READ8 {a:x}")
        if resp.startswith(b"ERR"):
            raise IOError("读取失败: " + resp.decode(errors="ignore"))
        return int(resp) & 0xFF

    def r16(self, a):
        resp = self._roundtrip(f"READ16 {a:x}")
        if resp.startswith(b"ERR"):
            raise IOError("读取失败: " + resp.decode(errors="ignore"))
        return int(resp) & 0xFFFF

    def r32(self, a):
        resp = self._roundtrip(f"READ32 {a:x}")
        if resp.startswith(b"ERR"):
            raise IOError("读取失败: " + resp.decode(errors="ignore"))
        return int(resp) & 0xFFFFFFFF

    def w8(self, a, v):
        resp = self._roundtrip(f"WRITE8 {a:x} {v & 0xFF}")
        if resp != b"OK":
            raise IOError("写入失败: " + resp.decode(errors="ignore"))
        return True

    def w16(self, a, v):
        resp = self._roundtrip(f"WRITE16 {a:x} {v & 0xFFFF}")
        if resp != b"OK":
            raise IOError("写入失败: " + resp.decode(errors="ignore"))
        return True

    def w32(self, a, v):
        resp = self._roundtrip(f"WRITE32 {a:x} {v & 0xFFFFFFFF}")
        if resp != b"OK":
            raise IOError("写入失败: " + resp.decode(errors="ignore"))
        return True


# ============ 游戏数据访问层 ============
class Trainer:
    def __init__(self, mem):
        self.g = mem
        self.sb1 = None
        self.money_addr = None

    def locate(self):
        self.sb1 = self.g.r32(ADDR["saveBlock1Ptr"])
        if not (0x02000000 <= self.sb1 <= 0x02040000):
            raise IOError(f"SaveBlock1 指针异常: 0x{self.sb1:08X}")
        self.money_addr = self.sb1 + ADDR["moneyOff"]

    def get_money(self):
        return self.g.r32(self.money_addr)

    def set_money(self, v):
        self.g.w32(self.money_addr, v)

    def get_coins(self):
        return self.g.r16(self.sb1 + ADDR["coinsOff"])

    def set_coins(self, v):
        self.g.w16(self.sb1 + ADDR["coinsOff"], v)

    def get_party_count(self):
        return self.g.r8(ADDR["partyCount"])

    def get_party(self):
        n = self.get_party_count()
        party = []
        for i in range(n):
            addr = ADDR["party"] + i * 100
            personality = self.g.r32(addr)
            order = ORDER[personality % 24]
            gpos = order.index("G")
            chunk = self.g.read(addr + 0x20 + gpos * 12, 12)
            species = struct.unpack("<H", chunk[0:2])[0]
            held = struct.unpack("<H", chunk[2:4])[0]
            exp = struct.unpack("<I", chunk[4:8])[0]
            level = self.g.r8(addr + 0x54)
            hp = self.g.r16(addr + 0x56)
            maxhp = self.g.r16(addr + 0x58)
            party.append({
                "species": species, "held": held, "exp": exp,
                "level": level, "hp": hp, "maxhp": maxhp,
                "addr": addr,
            })
        return party

    def set_party_species(self, i, species):
        addr = ADDR["party"] + i * 100
        personality = self.g.r32(addr)
        order = ORDER[personality % 24]
        gpos = order.index("G")
        self.g.w16(addr + 0x20 + gpos * 12, species)

    def set_party_level(self, i, level):
        self.g.w8(ADDR["party"] + i * 100 + 0x54, level)

    def set_party_exp(self, i, exp):
        addr = ADDR["party"] + i * 100
        personality = self.g.r32(addr)
        order = ORDER[personality % 24]
        gpos = order.index("G")
        self.g.write(addr + 0x20 + gpos * 12 + 4, struct.pack("<I", exp))

    def set_party_hp(self, i, hp, maxhp):
        addr = ADDR["party"] + i * 100
        self.g.w16(addr + 0x56, hp)
        self.g.w16(addr + 0x58, maxhp)

    def get_bag(self):
        items = []
        for i in range(ADDR["bagSlots"]):
            addr = ADDR["bag"] + i * 4
            iid = self.g.r16(addr)
            qty = self.g.r16(addr + 2)
            if iid != 0 or qty != 0:
                items.append({"slot": i, "id": iid, "qty": qty})
        return items

    def set_bag_item(self, slot, iid, qty):
        addr = ADDR["bag"] + slot * 4
        self.g.w16(addr, iid & 0xFFFF)
        self.g.w16(addr + 2, qty & 0xFFFF)


# ============ 名称表 ============
def load_names():
    path = resource_path("names.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ============ GUI ============
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("宝可梦水银FC 修改器 (mGBA)")
        # 内存客户端：优先 TCP 桥接（mercury_bridge.lua），失败回退 GDB
        self.mem = None
        self.mem_mode = "TCP 桥接"   # 显示用
        self.gdb = None              # 兼容旧引用
        self.trainer = Trainer(self)  # 占位，连接后重建
        self.names = load_names()
        self.breeds = self.names.get("breeds", {})
        self.items = self.names.get("items", {})

        # 物种下拉列表（ID + 名称）
        self.species_options = []
        self.species_by_label = {}
        for i in range(1, 412):
            name = self.breeds.get(str(i)) or self.breeds.get(i) or f"未知#{i}"
            label = f"{i:03d} - {name}"
            self.species_options.append(label)
            self.species_by_label[label] = i
        # 额外留出改版扩展精灵（>411 手动填编号）

        self._build_ui()
        self._auto_refresh()

    def _build_ui(self):
        # 顶部连接栏
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")
        self.status = tk.StringVar(value="未连接")
        ttk.Label(top, text="连接状态:").pack(side="left")
        ttk.Label(top, textvariable=self.status, foreground="red").pack(side="left", padx=4)
        ttk.Button(top, text="连接", command=self._connect).pack(side="left", padx=4)
        ttk.Button(top, text="刷新", command=self._refresh).pack(side="left", padx=4)
        ttk.Button(top, text="写入全部", command=self._write_all).pack(side="left", padx=4)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=6, pady=6)

        # ---- 数值页 ----
        self.tab_val = ttk.Frame(nb, padding=10)
        nb.add(self.tab_val, text="数值")
        self._build_val_tab()

        # ---- 队伍页 ----
        self.tab_party = ttk.Frame(nb, padding=10)
        nb.add(self.tab_party, text="队伍")
        self._build_party_tab()

        # ---- 背包页 ----
        self.tab_bag = ttk.Frame(nb, padding=10)
        nb.add(self.tab_bag, text="背包")
        self._build_bag_tab()

    def _build_val_tab(self):
        f = self.tab_val
        row = 0
        ttk.Label(f, text="金钱 (元):").grid(row=row, column=0, sticky="w", pady=4)
        self.var_money = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_money, width=16).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(f, text="代币 (游戏厅):").grid(row=row, column=0, sticky="w", pady=4)
        self.var_coins = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_coins, width=16).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(f, text="", foreground="gray").grid(row=row, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="提示：改完后点顶部「写入全部」", foreground="gray").grid(row=row + 1, column=0, columnspan=2, sticky="w")

    def _build_party_tab(self):
        f = self.tab_party
        self.party_rows = []
        for i in range(6):
            lf = ttk.LabelFrame(f, text=f"队伍 #{i + 1}")
            lf.grid(row=i // 2, column=i % 2, sticky="nsew", padx=6, pady=6)
            lf.columnconfigure(1, weight=1)
            r = 0
            ttk.Label(lf, text="物种:").grid(row=r, column=0, sticky="w")
            cb = ttk.Combobox(lf, values=self.species_options, width=20)
            cb.grid(row=r, column=1, sticky="w")
            r += 1
            ttk.Label(lf, text="等级:").grid(row=r, column=0, sticky="w")
            e_lv = ttk.Entry(lf, width=8)
            e_lv.grid(row=r, column=1, sticky="w")
            r += 1
            ttk.Label(lf, text="经验:").grid(row=r, column=0, sticky="w")
            e_exp = ttk.Entry(lf, width=10)
            e_exp.grid(row=r, column=1, sticky="w")
            r += 1
            ttk.Label(lf, text="HP(当前/最大):").grid(row=r, column=0, sticky="w")
            hp_frame = ttk.Frame(lf)
            hp_frame.grid(row=r, column=1, sticky="w")
            e_hp = ttk.Entry(hp_frame, width=6)
            e_hp.pack(side="left")
            ttk.Label(hp_frame, text="/").pack(side="left")
            e_maxhp = ttk.Entry(hp_frame, width=6)
            e_maxhp.pack(side="left")
            self.party_rows.append({
                "species": cb, "level": e_lv, "exp": e_exp,
                "hp": e_hp, "maxhp": e_maxhp,
            })
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

    def _build_bag_tab(self):
        f = self.tab_bag
        # 列表
        cols = ("slot", "id", "name", "qty")
        self.bag_tree = ttk.Treeview(f, columns=cols, show="headings", height=18)
        self.bag_tree.heading("slot", text="槽位")
        self.bag_tree.heading("id", text="编号")
        self.bag_tree.heading("name", text="道具")
        self.bag_tree.heading("qty", text="数量")
        self.bag_tree.column("slot", width=60, anchor="center")
        self.bag_tree.column("id", width=60, anchor="center")
        self.bag_tree.column("name", width=180, anchor="w")
        self.bag_tree.column("qty", width=60, anchor="center")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.bag_tree.yview)
        self.bag_tree.configure(yscrollcommand=sb.set)
        self.bag_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        # 编辑区
        ed = ttk.Frame(f, padding=4)
        ed.pack(side="left", fill="y", padx=8)
        ttk.Label(ed, text="道具编号:").pack(anchor="w")
        self.var_item_id = tk.StringVar()
        ttk.Entry(ed, textvariable=self.var_item_id, width=10).pack(anchor="w", pady=2)
        ttk.Label(ed, text="数量:").pack(anchor="w")
        self.var_item_qty = tk.StringVar()
        ttk.Entry(ed, textvariable=self.var_item_qty, width=10).pack(anchor="w", pady=2)
        ttk.Button(ed, text="写入选中", command=self._write_bag_selected).pack(fill="x", pady=4)
        ttk.Button(ed, text="清空选中(删除)", command=self._clear_bag_selected).pack(fill="x", pady=2)
        ttk.Label(ed, text="提示：在左侧点选一行后\n在右边改编号/数量，\n点「写入选中」。\n\n原版道具编号见\nnames.json，扩展道具\n手动填数字。", justify="left", foreground="gray").pack(anchor="w", pady=8)
        self.bag_tree.bind("<<TreeviewSelect>>", self._on_bag_select)

    def _on_bag_select(self, event):
        sel = self.bag_tree.selection()
        if not sel:
            return
        vals = self.bag_tree.item(sel[0], "values")
        if vals:
            self.var_item_id.set(vals[1])
            self.var_item_qty.set(vals[3])

    def _write_bag_selected(self):
        if self.mem is None:
            return
        sel = self.bag_tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在左侧列表选中一行")
            return
        try:
            slot = int(self.bag_tree.item(sel[0], "values")[0])
            iid = int(self.var_item_id.get())
            qty = int(self.var_item_qty.get())
            self.trainer.set_bag_item(slot, iid, qty)
            self.status.set(f"已写入背包槽 {slot}")
            self._refresh()
        except Exception as e:
            messagebox.showerror("写入失败", str(e))

    def _clear_bag_selected(self):
        if self.mem is None:
            return
        sel = self.bag_tree.selection()
        if not sel:
            return
        try:
            slot = int(self.bag_tree.item(sel[0], "values")[0])
            self.trainer.set_bag_item(slot, 0, 0)
            self.status.set(f"已清空背包槽 {slot}")
            self._refresh()
        except Exception as e:
            messagebox.showerror("清空失败", str(e))

    # ---- 动作 ----
    def _connect(self):
        try:
            if self.mem is None:
                self._do_connect()
            self.trainer.locate()
            self.status.set(f"已连接 ({self.mem_mode}) 0x{self.trainer.money_addr:08X}")
        except Exception as e:
            self.status.set("连接失败")
            self.mem = None
            self.trainer = Trainer(None)
            messagebox.showerror(
                "连接失败",
                "无法连接 mGBA 的内存服务。\n\n"
                "推荐方式（无需 GDB/无需 cmd）：\n"
                "  1. 正常双击启动 mGBA 并进入游戏\n"
                "  2. mGBA 菜单 Tools -> Scripting ->\n"
                "     File -> Load Script 加载 mercury_bridge.lua\n"
                "  3. 再点本窗口的「连接」\n\n"
                "旧方式（GDB 桩）：mGBA.exe -g \"游戏.gba\"\n\n"
                f"错误: {e}")

    def _do_connect(self):
        """优先尝试 TCP 桥接，失败则回退 GDB 桩。"""
        # 1. 尝试 TCP 桥接
        try:
            mc = MemClient()
            mc.connect()
            self.mem = mc
            self.mem_mode = "TCP 桥接"
            self.trainer = Trainer(mc)
            self.gdb = mc   # 兼容旧代码对 .s 的访问？不，用 mem
            return
        except Exception:
            pass
        # 2. 回退 GDB
        g = GDBClient()
        g.connect()
        self.mem = g
        self.mem_mode = "GDB 桩"
        self.trainer = Trainer(g)
        self.gdb = g

    def _refresh(self):
        if self.mem is None:
            return
        try:
            self.trainer.locate()
            self.var_money.set(str(self.trainer.get_money()))
            self.var_coins.set(str(self.trainer.get_coins()))
            party = self.trainer.get_party()
            for i, p in enumerate(party):
                if i >= 6:
                    break
                row = self.party_rows[i]
                name = self.breeds.get(str(p["species"])) or self.breeds.get(p["species"]) or f"未知#{p['species']}"
                row["species"].set(f"{p['species']:03d} - {name}")
                row["level"].delete(0, "end"); row["level"].insert(0, str(p["level"]))
                row["exp"].delete(0, "end"); row["exp"].insert(0, str(p["exp"]))
                row["hp"].delete(0, "end"); row["hp"].insert(0, str(p["hp"]))
                row["maxhp"].delete(0, "end"); row["maxhp"].insert(0, str(p["maxhp"]))
            # 背包
            for item in self.bag_tree.get_children():
                self.bag_tree.delete(item)
            for it in self.trainer.get_bag():
                iname = self.items.get(str(it["id"])) or f"扩展道具#{it['id']}"
                self.bag_tree.insert("", "end", values=(it["slot"], it["id"], iname, it["qty"]))
            self.status.set("已刷新")
        except Exception as e:
            self.status.set("读取失败")
            messagebox.showerror("读取失败", str(e))

    def _write_all(self):
        if self.mem is None:
            return
        try:
            self.trainer.locate()
            # 数值
            try:
                self.trainer.set_money(int(self.var_money.get()))
                self.trainer.set_coins(int(self.var_coins.get()))
            except ValueError:
                pass
            # 队伍
            for i, row in enumerate(self.party_rows):
                try:
                    label = row["species"].get()
                    if label in self.species_by_label:
                        self.trainer.set_party_species(i, self.species_by_label[label])
                    elif label.strip():
                        # 手动输入纯编号（改版扩展精灵）
                        self.trainer.set_party_species(i, int(label.strip()))
                except Exception:
                    pass
                try:
                    self.trainer.set_party_level(i, int(row["level"].get()))
                except ValueError:
                    pass
                try:
                    self.trainer.set_party_exp(i, int(row["exp"].get()))
                except ValueError:
                    pass
                try:
                    hp = int(row["hp"].get())
                    mhp = int(row["maxhp"].get())
                    self.trainer.set_party_hp(i, hp, mhp)
                except ValueError:
                    pass
            self.status.set("已写入")
        except Exception as e:
            self.status.set("写入失败")
            messagebox.showerror("写入失败", str(e))

    def _auto_refresh(self):
        if self.mem is not None:
            try:
                self.var_money.set(str(self.trainer.get_money()))
            except Exception:
                pass
        self.root.after(1500, self._auto_refresh)


def main():
    root = tk.Tk()
    root.geometry("760x560")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
