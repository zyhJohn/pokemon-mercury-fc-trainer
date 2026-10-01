import socket, sys

class GDB:
    def __init__(self):
        self.s = socket.socket()
        self.s.settimeout(15)
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
        d += self.s.recv(2)
        return d
    def cmd(self, payload):
        ck = sum(payload.encode()) & 0xFF
        self.s.sendall(b'$' + payload.encode() + b'#' + f'{ck:02x}'.encode())
        b = self.s.recv(1)
        if b == b'$':
            d = b
            while b'#' not in d: d += self.s.recv(1)
            d += self.s.recv(2)
            return d
        if b != b'+': return None
        return self._rp()
    def rd(self, addr, n):
        r = self.cmd(f'm{addr:x},{n:x}')
        if r is None: return None
        body = r[1:r.rfind(b'#')]
        if body.startswith(b'E') or body == b'': return None
        return bytes.fromhex(body.decode())

CHUNK = 0x80  # 128 字节/次
START = 0x02000000
END = 0x02040000

g = GDB()
out = bytearray()
for a in range(START, END, CHUNK):
    d = g.rd(a, CHUNK)
    if d is None:
        print('FAIL at', hex(a))
        sys.exit(1)
    out += d

print('dumped', len(out), 'bytes')
open('wram.bin', 'wb').write(out)

def findall(p):
    r = []; st = 0
    while True:
        i = out.find(p, st)
        if i < 0: break
        r.append(START + i); st = i + 1
    return r

print('zyh(EEEDDC) @', [hex(x) for x in findall(bytes([0xEE,0xED,0xDC]))])
print('88722 0x15A92(92 5A 01 00) @', [hex(x) for x in findall(bytes([0x92,0x5A,0x01,0x00]))])
print('personality 0x1C3EF035(35 F0 3E 1C) @', [hex(x) for x in findall(bytes([0x35,0xF0,0x3E,0x1C]))])
