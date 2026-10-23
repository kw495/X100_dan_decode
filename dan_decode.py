#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DJ-X100 メモリーデータ (.dan / .dat) デコーダ

アルインコ DJ-X100 EditTool が保存する .dan / .dat ファイルを復号し、
メモリーチャンネル・プログラムスキャン・バンク・リンクの内容を CSV に書き出します。
通信設定（白色化コード、秘話コード、CTCSS など）は EditTool の画面と同じ表記で
1 項目 1 列に展開します。

使い方:
    python3 dan_decode.py 入力ファイル(.dan/.dat) 出力フォルダ
    python3 dan_decode.py 入力ファイル 出力フォルダ --format dat   # 形式を明示する場合

"""
import argparse
import csv
import json
import os
import struct
import sys

__version__ = "1.0.0"

# ファイルの行構成（EditTool Ver.1.01 の保存処理と同じ）
LINE_HEX_LEN = 264           # 1 行 = 16 進 264 文字（132 バイト）
TOTAL_LINES = 1062
MEM_LINES = (0, 999)         # メモリーチャンネル 001〜999
SCAN_LINES = (999, 1049)     # プログラムスキャン 01〜50
BANK_NAME_LINES = (1049, 1053)
BANK_LINK_NAME_LINES = (1053, 1056)
SCAN_LINK_NAME_LINES = (1056, 1059)
BANK_LINK_DATA_LINE = 1059
SCAN_LINK_DATA_LINES = (1060, 1062)

# ---------------------------------------------------------------- スクランブル
# 平文 128 バイト + 16 ビットのチェック値を、16 ビットの乱数シードで初期化した
# 自己同期型スクランブラ（タップ 16/12/7/2）で処理し、シード 2 バイトを
# 出力の 25 バイト目と 105 バイト目に挿入したものが 1 行になっている。
_CHECK_MATRIX = [0,1,1,1,0,1,0,1,0,1,0,1,0,0,1,0,1,0,0,1,1,0,1,0,1,1,1,1,1,0,1,0,
                 1,0,1,0,1,0,1,1,0,1,0,0,1,0,1,0,1,1,1,1,1,1,0,1,0,1,0,1,0,0,0,1]
_CHECK_INIT = [1,0,1,1,1,1,0,1,0,1,1,0,0,0,1,0]


def _to_bits(data: bytes, nbits: int) -> list:
    return [(data[i >> 3] >> (i & 7)) & 1 for i in range(nbits)]


def _from_bits(bits: list, nbytes: int) -> bytes:
    return bytes(sum(bits[(n << 3) | k] << k for k in range(8)) for n in range(nbytes))


def _check_word(bits: list) -> list:
    """平文 1024 ビットに対する 16 ビットのチェック値を返す。"""
    chk = list(_CHECK_INIT)
    for blk in range(64):
        if _CHECK_MATRIX[blk]:
            for m in range(16):
                chk[m] ^= bits[(blk << 4) | m]
    return chk


def descramble(line: str) -> bytes:
    """1 行（16 進 264 文字）を平文 128 バイトに戻す。チェック値が合わなければ ValueError。"""
    h = line.strip()
    if len(h) != LINE_HEX_LEN:
        raise ValueError(f"行の長さが不正です（{len(h)} 文字）")
    try:
        raw = bytes.fromhex(h)
    except ValueError:
        raise ValueError("16 進数以外の文字が含まれています") from None
    seed = bytes((raw[25], raw[105]))
    body = raw[:25] + raw[26:105] + raw[106:]
    bits = _to_bits(body, 1040)
    reg = _to_bits(seed, 16)
    out = [0] * 1040
    for i, b in enumerate(bits):
        out[i] = b ^ reg[15] ^ reg[11] ^ reg[6] ^ reg[1]
        reg = [b] + reg[:15]
    if out[1024:] != _check_word(out[:1024]):
        raise ValueError("チェック値が一致しません（破損または非対応形式）")
    return _from_bits(out, 128)


def scramble(data: bytes, seed: bytes = None) -> str:
    """平文 128 バイトを 1 行（16 進 264 文字）に変換する。descramble の逆変換。

    EditTool は seed に毎回乱数を使う。省略時は os.urandom で生成する。
    """
    if len(data) != 128:
        raise ValueError("平文は 128 バイトである必要があります")
    seed = os.urandom(2) if seed is None else bytes(seed)
    bits = _to_bits(data, 1024)
    bits += _check_word(bits)
    reg = _to_bits(seed, 16)
    out = []
    for b in bits:
        o = b ^ reg[15] ^ reg[11] ^ reg[6] ^ reg[1]
        out.append(o)
        reg = [o] + reg[:15]
    body = _from_bits(out, 130)
    raw = body[:25] + seed[:1] + body[25:104] + seed[1:] + body[104:]
    return raw.hex().upper()


# ---------------------------------------------------------------- 一覧表（EditTool Ver.1.01 と同じ並び）
MODES = ["FM", "NFM", "AM", "NAM", "T98", "T102/B54", "DMR", "NXDN(N)", "T61(TYP1)", "T61(TYP2)",
         "T61(TYP3)", "T61(TYP4)", "T61(TYPX)", "ICDU", "dPMR", "D-STAR", "C4FM(DN)", "AIS", "ACARS",
         "POCSAG", "12kIF(W)", "12kIF(N)"]
STEPS = ["1.00k", "5.00k", "6.25k", "8.33k", "10.00k", "12.50k", "15.00k", "20.00k", "25.00k",
         "30.00k", "50.00k", "100.00k", "125.00k", "200.00k"]
SQ_TYPE = ["OFF", "CTCSS", "DCS", "逆CTCSS", "逆DCS", "JR", "MSK"]
CTCSS = ["67.0Hz", "69.3Hz", "71.9Hz", "74.4Hz", "77.0Hz", "79.7Hz", "82.5Hz", "85.4Hz", "88.5Hz",
         "91.5Hz", "94.8Hz", "97.4Hz", "100.0Hz", "103.5Hz", "107.2Hz", "110.9Hz", "114.8Hz",
         "118.8Hz", "123.0Hz", "127.3Hz", "131.8Hz", "136.5Hz", "141.3Hz", "146.2Hz", "151.4Hz",
         "156.7Hz", "159.8Hz", "162.2Hz", "165.5Hz", "167.9Hz", "171.3Hz", "173.8Hz", "177.3Hz",
         "179.9Hz", "183.5Hz", "186.2Hz", "189.9Hz", "192.8Hz", "196.6Hz", "199.5Hz", "203.5Hz",
         "206.5Hz", "210.7Hz", "218.1Hz", "225.7Hz", "229.1Hz", "233.6Hz", "241.8Hz", "250.3Hz",
         "254.1Hz"]
DCS = ["017", "023", "025", "026", "031", "032", "036", "043", "047", "050", "051", "053", "054",
       "065", "071", "072", "073", "074", "114", "115", "116", "122", "125", "131", "132", "134",
       "143", "145", "152", "155", "156", "162", "165", "172", "174", "205", "212", "223", "225",
       "226", "243", "244", "245", "246", "251", "252", "255", "261", "263", "265", "266", "271",
       "274", "306", "311", "315", "325", "331", "332", "343", "346", "351", "356", "364", "365",
       "371", "411", "412", "413", "423", "431", "432", "445", "446", "452", "454", "455", "462",
       "464", "465", "466", "503", "506", "516", "523", "526", "532", "546", "565", "606", "612",
       "624", "627", "631", "632", "654", "662", "664", "703", "712", "723", "731", "732", "734",
       "743", "754"]
ONOFF = ["OFF", "ON"]
INV_FREQ = ["2500Hz", "2600Hz", "2700Hz", "2800Hz", "2900Hz", "3000Hz", "3100Hz", "3200Hz",
            "3300Hz", "3400Hz", "3500Hz"]
ATT = ["OFF", "10dB", "20dB"]
SLOT = ["AUTO", "Slot 1", "Slot 2"]

# 標準機（T61 / ICDU / dPMR / POCSAG 非対応）で EditTool が非表示にするモード
UNSUPPORTED_STD = {"T61(TYP1)", "T61(TYP2)", "T61(TYP3)", "T61(TYP4)", "T61(TYPX)", "ICDU",
                   "dPMR", "POCSAG"}


def u8(b, o): return b[o]
def u16(b, o): return struct.unpack_from("<H", b, o)[0]
def u32(b, o): return struct.unpack_from("<I", b, o)[0]
def s32(b, o): return struct.unpack_from("<i", b, o)[0]


def pick(lst, i):
    """一覧から値を取り出す。範囲外なら ?番号 を返す。"""
    return lst[i] if 0 <= i < len(lst) else f"?{i}"


def sjis(b: bytes) -> str:
    """Shift_JIS（NUL 終端）の文字列を取り出す。未使用（0xFF）なら空文字。"""
    b = b.split(b"\x00", 1)[0]
    if not b or b[0] == 0xFF:
        return ""
    return b.decode("cp932", errors="replace")


# ---------------------------------------------------------------- 通信設定の表示ルール（EditTool の画面と同じ）
def wc_auto(v): return f"{v:03d}" if 0 < v < 512 else "AUTO"           # T98 / T102 白色化コード
def uc98(v):    return "OFF" if v == 0 or v >= 512 else f"{v:03d}"     # T98 ユーザーコード
def uc102(v):   return "OFF" if v == 0 or v >= 64 else f"{v:03d}"      # T102 / NXDN ユーザー(RAN)コード
def enc(v):     return "OFF" if v == 0 or v >= 32768 else f"{v:05d}"   # 秘話コード
def gc(v):      return "ALL" if v == 0 or v >= 65536 else f"{v:05d}"   # T98 / T102 グループコード
def gc_dmr(v):  return "ALL" if v == 0 or v >= 1000000000 else f"{v:08d}"
def cc_dmr(v):  return f"{v:03d}" if 0 <= v < 16 else "OFF"            # DMR カラーコード
def wc61(v):    return f"{v:03d}" if 0 <= v < 512 else "000"            # T61 / ICDU 白色化コード
def csq(v):     return f"{v:03d}" if 0 <= v < 100 else "OFF"            # D-STAR コードスケルチ
def dgid(v):    return f"{v:03d}" if 0 <= v < 128 else "OFF"            # C4FM DG-ID


def shift_str(v):
    s = f"{v / 1e6:.6f}"
    return "+" + s if v > 0 else s


def comm_columns(c: bytes) -> dict:
    """通信設定（55〜56 バイト）を画面表記の辞書にする。キーの右のコメントはバイト位置。"""
    return {
        "周波数シフト": shift_str(s32(c, 0)),                  # 0-3（全モード共通）
        "アッテネータ": pick(ATT, u8(c, 4)),                    # 4（全モード共通）
        "FM:スケルチタイプ": pick(SQ_TYPE, u8(c, 5)),           # 5
        "FM:CTCSSトーン": pick(CTCSS, u8(c, 6)),                # 6
        "FM:DCSコード": pick(DCS, u8(c, 7)),                    # 7
        "FM/AM:音声反転秘話": pick(ONOFF, u8(c, 8)),            # 8
        "FM/AM:反転周波数": pick(INV_FREQ, u8(c, 9)),           # 9
        "T98:白色化コード": wc_auto(u16(c, 10)),                 # 10-11
        "ICDU:白色化コード": wc61(u16(c, 12)),                   # 12-13
        "T102/NXDN:白色化コード": wc_auto(u16(c, 14)),           # 14-15
        "T61(TYP1):白色化コード": wc61(u16(c, 16)),              # 16-17
        "T61(TYP2):白色化コード": wc61(u16(c, 18)),              # 18-19
        "T61(TYP3):白色化コード": wc61(u16(c, 20)),              # 20-21
        "T61(TYP4):白色化コード": wc61(u16(c, 22)),              # 22-23
        "T61(TYPX):白色化コード": wc61(u16(c, 24)),              # 24-25
        "T98:ユーザーコード": uc98(u16(c, 26)),                  # 26-27
        "T102/NXDN:ユーザー(RAN)コード": uc102(u16(c, 28)),      # 28-29
        "T98:秘話コード": enc(u16(c, 30)),                       # 30-31
        "T102/NXDN:秘話コード": enc(u16(c, 32)),                 # 32-33
        "T98:グループコード": gc(u32(c, 34)),                    # 34-37
        "T102/NXDN:グループコード": gc(u32(c, 38)),              # 38-41
        "DMR:グループコード": gc_dmr(u32(c, 42)),                # 42-45
        "DMR:受信スロット": pick(SLOT, u8(c, 46)),               # 46
        "DMR:カラーコード": cc_dmr(u16(c, 47)),                  # 47-48
        "D-STAR:コードスケルチ": csq(u16(c, 49)),                # 49-50
        "C4FM:DG-ID": dgid(u16(c, 51)),                         # 51-52
        "T61(TYP1/2):基準経度": str(u8(c, 53)),                  # 53
        "T61(TYP1/2):基準緯度": str(u8(c, 54)),                  # 54
    }


# モードごとに画面のタブに表示される項目（周波数シフト・アッテネータは全タブ共通）
TAB_FIELDS = {
    "T98": ["T98:白色化コード", "T98:ユーザーコード", "T98:秘話コード", "T98:グループコード"],
    "T102": ["T102/NXDN:白色化コード", "T102/NXDN:ユーザー(RAN)コード", "T102/NXDN:秘話コード",
             "T102/NXDN:グループコード"],
    "DMR": ["DMR:受信スロット", "DMR:カラーコード", "DMR:グループコード"],
    "T61(TYP1)": ["T61(TYP1):白色化コード", "T61(TYP1/2):基準緯度", "T61(TYP1/2):基準経度"],
    "T61(TYP2)": ["T61(TYP2):白色化コード", "T61(TYP1/2):基準緯度", "T61(TYP1/2):基準経度"],
    "T61(TYP3)": ["T61(TYP3):白色化コード"],
    "T61(TYP4)": ["T61(TYP4):白色化コード"],
    "T61(TYPX)": ["T61(TYPX):白色化コード"],
    "ICDU": ["ICDU:白色化コード"],
    "D-STAR": ["D-STAR:コードスケルチ"],
    "C4FM(DN)": ["C4FM:DG-ID"],
}
MODE_TAB = {"T98": "T98", "T102/B54": "T102", "NXDN(N)": "T102", "DMR": "DMR"}


def active_summary(mode: str, cols: dict) -> str:
    """そのチャンネルのモードで意味を持つ設定だけを 1 つの文字列にまとめる。"""
    if mode in ("FM", "NFM"):
        st = cols["FM:スケルチタイプ"]
        parts = [f"スケルチ={st}"]
        if "CTCSS" in st:
            parts.append(f"CTCSS={cols['FM:CTCSSトーン']}")
        if "DCS" in st:
            parts.append(f"DCS={cols['FM:DCSコード']}")
        if cols["FM/AM:音声反転秘話"] == "ON":
            parts.append(f"反転秘話=ON({cols['FM/AM:反転周波数']})")
    elif mode in ("AM", "NAM"):
        parts = []
        if cols["FM/AM:音声反転秘話"] == "ON":
            parts.append(f"反転秘話=ON({cols['FM/AM:反転周波数']})")
    else:
        keys = TAB_FIELDS.get(MODE_TAB.get(mode, mode), [])
        parts = [f"{k.split(':', 1)[1]}={cols[k]}" for k in keys]
    if cols["周波数シフト"] != "0.000000":
        parts.append(f"シフト={cols['周波数シフト']}")
    if cols["アッテネータ"] != "OFF":
        parts.append(f"ATT={cols['アッテネータ']}")
    return " / ".join(parts)


def std_note(mode: str) -> str:
    return "標準機では非表示(非対応モード)" if mode in UNSUPPORTED_STD else ""


def mode_fix(v: int, is_dat: bool) -> int:
    """.dat（NXDN(N) 追加前の形式）はモード番号 7 以上が 1 小さい。EditTool と同じく +1 する。"""
    return v + 1 if is_dat and v >= 7 else v


# ---------------------------------------------------------------- ファイルの解析
def parse(path: str, fmt: str = None):
    """ファイルを読み、各行を復号する。戻り値: (is_dat, 行数, 平文リスト, エラー一覧)"""
    if fmt is None:
        fmt = "dat" if path.lower().endswith(".dat") else "dan"
    with open(path, encoding="ascii", errors="replace") as fh:
        lines = fh.read().splitlines()
    plain, bad = [], []
    for i, ln in enumerate(lines):
        try:
            plain.append(descramble(ln))
        except ValueError as e:
            plain.append(None)
            bad.append({"行": i + 1, "内容": str(e)})
    if len(lines) != TOTAL_LINES:
        bad.append({"行": None, "内容": f"行数が {len(lines)} 行です（想定は {TOTAL_LINES} 行）"})
    plain += [None] * max(0, TOTAL_LINES - len(plain))
    return fmt == "dat", len(lines), plain[:TOTAL_LINES], bad


def _names(rows, n, prefix):
    """名前の行（1 行に 8 件、各 16 バイト中 14 バイト）から n 件取り出す。"""
    out = []
    for r in rows:
        for q in range(8):
            out.append(sjis(r[q * 16:q * 16 + 14]) if r else "")
    return [out[i] or f"{prefix}{i + 1:02d}" for i in range(n)]


def decode(path: str, fmt: str = None) -> dict:
    """ファイルを解析して、表ごとの行リストを返す。"""
    is_dat, nlines, plain, bad = parse(path, fmt)
    sl = lambda r: plain[r[0]:r[1]]
    bank_names = _names(sl(BANK_NAME_LINES), 26, "")
    bank_names = [nm if not nm.isdigit() else f"ﾊﾞﾝｸ-{chr(65 + i)}" for i, nm in enumerate(bank_names)]
    blink_names = _names(sl(BANK_LINK_NAME_LINES), 20, "ﾊﾞﾝｸﾘﾝｸ-")
    slink_names = _names(sl(SCAN_LINK_NAME_LINES), 20, "ﾘﾝｸｽｷｬﾝ-")

    # メモリーチャンネル: 周波数系 17B / バンク 26B / 名前 29B / 通信設定 56B
    mem_rows = []
    for ch, p in enumerate(sl(MEM_LINES), start=1):
        if p is None:
            continue
        f = p[0:17]
        fr = u32(f, 0)
        if fr in (0, 0xFFFFFFFF):
            continue
        bank = p[17:43]
        mode = pick(MODES, mode_fix(f[4], is_dat))
        banks = [chr(65 + j) for j in range(26) if bank[j] != 0xFF and bank[j] & 1]
        cols = comm_columns(p[72:128])
        row = {
            "番号": f"{ch:03d}", "周波数": f"{fr / 1e6:.6f}", "名前": sjis(p[43:71]),
            "モード": mode, "標準機GUI": std_note(mode), "ステップ": pick(STEPS, f[5]),
            "スキップ": "SKIP" if f[16] else "-",
            "オフセットステップ": "OFFSET" if f[6] else "-",
            "緯度": f"{s32(f, 8) / 1e6:.6f}" if f[7] else "",
            "経度": f"{s32(f, 12) / 1e6:.6f}" if f[7] else "",
            "バンク": "".join(banks),
            "バンク割り当て状況": "/".join(f"[{bank_names[ord(b) - 65]}]" for b in banks),
            "通信設定(現モード)": active_summary(mode, cols),
        }
        row.update(cols)
        row["CommData(hex)"] = p[72:128].hex().upper()
        mem_rows.append(row)

    # プログラムスキャン: 開始 4B / 終了 4B / モード / ステップ / 通信設定 55B / オフセット / 名前 28B
    ps_names, scan_rows = [], []
    for n, p in enumerate(sl(SCAN_LINES), start=1):
        nm = sjis(p[66:94]) if p else ""
        ps_names.append(nm or f"ﾌﾟﾛｸﾞﾗﾑ-{n:02d}")
        if p is None:
            continue
        lo, hi = u32(p, 0), u32(p, 4)
        if lo in (0, 0xFFFFFFFF) or hi in (0, 0xFFFFFFFF):
            continue
        mode = pick(MODES, mode_fix(p[8], is_dat))
        cols = comm_columns(p[10:65] + b"\x00")
        row = {"番号": f"{n:02d}", "開始周波数": f"{lo / 1e6:.6f}", "終了周波数": f"{hi / 1e6:.6f}",
               "名前": nm, "モード": mode, "標準機GUI": std_note(mode), "ステップ": pick(STEPS, p[9]),
               "オフセットステップ": "OFFSET" if p[65] else "-",
               "通信設定(現モード)": active_summary(mode, cols)}
        row.update(cols)
        row["CommData(hex)"] = p[10:65].hex().upper()
        scan_rows.append(row)

    # バンクリンク: 1 件 4 バイトのビットマップ（ビット 0〜25 = バンク A〜Z）
    bank_links = []
    blink = plain[BANK_LINK_DATA_LINE]
    for k in range(20 if blink else 0):
        bits = _to_bits(blink[k * 4:(k + 1) * 4], 32)
        if all(bits[26:32]):
            continue
        members = [chr(65 + j) for j in range(26) if bits[j]]
        bank_links.append({"番号": f"{k + 1:02d}", "名前": blink_names[k], "バンク": "".join(members),
                           "リンク内容": ">".join(f"[{bank_names[ord(b) - 65]}]" for b in members)})

    # スキャンリンク: 1 件 8 バイトのビットマップ（ビット 0〜49 = プログラムスキャン 01〜50）
    scan_links = []
    for k in range(20):
        row = plain[SCAN_LINK_DATA_LINES[0] + (k >> 4)]
        if row is None:
            continue
        o = ((k >> 3) & 1) * 64 + (k & 7) * 8
        d = row[o:o + 8]
        if d[7] == 0xFF:
            continue
        members = [j + 1 for j in range(50) if (d[j >> 3] >> (j & 7)) & 1]
        scan_links.append({"番号": f"{k + 1:02d}", "名前": slink_names[k],
                           "PS番号": " ".join(f"{m:02d}" for m in members),
                           "リンク内容": "/".join(f"[{ps_names[m - 1]}]" for m in members)})

    return {"is_dat": is_dat, "lines": nlines, "errors": bad, "plain": plain,
            "mem": mem_rows, "scan": scan_rows, "bank_names": bank_names,
            "bank_links": bank_links, "scan_links": scan_links}


def write_csv(path: str, rows: list) -> None:
    """UTF-8（BOM 付き）で CSV を書く。Excel でそのまま開ける。"""
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


wcsv = write_csv  # 旧名との互換


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="DJ-X100 EditTool の .dan / .dat を復号して CSV に書き出します（非公式ツール）。")
    ap.add_argument("input", help="入力ファイル（.dan または .dat）")
    ap.add_argument("outdir", help="出力フォルダ（なければ作成）")
    ap.add_argument("--format", choices=["dan", "dat"],
                    help="ファイル形式を明示する（省略時は拡張子で判定）")
    ap.add_argument("--no-hex", action="store_true", help="plain_hex.txt を出力しない")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.input):
        print(f"エラー: ファイルが見つかりません: {a.input}", file=sys.stderr)
        return 2
    os.makedirs(a.outdir, exist_ok=True)
    d = decode(a.input, a.format)
    write_csv(os.path.join(a.outdir, "memory_channels.csv"), d["mem"])
    write_csv(os.path.join(a.outdir, "program_scans.csv"), d["scan"])
    write_csv(os.path.join(a.outdir, "bank_links.csv"), d["bank_links"])
    write_csv(os.path.join(a.outdir, "scan_links.csv"), d["scan_links"])
    with open(os.path.join(a.outdir, "banks.json"), "w", encoding="utf-8") as fh:
        json.dump({chr(65 + i): d["bank_names"][i] for i in range(26)}, fh, ensure_ascii=False, indent=1)
    if not a.no_hex:
        with open(os.path.join(a.outdir, "plain_hex.txt"), "w", encoding="utf-8") as fh:
            for p in d["plain"]:
                fh.write((p.hex().upper() if p else "<<復号できません>>") + "\n")

    print(f"形式: {'dat（NXDN対応前）' if d['is_dat'] else 'dan'} / 行数: {d['lines']}")
    print(f"メモリーCH: {len(d['mem'])} 件 / プログラムスキャン: {len(d['scan'])} 件 / "
          f"バンクリンク: {len(d['bank_links'])} 件 / スキャンリンク: {len(d['scan_links'])} 件")
    for e in d["errors"]:
        print(f"警告: {('%d 行目: ' % e['行']) if e['行'] else ''}{e['内容']}", file=sys.stderr)
    print(f"出力先: {os.path.abspath(a.outdir)}")
    return 1 if d["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
