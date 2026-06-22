"""superuniverseoracle の予約投稿を公開（cron・10分毎）。

既存 threads_publish.py は env THREADS_ACCESS_TOKEN ゲートで早期returnするが、
本スクリプトは**そのアカウントのoverridesトークンのみ**を使い、env不要・他アカ非干渉。
scheduled_at<=now の pending（superuniverseoracle・テキストのみ）だけを公開する。

  python scripts/uranai_publish.py            # 期限到来分を最大 limit 件公開
  python scripts/uranai_publish.py --limit 1

安全: account=superuniverseoracle 以外は一切触らない。mode!=live なら何もしない。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import overrides, threads_pipeline as tp  # noqa: E402

ACCOUNT = "superuniverseoracle"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1, help="1回で公開する最大数")
    a = ap.parse_args()

    overrides.load(force=True)
    acc = tp.get_account(ACCOUNT)
    if acc.get("id") != ACCOUNT:
        print(f"[uranai-pub] アカウント {ACCOUNT} 未登録（overrides）。中止")
        return
    if tp.account_publish_mode(acc) != "live":
        print(f"[uranai-pub] {ACCOUNT} は live でない（{tp.account_publish_mode(acc)}）。何もしない")
        return
    if not tp.account_token(acc):
        print(f"[uranai-pub] {ACCOUNT} のトークン未設定。中止")
        return

    q = tp.queue()
    now = int(time.time())
    due = [x for x in q
           if x.get("account") == ACCOUNT
           and x.get("status") == "pending"
           and x.get("scheduled_at", 0) <= now]
    due.sort(key=lambda x: x.get("scheduled_at", 0))
    if not due:
        print("[uranai-pub] 公開対象なし")
        return

    published = 0
    uids: dict = {}
    for item in due:
        if published >= a.limit:
            break
        r = tp._publish_item(item, uids)   # itemを直接更新（qは参照）
        if r.get("ok"):
            published += 1
            print(f"[uranai-pub] ✅ {r['id']} → {r.get('permalink')}")
        else:
            print(f"[uranai-pub] ✗ {r['id']}: {r.get('error')}")
    tp._save("_threads_queue", q[-300:])
    print(f"[uranai-pub] {published}件公開")


if __name__ == "__main__":
    main()
