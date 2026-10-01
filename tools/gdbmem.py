import socket, struct

class GDBClient:
    def __init__(self, host="127.0.0.1", port=2345):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(8)
        self.sock.connect((host, port))

    def _read_packet(self):
        # 逐字节读到完整 $...#ck 包
        data = b""
        # 找 '$'
        while b"$" not in data:
            b = self.sock.recv(1)
            if not b: return None
            data += b
        # 找 '#'
        while b"#" not in data:
            b = self.sock.recv(1)
            if not b: return None
            data += b
        # 读 2 字节 checksum
        data += self.sock.recv(2)
        return data

    def send_packet(self, payload):
        ck = sum(payload.encode('latin1')) & 0xFF
        pkt = b"$" + payload.encode('latin1') + b"#" + f"{ck:02x}".encode()
        self.sock.sendall(pkt)
        # 读 ack '+'
        while True:
            b = self.sock.recv(1)
            if b == b'+':
                break
            if b == b'$':
                # 没有独立 ack，直接是数据包，塞回处理
                data = b
                while b"#" not in data:
                    data += self.sock.recv(1)
                data += self.sock.recv(2)
                return data
        return self._read_packet()

    def read_mem(self, addr, length):
        r = self.send_packet(f"m{addr:x},{length:x}")
        body = r[1:r.rfind(b'#')]
        if body.startswith(b'E') or body == b'':
            return None
        return bytes.fromhex(body.decode())

    def read_u32(self, addr):
        d = self.read_mem(addr, 4)
        return struct.unpack('<I', d)[0] if d and len(d) == 4 else None

    def read_u16(self, addr):
        d = self.read_mem(addr, 2)
        return struct.unpack('<H', d)[0] if d and len(d) == 2 else None

def main():
    candidates = {
        "money(0x02025838)": 0x02025838,
        "coins(0x0202583C)": 0x0202583C,
        "item_slot1(0x02025840)": 0x02025840,
        "party_count(0x02024029)": 0x02024029,
        "party1(0x02024284)": 0x02024284,
        "alt_money(0x020244DC)": 0x020244DC,
    }
    c = GDBClient()
    print("=== 连接成功 ===")
    for name, addr in candidates.items():
        v32 = c.read_u32(addr)
        v16 = c.read_u16(addr)
        print(f"{name:26s} u32=0x{v32:08X}({v32})  u16=0x{v16:04X}({v16})")
    d = c.read_mem(0x02024284, 100)
    if d:
        print("party1 前16字节:", d[:16].hex(' '))
        print("party1 level(0x54):", d[0x54], " hp:", struct.unpack('<H', d[0x56:0x58])[0], "/", struct.unpack('<H', d[0x58:0x5A])[0])

if __name__ == "__main__":
    main()
