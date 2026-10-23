#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DJ-X100 メモリーデータ比較ツール

2 つの .dan / .dat ファイルを復号して、メモリーチャンネル・プログラムスキャン・
バンク・リンクの追加／削除／変更を CSV に書き出します。dan_decode.py と同じフォルダに置いてください。

使い方:
    python3 compare.py 旧ファイル 新ファイル 出力フォルダ

本ツールは非公式です。アルインコ株式会社とは関係ありません。
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dan_decode as dd  # noqa: E402

# 比較から除外する列（番号は並べ替えで変わる、他は派生値）
_SKIP = {"番号", "CommData(hex)", "通信設定(現モード)", "標準機GUI", "バンク割り当て状況"}
_ORDER = {"追加": 0, "削除": 1, "変更": 2, "番号移動のみ": 3, "同一": 4}


def _diff(a: dict, b: dict) -> list:
    return [k for k in a if k not in _SKIP and a.get(k) != b.get(k)]


def _match(old: list, new: list, keys: list):
    """行を対応づける。まず keys 全部（周波数＋名前）が一致するもの、次に先頭キー（周波数）だけ一致するもの。"""
    pairs, used_old, used_new = [], set(), set()
    for ks in (keys, keys[:1]):
        idx = collections.defaultdict(list)
        for j, r in enumerate(new):
            if j not in used_new:
                idx[tuple(r[k] for k in ks)].append(j)
        for i, r in enumerate(old):
            if i in used_old:
                continue
            cands = [j for j in idx.get(tuple(r[k] for k in ks), []) if j not in used_new]
            if cands:
                j = cands[0]
                used_old.add(i); used_new.add(j); pairs.append((i, j))
    removed = [i for i in range(len(old)) if i not in used_old]
    added = [j for j in range(len(new)) if j not in used_new]
    return pairs, removed, added


def compare_table(old: list, new: list, keys: list, labels: list) -> list:
    pairs, removed, added = _match(old, new, keys)
    rows = []
    for i, j in pairs:
        a, b = old[i], new[j]
        dif = _diff(a, b)
        status = "変更" if dif else ("番号移動のみ" if a["番号"] != b["番号"] else "同一")
        rows.append({"区分": status, "旧番号": a["番号"], "新番号": b["番号"],
                     **{f: b[f] for f in labels},
                     "変更項目": " | ".join(f"{k}: {a[k]} → {b[k]}" for k in dif)})
    for j in added:
        b = new[j]
        rows.append({"区分": "追加", "旧番号": "", "新番号": b["番号"],
                     **{f: b[f] for f in labels}, "変更項目": ""})
    for i in removed:
        a = old[i]
        rows.append({"区分": "削除", "旧番号": a["番号"], "新番号": "",
                     **{f: a[f] for f in labels}, "変更項目": ""})
    rows.sort(key=lambda r: (_ORDER[r["区分"]], int(r["新番号"] or r["旧番号"])))
    return rows


def compare_banks_links(A: dict, B: dict) -> list:
    rows = []
    for i in range(26):
        if A["bank_names"][i] != B["bank_names"][i]:
            rows.append({"種類": "バンク名", "番号": chr(65 + i),
                         "旧": A["bank_names"][i], "新": B["bank_names"][i]})
    for kind, key in (("バンクリンク", "bank_links"), ("スキャンリンク", "scan_links")):
        da = {r["番号"]: r for r in A[key]}
        db = {r["番号"]: r for r in B[key]}
        for n in sorted(set(da) | set(db)):
            fa = f"{da[n]['名前']} = {da[n]['リンク内容']}" if n in da else "(なし)"
            fb = f"{db[n]['名前']} = {db[n]['リンク内容']}" if n in db else "(なし)"
            if fa != fb:
                rows.append({"種類": kind, "番号": n, "旧": fa, "新": fb})
    return rows


def _stats(D: dict) -> dict:
    m = D["mem"]
    return {"形式": "dat" if D["is_dat"] else "dan", "使用メモリーCH": len(m),
            "プログラムスキャン": len(D["scan"]), "バンクリンク": len(D["bank_links"]),
            "スキャンリンク": len(D["scan_links"]),
            "標準機で非表示になるモードのCH": sum(r["モード"] in dd.UNSUPPORTED_STD for r in m),
            "モード別": dict(collections.Counter(r["モード"] for r in m).most_common()),
            "バンク別": dict(collections.Counter(r["バンク"] for r in m).most_common())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="2 つの DJ-X100 データファイル（.dan / .dat）を比較します（非公式ツール）。")
    ap.add_argument("old", help="旧ファイル")
    ap.add_argument("new", help="新ファイル")
    ap.add_argument("outdir", help="出力フォルダ（なければ作成）")
    a = ap.parse_args(argv)
    for p in (a.old, a.new):
        if not os.path.isfile(p):
            print(f"エラー: ファイルが見つかりません: {p}", file=sys.stderr)
            return 2

    os.makedirs(a.outdir, exist_ok=True)
    A, B = dd.decode(a.old), dd.decode(a.new)
    mem = compare_table(A["mem"], B["mem"], ["周波数", "名前"], ["周波数", "名前", "モード"])
    scan = compare_table(A["scan"], B["scan"], ["開始周波数", "名前"],
                         ["開始周波数", "終了周波数", "名前", "モード"])
    other = compare_banks_links(A, B)
    dd.write_csv(os.path.join(a.outdir, "compare_memory.csv"), mem)
    dd.write_csv(os.path.join(a.outdir, "compare_program_scan.csv"), scan)
    dd.write_csv(os.path.join(a.outdir, "compare_banks_links.csv"), other)

    summary = {
        "旧": _stats(A), "新": _stats(B),
        "メモリー差分": dict(collections.Counter(r["区分"] for r in mem)),
        "プログラムスキャン差分": dict(collections.Counter(r["区分"] for r in scan)),
        "変更項目の内訳": dict(collections.Counter(
            part.split(":")[0] for r in mem if r["区分"] == "変更"
            for part in r["変更項目"].split(" | ")).most_common()),
        "復号エラー": {"旧": A["errors"], "新": B["errors"]},
    }
    with open(os.path.join(a.outdir, "compare_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)

    print(f"メモリー: {summary['メモリー差分']}")
    print(f"プログラムスキャン: {summary['プログラムスキャン差分']}")
    print(f"バンク・リンクの差分: {len(other)} 件")
    print(f"出力先: {os.path.abspath(a.outdir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
