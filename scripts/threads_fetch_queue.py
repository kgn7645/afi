"""@cosme/LIPS 取得待ちキューの処理（cron・日本IPのXserverで実行）。

RenderなどIP制限環境で積まれた @cosme/LIPS の商品URLを、日本IPのこのサーバーが
クロール（商品名・公式画像・口コミ傾向）→ 商品名で楽天を自動マッチ → 商品選定に追加する。
データは overrides(WP共有ページ) 経由なので、追加結果はRenderの /threads/select にも出る。

  python scripts/threads_fetch_queue.py            # 既定 limit 件処理
  python scripts/threads_fetch_queue.py --limit 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import threads_pipeline  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=10, help="1回の最大処理件数")
    args = p.parse_args()

    pending = len(threads_pipeline.fetchqueue())
    if not pending:
        print("[threads-fetch] 取得待ちなし")
        return
    print(f"[threads-fetch] 取得待ち {pending} 件 → 最大 {args.limit} 件処理")
    r = threads_pipeline.process_fetch_queue(limit=args.limit)
    print(f"[threads-fetch] 追加 {r['done']} / 失敗 {r['failed']} / 残り {r['left']} 件")


if __name__ == "__main__":
    main()
