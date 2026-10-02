"""Read mGBA 0.10.x GBA states offline without changing the game or save."""

from pathlib import Path
import struct
import zlib

STATE_SIZE = 0x61000


def read_state(path):
    data = Path(path).read_bytes()
    if len(data) == STATE_SIZE and not data.startswith(b"\x89PNG"):
        return data
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("不是支持的 mGBA GBA 即时存档")
    offset = 8
    payload = None
    ended = False
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        if offset + length + 12 > len(data):
            raise ValueError("PNG 区块被截断")
        tag = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        expected = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(tag + chunk) & 0xFFFFFFFF != expected:
            raise ValueError("PNG CRC 校验失败")
        if tag == b"gbAs":
            if payload is not None:
                raise ValueError("重复的状态区块")
            decoder = zlib.decompressobj()
            payload = decoder.decompress(chunk, STATE_SIZE + 1)
            if len(payload) != STATE_SIZE or not decoder.eof or decoder.unused_data:
                raise ValueError("GBA 状态长度或压缩流不符合预期")
        offset += length + 12
        if tag == b"IEND":
            ended = True
            break
    if payload is None or not ended:
        raise ValueError("缺少完整的 GBA 状态区块")
    return payload


class StateMemory:
    capabilities = set()

    def __init__(self, state, rom):
        if len(state) != STATE_SIZE:
            raise ValueError("状态大小错误")
        self.regions = [
            (0x02000000, state[0x21000:]),
            (0x03000000, state[0x19000:0x21000]),
            (0x08000000, rom),
        ]

    def read(self, a, n):
        for start, data in self.regions:
            if start <= a and a + n <= start + len(data):
                return data[a - start : a - start + n]
        raise ValueError(f"读取范围不在快照中：{a:08x}+{n}")

    def r8(self, a):
        return self.read(a, 1)[0]

    def r32(self, a):
        return struct.unpack("<I", self.read(a, 4))[0]
