"""Minimal OLE2 + BIFF8 reader: enough to read Shiller's `ie_data.xls` without `xlrd`.

The research venv has no `xlrd`/`openpyxl` and the protocol forbids installing packages, so this
module reads the compound file itself:

  * OLE2 (CFB): header, DIFAT -> FAT -> directory -> mini stream/mini FAT,
  * BIFF8: the record stream of the workbook, the SST (with CONTINUE records), the BOUNDSHEET
    table, and the cell records NUMBER / RK / MULRK / LABELSST / FORMULA / BOOLERR.

Only the two sheets' cell values and the sheet names are exposed, which is all a data extraction
needs. Validated against the published `ie_data.xls` values (see `screen.py --v0` cross-check rows
and `DATA_MANIFEST.json`).
"""

from __future__ import annotations

import struct

ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF

# BIFF8 record types used here
BOF = 0x0809
CONTINUE = 0x003C
SST = 0x00FC
LABELSST = 0x00FD
NUMBER = 0x0203
RK = 0x027E
MULRK = 0x00BD
FORMULA = 0x0006
STRING = 0x0207
BOOLERR = 0x0205
BOUNDSHEET = 0x0085


def _rk_value(rk: int) -> float:
    """BIFF RK: bit0 = /100 flag, bit1 = integer flag, else the top 4 bytes of a double."""
    if rk & 0x02:
        v = float((rk >> 2) - (1 << 30) if rk & 0x80000000 else rk >> 2)
    else:
        v = struct.unpack("<d", struct.pack("<II", 0, rk & 0xFFFFFFFC))[0]
    return v / 100.0 if rk & 0x01 else v


class Ole:
    """The compound-file container."""

    def __init__(self, data: bytes):
        if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError("not an OLE2/CFB file")
        self.data = data
        (self.minor, self.major, self.byte_order, self.sector_shift, self.mini_shift) = struct.unpack_from(
            "<5H", data, 24
        )
        (self.num_dir_sectors, self.num_fat_sectors, self.first_dir, self.trans, self.mini_cutoff) = struct.unpack_from(
            "<IIIII", data, 40
        )
        (self.first_minifat, self.num_minifat, self.first_difat, self.num_difat) = struct.unpack_from("<4I", data, 60)
        self.ssz = 1 << self.sector_shift
        self.mssz = 1 << self.mini_shift
        self._fat = self._build_fat()
        self.dirs = self._build_dirs()
        root = next(d for d in self.dirs if d["type"] == 5)
        self.ministream = self._read_chain(root["start"], root["size"])
        self._minifat = self._build_minifat()

    # -- sectors -------------------------------------------------------------------------------
    def _sector(self, n: int) -> bytes:
        off = (n + 1) * self.ssz
        return self.data[off : off + self.ssz]

    def _difat(self) -> list[int]:
        difat = list(struct.unpack_from("<109I", self.data, 76))
        nxt, guard = self.first_difat, 0
        while nxt not in (ENDOFCHAIN, FREESECT) and guard <= self.num_difat:
            per = self.ssz // 4
            entries = struct.unpack_from(f"<{per}I", self._sector(nxt), 0)
            difat.extend(entries[:-1])
            nxt = entries[-1]
            guard += 1
        return [x for x in difat if x not in (FREESECT, ENDOFCHAIN)][: self.num_fat_sectors]

    def _build_fat(self) -> list[int]:
        fat: list[int] = []
        per = self.ssz // 4
        for fs in self._difat():
            fat.extend(struct.unpack_from(f"<{per}I", self._sector(fs), 0))
        return fat

    def _build_minifat(self) -> list[int]:
        mf: list[int] = []
        per = self.ssz // 4
        for s in self._chain(self.first_minifat):
            mf.extend(struct.unpack_from(f"<{per}I", self._sector(s), 0))
        return mf

    def _chain(self, start: int) -> list[int]:
        out, cur, guard = [], start, 0
        while cur not in (ENDOFCHAIN, FREESECT) and guard < len(self._fat) + 8:
            out.append(cur)
            cur = self._fat[cur]
            guard += 1
        return out

    def _read_chain(self, start: int, size: int) -> bytes:
        if size == 0:
            return b""
        return b"".join(self._sector(s) for s in self._chain(start))[:size]

    # -- directory -----------------------------------------------------------------------------
    def _build_dirs(self) -> list[dict]:
        raw = b"".join(self._sector(s) for s in self._chain(self.first_dir))
        out = []
        for off in range(0, len(raw) - 127, 128):
            e = raw[off : off + 128]
            nlen = struct.unpack_from("<H", e, 64)[0]
            name = e[: max(0, nlen - 2)].decode("utf-16-le", "replace") if nlen >= 2 else ""
            typ = e[66]
            if typ == 0:
                continue
            start, size = struct.unpack_from("<II", e, 116)
            out.append({"name": name, "type": typ, "start": start, "size": size})
        return out

    def stream(self, name: str) -> bytes:
        """The bytes of a named stream (mini-stream aware)."""
        d = next((d for d in self.dirs if d["name"] == name and d["type"] == 2), None)
        if d is None:
            avail = [x["name"] for x in self.dirs if x["type"] == 2]
            raise KeyError(f"stream {name!r} not found; have {avail}")
        if d["size"] < self.mini_cutoff:
            out, cur, guard = b"", d["start"], 0
            while cur not in (ENDOFCHAIN, FREESECT) and guard < len(self._minifat) + 8:
                out += self.ministream[cur * self.mssz : (cur + 1) * self.mssz]
                cur = self._minifat[cur]
                guard += 1
            return out[: d["size"]]
        return self._read_chain(d["start"], d["size"])


class Biff:
    """The record stream of one workbook, with the SST and the cell values per sheet."""

    def __init__(self, wb: bytes):
        self.wb = wb
        self.records = self._records()
        self.sst = self._parse_sst()

    def _records(self) -> list[tuple[int, int]]:
        """(type, data offset, length) for every record, in order."""
        out, pos, n = [], 0, len(self.wb)
        while pos + 4 <= n:
            typ, ln = struct.unpack_from("<HH", self.wb, pos)
            out.append((typ, pos + 4, ln))
            pos += 4 + ln
        return out

    def _parse_sst(self) -> list[str]:
        idx = next((k for k, (t, _, _) in enumerate(self.records) if t == SST), None)
        if idx is None:
            return []
        chunks = []
        typ, off, ln = self.records[idx]
        chunks.append((off, ln))
        for t, o, l in self.records[idx + 1 :]:
            if t != CONTINUE:
                break
            chunks.append((o, l))
        # the concatenated buffer plus, for each chunk, its start offset in that buffer
        starts, buf, base = [], [], 0
        for o, l in chunks:
            starts.append((base, base + l))
            buf.append(self.wb[o : o + l])
            base += l
        blob = b"".join(buf)
        n_unique = struct.unpack_from("<I", blob, 4)[0]
        ci, pos = 0, 8

        def adv(k: int) -> bytes:
            nonlocal ci, pos
            acc = b""
            while k > 0:
                if ci < len(starts) and pos >= starts[ci][1]:
                    ci += 1
                    pos = starts[ci][0] if ci < len(starts) else 0
                room = (starts[ci][1] - pos) if ci < len(starts) else k
                take = min(k, room)
                acc += blob[pos : pos + take]
                pos += take
                k -= take
            return acc

        out = []
        for _ in range(n_unique):
            cch = struct.unpack("<H", adv(2))[0]
            grbit = adv(1)[0]
            crun = struct.unpack("<H", adv(2))[0] if grbit & 0x08 else 0
            cbext = struct.unpack("<I", adv(4))[0] if grbit & 0x04 else 0
            chars, left, high = [], cch, bool(grbit & 0x01)
            while left > 0:
                if ci < len(starts) and pos >= starts[ci][1]:
                    ci += 1
                    pos = starts[ci][0]
                    high = bool(blob[pos] & 0x01)
                    pos += 1
                room = starts[ci][1] - pos if ci < len(starts) else left
                per = 2 if high else 1
                take = min(left, room // per)
                raw = blob[pos : pos + take * per]
                pos += take * per
                chars.append(raw.decode("utf-16-le" if high else "latin-1", "replace"))
                left -= take
            out.append("".join(chars))
            if crun * 4 + cbext:
                adv(crun * 4 + cbext)
        return out

    def sheet_names(self) -> list[tuple[str, int]]:
        out = []
        for t, off, ln in self.records:
            if t != BOUNDSHEET:
                continue
            # [MS-XLS] BOUNDSHEET as written by Excel 97+: lbPlyPos(4), hsState(1), grbit(1),
            # cch(1), pad(1), rgch(cch) -- the pad byte is why the name starts at off + 8.
            pos, grbit, cch = (
                struct.unpack_from("<I", self.wb, off)[0],
                self.wb[off + 5],
                self.wb[off + 6],
            )
            raw = self.wb[off + 8 : off + 8 + cch * (2 if grbit & 0x01 else 1)]
            name = raw.decode("utf-16-le" if grbit & 0x01 else "latin-1", "replace")
            out.append((name, pos))
        return out

    def cells(self, sheet_pos: int) -> dict[tuple[int, int], object]:
        """{(row, col): value} for the sheet whose BOF is at `sheet_pos`."""
        out: dict[tuple[int, int], object] = {}
        start = next(k for k, (t, off, _) in enumerate(self.records) if t == BOF and off - 4 == sheet_pos)
        pending = None
        for t, off, ln in self.records[start + 1 :]:
            if t == BOF:  # next sheet
                break
            if t == NUMBER:
                r, c = struct.unpack_from("<HH", self.wb, off)
                out[(r, c)] = struct.unpack_from("<d", self.wb, off + 6)[0]
            elif t == RK:
                r, c = struct.unpack_from("<HH", self.wb, off)
                out[(r, c)] = _rk_value(struct.unpack_from("<I", self.wb, off + 6)[0])
            elif t == MULRK:
                r, c0 = struct.unpack_from("<HH", self.wb, off)
                n = (ln - 6) // 6
                for k in range(n):
                    rk = struct.unpack_from("<I", self.wb, off + 4 + k * 6 + 2)[0]
                    out[(r, c0 + k)] = _rk_value(rk)
            elif t == LABELSST:
                r, c = struct.unpack_from("<HH", self.wb, off)
                isst = struct.unpack_from("<I", self.wb, off + 6)[0]
                out[(r, c)] = self.sst[isst] if isst < len(self.sst) else None
            elif t == FORMULA:
                r, c = struct.unpack_from("<HH", self.wb, off)
                res = self.wb[off + 6 : off + 14]
                if res[:2] == b"\xff\xff":
                    pending = (r, c)
                    code = res[2]
                    if code == 0:  # string result, value in the following STRING record
                        pass
                    elif code == 1:
                        out[(r, c)] = bool(res[3])
                    else:
                        out[(r, c)] = None
                else:
                    out[(r, c)] = struct.unpack("<d", res)[0]
                    pending = None
            elif t == STRING:
                if pending is not None:
                    cch, grbit = struct.unpack_from("<HB", self.wb, off)
                    raw = self.wb[off + 3 : off + 3 + cch * (2 if grbit & 0x01 else 1)]
                    out[pending] = raw.decode("utf-16-le" if grbit & 0x01 else "latin-1", "replace")
                    pending = None
            elif t == BOOLERR:
                r, c = struct.unpack_from("<HH", self.wb, off)
                vb, is_err = self.wb[off + 6], self.wb[off + 7]
                out[(r, c)] = None if is_err else bool(vb)
        return out


def load(path) -> tuple[list[str], dict[str, dict[tuple[int, int], object]]]:
    """(sheet names, {sheet name: cells}) for a .xls workbook."""
    with open(path, "rb") as fh:
        ole = Ole(fh.read())
    wb = Biff(ole.stream("Workbook"))
    sheets = wb.sheet_names()
    return [n for n, _ in sheets], {n: wb.cells(pos) for n, pos in sheets}
