"""Audited layout baselines; compatibility is checked independently of labels."""

import hashlib
import json
from pathlib import Path


RELEASES = {
    "628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821": (
        "1.0",
        "b4af11c8",
        "rom_profile.json",
    ),
    "9801866280284a8ac12650666b8526ff3d07aa455f8dc623c2c56c0537313ec4": (
        "1.33",
        "518482f7",
        "rom_profile_v133.json",
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


# V1.33 symbols audited against its exact SHA-256 and isolated ROM routines.
# Keys include original V1.0 symbols and explicit V1.2 research aliases.
V133_ADDRESSES = {
    0x088F5644: 0x088F5644,
    0x08DF7BF0: 0x08DF7D0C,
    0x08E3BA50: 0x08E3BC7C,
    0x097BBA40: 0x097BBA44,
    0x097BBA44: 0x097BBA44,
    0x097D6E60: 0x097D6E80,
    0x097D6E80: 0x097D6E80,
    0x097E5368: 0x097E5388,
    0x097E5388: 0x097E5388,
    0x09CCD0AC: 0x09CCD170,
    0x09CCD170: 0x09CCD170,
    0x09CCD198: 0x09CCD198,
    0x09D070C4: 0x09D09E3C,
    0x09D073D6: 0x09D0A14E,
    0x09D07430: 0x09D0A1A8,
    0x09D07C3C: 0x09D0A9B4,
    0x09D09548: 0x09D09E3C,
    0x09D0985A: 0x09D0A14E,
    0x09D098B4: 0x09D0A1A8,
    0x09D0A0C0: 0x09D0A9B4,
    0x09D260F4: 0x09D29F74,
    0x09D26118: 0x09D29F98,
    0x09D2867C: 0x09D29F74,
    0x09D286A0: 0x09D29F98,
    0x09D30C04: 0x09D34DC4,
    0x09D30C5C: 0x09D34E1C,
    0x09D310F4: 0x09D352B4,
    0x09D31148: 0x09D35308,
    0x09D3114C: 0x09D3530C,
    0x09D31154: 0x09D35314,
    0x09D331F0: 0x09D34DC4,
    0x09D33248: 0x09D34E1C,
    0x09D336E0: 0x09D352B4,
    0x09D33734: 0x09D35308,
    0x09D33738: 0x09D3530C,
    0x09D33740: 0x09D35314,
    0x09D3D570: 0x09D4197C,
    0x09D3D590: 0x09D4199C,
    0x09D3D69E: 0x09D41AAA,
    0x09D3D852: 0x09D41C5E,
    0x09D3D9C4: 0x09D41DD0,
    0x09D3DCA4: 0x09D420B0,
    0x09D3E290: 0x09D4269C,
    0x09D3FDA8: 0x09D4197C,
    0x09D3FDC8: 0x09D4199C,
    0x09D3FED6: 0x09D41AAA,
    0x09D4008A: 0x09D41C5E,
    0x09D401FC: 0x09D41DD0,
    0x09D404DC: 0x09D420B0,
    0x09D40AC8: 0x09D4269C,
    0x09D41AF8: 0x09D46910,
    0x09D44D18: 0x09D46910,
    0x09D4C8E8: 0x09D51770,
    0x09D4FB68: 0x09D51770,
    0x09D54868: 0x09D5A404,
    0x09D54A44: 0x09D5A5E0,
    0x09D54A94: 0x09D5A630,
    0x09D54CA8: 0x09D5A844,
    0x09D569D4: 0x09D5C664,
    0x09D56A28: 0x09D5C840,
    0x09D56AF4: 0x09D5CA44,
    0x09D56C48: 0x09D5CBB4,
    0x09D56C88: 0x09D5CBF4,
    0x09D56C9C: 0x09D5CC08,
    0x09D573B8: 0x09D5D2E8,
    0x09D58628: 0x09D5A404,
    0x09D58804: 0x09D5A5E0,
    0x09D58854: 0x09D5A630,
    0x09D58A68: 0x09D5A844,
    0x09D59350: 0x09D5F280,
    0x09D59410: 0x09D5F488,
    0x09D59548: 0x09D5F400,
    0x09D59704: 0x09D5F670,
    0x09D59F24: 0x09D5FE90,
    0x09D59F2C: 0x09D5FE98,
    0x09D59F30: 0x09D5FE9C,
    0x09D59F3C: 0x09D5FEA8,
    0x09D59F64: 0x09D5FED0,
    0x09D5A8B8: 0x09D5C664,
    0x09D5A90C: 0x09D5C6B8,
    0x09D5AA94: 0x09D5C840,
    0x09D5AC98: 0x09D5CA44,
    0x09D5AE08: 0x09D5CBB4,
    0x09D5AE48: 0x09D5CBF4,
    0x09D5AE5C: 0x09D5CC08,
    0x09D5B530: 0x09D5D2E8,
    0x09D5B8FC: 0x09D619AC,
    0x09D5B91C: 0x09D619CC,
    0x09D5D4C8: 0x09D5F280,
    0x09D5D648: 0x09D5F400,
    0x09D5D6D0: 0x09D5F488,
    0x09D5D8B8: 0x09D5F670,
    0x09D5E0D8: 0x09D5FE90,
    0x09D5E0E0: 0x09D5FE98,
    0x09D5E0E4: 0x09D5FE9C,
    0x09D5E0F0: 0x09D5FEA8,
    0x09D5E118: 0x09D5FED0,
    0x09D5FB84: 0x09D619AC,
    0x09D5FBA4: 0x09D619CC,
    0x09D60CF0: 0x09D66FFC,
    0x09D628A8: 0x09D69B24,
    0x09D64C04: 0x09D6BEE4,
    0x09D6506C: 0x09D66FFC,
    0x09D65268: 0x09D671F8,
    0x09D66C24: 0x09D69B24,
    0x09D68FD8: 0x09D6BEE4,
    0x09DD5E68: 0x09DE36AC,
    0x09DD5E9A: 0x09DE36DE,
    0x09DD5ECC: 0x09DE3710,
    0x09DD6250: 0x09DE3A94,
    0x09DD6258: 0x09DE3A9C,
    0x09DD6260: 0x09DE3AA4,
    0x09DD6268: 0x09DE3AAC,
    0x09DD6270: 0x09DE3AB4,
    0x09DD71AC: 0x09DE4A7C,
    0x09DD7210: 0x09DE4AE0,
    0x09DDD79C: 0x09DE36AC,
    0x09DDD7CE: 0x09DE36DE,
    0x09DDD800: 0x09DE3710,
    0x09DDDB84: 0x09DE3A94,
    0x09DDDB8C: 0x09DE3A9C,
    0x09DDDB94: 0x09DE3AA4,
    0x09DDDB9C: 0x09DE3AAC,
    0x09DDDBA4: 0x09DE3AB4,
    0x09DDEB68: 0x09DE4A7C,
    0x09DDEBCC: 0x09DE4AE0,
    0x09DE050C: 0x09DE6420,
}

ADDRESS_MAPS = {"1.0": {}, "1.2": V12_ADDRESSES, "1.33": V133_ADDRESSES}


def release(rom):
    sha = hashlib.sha256(rom).hexdigest()
    if sha not in RELEASES:
        raise ValueError("未知 ROM SHA-256，未开放编辑；请先核验该版本")
    return sha, RELEASES[sha]


def rom_identity(profile):
    """Actual ROM identity; layout SHA remains available to existing modules."""
    return profile.get("rom_identity") or profile["rom_sha256"]


def runtime_compatibility_check(memory, profile, *, full=True):
    from rom_compatibility import runtime_compatibility_check as check
    return check(memory, profile, full=full)


def load_profile(rom=None, *, crc=None, root=None, memory=None):
    """Local ROM uses SHA-256; a verified Lua bridge uses its complete CRC32."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent
    if rom is not None:
        sha = hashlib.sha256(rom).hexdigest()
        info = RELEASES.get(sha)
        if info is None:
            from rom_compatibility import compatible_profile
            return compatible_profile(rom=rom, root=root)
    else:
        from rom_compatibility import normalize_crc
        crc = normalize_crc(crc)
        matches = [(sha, info) for sha, info in RELEASES.items() if info[1] == crc]
        if len(matches) != 1:
            from rom_compatibility import compatible_profile
            return compatible_profile(crc=crc, memory=memory, root=root)
        sha, info = matches[0]
    profile = json.loads((root / info[2]).read_text(encoding="utf-8"))
    if profile["rom_sha256"] != sha or profile["rom_crc32"] != info[1]:
        raise ValueError("ROM 配置身份不一致")
    return profile
