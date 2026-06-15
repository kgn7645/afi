"""
Issue #3: Amazon売れ筋/検索から商品候補をクロールし、候補プール(スプレッドシート)へ投入。
Xserverのcronで定期実行する想定（日本IPでないとAmazonにブロックされやすい）。

使い方:
  python scripts/crawl_candidates.py            # config.yaml の keywords/nodes をクロール→投入
  python scripts/crawl_candidates.py --print    # 投入せず候補をJSON表示（動作確認）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import amazon_rank, candidates  # noqa: E402
from core.config import get_rules  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="商品候補のクロール→候補プール投入")
    p.add_argument("--print", action="store_true", dest="dry", help="投入せずJSON表示")
    p.add_argument("--limit", type=int, default=0, help="max_totalを上書き")
    args = p.parse_args()

    cfg = get_rules().get("candidates", {})
    keywords = cfg.get("keywords", [])
    nodes = cfg.get("ranking_nodes", [])
    per_source = cfg.get("per_source", 10)
    max_total = args.limit or cfg.get("max_total", 40)

    exclude = set() if args.dry else candidates.known_asins()
    print(f"[crawl] keywords={len(keywords)} nodes={len(nodes)} "
          f"既存除外={len(exclude)}件 …収集中")

    found = amazon_rank.collect(keywords=keywords, nodes=nodes,
                                per_source=per_source, max_total=max_total,
                                exclude_asins=exclude)
    print(f"[crawl] 新規候補 {len(found)} 件")

    if args.dry or not candidates.enabled():
        if not candidates.enabled() and not args.dry:
            print("[crawl] CANDIDATES_WEBHOOK_URL 未設定のため投入せず表示します。")
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return

    ok = candidates.push(found)
    print(f"[crawl] 候補プールへ投入: {'OK' if ok else '失敗'}（{len(found)}件）")


if __name__ == "__main__":
    main()
