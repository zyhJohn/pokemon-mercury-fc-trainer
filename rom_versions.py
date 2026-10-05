"""Exact supported releases; never infer compatibility from a game title."""

import hashlib
import json
from pathlib import Path


RELEASES = {
    "628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821": (
        "1.0",
        "b4af11c8",
        "rom_profile.json",
    ),
    "b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896": (
        "1.2",
        "4755f497",
        "rom_profile_v12.json",
    ),
}

# Audited ROM addresses, NOT RAM offsets. Used only by metadata extraction and
# isolated research routines expressed with the original 1.0 symbol addresses.
# This map is tied to the complete 1.2 SHA-256, not a generic relocation rule.
V12_ADDRESSES = {
    0x09DD6250: 0x09DDDB84,
    0x09D07C3C: 0x09D0A0C0,
    0x09D628A8: 0x09D66C24,
    0x09D070C4: 0x09D09548,
    0x09D41AF8: 0x09D44D18,
    0x09D3D9C4: 0x09D401FC,
    0x09D3DCA4: 0x09D404DC,
    0x09D3D852: 0x09D4008A,
    0x09D3D69E: 0x09D3FED6,
    0x09D073D6: 0x09D0985A,
    0x09D07430: 0x09D098B4,
    0x09D30C04: 0x09D331F0,
    0x09D30C5C: 0x09D33248,
    0x09DD5E68: 0x09DDD79C,
    0x09D260F4: 0x09D2867C,
    0x09D26118: 0x09D286A0,
    0x09D310F4: 0x09D336E0,
    0x09D31148: 0x09D33734,
    0x09D3114C: 0x09D33738,
    0x09D31154: 0x09D33740,
    0x09D5B8FC: 0x09D5FB84,
    0x09D5B91C: 0x09D5FBA4,
    0x09DD5E9A: 0x09DDD7CE,
    0x09DD5ECC: 0x09DDD800,
    0x09D54A44: 0x09D58804,
    0x09D54868: 0x09D58628,
    0x09DD71AC: 0x09DDEB68,
    0x09D59F24: 0x09D5E0D8,
    0x09D59F2C: 0x09D5E0E0,
    0x09D59F30: 0x09D5E0E4,
    0x09D59F3C: 0x09D5E0F0,
    0x09D59F64: 0x09D5E118,
    0x09CCD0AC: 0x09CCD170,
    0x09D573B8: 0x09D5B530,
    0x09D56C48: 0x09D5AE08,
    0x09D56C88: 0x09D5AE48,
    0x09D56C9C: 0x09D5AE5C,
    0x09D3E290: 0x09D40AC8,
    0x09D3D570: 0x09D3FDA8,
    0x09D3D590: 0x09D3FDC8,
    0x09D569D4: 0x09D5A8B8,
    0x09D56A28: 0x09D5AA94,
    0x09D56AF4: 0x09D5AC98,
    0x09D59704: 0x09D5D8B8,
    0x09D59350: 0x09D5D4C8,
    0x09D59410: 0x09D5D6D0,
    0x09D59548: 0x09D5D648,
    0x09D64C04: 0x09D68FD8,
    0x097BBA40: 0x097BBA44,
    0x097D6E60: 0x097D6E80,
    0x097E5368: 0x097E5388,
}
V12_ADDRESSES.update({0x09DD6250 + 8 * i: 0x09DDDB84 + 8 * i for i in range(5)})


def release(rom):
    sha = hashlib.sha256(rom).hexdigest()
    if sha not in RELEASES:
        raise ValueError("未知 ROM SHA-256，未开放编辑；请先核验该版本")
    return sha, RELEASES[sha]


def load_profile(rom=None, *, crc=None, root=None):
    """Local ROM uses SHA-256; a verified Lua bridge uses its complete CRC32."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    if rom is not None:
        sha, info = release(rom)
    else:
        matches = [(sha, info) for sha, info in RELEASES.items() if info[1] == crc]
        if len(matches) != 1:
            raise ValueError("当前 ROM 版本尚未验证，未开放编辑（支持 V1.0 / V1.2）")
        sha, info = matches[0]
    profile = json.loads((root / info[2]).read_text(encoding="utf-8"))
    if profile["rom_sha256"] != sha or profile["rom_crc32"] != info[1]:
        raise ValueError("ROM 配置身份不一致")
    return profile
