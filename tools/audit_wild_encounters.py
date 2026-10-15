"""Read-only isolated CPU audit for a temporary wild-encounter ROM overlay.

Never connects to mGBA or writes ROM/save/state files. Optional unicorn and
capstone dependencies are research-only. Reports go in ignored diagnostics/.
"""
import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokemon_data import Pokemon, calculate_stats
from rom_versions import RELEASES
from tools.read_state import read_state
from tools.verify_rom_routines import RomCPU
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_PC, UC_ARM_REG_SP

BASE = 0x08000000
STUB = 0x08A8C000
STUB_SIZE = 100
ENEMY = 0x0202402C
PLAYER = 0x02024284
SYMBOLS = {
    '1.0': dict(generator=0x09D64798, ordinary=0x09D64D10, ordinary_end=0x09D65004,
                fishing=0x09D65DF8, fishing_end=0x09D65EA0,
                callers=[0x09D64E28,0x09D64E90,0x09D64EFA,0x09D64F56,0x09D65E2C,0x09D65E5E]),
    '1.2': dict(generator=0x09D68B6C, ordinary=0x09D69104, ordinary_end=0x09D693F8,
                fishing=0x09D6A1EC, fishing_end=0x09D6A294,
                callers=[0x09D6921C,0x09D69284,0x09D692EE,0x09D6934A,0x09D6A220,0x09D6A252]),
    '1.33': dict(generator=0x09D6BA78, ordinary=0x09D6C010, ordinary_end=0x09D6C304,
                fishing=0x09D6D0F8, fishing_end=0x09D6D1A0,
                callers=[0x09D6C128,0x09D6C190,0x09D6C1FA,0x09D6C256,0x09D6D12C,0x09D6D15E]),
}


def overlay_bytes(layout, species, level):
    from wild_encounter_data import _overlay_bytes
    return _overlay_bytes(layout, species, level)


def make_layout(rom):
    sha = hashlib.sha256(rom).hexdigest()
    if sha not in RELEASES: raise ValueError('exact audited ROM required')
    version = RELEASES[sha][0]
    layout = dict(SYMBOLS[version], version=version, rom_sha256=sha,
                  stub_address=STUB, stub_size=STUB_SIZE)
    layout['original_generator'] = rom[layout['generator']-BASE:layout['generator']-BASE+12].hex()
    layout['original_stub'] = rom[STUB-BASE:STUB-BASE+STUB_SIZE].hex()
    assert layout['original_generator'] == 'f0b58bb00792012205000691'
    assert struct.unpack_from('<I', rom, 0x82A00)[0] == layout['generator'] | 1
    return layout


def verify_stub_padding(rom):
    """Reject occupied padding and any existing direct/pointer entry to it."""
    offset = STUB - BASE
    assert rom[offset:offset + STUB_SIZE] == b'\xff' * STUB_SIZE
    # A B/Bcc source capable of reaching the selected span is also erased.
    assert rom[offset - 2048:offset + STUB_SIZE + 2048] == b'\xff' * (4096 + STUB_SIZE)
    # Scan at every byte alignment, including odd Thumb addresses.
    needle = bytes((STUB_SIZE - 1,))
    pattern = b'[\x00-' + re.escape(needle) + b']' + re.escape(struct.pack('<I', STUB)[1:])
    assert not list(re.finditer(pattern, rom)), 'existing raw pointer enters stub padding'
    for source in range(max(0, offset - 0x400000), min(len(rom) - 3, offset + STUB_SIZE + 0x400000), 2):
        hi, lo = struct.unpack_from('<HH', rom, source)
        if hi & 0xF800 != 0xF000 or lo & 0xF800 != 0xF800:
            continue
        delta = ((hi & 0x7FF) << 11) | (lo & 0x7FF)
        if delta & 0x200000:
            delta -= 0x400000
        target = BASE + source + 4 + delta * 2
        assert not STUB <= target < STUB + STUB_SIZE, 'existing Thumb BL enters stub padding'
    return {'passed': True, 'address': hex(STUB), 'size': STUB_SIZE,
            'original_byte': 'FF', 'all_byte_alignment_raw_pointers': 0,
            'incoming_thumb_bl': 0, 'surrounding_ff_bytes_each_side': 2048}


def call_with_caller(engine, address, caller, species, level, first=1):
    from unicorn.arm_const import UC_ARM_REG_R2, UC_ARM_REG_R3
    for reg, value in [(UC_ARM_REG_R0,species),(UC_ARM_REG_R1,level),(UC_ARM_REG_R2,0),(UC_ARM_REG_R3,first)]:
        engine.cpu.reg_write(reg,value)
    engine.cpu.reg_write(UC_ARM_REG_SP,0x03007E00)
    engine.cpu.reg_write(UC_ARM_REG_LR,caller+5)
    # Stop exactly at the native caller's next instruction, before unrelated code.
    engine.cpu.emu_start(address|1,caller+4,count=1000000)
    if engine.cpu.reg_read(UC_ARM_REG_PC) != caller+4: raise AssertionError('native routine did not return')


def verify_all_species(rom, state, profile):
    layout = make_layout(rom)
    engine = RomCPU(rom, state)
    ram = engine.read(0x02000000, 0x40000)
    iram = engine.read(0x03000000, 0x8000)
    transformations = []
    for ident in map(int, profile['species']):
        engine.write(0x02000000, ram)
        engine.write(0x03000000, iram)
        for address, data in overlay_bytes(layout, ident, 37):
            engine.write(address, data)
        call_with_caller(engine, layout['generator'], layout['callers'][0], 4, 7)
        mon = Pokemon(engine.read(ENEMY, 100))
        if mon.species != ident:
            transformations.append([ident, mon.species])
        assert str(mon.species) in profile['species'] and mon.level == 37
        assert engine.read(PLAYER, 600) == ram[PLAYER - 0x02000000:PLAYER - 0x02000000 + 600]
        assert mon.stats == calculate_stats(profile['species'][str(mon.species)]['base'], mon.ivs, mon.evs, 37, mon.pid % 25, mon.species == 303)
    return {'passed': True, 'native_species_cases': len(profile['species']),
            'native_species_transformations': transformations,
            'scope': 'One fixed initial memory seed and level 37 per valid species; native form changes remain enabled.'}


def verify(rom,state,profile):
    layout=make_layout(rom); gen=layout['generator']
    from wild_encounter_data import wild_layout, build_wild_patches
    declared = wild_layout(profile)
    for key, value in layout.items():
        assert declared[key] == value, key
    for guard in declared['guards']:
        address = guard['address'] - BASE
        expected = bytes.fromhex(guard['expected_hex'])
        assert rom[address:address + len(expected)] == expected
    built = build_wild_patches(profile, 25, 37)
    assert [(address, after) for address, before, after in built] == overlay_bytes(layout, 25, 37)
    assert built[0][2] == bytes.fromhex(declared['hook_template'])
    template = bytearray.fromhex(declared['stub_template'])
    struct.pack_into('<I', template, declared['stub_species_offset'], 25)
    template[declared['stub_level_offset']] = 37
    assert built[1][2] == template
    original_player=RomCPU(rom,state).read(PLAYER,600)
    cases=[]
    # Native function results before and after this overlay must be byte-identical
    # given the same final arguments and initial state, including stats/moves/PP.
    for ident,level in [(1,1),(25,37),(201,50),(303,100),(500,65)]:
      for caller in layout['callers']:
       for first in (0, 1):
        e=RomCPU(rom,state); base=RomCPU(rom,state)
        for address,data in overlay_bytes(layout,ident,level): e.write(address,data)
        try: call_with_caller(base,gen,caller,ident,level,first)
        except Exception as exc: raise AssertionError((ident,level,hex(caller))) from exc
        call_with_caller(e,gen,caller,4,7,first)
        raw=e.read(ENEMY,600)
        assert raw==base.read(ENEMY,600), (ident,level,hex(caller),'full native record differs')
        mon=Pokemon(raw[100 * (not first):100 * (not first) + 100]); assert (mon.species,mon.level)==(ident,level)
        assert mon.stats==calculate_stats(profile['species'][str(ident)]['base'],mon.ivs,mon.evs,level,mon.pid%25,ident==303)
        assert e.read(PLAYER,600)==original_player
        cases.append({'species':ident,'level':level,'caller':hex(caller),'full_enemy_party_equal':True})
    # Every other statically direct native caller is excluded.
    from tools.verify_player_rival import thumb_bl_callers
    all_callers=thumb_bl_callers(rom,gen)
    excluded=[x for x in all_callers if x not in layout['callers']]
    for caller in excluded:
        base=RomCPU(rom,state); e=RomCPU(rom,state)
        for address,data in overlay_bytes(layout,25,37): e.write(address,data)
        call_with_caller(base,gen,caller,4,7)
        call_with_caller(e,gen,caller,4,7)
        assert e.read(ENEMY,600)==base.read(ENEMY,600),hex(caller)
        assert e.read(PLAYER,600)==original_player
    # Execute the full normal land/water/rock generation chain, not just stub.
    full=[]
    for area in [0,1,2]:
      e=RomCPU(rom,state)
      e.write(0x02000800,struct.pack('<II',20,0x02000900))
      e.write(0x02000900,struct.pack('<BBH',30,30,4)*12)
      for address,data in overlay_bytes(layout,25,37): e.write(address,data)
      assert e.call(layout['ordinary'],0x02000800,area,0)==1
      mon=Pokemon(e.read(ENEMY,100)); assert (mon.species,mon.level)==(25,37)
      assert e.read(PLAYER,600)==original_player
      full.append({'area':area,'species':mon.species,'level':mon.level})
    # Execute the patched code before restoring both spans in that SAME CPU.
    # Unicorn caches translated blocks; raw mem_write alone does not invalidate
    # them. This is isolated-CPU maintenance, not evidence about mGBA's rawWrite.
    e=RomCPU(rom,state)
    for address,data in overlay_bytes(layout,25,37): e.write(address,data)
    call_with_caller(e,gen,layout['callers'][0],4,7)
    assert Pokemon(e.read(ENEMY,100)).species == 25
    e.write(gen,bytes.fromhex(layout['original_generator']));e.write(STUB,bytes.fromhex(layout['original_stub']))
    e.cpu.ctl_remove_cache(gen,gen+12)
    e.cpu.ctl_remove_cache(STUB,STUB+STUB_SIZE)
    e.write(0x02000000,state[0x21000:])
    e.write(0x03000000,state[0x19000:0x21000])
    call_with_caller(e,gen,layout['callers'][0],4,7)
    mon=Pokemon(e.read(ENEMY,100));assert (mon.species,mon.level)==(4,7)
    native=RomCPU(rom,state)
    call_with_caller(native,gen,layout['callers'][0],4,7)
    assert e.read(ENEMY,600) == native.read(ENEMY,600)
    return dict(passed=True,layout=layout,stub_padding=verify_stub_padding(rom),native_full_record_cases=len(cases),excluded_native_callers=[hex(x) for x in excluded],
                ordinary_chain=full,restore_passed=True,player_party_unchanged=True,
                scope='Isolated actual ROM CPU only; does not establish live mGBA overlay support, save/reload, or captured origin legality.')


def main():
    p=argparse.ArgumentParser();p.add_argument('rom',type=Path);p.add_argument('state',type=Path);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--all-species', action='store_true')
    args=p.parse_args();rom=args.rom.read_bytes();layout=make_layout(rom)
    profile=json.loads((Path(__file__).resolve().parents[1]/RELEASES[layout['rom_sha256']][2]).read_text(encoding='utf-8'))
    result=verify(rom,read_state(args.state),profile)
    result['state_sha256'] = hashlib.sha256(args.state.read_bytes()).hexdigest()
    if args.all_species:
        result['all_species'] = verify_all_species(rom,read_state(args.state),profile)
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
