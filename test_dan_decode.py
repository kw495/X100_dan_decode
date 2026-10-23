# -*- coding: utf-8 -*-
"""dan_decode.py / compare.py のテスト。

メーカー提供のサンプルや個人のデータは使わず、テスト内で合成したデータを使う。
実行: python3 -m unittest discover -s tests -v
"""
import csv
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import dan_decode as dd  # noqa: E402
import compare  # noqa: E402

BLANK = b"\xff" * 128


def mem_record(freq_hz, mode, step, name, banks="", skip=False, comm=None):
    """メモリーチャンネル 1 件分（128 バイト）を作る。"""
    f = bytearray(17)
    struct.pack_into("<I", f, 0, freq_hz)
    f[4], f[5], f[16] = mode, step, 1 if skip else 0
    bank = bytes(1 if chr(65 + j) in banks else 0 for j in range(26))
    nm = name.encode("cp932")[:28].ljust(29, b"\x00")
    if comm is None:
        comm = default_comm()
    return bytes(f) + bank + nm + comm


def default_comm():
    """EditTool の初期値と同じ通信設定（56 バイト）。"""
    return bytes.fromhex("00000000000000000000E4000000E400000000000000000000000180018001800180"
                         "010000800100008001000080000080008000807B1700")


def scan_record(lo, hi, mode, step, name):
    p = bytearray(b"\x00" * 128)
    struct.pack_into("<II", p, 0, lo, hi)
    p[8], p[9] = mode, step
    p[10:65] = default_comm()[:55]
    p[65] = 0
    p[66:94] = name.encode("cp932")[:28].ljust(28, b"\x00")
    p[94:] = b"\xff" * 34
    return bytes(p)


def name_rows(names, nrows):
    """名前の行（1 行 8 件 x 16 バイト）を作る。"""
    rows = []
    for r in range(nrows):
        buf = bytearray(128)
        for q in range(8):
            i = r * 8 + q
            if i < len(names):
                buf[q * 16:q * 16 + 14] = names[i].encode("cp932")[:14].ljust(14, b"\x00")
        rows.append(bytes(buf))
    return rows


def build_file(path, mem, scans=(), bank_names=(), bank_links=(), scan_links=()):
    """合成データから .dan / .dat ファイルを書く。"""
    lines = []
    for i in range(999):
        lines.append(mem[i] if i < len(mem) else BLANK)
    for i in range(50):
        lines.append(scans[i] if i < len(scans) else BLANK)
    lines += name_rows(list(bank_names), 4)
    lines += name_rows([], 3)          # バンクリンク名
    lines += name_rows([], 3)          # スキャンリンク名
    bl = bytearray(b"\xff" * 128)
    for k, members in enumerate(bank_links):
        v = sum(1 << (ord(c) - 65) for c in members)
        bl[k * 4:k * 4 + 4] = struct.pack("<I", v)
    lines.append(bytes(bl))
    sl = [bytearray(b"\xff" * 128), bytearray(b"\xff" * 128)]
    for k, members in enumerate(scan_links):
        v = sum(1 << (m - 1) for m in members)
        o = ((k >> 3) & 1) * 64 + (k & 7) * 8
        sl[k >> 4][o:o + 8] = struct.pack("<Q", v)
    lines += [bytes(x) for x in sl]
    assert len(lines) == dd.TOTAL_LINES
    with open(path, "w", newline="") as fh:
        for p in lines:
            fh.write(dd.scramble(p) + "\r\n")


class TestScramble(unittest.TestCase):
    def test_roundtrip(self):
        for seed in (b"\x00\x00", b"\x12\x34", b"\xff\xff", None):
            data = bytes(range(128))
            line = dd.scramble(data, seed)
            self.assertEqual(len(line), 264)
            self.assertEqual(dd.descramble(line), data)

    def test_random_seed_changes_output(self):
        data = bytes(128)
        self.assertNotEqual(dd.scramble(data, b"\x01\x00"), dd.scramble(data, b"\x02\x00"))

    def test_corruption_detected(self):
        line = dd.scramble(bytes(range(128)), b"\xab\xcd")
        bad = ("0" if line[0] != "0" else "1") + line[1:]
        with self.assertRaises(ValueError):
            dd.descramble(bad)

    def test_bad_length(self):
        with self.assertRaises(ValueError):
            dd.descramble("00" * 10)


class TestCommColumns(unittest.TestCase):
    def test_default_values(self):
        c = dd.comm_columns(default_comm())
        self.assertEqual(c["周波数シフト"], "0.000000")
        self.assertEqual(c["アッテネータ"], "OFF")
        self.assertEqual(c["T98:白色化コード"], "228")
        self.assertEqual(c["T98:ユーザーコード"], "OFF")
        self.assertEqual(c["T98:秘話コード"], "OFF")
        self.assertEqual(c["T98:グループコード"], "ALL")
        self.assertEqual(c["T102/NXDN:白色化コード"], "228")

    def test_t102_example(self):
        # 白色化 261 / ユーザー OFF / 秘話 08537 / グループ ALL / シフト -2MHz（EditTool 画面で確認した値）
        comm = bytes.fromhex("807BE1FF000000000000E40000000501000000000000000000000180018001805921"
                             "010000800100008001000080000080008000807B1700")
        c = dd.comm_columns(comm)
        self.assertEqual(c["T102/NXDN:白色化コード"], "261")
        self.assertEqual(c["T102/NXDN:ユーザー(RAN)コード"], "OFF")
        self.assertEqual(c["T102/NXDN:秘話コード"], "08537")
        self.assertEqual(c["T102/NXDN:グループコード"], "ALL")
        self.assertEqual(c["周波数シフト"], "-2.000000")


class TestDecodeFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _csv(self, path):
        with open(path, encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))

    def test_dan(self):
        path = os.path.join(self.dir, "t.dan")
        build_file(path,
                   mem=[mem_record(467_000_000, 4, 2, "テスト1", "A"),
                        mem_record(148_050_000, 0, 7, "バス", "B", skip=True),
                        mem_record(453_100_000, 7, 2, "NXDN局", "AB")],
                   scans=[scan_record(351_200_000, 351_381_250, 4, 2, "デジ簡登録")],
                   bank_names=["簡易無線", "交通"],
                   bank_links=["AB"], scan_links=[[1]])
        out = os.path.join(self.dir, "out")
        self.assertEqual(dd.main([path, out]), 0)
        mem = self._csv(os.path.join(out, "memory_channels.csv"))
        self.assertEqual([r["周波数"] for r in mem], ["467.000000", "148.050000", "453.100000"])
        self.assertEqual(mem[0]["モード"], "T98")
        self.assertEqual(mem[0]["ステップ"], "6.25k")
        self.assertEqual(mem[1]["スキップ"], "SKIP")
        self.assertEqual(mem[1]["バンク割り当て状況"], "[交通]")
        self.assertEqual(mem[2]["モード"], "NXDN(N)")
        self.assertEqual(mem[2]["バンク"], "AB")
        scans = self._csv(os.path.join(out, "program_scans.csv"))
        self.assertEqual(scans[0]["名前"], "デジ簡登録")
        self.assertEqual(scans[0]["終了周波数"], "351.381250")
        self.assertEqual(self._csv(os.path.join(out, "bank_links.csv"))[0]["バンク"], "AB")
        self.assertEqual(self._csv(os.path.join(out, "scan_links.csv"))[0]["PS番号"], "01")

    def test_dat_mode_shift(self):
        # .dat では番号 7 = T61(TYP1)、16 = AIS（.dan より 1 小さい）
        path = os.path.join(self.dir, "t.dat")
        build_file(path, mem=[mem_record(161_975_000, 16, 7, "AIS"),
                              mem_record(150_000_000, 6, 2, "DMR"),
                              mem_record(150_100_000, 7, 2, "T61")])
        d = dd.decode(path)
        self.assertTrue(d["is_dat"])
        self.assertEqual([r["モード"] for r in d["mem"]], ["AIS", "DMR", "T61(TYP1)"])
        self.assertEqual(d["mem"][2]["標準機GUI"], "標準機では非表示(非対応モード)")
        # --format で明示すると拡張子より優先される
        self.assertEqual(dd.decode(path, "dan")["mem"][0]["モード"], "C4FM(DN)")

    def test_broken_line_reported(self):
        path = os.path.join(self.dir, "t.dan")
        build_file(path, mem=[mem_record(467_000_000, 4, 2, "A")])
        with open(path) as fh:
            lines = fh.read().splitlines()
        lines[0] = ("0" if lines[0][0] != "0" else "1") + lines[0][1:]
        with open(path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        d = dd.decode(path)
        self.assertEqual(d["mem"], [])
        self.assertEqual(d["errors"][0]["行"], 1)


class TestCompare(unittest.TestCase):
    def test_compare(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = os.path.join(tmp, "old.dat"), os.path.join(tmp, "new.dan")
            build_file(old, mem=[mem_record(150_000_000, 0, 7, "残す"),
                                 mem_record(150_100_000, 0, 7, "消す"),
                                 mem_record(150_200_000, 0, 7, "変える")])
            build_file(new, mem=[mem_record(150_300_000, 0, 7, "足す"),
                                 mem_record(150_000_000, 0, 7, "残す"),
                                 mem_record(150_200_000, 1, 7, "変える")])
            out = os.path.join(tmp, "cmp")
            self.assertEqual(compare.main([old, new, out]), 0)
            with open(os.path.join(out, "compare_memory.csv"), encoding="utf-8-sig") as fh:
                rows = {r["名前"]: r for r in csv.DictReader(fh)}
            self.assertEqual(rows["足す"]["区分"], "追加")
            self.assertEqual(rows["消す"]["区分"], "削除")
            self.assertEqual(rows["残す"]["区分"], "番号移動のみ")
            self.assertEqual(rows["変える"]["区分"], "変更")
            self.assertIn("モード: FM → NFM", rows["変える"]["変更項目"])


if __name__ == "__main__":
    unittest.main()
