"""SARC names must not allocate copies of unrelated archive payloads."""
import struct
import sys
import tracemalloc
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import shader_split as split


def archive(padding=0):
    names = b'first\0\0\0second\0\0'
    data_offset = 20 + 12 + 32 + 8 + len(names)
    data = b'DATAITEM' + bytes(padding)
    header = struct.pack('<4sHHIII', b'SARC', 20, 0xfeff, data_offset + len(data), data_offset, 0x100)
    sfat = struct.pack('<4sHHI', b'SFAT', 12, 2, 0x65)
    entries = struct.pack('<IIIIIIII', 0, 0x01000000, 0, 4, 0, 0x01000002, 4, 8)
    return header + sfat + entries + struct.pack('<4sHH', b'SFNT', 8, 0) + names + data


class SarcNamesTests(unittest.TestCase):
    def test_names_and_member_payloads(self):
        self.assertEqual(list(split.sarc_members(archive())), [('first', b'DATA'), ('second', b'ITEM')])

    def test_small_member_does_not_allocate_the_unrelated_payload(self):
        data = archive(8 * 1024 * 1024)
        tracemalloc.start()
        try:
            member = next(split.sarc_members(data))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(member, ('first', b'DATA'))
        self.assertLess(peak, 1024 * 1024, 'reading a short name allocated the archive tail')


if __name__ == '__main__':
    unittest.main()
