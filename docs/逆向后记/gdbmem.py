#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mGBA GDB 桩（端口 2345）RSP 客户端，用于读写 GBA 内存。

用法：
  mGBA.exe -g game.gba          # 启动 GDB 桩
  python gdbmem.py              # 示例：读金钱等

注意：mGBA GDB 桩为单连接，断开后需重启 mGBA 再连。
"""
import socket
import struct


class GDBClient:
    def __init__(self, host="127.0.0.1", port=2345):
        self.s = socket.socket()
        self.s.settimeout(8)
        self.s.connect((host, port))

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
    def w8(self, a, v): self.write(a, struct.pack("<B", v & 0xFF))
    def w16(self, a, v): self.write(a, struct.pack("<H", v & 0xFFFF))
    def w32(self, a, v): self.write(a, struct.pack("<I", v & 0xFFFFFFFF))

    def dump(self, start, end, chunk=0x80):
        """分块 dump 内存区间，返回 bytes。"""
        out = bytearray()
        for a in range(start, end, chunk):
            out += self.read(a, chunk)
        return bytes(out)


if __name__ == "__main__":
    g = GDBClient()
    # 示例：读 gSaveBlock1Ptr，进而读金钱（火红改版实测偏移 +0x290）
    sb1 = g.r32(0x03005008)
    money = g.r32(sb1 + 0x290)
    print(f"SaveBlock1=0x{sb1:08X} 金钱={money}")
