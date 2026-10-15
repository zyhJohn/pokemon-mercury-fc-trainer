"""Build audited, reversible in-memory wild encounter overlays.

The caller must verify ROM identity, feature guards, old bytes, and the complete
transaction before using these patches. This module never writes any memory.
"""
import json
import struct
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _layouts():
    data = json.loads(Path(__file__).with_name('wild_encounter_layout.json').read_text(encoding='utf-8'))
    if data.get('schema') != 1:
        raise ValueError('野生遭遇布局格式不支持')
    return {entry['rom_sha256']: entry for entry in data['releases'].values()}


def wild_layout(profile):
    """Resolve the verified layout identity independently of actual ROM identity."""
    sha = profile.get('layout_sha256', profile.get('rom_sha256'))
    try:
        layout = _layouts()[sha]
    except KeyError as exc:
        raise ValueError('此 ROM 尚未核验野生遭遇覆盖布局') from exc
    # JSON round-trip keeps callers from changing the process-wide cached layout.
    return json.loads(json.dumps(layout))


def _overlay_bytes(layout, species, level):
    stub = layout['stub_address']
    data = bytearray()
    labels, fixes = {}, []
    def half(value): data.extend(struct.pack('<H', value))
    def label(name): labels[name] = len(data)
    def branch(op, target):
        fixes.append(('branch', len(data), op, target)); half(op)
    def literal(reg, target):
        fixes.append(('literal', len(data), reg, target)); half(0)
    half(0xBC08)  # restore r3 saved by entry hook
    half(0xB5F0)  # replay original push r4-r7,lr before borrowing registers
    half(0x4674)  # mov r4,lr
    literal(5, 'list_address')
    half(0x2606)
    label('loop')
    half(0x682F)
    half(0x42BC)
    branch(0xD000, 'override')
    half(0x3504)
    half(0x3E01)
    branch(0xD100, 'loop')
    branch(0xE000, 'replay')
    label('override')
    literal(0, 'species')
    half(0x2100 | level)
    label('replay')
    for opcode in (0xB08B, 0x9207, 0x2201, 0x0005, 0x9106):
        half(opcode)  # original generator prologue after its push
    literal(4, 'return')
    half(0x4720)
    while len(data) % 4: half(0x46C0)
    label('list_address'); data.extend(struct.pack('<I', stub + len(data) + 12))
    label('species'); data.extend(struct.pack('<I', species))
    label('return'); data.extend(struct.pack('<I', (layout['generator'] + 12) | 1))
    label('list')
    data.extend(struct.pack('<6I', *[address + 5 for address in layout['callers']]))
    for kind, offset, op, target in fixes:
        if kind == 'branch':
            delta = (labels[target] - offset - 4) // 2
            value = op | (delta & (0x7FF if op == 0xE000 else 0xFF))
        else:
            delta = (stub + labels[target] - ((stub + offset + 4) & ~3)) // 4
            value = 0x4800 | op << 8 | delta
        struct.pack_into('<H', data, offset, value)
    if len(data) > layout['stub_size']:
        raise ValueError('野生遭遇覆盖长度超出已核验区域')
    data.extend(b'\xc0\x46' * ((layout['stub_size'] - len(data)) // 2))
    hook = bytes.fromhex('08b4014b1847c046') + struct.pack('<I', stub | 1)
    return [(layout['generator'], hook), (stub, bytes(data))]


def build_wild_patches(profile, species, level):
    """Return exactly two (address, original, replacement) tuples.

    Covers later ordinary land/water/rock and fishing generation calls. The game
    constructs complete enemy records. Scripted battles and trainer calls retain
    original arguments. Native form changes and pre-generation filters apply.
    """
    if isinstance(species, bool) or not isinstance(species, int) or str(species) not in profile['species']:
        raise ValueError('请选择此 ROM 已核验的有效物种')
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 100:
        raise ValueError('野生遭遇等级须为 1～100')
    layout = wild_layout(profile)
    if not any(lo <= species <= hi for lo, hi in layout['valid_species_ranges']):
        raise ValueError('请选择此 ROM 已核验的有效物种')
    originals = {layout['generator']: bytes.fromhex(layout['original_generator']),
                 layout['stub_address']: bytes.fromhex(layout['original_stub'])}
    return tuple((address, originals[address], replacement)
                 for address, replacement in _overlay_bytes(layout, species, level))
