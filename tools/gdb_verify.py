"""GDB test transport for an isolated, halted emulator, never the GUI backend."""

import socket


class RSP:
    def __init__(self, host="127.0.0.1", port=2345):
        self.s = socket.create_connection((host, port), timeout=5)
        self.s.settimeout(5)

    def close(self):
        self.s.close()

    def packet(self, command):
        payload = command.encode("ascii")
        self.s.sendall(b"$" + payload + b"#" + f"{sum(payload) & 255:02x}".encode())
        while True:
            value = self.s.recv(1)
            if not value:
                raise IOError("GDB EOF")
            if value == b"-":
                raise IOError("GDB rejected checksum")
            if value == b"$":
                break
        body = bytearray()
        while True:
            value = self.s.recv(1)
            if not value:
                raise IOError("GDB EOF inside packet")
            if value == b"#":
                break
            body += value
            if len(body) > 16384:
                raise IOError("GDB packet too large")
        checksum = b""
        while len(checksum) < 2:
            value = self.s.recv(2 - len(checksum))
            if not value:
                raise IOError("GDB EOF at checksum")
            checksum += value
        if int(checksum, 16) != (sum(body) & 255):
            self.s.sendall(b"-")
            raise IOError("GDB response checksum invalid")
        self.s.sendall(b"+")
        if body.startswith(b"E"):
            raise IOError("GDB error " + body.decode())
        return bytes(body)

    def read(self, address, size):
        result = b""
        for offset in range(0, size, 128):
            count = min(128, size - offset)
            part = bytes.fromhex(
                self.packet(f"m{address + offset:x},{count:x}").decode()
            )
            if len(part) != count:
                raise IOError("GDB short read")
            result += part
        return result

    def write(self, address, data):
        for offset in range(0, len(data), 128):
            chunk = data[offset : offset + 128]
            if (
                self.packet(f"M{address + offset:x},{len(chunk):x}:{chunk.hex()}")
                != b"OK"
            ):
                raise IOError("GDB write failed")

    def r8(self, a):
        return self.read(a, 1)[0]

    def r32(self, a):
        return int.from_bytes(self.read(a, 4), "little")
