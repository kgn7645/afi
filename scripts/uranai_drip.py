"""superuniverseoracle 年間スケジュールから、当日分(3件)を公開キューへ投入（cron・毎朝）。

年間ファイル data/superuniverseoracle_year.json の「今日(JST)」の3投稿を
_threads_queue に type=musing(テキストのみ・リンクなし) で積む。冪等＝二重投入しない。
実公開は既存の threads_publish.py(publish_due) が scheduled_at 到来分を出す。

  python scripts/uranai_drip.py              # 今日分を投入
  python scripts/uranai_drip.py --date 2026-06-21
  python scripts/uranai_drip.py --days 3     # 今日から3日分まとめて投入（先行ストック用）
  python scripts/uranai_drip.py --dry-run

前提: config threads.accounts に superuniverseoracle を追加＋公開トークン設定＋mode=live。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import threads_pipeline  # noqa: E402

ACCOUNT = "superuniverseoracle"
JST = timezone(timedelta(hours=9))
YEAR_FILE = Path(__file__).resolve().parent.parent / "data" / "superuniverseoracle_year.json"


def _load_year() -> list[dict]:
    if not YEAR_FILE.exists():
        sys.exit(f"[drip] 年間ファイルが無い: {YEAR_FILE}（先に uranai_year_generator.py を実行）")
    return json.loads(YEAR_FILE.read_text())


def _ts(date_str: str, hhmm: str) -> int:
    dt = datetime.strptime(f"{date_str} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    return int(dt.timestamp())


def enqueue_for(date_str: str, *, dry: bool = False) -> int:
    posts = [p for p in _load_year() if p["date"] == date_str]
    if not posts:
        print(f"[drip] {date_str}: 年間ファイルに該当なし")
        return 0
    q = threads_pipeline.queue()
    existing = {x.get("id") for x in q}
    added = 0
    for p in posts:
        pid = f"suo-{date_str}-{p['time'].replace(':', '')}"
        if pid in existing:
            continue
        item = {
            "id": pid,
            "account": ACCOUNT,
            "type": "musing",            # テキストのみ → publish_text で公開
            "caption": p["text"],
            "image": "", "images": [], "link": "", "reply": "",
            "scheduled_at": _ts(date_str, p["time"]),
            "status": "pending",
            "style": p.get("style", ""),
            "created": int(time.time()),
        }
        if dry:
            print(f"[dry] {pid} @{p['time']} ({p.get('style')}): {p['text'][:50]}…")
        else:
            q.append(item)
        added += 1
    if added and not dry:
        # 直近分のみ保持（publish側の300トリムと整合）
        threads_pipeline._save("_threads_queue", q[-300:])
    print(f"[drip] {date_str}: {added}件 {'(dry-run)' if dry else '投入'}")
    return added


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD（既定=今日JST）")
    ap.add_argument("--days", type=int, default=1, help="今日から何日分投入するか")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    start = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else datetime.now(JST).date()
    total = 0
    for i in range(a.days):
        total += enqueue_for((start + timedelta(days=i)).isoformat(), dry=a.dry_run)
    print(f"[drip] 合計 {total}件")


if __name__ == "__main__":
    main()
