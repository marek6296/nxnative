"""Scimitar v31 layouts observed in Ezio Collection 1.3; synthetic assets only."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import shader_split as split


def program(stage, embedded=False):
    control, code = bytearray(0x880), bytearray(0x100)
    control[:4] = bytes.fromhex('34127698')
    struct.pack_into('<I', control, 0x20, 0x880)
    struct.pack_into('<I', control, 0x704, 0x100)
    code[:4] = bytes.fromhex('79563412' if stage == 'cs' else '78563412')
    struct.pack_into('<I', code, 0x30, (1 if stage == 'vs' else 5) << 10 | 1)
    descriptor = struct.pack('<4I', 0x880, 0x880, 0x100, 0 if embedded else 1) + bytes(32)
    return descriptor + control + code, bytes(control), bytes(code)


def stream(data, chunk=0x8000, compress=False):
    blocks = [data[i:i + chunk] for i in range(0, len(data), chunk)]
    packed = [zlib.compress(b) for b in blocks] if compress else blocks
    return (bytes.fromhex('33aafb5799fa0410') + struct.pack('<HBHHH', 1, 6, 0x8000, 0x8000, len(blocks))
            + b''.join(struct.pack('<HH', len(b), len(c)) for b, c in zip(blocks, packed))
            + b''.join(bytes(4) + b for b in packed))


def archive(resources, dependencies=bytes(4), compress=False):
    # v31: index at 0x416, page at 0x43e, 16-byte entries at 0x46e;
    # FILEDATA at 0x800, 0x1b8-byte header. Its size includes a four-byte
    # prefix before the two streams. TOC records are followed by dependencies.
    toc = struct.pack('<H', len(resources)) + b''.join(struct.pack('<IIH', i + 1, len(r), 0xffff) for i, r in enumerate(resources)) + dependencies
    payload = bytes(4) + stream(toc) + stream(b''.join(resources), chunk=701, compress=compress)  # split a shader over several compression blocks
    d = bytearray(0x800 + 0x1b8)
    d[:9] = b'scimitar\0'
    struct.pack_into('<IQII', d, 9, 31, 0x416, 16, 1)
    struct.pack_into('<II', d, 0x416, 1, 1)
    struct.pack_into('<Q', d, 0x436, 0x43e)
    struct.pack_into('<IIQQQQ', d, 0x43e, 1, 1, 0x46e, 0xffffffffffffffff, 0, 0)
    struct.pack_into('<QII', d, 0x46e, 0x800, 17, len(payload))
    d[0x800:0x808] = b'FILEDATA'
    d[0x808:0x80d] = b'test\0'
    struct.pack_into('<II', d, 0x987, 17, len(payload))
    return d + payload


class ForgeTests(unittest.TestCase):
    def scan(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'assets.forge'
            path.write_bytes(data)
            return split.scan_file_in((str(path), 'assets.forge'))

    def test_stages_cross_blocks_and_resources_are_deduplicated(self):
        v, vc, vk = program('vs'); p, pc, pk = program('ps')
        _, found, _, _ = self.scan(archive([b'texture', v + p, v]))
        self.assertEqual(len(found), 2)
        self.assertEqual({r[1]: r[2:] for r in found.values()}, {'vs': (vc, vk), 'ps': (pc, pk)})

    def test_truncated_entry_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'forge|FILEDATA|Scimitar'):
            self.scan(archive([program('vs')[0]])[:-3])

    def test_shader_cannot_borrow_code_from_the_next_resource(self):
        v, _, _ = program('vs')
        _, found, _, _ = self.scan(archive([v[:-100], v[-100:]]))
        self.assertFalse(found)

    def test_control_magic_without_matching_descriptor_is_not_a_shader(self):
        v, _, _ = program('vs'); v = bytes(48) + v[48:]
        _, found, _, _ = self.scan(archive([v]))
        self.assertFalse(found)

    def test_unknown_archive_version_is_rejected(self):
        d = archive([b'x']); struct.pack_into('<I', d, 9, 32)
        with self.assertRaisesRegex(ValueError, 'version'):
            self.scan(d)

    def test_unexpected_bytes_after_stream_are_rejected(self):
        d = archive([b'x']); d += b'extra'
        size = struct.unpack_from('<I', d, 0x47a)[0] + 5
        struct.pack_into('<I', d, 0x47a, size); struct.pack_into('<I', d, 0x98b, size)
        with self.assertRaisesRegex(ValueError, 'stream'):
            self.scan(d)

    def test_dependency_metadata_is_not_scanned_as_resources(self):
        v, _, _ = program('vs'); p, _, _ = program('ps')
        found = self.scan(archive([v], dependencies=bytes(4) + p))[1]
        self.assertEqual([r[1] for r in found.values()], ['vs'])

    def test_entry_does_not_read_the_next_file_header(self):
        self.assertEqual(len(self.scan(archive([program('vs')[0]]) + b'FILEDATA')[1]), 1)

    def test_embedded_graphics_and_compute_records(self):
        for stage in ('vs', 'ps', 'cs'):
            d, ctl, code = program(stage, embedded=True)
            found = list(split.forge_shaders(d, 'main'))
            self.assertEqual([r[1:] for r in found], [(stage, ctl, code)])

    def test_headerless_compute_uses_control_thread_group(self):
        d, _, _ = program('cs', embedded=True); d = bytearray(d)
        d[48 + 0x880:48 + 0x884] = b'\xf0\x07\x20\xe2'
        struct.pack_into('<3I', d, 48 + 0x748, 8, 8, 1)
        self.assertEqual([r[1] for r in split.forge_shaders(d, 'main')], ['cs'])
        struct.pack_into('<3I', d, 48 + 0x748, 0, 0, 0)
        self.assertFalse(list(split.forge_shaders(d, 'main')))

    def test_compressed_blocks_use_decoder_and_check_output_length(self):
        # The external game's decoder is replaced by zlib for synthetic bytes;
        # resource assembly, chunk sizes and shader validation stay real.
        v, vc, vk = program('vs'); d = archive([v], compress=True)
        with mock.patch.object(split, 'oodle', side_effect=lambda c, n: zlib.decompress(c)) as decode:
            found = self.scan(d)[1]
            self.assertEqual([r[1:] for r in found.values()], [('vs', vc, vk)])
            self.assertGreater(decode.call_count, 1)
        with mock.patch.object(split, 'oodle', return_value=b'wrong'):
            with self.assertRaisesRegex(ValueError, 'decoder returned the wrong size'):
                self.scan(d)

    def test_deleted_slot_is_not_scanned(self):
        d = archive([program('vs')[0]]); struct.pack_into('<I', d, 0x47a, 0)
        self.assertFalse(self.scan(d)[1])

    def test_unknown_chunk_codec_is_not_treated_as_stored(self):
        d = archive([b'x']); d[0x9bc + 10] = 42
        with self.assertRaisesRegex(ValueError, 'codec'):
            self.scan(d)

    def test_profile_requests_decoder_for_scimitar_archives(self):
        import profile_guess
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'data.forge'; path.write_bytes(archive([b'x']))
            # Only NSO parsing is replaced: the RomFS detection and decision run normally.
            with mock.patch.object(profile_guess, 'nso', return_value=(b'', None)), mock.patch.object(profile_guess.nso_syms, 'dynsyms', return_value=[]):
                source = profile_guess.build_sources(Path(tmp), Path(tmp), [])
            self.assertTrue(source['oodle_packs'][0])


if __name__ == '__main__':
    unittest.main()
