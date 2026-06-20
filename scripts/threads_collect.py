"""Threads商品候補の楽天収集（cron）。各媒体のキーワードで選定リストを補充する。

AI生成なし＝Gemini不使用・無料・速い。記事化（5案生成）は手動（Web /threads/select）で行う。
媒体ごとに account.keywords を使うので、媒体（女性/メンズ等）ごとに別ジャンルが集まる。

  python scripts/threads_collect.py            # threads.enabled=true のとき収集
  python scripts/threads_collect.py --force    # enabled無視で収集（確認用）
  python scripts/threads_collect.py --count 3  # 1媒体あたりの追加上限（既定=各媒体のper_run）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import threads_pipeline  # noqa: E402
from core.config import get_rules  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--count", type=int, default=0, help="1媒体あたりの追加数(0=媒体のper_run)")
    args = p.parse_args()

    tc = get_rules().get("threads", {}) or {}
    if not args.force and not tc.get("enabled", False):
        print("[threads-collect] 無効（threads.enabled=false）")
        return
    total = 0
    for acc in threads_pipeline.accounts():
        cnt = args.count or int(acc.get("per_run", 3))
        try:
            n = threads_pipeline.collect_products_rakuten(acc, cnt)
            print(f"[threads-collect] {acc.get('id')}/{acc.get('name')}: 楽天KW +{n}件")
            total += n
            if acc.get("mens_discovery"):       # メンズ発見ソース(m-cosme/@cosme)も使う媒体
                nd = threads_pipeline.collect_mens_discovery(acc, cnt)
                print(f"[threads-collect] {acc.get('id')}: メンズ発見 +{nd}件")
                total += nd
        except Exception as e:  # noqa: BLE001
            print(f"[threads-collect] {acc.get('id')}: 失敗 {e}")
    print(f"[threads-collect] 合計 +{total}件")


if __name__ == "__main__":
    main()
