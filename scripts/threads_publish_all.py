"""全 live アカウントの予約投稿を公開（cron・10分毎）。

既存 threads_publish.py は env THREADS_ACCESS_TOKEN ゲートで早期returnするが、
本スクリプトは publish_due() を直接呼ぶ＝**各アカウントのoverridesトークン**で公開し、
env不要・全live対象（m2/KAI・superuniverseoracle 等）。draft_only(m1/m3)はpublish_due側でスキップ。

  python scripts/threads_publish_all.py            # due分を最大 --limit 件
  python scripts/threads_publish_all.py --limit 5
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import overrides, threads_pipeline as tp  # noqa: E402


def _load_queue_resilient(retries: int = 4, delay: float = 2.0) -> bool:
    """overrides(WP)読み取りが間欠失敗するため、キューが空なら再取得をリトライ。

    True=キューにpending有り（読めた）。空のままなら False（本当に空 or 連続失敗）。
    pendingが1件でも見えた時点で確定（誤公開防止のため空→非空のみ信頼）。
    """
    for i in range(retries):
        overrides.load(force=True)
        q = tp.queue()
        if any(x.get("status") == "pending" for x in q):
            return True
        if i < retries - 1:
            time.sleep(delay)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5, help="1回で公開する最大数（全アカ合計）")
    a = ap.parse_args()
    if not _load_queue_resilient():
        print("[pub-all] pendingなし（または読み取り連続失敗）")
        return
    results = tp.publish_due(limit=a.limit)
    if not results:
        print("[pub-all] 公開対象なし")
        return
    for r in results:
        if r.get("ok"):
            print(f"[pub-all] ✅ {r['id']} → {r.get('permalink')}")
        else:
            print(f"[pub-all] ✗ {r['id']}: {r.get('error')}")


if __name__ == "__main__":
    main()
