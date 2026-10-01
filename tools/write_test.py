import socket, struct

class GDB:
    def __init__(self):
        self.s = socket.socket(); self.s.settimeout(15)
        self.s.connect(('127.0.0.1', 2345))
    def _rp(self):
        d = b''
        while b'$' not in d:
            b = self.s.recv(1)
            if not b: return None
            d += b
        while b'#' not in d:
            b = self.s.recv(1)
            if not b: return None
            d += b
        d += self.s.recv(2); return d
    def cmd(self, payload):
        ck = sum(payload.encode()) & 0xFF
        self.s.sendall(b'$' + payload.encode() + b'#' + f'{ck:02x}'.encode())
        b = self.s.recv(1)
        if b == b'$':
            d = b
            while b'#' not in d: d += self.s.recv(1)
            d += self.s.recv(2); return d
        if b != b'+': return None
        return self._rp()
    def rd(self, addr, n):
        r = self.cmd(f'm{addr:x},{n:x}')
        if r is None: return None
        body = r[1:r.rfind(b'#')]
        if body.startswith(b'E') or body == b'': return None
        return bytes.fromhex(body.decode())
    def wr(self, addr, data):
        r = self.cmd(f'M{addr:x},{len(data)}:{data.hex()}')
        if r is None: return False
        body = r[1:r.rfind(b'#')]
        return body == b'OK'

g = GDB()
MONEY = 0x020257BC
d = g.rd(MONEY, 4)
print('当前金钱:', struct.unpack('<I', d)[0], '(应 118464)')
# 写 999999 = 0x000F423F
ok = g.wr(MONEY, struct.pack('<I', 999999))
print('写 999999:', ok)
d = g.rd(MONEY, 4)
print('读回:', struct.unpack('<I', d)[0], '(应 999999)')
# 恢复 118464
g.wr(MONEY, struct.pack('<I', 118464))
print('已恢复 118464')
