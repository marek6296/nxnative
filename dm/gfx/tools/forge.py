"""Scimitar v31 archives (Assassin's Creed: The Ezio Collection on Switch).

Read FILEDATA entries and their two chunk streams, a resource table and the
resource bytes. Codec 6 is Oodle (Leviathan in Ezio 1.3); the caller supplies the
game's decoder. The archive and resources are read sequentially, not expanded
to disk. Layout checked against all 27 v1.3 forge files; older versions differ.
"""
import struct
from romfs import open

MAGIC = b'scimitar\0'
CHUNK_MAGIC = bytes.fromhex('33aafb5799fa0410')


def read(f, n, end, label):
    if n < 0 or n > end - f.tell(): raise ValueError(f'{label}: read outside forge entry')
    b = f.read(n)
    if len(b) != n: raise ValueError(f'{label}: truncated forge entry')
    return b


def entries(path):
    """Live (name, payload offset, stored size) entries. Zero-size slots are deleted."""
    with open(path, 'rb') as f:
        f.seek(0, 2); end = f.tell(); f.seek(0)
        h = read(f, 29, end, path)
        if h[:9] != MAGIC: raise ValueError(f'{path}: not a Scimitar forge')
        version, index = struct.unpack_from('<IQ', h, 9)
        if version != 31: raise ValueError(f'{path}: unsupported forge version {version}')
        f.seek(index); h = read(f, 40, end, path)
        total, pages = struct.unpack_from('<II', h)
        page = struct.unpack_from('<Q', h, 32)[0]
        if pages != 1: raise ValueError(f'{path}: unsupported forge page count {pages}')
        f.seek(page); h = read(f, 48, end, path)
        count, _, table = struct.unpack_from('<IIQ', h)
        if count != total: raise ValueError(f'{path}: inconsistent forge entry counts')
        f.seek(table); table_bytes = read(f, count * 16, end, path)
        out = []
        for offset, identity, size in struct.iter_unpack('<QII', table_bytes):
            if not size: continue
            f.seek(offset); h = read(f, 0x1b8, end, path)
            if h[:8] != b'FILEDATA' or struct.unpack_from('<II', h, 0x187) != (identity, size):
                raise ValueError(f'{path}: inconsistent FILEDATA header at {offset:#x}')
            if size > end - f.tell(): raise ValueError(f'{path}: FILEDATA payload outside forge')
            name = h[8:136].split(b'\0', 1)[0].decode('utf-8', 'replace')
            out.append((f'{identity:08x}:{name}', f.tell(), size))
        return out


def chunks(f, end, label):
    """Read a chunk stream header; leave f at its first checksum/payload."""
    h = read(f, 17, end, label)
    version, codec, minimum, maximum, count = struct.unpack_from('<HBHHH', h, 8)
    if h[:8] != CHUNK_MAGIC or version != 1 or codec != 6:
        raise ValueError(f'{label}: unsupported forge chunk stream (version {version}, codec {codec})')
    sizes = list(struct.iter_unpack('<HH', read(f, 4 * count, end, label)))
    if not minimum or maximum < minimum or any(not u or not c or u > maximum for u, c in sizes):
        raise ValueError(f'{label}: invalid forge chunk sizes')
    if sum(c + 4 for _, c in sizes) > end - f.tell(): raise ValueError(f'{label}: truncated forge chunks')
    return sizes


def blocks(f, sizes, end, label, decode):
    for raw_size, stored_size in sizes:
        read(f, 4, end, label)  # opaque per-block checksum, not CRC32 or Adler32
        packed = read(f, stored_size, end, label)
        raw = packed if raw_size == stored_size else decode(packed, raw_size)
        if len(raw) != raw_size: raise ValueError(f'{label}: forge decoder returned the wrong size')
        yield raw


def resources(f, entry, decode):
    """Yield (resource label, bytes), preserving resource boundaries across chunks."""
    name, offset, size = entry
    end = offset + size; f.seek(offset)
    # FILEDATA's size includes this four-byte prefix. Counting from the stream
    # magic instead reads four bytes of alignment padding or the next FILEDATA.
    read(f, 4, end, name)
    magic = read(f, min(8, end - f.tell()), end, name); f.seek(offset + 4)
    if magic != CHUNK_MAGIC:
        yield name, read(f, end - f.tell(), end, name)  # GlobalMetaFile and other stored entries
        return
    toc = b''.join(blocks(f, chunks(f, end, name), end, name, decode))
    if len(toc) < 6: raise ValueError(f'{name}: truncated forge resource table')
    count = struct.unpack_from('<H', toc)[0]
    table_end = 2 + count * 10
    if table_end + 4 > len(toc): raise ValueError(f'{name}: truncated forge resource table')
    # Dependency metadata follows the resource records; it is not shader data.
    # Its size varies between entries. The second stream's exact length checks
    # the resource sizes independently of those opaque dependency records.
    records = list(struct.iter_unpack('<IIH', toc[2:table_end]))
    sizes = chunks(f, end, name)
    if sum(n for _, n, _ in records) != sum(n for n, _ in sizes):
        raise ValueError(f'{name}: forge resource lengths do not match the data stream')
    decoded = iter(blocks(f, sizes, end, name, decode)); pending = b''; cursor = 0
    for identity, n, _ in records:
        parts = []; remaining = n
        while remaining:
            if cursor == len(pending): pending = next(decoded); cursor = 0
            take = min(remaining, len(pending) - cursor)
            parts.append(pending[cursor:cursor + take]); cursor += take; remaining -= take
        yield f'{name}/{identity:08x}', b''.join(parts)
    if f.tell() != end: raise ValueError(f'{name}: unexpected bytes after forge data stream')


def uses_oodle(root):
    """Recognize v31 by magic/version, not the game's name; the split validates its tables."""
    from romfs import os
    for d, _, names in os.walk(root):
        for n in names:
            if n.lower().endswith('.forge'):
                with open(os.path.join(d, n), 'rb') as f: h = f.read(13)
                if h == MAGIC + struct.pack('<I', 31): return True
    return False
