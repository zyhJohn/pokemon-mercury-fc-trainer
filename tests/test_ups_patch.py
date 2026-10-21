"""Synthetic UPS fixtures; no ROMs or third-party patch bytes are included."""

import random
import unittest
import zlib
from dataclasses import FrozenInstanceError

from ups_patch import MAX_IMAGE_SIZE, UpsPatchError, apply_ups, encode_ups, parse_ups


def _number(value):
    encoded = bytearray()
    while True:
        low = value & 0x7f
        value >>= 7
        if value == 0:
            return bytes(encoded + bytes((low | 0x80,)))
        encoded.append(low)
        value -= 1


def _finish(payload, source_crc=0, target_crc=0):
    payload += source_crc.to_bytes(4, "little")
    payload += target_crc.to_bytes(4, "little")
    return payload + zlib.crc32(payload).to_bytes(4, "little")


def _patch(source, target):
    size = max(len(source), len(target))
    left, right = source.ljust(size, b"\0"), target.ljust(size, b"\0")
    payload = b"UPS1" + _number(len(source)) + _number(len(target))
    cursor = 0
    index = 0
    while index < size:
        if left[index] == right[index]:
            index += 1
            continue
        payload += _number(index - cursor)
        while index < size and left[index] != right[index]:
            payload += bytes((left[index] ^ right[index],))
            index += 1
        payload += b"\0"
        index += 1
        cursor = index
    return _finish(payload, zlib.crc32(source), zlib.crc32(target))


class UpsPatchTests(unittest.TestCase):
    def test_encoder_forward_reverse_length_changes_and_eof(self):
        for source, target in ((b"", b""), (b"", b"x"), (b"x", b""),
                               (b"same", b"same"), (b"abc", b"abc\0\0"),
                               (b"abc\0\0", b"abc"), (b"a" * 130, b"a" * 129 + b"z")):
            with self.subTest(source=source, target=target):
                encoded = encode_ups(source, target)
                self.assertEqual(apply_ups(source, encoded), target)
                self.assertEqual(apply_ups(target, encoded, reverse=True), source)
                self.assertEqual(encoded, _patch(source, target))

    def test_encoder_random_equal_and_unequal_images(self):
        rng = random.Random(20261021)
        for _ in range(400):
            source = rng.randbytes(rng.randrange(512))
            target = rng.randbytes(rng.randrange(512))
            encoded = encode_ups(source, target)
            self.assertEqual(apply_ups(source, encoded), target)
            self.assertEqual(apply_ups(target, encoded, reverse=True), source)
            self.assertEqual(encoded, _patch(source, target))

    def test_encoder_immutable_input_types(self):
        for source, target in ((bytearray(b"a"), b"b"), (b"a", "b")):
            with self.assertRaises(TypeError):
                encode_ups(source, target)

    def test_parse_identity_and_frozen_records(self):
        patch = _patch(b"abc def", b"ABC dEf")
        parsed = parse_ups(patch)
        self.assertEqual((parsed.source_size, parsed.target_size), (7, 7))
        self.assertEqual(parsed.source_crc32, f"{zlib.crc32(b'abc def'):08x}")
        self.assertEqual(parsed.target_crc32, f"{zlib.crc32(b'ABC dEf'):08x}")
        self.assertEqual(parsed.patch_crc32, f"{zlib.crc32(patch[:-4]):08x}")
        self.assertEqual(parsed.records, ((0, b"   "), (5, b" ")))
        with self.assertRaises(FrozenInstanceError):
            parsed.source_size = 0

    def test_forward_reverse_and_input_unchanged(self):
        for source, target in ((b"abcdef", b"ABCdEf"), (b"ab", b"abcXYZ"),
                               (b"abcXYZ", b"ab"), (b"", b"ABC"),
                               (b"ABC", b""), (b"abc", b"abc\0\0"),
                               (b"", b""), (b"same", b"same")):
            with self.subTest(source=source, target=target):
                patch = _patch(source, target)
                before = (source, target, patch)
                self.assertEqual(apply_ups(source, patch), target)
                self.assertEqual(apply_ups(target, patch, reverse=True), source)
                self.assertEqual((source, target, patch), before)

    def test_direction_never_guessed(self):
        patch = _patch(b"abc", b"ABC")
        with self.assertRaisesRegex(UpsPatchError, "source CRC32"):
            apply_ups(b"ABC", patch)
        with self.assertRaisesRegex(UpsPatchError, "source CRC32"):
            apply_ups(b"abc", patch, reverse=True)

    def test_source_size_and_crc_checked(self):
        patch = _patch(b"abc", b"ABC")
        with self.assertRaisesRegex(UpsPatchError, "source size"):
            apply_ups(b"ab", patch)
        with self.assertRaisesRegex(UpsPatchError, "source CRC32"):
            apply_ups(b"abd", patch)

    def test_patch_and_output_crc_checked(self):
        patch = _patch(b"abc", b"ABC")
        with self.assertRaisesRegex(UpsPatchError, "patch CRC32"):
            parse_ups(patch[:-1] + bytes((patch[-1] ^ 1,)))
        wrong_target = _finish(patch[:-12], zlib.crc32(b"abc"), 0)
        with self.assertRaisesRegex(UpsPatchError, "target CRC32"):
            apply_ups(b"abc", wrong_target)
        bad_body = bytearray(patch)
        bad_body[7] ^= 1
        with self.assertRaisesRegex(UpsPatchError, "patch CRC32"):
            apply_ups(b"abc", bytes(bad_body))

    def test_truncation_and_header(self):
        patch = _patch(b"source", b"target")
        for length in range(len(patch)):
            with self.subTest(length=length), self.assertRaises(UpsPatchError):
                parse_ups(patch[:length])
        for signature in (b"UPS2", b"BPS1", b"ups1"):
            with self.subTest(signature=signature), self.assertRaises(UpsPatchError):
                parse_ups(signature + patch[4:])

    def test_incomplete_numbers_and_unterminated_records(self):
        for payload in (b"UPS1\x00\x00", b"UPS1\x80\x00",
                        b"UPS1\x81\x81\x00", b"UPS1\x81\x81\x80\xff"):
            with self.subTest(payload=payload), self.assertRaises(UpsPatchError):
                parse_ups(_finish(payload))

    def test_huge_sizes_and_offsets_are_bounded(self):
        for size in (MAX_IMAGE_SIZE + 1, 1 << 1000):
            for source_size, target_size in ((size, 0), (0, size)):
                payload = b"UPS1" + _number(source_size) + _number(target_size)
                with self.subTest(size=size), self.assertRaises(UpsPatchError):
                    parse_ups(_finish(payload))
        payload = b"UPS1\x81\x81" + _number(1 << 1000) + b"\0"
        with self.assertRaises(UpsPatchError):
            parse_ups(_finish(payload))
        payload = b"UPS1" + _number(MAX_IMAGE_SIZE) * 2
        self.assertEqual(parse_ups(_finish(payload)).source_size, MAX_IMAGE_SIZE)

    def test_number_boundaries(self):
        for size in (0, 1, 127, 128, 129, 16511, 16512, 1 << 20,
                     MAX_IMAGE_SIZE):
            payload = b"UPS1" + _number(size) * 2
            parsed = parse_ups(_finish(payload))
            self.assertEqual((parsed.source_size, parsed.target_size), (size, size))

    def test_final_terminator_at_image_eof_is_legal(self):
        patch = _patch(b"abc", b"abC")
        self.assertEqual(parse_ups(patch).records, ((2, b" "),))
        self.assertEqual(apply_ups(b"abc", patch), b"abC")
        payload = b"UPS1\x83\x83" + _number(3) + b"\0"
        patch = _finish(payload, zlib.crc32(b"abc"), zlib.crc32(b"abc"))
        self.assertEqual(apply_ups(b"abc", patch), b"abc")

    def test_record_boundaries_and_implicit_plus_one(self):
        patch = _patch(b"00000", b"A0B0C")
        self.assertEqual(tuple(offset for offset, _ in parse_ups(patch).records),
                         (0, 2, 4))
        self.assertEqual(apply_ups(b"00000", patch), b"A0B0C")
        for records in (_number(4) + b"\0", _number(3) + b"\x01\0",
                        _number(2) + b"\x01\x02\0",
                        _number(2) + b"\x01\0\x80\0"):
            with self.subTest(records=records), self.assertRaises(UpsPatchError):
                parse_ups(_finish(b"UPS1\x83\x83" + records))

    def test_types_are_explicit(self):
        patch = _patch(b"", b"")
        with self.assertRaises(TypeError):
            parse_ups(bytearray(patch))
        with self.assertRaises(TypeError):
            apply_ups(bytearray(), patch)
        with self.assertRaises(TypeError):
            apply_ups(b"", patch, reverse=1)


if __name__ == "__main__":
    unittest.main()
