import unittest
import tempfile
import struct, zlib
from pathlib import Path
from tools.read_state import read_state, STATE_SIZE


def chunk(tag, data):
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


class StateTests(unittest.TestCase):
    def test_crc_and_decompression_bounds(self):
        prefix = b"\x89PNG\r\n\x1a\n"
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "test.ss1"
            expected = bytes(STATE_SIZE)
            good = (
                prefix + chunk(b"gbAs", zlib.compress(expected)) + chunk(b"IEND", b"")
            )
            path.write_bytes(good)
            self.assertEqual(read_state(path), expected)
            damaged = bytearray(good)
            damaged[20] ^= 1
            path.write_bytes(damaged)
            with self.assertRaises(ValueError):
                read_state(path)
            path.write_bytes(
                prefix
                + chunk(b"gbAs", zlib.compress(bytes(STATE_SIZE + 1)))
                + chunk(b"IEND", b"")
            )
            with self.assertRaises(ValueError):
                read_state(path)
            path.write_bytes(good[:-12])
            with self.assertRaises(ValueError):
                read_state(path)


if __name__ == "__main__":
    unittest.main()
