"""桜.csv 由来の書き換え済み(sakura_rewrites.json)を m1(さくら) の承認待ち(ドラフト)へ投入。

type=musing・caption=親投稿・reply=返信詳細文。承認画面で親＋返信を確認・編集して承認すると
キューへ入り、Xserver cronが「親→返信」のスレッドで自動公開する。
id=m1-sakura-<No> で冪等。

  python scripts/import_sakura_drafts.py --dry-run
  python scripts/import_sakura_drafts.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import overrides, threads_pipeline as tp  # noqa: E402

ACCOUNT = "m1"
SRC = Path("/Users/sou/Documents/アフィリエイト/sakura_rewrites.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    data = json.loads(SRC.read_text())
    overrides.load(force=True)
    ds = tp.drafts()
    existing = {d.get("id") for d in ds}
    added = 0
    for no, v in sorted(data.items(), key=lambda kv: int(kv[0])):
        did = f"m1-sakura-{no}"
        if did in existing:
            continue
        item = {
            "id": did, "account": ACCOUNT, "type": "musing",
            "caption": v["main"].strip(), "reply": v["reply"].strip(),
            "images": [], "link": "", "product": "",
            "created": int(time.time()),
        }
        if a.dry_run:
            print(f"[dry] {did} 親:{item['caption'][:30]} / 返:{item['reply'][:30]}")
        else:
            ds.append(item)
        added += 1
    if not a.dry_run and added:
        tp._save("_threads_drafts", ds)
    print(f"{'(dry-run) ' if a.dry_run else ''}{added}件を m1 承認待ちへ投入")


if __name__ == "__main__":
    main()
