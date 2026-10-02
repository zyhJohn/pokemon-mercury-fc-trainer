"""Exercise real Lua callbacks with socket API doubles; no emulator is launched."""

import unittest
from pathlib import Path
from tests.test_bridge import LuaRuntime


@unittest.skipIf(LuaRuntime is None, "lupa is required")
class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(encoding=None, unpack_returned_tuples=True)
        self.lua.execute(b"""
events={}; logs={}; connections={}; pending=nil
emu={readRange=function(_,a,n) return string.rep(string.char(0),n) end,
     checksum=function() return string.char(1,2,3,4) end,
     write8=function() end}
console={log=function(_,s) logs[#logs+1]=s end}
callbacks={add=function(_,name,fn) events[name]=fn end}
listener={add=function(self,name,fn) self[name]=fn end,
          listen=function() return true end,
          accept=function() local p=pending;pending=nil;return p end,
          close=function(self) self.closed=true end}
socket={ERRORS={AGAIN=1,ADDRESS_IN_USE=2},bind=function(host,port)
    assert(host=="127.0.0.1");return listener end}
function connect()
    local c={chunks={},sent="",limit=3,blocked=false,closed=false}
    function c:add(name,fn) self[name]=fn end
    function c:receive(n)
        if #self.chunks>0 then return table.remove(self.chunks,1) end
        if self.eof then return nil,9 end
        return nil,1
    end
    function c:send(s)
        if self.blocked then return nil,1 end
        local n=math.min(self.limit,#s);self.sent=self.sent..s:sub(1,n);return n
    end
    function c:close() self.closed=true end
    pending=c;listener.received();connections[#connections+1]=c;return c
end
function feed(c,s) c.chunks[#c.chunks+1]=s;c.received() end
""")
        self.lua.execute(Path("mercury_bridge.lua").read_bytes())
        self.g = self.lua.globals()

    def test_fragmented_input_partial_send_and_frame_retry(self):
        c = self.g.connect()
        self.g.feed(c, b"PI")
        self.assertEqual(c[b"sent"], b"")
        self.g.feed(c, b"NG\nCAPS\n")
        self.assertEqual(
            c[b"sent"], b"PONG\nMERCURY/3 BATCH ROMCRC CRCBATCH BATCH8192 BATCHVERIFY\n"
        )
        c[b"blocked"] = True
        self.g.feed(c, b"PING\n")
        self.assertFalse(c[b"closed"])
        c[b"blocked"] = False
        self.g.events[b"frame"]()
        self.assertTrue(c[b"sent"].endswith(b"BATCHVERIFY\nPONG\n"))

    def test_disconnect_resets_partial_input_and_refuses_second_client(self):
        first = self.g.connect()
        second = self.g.connect()
        self.assertTrue(second[b"closed"])
        self.assertFalse(first[b"closed"])
        self.g.feed(first, b"READ 2000")
        first[b"eof"] = True
        first[b"received"]()
        self.assertTrue(first[b"closed"])
        third = self.g.connect()
        self.g.feed(third, b"PING\n")
        self.assertEqual(third[b"sent"], b"PONG\n")
        first[
            b"error"
        ]()  # A queued event from the closed socket must not kill the new one.
        self.assertFalse(third[b"closed"])

    def test_oversized_input_disconnects_without_processing(self):
        c = self.g.connect()
        self.g.feed(c, b"X" * 40001)
        self.assertTrue(c[b"closed"])
        self.assertEqual(c[b"sent"], b"")


if __name__ == "__main__":
    unittest.main()
