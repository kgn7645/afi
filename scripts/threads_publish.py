"""公開キューのスケジュール公開（cron・5〜20分毎）。

scheduled_at<=now の承認済み投稿を、画像メイン＋リンクをリプライで Threads に公開する。
per_run で1回の最大公開数を制限（一気に出さない＝人間らしく）。

  python scripts/threads_publish.py
要: env THREADS_ACCESS_TOKEN。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import threads_client, threads_pipeline  # noqa: E402
from core.config import get_rules  # noqa: E402


def main() -> None:
    if not threads_client.enabled():
        print("[threads-pub] THREADS_ACCESS_TOKEN 未設定")
        return
    per = int(((get_rules().get("threads", {}) or {}).get("schedule", {}) or {}).get("per_run", 1))
    results = threads_pipeline.publish_due(limit=per)
    if not results:
        print("[threads-pub] 公開対象なし")
        return
    for r in results:
        if r.get("ok"):
            print(f"[threads-pub] ✅ {r['id']} → {r.get('permalink')}")
        else:
            print(f"[threads-pub] ✗ {r['id']}: {r.get('error')}")


if __name__ == "__main__":
    main()
