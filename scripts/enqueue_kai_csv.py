"""KAI.csv 由来の書き換え済みつぶやき(kai_rewrites.json)を m2(KAI) のThreadsキューへ投入。

musing(テキストのみ)として、3件/日（8/12/20時 JST・分はランダム）・明日から順に予約。
既存スロットは回避。id=m2-kaicsv-<No> で冪等（再実行で重複しない）。

  python scripts/enqueue_kai_csv.py            # 投入
  python scripts/enqueue_kai_csv.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import overrides, threads_pipeline as tp  # noqa: E402

JST = timezone(timedelta(hours=9))
ACCOUNT = "m2"
HOURS = [8, 12, 20]
SRC = Path("/Users/sou/Documents/アフィリエイト/kai_rewrites.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rewrites = json.loads(SRC.read_text())
    items = sorted(rewrites.items(), key=lambda kv: int(kv[0]))

    overrides.load(force=True)
    q = tp.queue()
    existing_ids = {x.get("id") for x in q}
    taken = {x.get("scheduled_at") for x in q if x.get("account") == ACCOUNT}

    now = datetime.now(JST)
    rnd = random.Random(20260623)
    added = 0
    day = 1  # 明日から
    slot_i = 0
    pending = [(no, t) for no, t in items if f"m2-kaicsv-{no}" not in existing_ids]
    random.Random().shuffle(pending)  # カテゴリが固まらないよう順番をランダム化
    print(f"対象 {len(pending)}件（既存スキップ {len(items)-len(pending)}件）")

    for no, text in pending:
        # 次の空きスロットを探す
        while True:
            h = HOURS[slot_i % 3]
            d = slot_i // 3
            dt = (now + timedelta(days=day + d)).replace(
                hour=h, minute=rnd.randint(0, 59), second=0, microsecond=0)
            ts = int(dt.timestamp())
            slot_i += 1
            if ts not in taken:
                taken.add(ts)
                break
        item = {
            "id": f"m2-kaicsv-{no}", "account": ACCOUNT, "type": "musing",
            "caption": text, "image": "", "images": [], "link": "", "reply": "",
            "scheduled_at": ts, "status": "pending", "created": int(time.time()),
        }
        if a.dry_run:
            print(f"[dry] {item['id']} {datetime.fromtimestamp(ts, JST):%m/%d %H:%M} {text[:34]}")
        else:
            q.append(item)
        added += 1

    if not a.dry_run and added:
        tp._save("_threads_queue", q)
    last = datetime.fromtimestamp(max(taken), JST) if taken else now
    print(f"{'(dry-run) ' if a.dry_run else ''}{added}件をm2キューへ。最終予約: {last:%Y-%m-%d %H:%M}")


if __name__ == "__main__":
    main()
