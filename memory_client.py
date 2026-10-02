"""Bounded line protocol and compare-before-write transactions for mGBA."""

import socket
import struct


class MemClient:
    def __init__(self, host="127.0.0.1", port=8888):
        self.host, self.port = host, port
        self.s = None
        self.buffer = b""
        self.capabilities = set()
        self.expected_rom_crc32 = None

    def connect(self, scan=8):
        self.close()
        last = None
        fallback = None
        for port in range(self.port, self.port + scan):
            try:
                self.s = socket.create_connection((self.host, port), timeout=0.4)
                self.s.settimeout(3)
                self.buffer = b""
                if self.command("PING") != b"PONG":
                    raise IOError("服务握手不匹配")
                response = self.command("CAPS", allow_error=True)
                self.capabilities = (
                    set(response.decode("ascii").split())
                    if not response.startswith(b"ERR")
                    else set()
                )
                if "CRCBATCH" in self.capabilities:
                    if fallback:
                        fallback[0].close()
                    self.port = port
                    return
                if fallback is None:
                    fallback = (self.s, self.buffer, port, self.capabilities)
                    self.s = None
                else:
                    self.close()
            except (OSError, ValueError) as exc:
                last = exc
                self.close()
        if fallback:
            self.s, self.buffer, self.port, self.capabilities = fallback
            return
        raise IOError(
            f"没有找到内存桥接服务。请保持模拟器运行（不要暂停），并加载本项目的 mercury_bridge.lua。{last}"
        )

    def close(self):
        if self.s is not None:
            self.s.close()
        self.s = None
        self.buffer = b""
        self.capabilities = set()
        self.expected_rom_crc32 = None

    def command(self, command, allow_error=False):
        if self.s is None:
            raise IOError("未连接")
        try:
            self.s.sendall(command.encode("ascii") + b"\n")
            while b"\n" not in self.buffer:
                data = self.s.recv(4096)
                if not data:
                    raise IOError("mGBA 已断开连接")
                self.buffer += data
                if len(self.buffer) > 32768:
                    raise IOError("桥接响应超过长度限制")
            line, self.buffer = self.buffer.split(b"\n", 1)
        except OSError:
            self.close()
            raise
        if line.startswith(b"ERR") and not allow_error:
            if line == b"ERR stale":
                raise IOError("游戏数据已变化，本次未写入；请刷新后重试")
            if line == b"ERR rom":
                raise IOError("游戏 ROM 已变化，本次未写入；请重新连接")
            raise IOError(line.decode("ascii", errors="replace"))
        return line

    def read(self, address, size):
        if not isinstance(size, int) or not 0 <= size <= 4096:
            raise ValueError("读取大小须为 0～4096")
        if size == 0:
            return b""
        try:
            result = bytes.fromhex(
                self.command(f"READ {address:x} {size:x}").decode("ascii")
            )
        except ValueError as exc:
            raise IOError("内存响应不是有效十六进制") from exc
        if len(result) != size:
            raise IOError("内存响应长度不完整")
        return result

    def batch(self, patches):
        if "BATCH" not in self.capabilities:
            raise IOError(
                "当前为旧桥接脚本，仅支持读取；请重新加载本项目的 mercury_bridge.lua"
            )
        if not patches:
            return
        parts = []
        for address, before, after in patches:
            if len(before) != len(after) or not before:
                raise ValueError("事务长度不一致")
            parts.append(f"{address:x}:{before.hex()}:{after.hex()}")
        if "CRCBATCH" in self.capabilities:
            if not self.expected_rom_crc32:
                raise IOError("尚未校验 ROM，不能写入")
            command = "BATCHCRC " + self.expected_rom_crc32 + " "
        else:
            command = "BATCH "
        response = self.command(command + ";".join(parts))
        if response != b"OK":
            raise IOError("事务没有返回成功确认")

    def r8(self, a):
        return self.read(a, 1)[0]

    def r16(self, a):
        return struct.unpack("<H", self.read(a, 2))[0]

    def r32(self, a):
        return struct.unpack("<I", self.read(a, 4))[0]
