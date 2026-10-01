# -*- coding: utf-8 -*-
"""模拟 mercury_bridge.lua 的 TCP 服务，用于验证修改器 MemClient 协议"""
import socket, struct, threading

# 模拟内存：SaveBlock1 指针 0x03005008 指向 0x0202552C
MEM = bytearray(0x04000000)  # 64MB 空间
SB1 = 0x0202552C

def w32(a, v): MEM[a:a+4] = struct.pack('<I', v & 0xFFFFFFFF)
def w16(a, v): MEM[a:a+2] = struct.pack('<H', v & 0xFFFF)
def w8(a, v):  MEM[a] = v & 0xFF

# 初始化 SaveBlock1 指针
w32(0x03005008, SB1)
# 金钱 118464 (0x1CEC0) 放在 SB1+0x290
w32(SB1 + 0x290, 118464)
# 代币 5 放在 SB1+0x294
w16(SB1 + 0x294, 5)
# 队伍数量 6
w8(0x02024029, 6)
# 队伍第1只：personality=0x1C3EF035, species 放明文 chunk
party = 0x02024284
w32(party, 0x1C3EF035)
# species 明文放在 growth chunk（简化：直接放 0x20+0*12 处 = species 159 蓝鳄）
w16(party + 0x20, 159)
w8(party + 0x54, 31)   # level
w16(party + 0x56, 107) # hp
w16(party + 0x58, 107) # maxhp
# 背包 0x0203BB20：第一格 伤药(ID13)x5
w16(0x0203BB20, 13)
w16(0x0203BB22, 5)

def handle(line):
    line = line.strip()
    if not line: return None
    parts = line.split()
    cmd = parts[0]
    if cmd == 'PING': return 'PONG'
    if cmd == 'READ':
        addr = int(parts[1], 16); n = int(parts[2], 16)
        return bytes(MEM[addr:addr+n]).hex()
    if cmd == 'READ8': return str(MEM[int(parts[1],16)])
    if cmd == 'READ16': return str(struct.unpack('<H', bytes(MEM[int(parts[1],16):int(parts[1],16)+2]))[0])
    if cmd == 'READ32': return str(struct.unpack('<I', bytes(MEM[int(parts[1],16):int(parts[1],16)+4]))[0])
    if cmd == 'WRITE8': w8(int(parts[1],16), int(parts[2])); return 'OK'
    if cmd == 'WRITE16': w16(int(parts[1],16), int(parts[2])); return 'OK'
    if cmd == 'WRITE32': w32(int(parts[1],16), int(parts[2])); return 'OK'
    if cmd == 'WRITE':
        addr = int(parts[1],16); data = bytes.fromhex(parts[2])
        MEM[addr:addr+len(data)] = data; return 'OK'
    return 'ERR unknown'

def client_thread(conn):
    buf = b''
    while True:
        try:
            data = conn.recv(4096)
            if not data: break
            buf += data
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                resp = handle(line.decode())
                if resp is not None:
                    conn.sendall((resp+'\n').encode())
        except Exception:
            break
    conn.close()

srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(('127.0.0.1', 8888)); srv.listen(1)
print('mock bridge 监听 8888')
while True:
    conn, _ = srv.accept()
    threading.Thread(target=client_thread, args=(conn,), daemon=True).start()
