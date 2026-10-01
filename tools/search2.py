import socket, struct, sys

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

g = GDB()
# IWRAM 指针（pokefirered: gSaveBlock1Ptr=0x03005008, gSaveBlock2Ptr=0x0300500C）
for name, a in [('gSaveBlock1Ptr', 0x03005008), ('gSaveBlock2Ptr', 0x0300500C),
                ('gSaveBlock3Ptr?', 0x03005010), ('?', 0x03005000)]:
    d = g.rd(a, 4)
    if d:
        print(f'{name} @0x{a:08X} = 0x{struct.unpack("<I", d)[0]:08X}')
    else:
        print(f'{name} @0x{a:08X} = 读取失败')

# 也 dump IWRAM 0x03000000-0x03008000 找 zyh
CHUNK = 0x80
def dump(start, end):
    out = bytearray()
    for a in range(start, end, CHUNK):
        d = g.rd(a, CHUNK)
        if d is None:
            return None
        out += d
    return bytes(out)

iw = dump(0x03000000, 0x03008000)
if iw:
    open('iwram.bin', 'wb').write(iw)
    print('iwram dumped', len(iw))
    def fa(p):
        r=[]; st=0
        while True:
            i=p.find(p, st) if isinstance(p, bytes) else None
            break
    for pat, name in [(b'\xee\xed\xdc', 'zyh'), (b'\x35\xf0\x3e\x1c', 'pers')]:
        r=[]; st=0
        while True:
            i = iw.find(pat, st)
            if i<0: break
            r.append(0x03000000+i); st=i+1
        print(f'{name} @iwram', [hex(x) for x in r])

# dump EWRAM
ew = dump(0x02000000, 0x02040000)
if ew:
    open('wram.bin','wb').write(ew)
    print('ewram dumped', len(ew))
    for pat, name in [(b'\xee\xed\xdc', 'zyh'), (b'\x35\xf0\x3e\x1c', 'pers'),
                      (b'\x92\x5a\x01\x00', '88722')]:
        r=[]; st=0
        while True:
            i = ew.find(pat, st)
            if i<0: break
            r.append(0x02000000+i); st=i+1
        print(f'{name} @ewram', [hex(x) for x in r[:20]])
