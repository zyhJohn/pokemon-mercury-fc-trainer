"""Mercury's independently verified ordinary-field guard for party removal."""

import struct

SUPPORTED_LAYOUTS = frozenset(
    {
        "628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821",
        "b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896",
        "9801866280284a8ac12650666b8526ff3d07aa455f8dc623c2c56c0537313ec4",
    }
)
CALLBACK_ADDRESS = 0x030030F0
CALLBACKS = struct.pack("<II", 0x08056535, 0x080565B5)
SCRIPT_LOCK_ADDRESS = 0x03000F9C
CODE_SIGNATURES = (
    # ScriptContext2 enable/disable/getter and their exact RAM literals.
    (
        0x08069940,
        bytes.fromhex(
            "01490120087070479c0f000301490020087070479c0f000301480078704700009c0f0003"
        ),
    ),
    # Native overworld input branch calls the getter and refuses locked input.
    (0x0805647C, bytes.fromhex("13f06cfa0006002819d1")),
)


def party_deletion_layout(profile):
    layout_sha = profile.get("layout_sha256", profile.get("rom_sha256"))
    if layout_sha not in SUPPORTED_LAYOUTS:
        raise ValueError("当前布局尚未核验队伍删除的地图与菜单状态，只开放只读诊断")
    return {
        "callback_address": CALLBACK_ADDRESS,
        "callbacks": CALLBACKS,
        "script_lock_address": SCRIPT_LOCK_ADDRESS,
        "code_signatures": CODE_SIGNATURES,
    }


def party_deletion_guards(memory, profile):
    """Return disjoint compare-only patches for the SAME emulator callback."""
    layout = party_deletion_layout(profile)
    guards = []
    for address, expected in layout["code_signatures"]:
        if memory.read(address, len(expected)) != expected:
            raise ValueError("队伍删除的原生菜单/脚本守卫代码不匹配")
        guards.append((address, expected, expected))
    callbacks = memory.read(CALLBACK_ADDRESS, 8)
    locked = memory.read(SCRIPT_LOCK_ADDRESS, 1)
    if callbacks != CALLBACKS or locked != b"\0":
        raise ValueError(
            "请返回普通地图，退出菜单、对话、脚本或联机后再删除/恢复队伍异常记录"
        )
    guards += [
        (CALLBACK_ADDRESS, callbacks, callbacks),
        (SCRIPT_LOCK_ADDRESS, locked, locked),
    ]
    return guards
