"""Real Lua and TCP tests over synthetic ROM bytes; no emulator is launched."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from memory_client import MemClient
from rom_compatibility import runtime_compatibility_check
from tests.test_bridge import LuaRuntime
from tests.test_core import Memory
from tests.test_transport import Server
from wild_encounter_control import WildEncounterControl
from wild_encounter_data import build_wild_patches, wild_layout


@unittest.skipIf(LuaRuntime is None, 'lupa is required')
class WildControlTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(Path('rom_profile_v133.json').read_text(encoding='utf-8'))
        self.layout = wild_layout(self.profile)
        self.ram = Memory()
        self.bytes = {}
        self.crc = self.profile['rom_crc32']
        self.pc = 0x08000100
        self.writes = []
        self.fail_at = None
        self.persistent_fail_at = None
        self.persistent_fail_writes = set()
        self.fail_after_at = None
        self.fail_read_at = None
        self.persistent_read_fail_at = None
        self.ignore = False
        def put(address, data):
            self.bytes.update((address+i, value) for i, value in enumerate(data))
        self.put = put
        for guard in self.layout['guards']:
            put(guard['address'], bytes.fromhex(guard['expected_hex']))
        for sig in self.profile['signatures']:
            put(sig['address'], bytes.fromhex(sig['hex']))
        for a, before, _ in build_wild_patches(self.profile, 201, 35):
            put(a, before)
        put(self.layout['base_stats_address']+201*28, b'\x30'*6)
        self.lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
        emu = self.lua.table()
        def read(_, address, size):
            if size == 1 and self.persistent_read_fail_at == address:
                raise RuntimeError('persistent readback failure')
            if size == 1 and self.fail_read_at == address:
                self.fail_read_at = None
                raise RuntimeError('one readback failure')
            return bytes(self.bytes.get(address+i, 0) for i in range(size))
        emu[b'readRange'] = read
        emu[b'checksum'] = lambda _: bytes.fromhex(self.crc)
        emu[b'readRegister'] = lambda _, name: self.pc
        emu[b'write8'] = lambda *_: self.fail('ROM must use cart0 domain')
        cart = self.lua.table()
        def write(_, offset, value):
            address = 0x08000000+offset
            self.writes.append(address)
            if (address, value) in self.persistent_fail_writes:
                raise RuntimeError("second persistent write failure")
            if self.persistent_fail_at == address:
                raise RuntimeError("persistent domain failure")
            if self.fail_at == address:
                self.fail_at = None
                raise RuntimeError('one injected domain failure')
            if not self.ignore:
                self.bytes[address] = value
            if self.fail_after_at == address:
                self.fail_after_at = None
                raise RuntimeError("domain wrote then raised")
        cart[b'write8'] = write
        emu[b'memory'] = self.lua.table()
        emu[b'memory'][b'cart0'] = cart
        self.lua.globals()[b'emu'] = emu
        self.lua.globals()[b'MERCURY_BRIDGE_TEST'] = True
        self.handle = self.lua.execute(Path('mercury_bridge.lua').read_bytes())
        self.tick = self.lua.globals()[b'MERCURY_WILD_TEST'][b'tick']
        self.disconnect = self.lua.globals()[b'MERCURY_WILD_TEST'][b'disconnect']
        self.nonce = 'a'*32
        self.patches = build_wild_patches(self.profile, 201, 35)

    def on(self, species=201, level=35, nonce=None, crc=None):
        return self.handle(f"WILDON {crc or self.crc} {self.layout['rom_sha256']} {species} {level} {nonce or self.nonce}".encode())

    def off(self, nonce=None):
        return self.handle(('WILDOFF '+(nonce or self.nonce)).encode())

    def assert_patches(self, field):
        for address, before, after in self.patches:
            expected = after if field == 'after' else before
            self.assertEqual(bytes(self.bytes.get(address+i, 0) for i in range(len(expected))), expected)

    def test_fixed_whitelist_and_parameters_reject_without_write(self):
        for species, level in [(0, 35), (252, 35), (1554, 35), (201, 0), (201, 101)]:
            self.assertTrue(self.on(species, level).startswith(b'ERR'))
        self.assertEqual(self.on(crc='00000000'), b'ERR rom')
        self.assertEqual(self.on(nonce='a'), b'ERR format')
        self.assertEqual(self.handle(b'WRITE8 8000000 1'), b'ERR unsupported')
        self.assertEqual(self.handle(b'BATCHCRC '+self.crc.encode()+b' 8000000:00:ff'), b'ERR range')
        self.assertEqual(self.writes, [])

    def test_native_enable_restore_order_nonce_and_disconnect(self):
        self.assertEqual(self.on(), b'OK')
        self.assert_patches('after')
        self.assertEqual(self.writes[0], self.layout['stub_address'])
        self.assertEqual(self.off('b'*32), b'ERR owner')
        self.assertEqual(self.handle(('WILDKEEP '+'b'*32).encode()), b'ERR owner')
        self.writes.clear()
        self.disconnect()
        self.assert_patches('before')
        self.assertEqual(self.writes[0], self.layout['generator'])
        self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE disconnected')

    def test_lease_expiry_and_heartbeat(self):
        self.assertEqual(self.on(), b'OK')
        for _ in range(3599): self.tick()
        self.assertTrue(self.handle(b'WILDSTATE').startswith(b'ACTIVE'))
        self.assertEqual(self.handle(('WILDKEEP '+self.nonce).encode()), b'OK')
        for _ in range(3600): self.tick()
        self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE lease_expired')
        self.assert_patches('before')

    def test_stale_guard_rejected_and_runtime_guard_stops(self):
        address = self.layout['guards'][1]['address']
        original = self.bytes[address]
        self.bytes[address] ^= 1
        self.assertEqual(self.on(), b'ERR stale')
        self.assertEqual(self.writes, [])
        self.bytes[address] = original
        self.assertEqual(self.on(), b'OK')
        self.bytes[address] ^= 1
        for _ in range(60): self.tick()
        self.assert_patches('before')
        self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE guard_changed')

    def test_external_patch_mutation_and_rom_swap_never_overwritten(self):
        self.assertEqual(self.on(), b'OK')
        address = self.layout['stub_address']+3
        self.bytes[address] ^= 1
        writes = len(self.writes)
        self.assertEqual(self.off(), b'ERR restore')
        self.assertEqual(len(self.writes), writes)
        self.assertTrue(self.handle(b'WILDSTATE').startswith(b'ERROR'))
        self.crc = '11223344'
        self.disconnect()
        self.assertEqual(len(self.writes), writes)

    def test_pc_guard_defers_restore_until_outside_trampoline(self):
        self.pc = self.layout['generator']+4
        self.assertEqual(self.on(), b'ERR busy')
        self.pc = 0x08000100
        self.assertEqual(self.on(), b'OK')
        self.pc = self.layout['stub_address']+24
        self.assertEqual(self.off(), b'ERR restore')
        self.assert_patches('after')
        self.pc = 0x08000100
        self.tick()
        self.assert_patches('before')

    def test_every_partial_hook_write_failure_rolls_back_before_return(self):
        for offset in range(12):
            with self.subTest(offset=offset):
                self.fail_at = self.layout['generator']+offset
                self.assertEqual(self.on(), b'ERR readback')
                self.assert_patches('before')
                self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE apply_failed')
        self.ignore = True
        self.assertEqual(self.on(), b'ERR readback')
        self.assert_patches('before')

    def tcp(self):
        server = Server(lambda line: [self.handle(line)+b'\n'])
        mem = MemClient(port=server.port)
        self.addCleanup(server.join)
        self.addCleanup(mem.close)
        mem.connect(scan=1)
        return mem

    def test_controller_tcp_journal_overlay_raw_reads_and_contract(self):
        mem = self.tcp()
        with tempfile.TemporaryDirectory() as folder:
            control = WildEncounterControl(mem, self.profile, folder)
            state = control.arm(201, 35)
            self.assertTrue(control.active)
            self.assertEqual(state['state'], 'active')
            self.assertEqual(control.heartbeat()['species'], 201)
            address, before, after = self.patches[0]
            self.assertEqual(mem.read_rom(address, len(after)), after)
            self.assertEqual(mem.read(address, len(after)), after)
            self.assertEqual(mem.read_compatible_rom(address+2, 4), before[2:6])
            guarded = dict(self.profile, compatibility={'test': True}, _compatibility_guards=[
                (address, len(before), hashlib.sha256(before).hexdigest(), 'engine')])
            runtime_compatibility_check(mem, guarded, full=True)
            journal = json.loads(next(Path(folder).rglob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(journal['status'], 'active')
            self.assertEqual(control.stop()['state'], 'inactive')
            self.assertFalse(control.active)
            self.assertIsNone(mem._wild_overlay)
            self.assert_patches('before')

    def test_overlay_refuses_blind_mask_and_clears_on_lease_restore(self):
        mem = self.tcp()
        with tempfile.TemporaryDirectory() as folder:
            control = WildEncounterControl(mem, self.profile, folder)
            control.arm(201, 35)
            for _ in range(3600): self.tick()
            address, before, _ = self.patches[0]
            self.assertEqual(mem.read_compatible_rom(address, len(before)), before)
            self.assertIsNone(mem._wild_overlay)
            control.status()
            control.arm(201, 35)
            self.bytes[self.layout['stub_address']+2] ^= 1
            with self.assertRaises(IOError):
                mem.read_compatible_rom(address, len(before))
            self.assertIsNotNone(mem._wild_overlay)

    def test_close_stops_and_connection_generation_invalidates_controller(self):
        mem = self.tcp()
        with tempfile.TemporaryDirectory() as folder:
            control = WildEncounterControl(mem, self.profile, folder)
            control.arm(201, 35)
            mem.close()
            self.assert_patches('before')
            self.assertIsNone(mem._wild_overlay)
            with self.assertRaises(IOError): control.heartbeat()

    def test_production_socket_disconnect_and_reset_callbacks_restore(self):
        self.lua.globals()[b'MERCURY_BRIDGE_TEST'] = False
        self.lua.execute(b"""
callbacks={add=function(_,name,fn) events[name]=fn end};events={}
console={log=function() end}
listener={add=function(self,name,fn) self[name]=fn end,
 listen=function() return true end,accept=function() local c=pending;pending=nil;return c end}
socket={ERRORS={AGAIN=1,ADDRESS_IN_USE=2},bind=function() return listener end}
function newClient()
 local c={chunks={},sent=""}
 function c:add(name,fn) self[name]=fn end
 function c:receive() if #self.chunks>0 then return table.remove(self.chunks,1) end
  if self.eof then return nil,9 end return nil,1 end
 function c:send(s) self.sent=self.sent..s;return #s end
 function c:close() self.closed=true end
 pending=c;listener.received();return c
end
function feed(c,s) c.chunks[#c.chunks+1]=s;c.received() end
""")
        self.lua.execute(Path('mercury_bridge.lua').read_bytes())
        g = self.lua.globals()
        c = g.newClient()
        command = f"WILDON {self.crc} {self.layout['rom_sha256']} 201 35 {self.nonce}\n".encode()
        g.feed(c, command)
        self.assertEqual(c[b'sent'], b'OK\n')
        self.assert_patches('after')
        c[b'eof'] = True
        c[b'received']()
        self.assertTrue(c[b'closed'])
        self.assert_patches('before')
        c = g.newClient()
        g.feed(c, command)
        self.assert_patches('after')
        g.events[b'reset']()
        self.assert_patches('before')
        # Exercise the real disconnect/frame callbacks while restoration fails.
        g.feed(c, command)
        self.persistent_fail_at = self.layout['generator']+3
        c[b'eof'] = True
        c[b'received']()
        self.assertTrue(c[b'closed'])
        for _ in range(3): g.events[b'frame']()
        c = g.newClient()
        g.feed(c, b'WILDSTATE\n')
        self.assertTrue(c[b'sent'].startswith(b'ERROR'))
        self.persistent_fail_at = None
        g.events[b'frame']()
        self.assert_patches('before')
        g.feed(c, b'WILDSTATE\n')
        self.assertTrue(c[b'sent'].endswith(b'INACTIVE disconnected\n'))

    def test_concurrent_tcp_commands_keep_replies_paired(self):
        from concurrent.futures import ThreadPoolExecutor
        mem = self.tcp()
        address, before, _ = self.patches[0]
        def query(index):
            if index % 2:
                return mem.command('ROMCRC') == self.crc.encode()
            return mem.read_rom(address, len(before)) == before
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertTrue(all(pool.map(query, range(120))))

    def test_battle_flag_rejects_enable_but_does_not_block_stop(self):
        flag = self.profile['battle_flag']
        self.put(flag['address'], bytes([flag['mask']]))
        self.assertEqual(self.on(), b'ERR battle')
        self.assertEqual(self.writes, [])
        self.put(flag['address'], b'\0')
        self.assertEqual(self.on(), b'OK')
        self.put(flag['address'], bytes([flag['mask']]))
        self.assertEqual(self.off(), b'OK')
        self.assert_patches('before')

    def test_every_stop_write_position_once_recovers_before_callback_returns(self):
        for patch_index, (address, before, _) in enumerate(self.patches):
            for offset in range(len(before)):
                with self.subTest(patch=patch_index, offset=offset):
                    self.assertEqual(self.on(), b'OK')
                    self.fail_at = address+offset
                    self.writes.clear()
                    self.assertEqual(self.off(), b'OK')
                    # The CPU can run immediately after off returns. Neither
                    # a mixed hook nor a pending stub may escape this callback.
                    self.assert_patches('before')
                    hook, hook_before, _ = self.patches[0]
                    stub, _, _ = self.patches[1]
                    first_stub_write = next(i for i, value in enumerate(self.writes) if value == stub)
                    self.assertTrue(all(a < stub or a >= stub+100 for a in self.writes[:first_stub_write]))
                    self.assertEqual(self.writes[first_stub_write-1], hook+len(hook_before)-1)
                    self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE stopped')

    def test_every_stop_position_persistent_fault_retains_error_until_recoverable(self):
        for patch_index, (address, before, after) in enumerate(self.patches):
            for offset in range(len(before)):
                with self.subTest(patch=patch_index, offset=offset):
                    self.assertEqual(self.on(), b'OK')
                    self.persistent_fail_at = address+offset
                    expected_reply = b'ERR restore' if before[offset] != after[offset] else b'OK'
                    self.assertEqual(self.off(), expected_reply)
                    if patch_index == 0 and before[offset] != after[offset]:
                        self.assert_patches('after')
                    for _ in range(3): self.tick()
                    if before[offset] != after[offset]:
                        self.assertTrue(self.handle(b'WILDSTATE').startswith(b'ERROR'))
                        if patch_index == 0:
                            stub, _, stub_after = self.patches[1]
                            self.assertEqual(bytes(self.bytes[stub+i] for i in range(len(stub_after))), stub_after)
                    else:
                        # A throwing no-op need not block restoration of bytes
                        # already proved original by the readback.
                        self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE stopped')
                    self.persistent_fail_at = None
                    self.tick()
                    self.assert_patches('before')
                    self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE stopped')

    def test_restore_retry_never_accepts_third_party_before_after_mixtures(self):
        for patch_index, (address, before, after) in enumerate(self.patches):
            for mutation_offset in (0, 5):
                with self.subTest(patch=patch_index, mutation=mutation_offset):
                    self.assertEqual(self.on(), b'OK')
                    if patch_index == 0:
                        # Stop fails at byte 3 and rollback fails at byte 0;
                        # retain a precisely tracked mixed prefix for this test.
                        self.persistent_fail_writes = {(address+3, before[3]), (address, after[0])}
                    else:
                        self.persistent_fail_at = address+3
                    self.assertEqual(self.off(), b'ERR restore')
                    self.persistent_fail_at = None
                    self.persistent_fail_writes.clear()
                    # Change an already restored byte back to after, or an
                    # untouched byte to before. Both are known values but
                    # neither equals this transaction's exact current ledger.
                    replacement = after[mutation_offset] if mutation_offset < 3 else before[mutation_offset]
                    self.assertNotEqual(self.bytes[address+mutation_offset], replacement)
                    self.bytes[address+mutation_offset] = replacement
                    writes = len(self.writes)
                    self.tick()
                    self.assertEqual(self.off(), b'ERR restore')
                    self.assertTrue(self.handle(b'WILDSTATE').endswith(b' modified'))
                    self.assertEqual(len(self.writes), writes)
                    self.assertEqual(self.bytes[address+mutation_offset], replacement)
                    # Simulate explicit external correction to the exact state;
                    # no unknown bytes are accepted as ours during recovery.
                    expected = before[mutation_offset] if mutation_offset < 3 else after[mutation_offset]
                    self.bytes[address+mutation_offset] = expected
                    self.tick()
                    self.assert_patches('before')

    def test_stop_write_then_throw_tracks_observed_byte_before_retry(self):
        for address, before, _ in self.patches:
            for offset in range(len(before)):
                with self.subTest(address=address, offset=offset):
                    self.assertEqual(self.on(), b'OK')
                    self.fail_after_at = address+offset
                    self.assertEqual(self.off(), b'OK')
                    self.assert_patches('before')
                    self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE stopped')

    def test_disconnect_and_lease_failures_remain_retryable_without_callback_escape(self):
        for trigger in ('disconnect', 'lease'):
            for address, _, _ in self.patches:
                with self.subTest(trigger=trigger, address=address):
                    self.assertEqual(self.on(), b'OK')
                    self.persistent_fail_at = address+3
                    if trigger == 'disconnect':
                        self.disconnect()
                        reason = b'disconnected'
                    else:
                        for _ in range(3600): self.tick()
                        reason = b'lease_expired'
                    for _ in range(4): self.tick()
                    self.assertTrue(self.handle(b'WILDSTATE').startswith(b'ERROR'))
                    self.assertEqual(self.handle(('WILDKEEP '+self.nonce).encode()), b'ERR stopping')
                    self.persistent_fail_at = None
                    self.tick()
                    self.assert_patches('before')
                    self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE '+reason)

    def test_each_restore_readback_exception_resolves_only_its_attempted_byte(self):
        for address, before, _ in self.patches:
            for offset in range(len(before)):
                with self.subTest(address=address, offset=offset):
                    self.assertEqual(self.on(), b'OK')
                    self.fail_read_at = address+offset
                    self.assertEqual(self.off(), b'OK')
                    self.assert_patches('before')
                    self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE stopped')

    def test_persistent_readback_exception_never_escapes_callback_or_loses_ownership(self):
        for address, _, _ in self.patches:
            self.assertEqual(self.on(), b'OK')
            self.persistent_read_fail_at = address+3
            self.disconnect()
            for _ in range(4): self.tick()
            self.assertTrue(self.handle(b'WILDSTATE').startswith(b'ERROR'))
            self.persistent_read_fail_at = None
            self.tick()
            self.assert_patches('before')
            self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE disconnected')

    def test_second_fault_during_hook_rollback_keeps_error_and_exact_ledger(self):
        address, before, after = self.patches[0]
        self.assertEqual(self.on(), b'OK')
        self.persistent_fail_writes = {(address+3, before[3]), (address, after[0])}
        self.assertEqual(self.off(), b'ERR restore')
        raw = bytes(self.bytes[address+i] for i in range(len(before)))
        self.assertNotIn(raw, (before, after))
        self.assertTrue(self.handle(b'WILDSTATE').startswith(b'ERROR'))
        stub, _, stub_after = self.patches[1]
        self.assertEqual(bytes(self.bytes[stub+i] for i in range(len(stub_after))), stub_after)
        self.persistent_fail_writes.clear()
        self.tick()
        self.assert_patches('before')
        self.assertEqual(self.handle(b'WILDSTATE'), b'INACTIVE stopped')

    def test_lua_whitelist_is_generated_from_final_layout(self):
        from tools.sync_wild_bridge import sync
        sync(check=True)


if __name__ == '__main__':
    unittest.main()
