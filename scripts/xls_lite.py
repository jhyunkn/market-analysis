"""Minimal reader for old-style Excel (.xls, BIFF8) files: numbers and text from one sheet. Standard library only.

Enough for Robert Shiller's monthly S&P data (ie_data.xls). Not a general Excel reader: no dates, styles or formulas
beyond their cached numeric results.
"""
from __future__ import annotations

import struct


def _ole_stream(data: bytes, want: str) -> bytes:
    if data[:8] != bytes.fromhex("d0cf11e0a1b11ae1"):
        raise ValueError("not an OLE file")
    ssz = 1 << struct.unpack_from("<H", data, 30)[0]
    mssz = 1 << struct.unpack_from("<H", data, 32)[0]
    n_fat, dir_start = struct.unpack_from("<I", data, 44)[0], struct.unpack_from("<I", data, 48)[0]
    cutoff, mfat_start, n_mfat = struct.unpack_from("<III", data, 56)
    difat_start, n_difat = struct.unpack_from("<II", data, 68)
    sec = lambda i: data[512 + i * ssz: 512 + (i + 1) * ssz]
    difat = list(struct.unpack_from("<109I", data, 76))
    d, per = difat_start, ssz // 4 - 1
    for _ in range(n_difat):
        s = sec(d)
        difat += struct.unpack_from(f"<{per}I", s)
        d = struct.unpack_from("<I", s, per * 4)[0]
    fat = []
    for i in difat[:n_fat]:
        fat += struct.unpack(f"<{ssz // 4}I", sec(i))

    def chain(start, table, getter):
        out, i, seen = [], start, set()
        while i < 0xFFFFFFFA and i not in seen:
            seen.add(i); out.append(getter(i)); i = table[i]
        return b"".join(out)

    dirs = chain(dir_start, fat, sec)
    entries = []
    for k in range(len(dirs) // 128):
        e = dirs[k * 128:(k + 1) * 128]
        nlen = struct.unpack_from("<H", e, 64)[0]
        name = e[:max(nlen - 2, 0)].decode("utf-16-le", "ignore")
        start, size = struct.unpack_from("<I", e, 116)[0], struct.unpack_from("<I", e, 120)[0]
        entries.append((name, e[66], start, size))
    root = next(x for x in entries if x[1] == 5)
    for name, typ, start, size in entries:
        if typ == 2 and name.lower() == want.lower():
            if size >= cutoff:
                return chain(start, fat, sec)[:size]
            minifat = []
            if n_mfat:
                raw = chain(mfat_start, fat, sec)
                minifat = list(struct.unpack(f"<{len(raw) // 4}I", raw))
            mini = chain(root[2], fat, sec)
            return chain(start, minifat, lambda i: mini[i * mssz:(i + 1) * mssz])[:size]
    raise KeyError(want)


def _rk(v: int) -> float:
    x = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0] if not v & 2 else float(v >> 2 if v < 1 << 31 else (v >> 2) - (1 << 30))
    return x / 100 if v & 1 else x


def _str(buf: bytes, pos: int, nchars: int) -> tuple[str, int]:
    flags = buf[pos]; pos += 1
    rich = struct.unpack_from("<H", buf, pos)[0] if flags & 8 else 0; pos += 2 if flags & 8 else 0
    ext = struct.unpack_from("<I", buf, pos)[0] if flags & 4 else 0; pos += 4 if flags & 4 else 0
    if flags & 1:
        s = buf[pos:pos + 2 * nchars].decode("utf-16-le", "ignore"); pos += 2 * nchars
    else:
        s = buf[pos:pos + nchars].decode("latin-1"); pos += nchars
    return s, pos + rich * 4 + ext


def read_sheet(path: str, sheet: str) -> dict[tuple[int, int], float | str]:
    """{(row, col): value} for one sheet (0-based)."""
    wb = _ole_stream(open(path, "rb").read(), "Workbook")
    recs, p = [], 0
    while p + 4 <= len(wb):
        rid, ln = struct.unpack_from("<HH", wb, p)
        recs.append((p, rid, wb[p + 4:p + 4 + ln])); p += 4 + ln
    sheets, sst = {}, []
    for i, (pos, rid, b) in enumerate(recs):
        if rid == 0x85:  # BOUNDSHEET
            n = b[6]
            name = b[8:8 + 2 * n].decode("utf-16-le") if b[7] & 1 else b[8:8 + n].decode("latin-1")
            sheets[name] = struct.unpack_from("<I", b, 0)[0]
        elif rid == 0xFC:  # SST + CONTINUE (strings rarely split across them in this file; skip any that are)
            buf = b + b"".join(c for _, r2, c in recs[i + 1:i + 40] if r2 == 0x3C) if False else b
            total = struct.unpack_from("<I", buf, 4)[0]; q = 8
            try:
                for _ in range(total):
                    n = struct.unpack_from("<H", buf, q)[0]
                    s, q = _str(buf, q + 2, n); sst.append(s)
            except (struct.error, IndexError):
                pass
    if sheet not in sheets:
        raise KeyError(f"sheet {sheet!r} not in {list(sheets)}")
    start = sheets[sheet]
    cells: dict[tuple[int, int], float | str] = {}
    for pos, rid, b in recs:
        if pos < start:
            continue
        if rid == 0x0A and pos > start:  # EOF of this sheet
            break
        if rid == 0x203:
            r, c = struct.unpack_from("<HH", b); cells[(r, c)] = struct.unpack_from("<d", b, 6)[0]
        elif rid == 0x27E:
            r, c = struct.unpack_from("<HH", b); cells[(r, c)] = _rk(struct.unpack_from("<I", b, 6)[0])
        elif rid == 0xBD:
            r, c = struct.unpack_from("<HH", b)
            for k in range((len(b) - 6) // 6):
                cells[(r, c + k)] = _rk(struct.unpack_from("<I", b, 4 + k * 6 + 2)[0])
        elif rid == 0xFD:
            r, c = struct.unpack_from("<HH", b); i = struct.unpack_from("<I", b, 6)[0]
            if i < len(sst):
                cells[(r, c)] = sst[i]
        elif rid == 0x06:  # FORMULA: cached number unless the top bytes mark a non-number
            r, c = struct.unpack_from("<HH", b)
            if b[12:14] != b"\xff\xff":
                cells[(r, c)] = struct.unpack_from("<d", b, 6)[0]
    return cells
