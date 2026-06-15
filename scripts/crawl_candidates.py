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

from core import amazon_rank, candidates, overrides  # noqa: E402
from core.config import ROOT, get_rules  # noqa: E402

_MARKER = ROOT / "data" / ".crawl_request"


def _crawl_requested() -> bool:
    """Web設定からの手動クロール要求が前回処理時刻より新しければ True（処理済みに更新）。"""
    req = int(overrides.load(force=True).get("_crawl_request", 0) or 0)
    last = 0
    try:
        last = int(_MARKER.read_text().strip())
    except Exception:  # noqa: BLE001
        last = 0
    if req <= last:
        return False
    try:
        _MARKER.parent.mkdir(parents=True, exist_ok=True)
        _MARKER.write_text(str(req))
    except Exception:  # noqa: BLE001
        pass
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="商品候補のクロール→候補プール投入")
    p.add_argument("--print", action="store_true", dest="dry", help="投入せずJSON表示")
    p.add_argument("--limit", type=int, default=0, help="max_totalを上書き")
    p.add_argument("--if-requested", action="store_true", dest="if_requested",
                   help="Web設定から手動クロール要求があった時のみ実行（cron用）")
    args = p.parse_args()

    if args.if_requested and not _crawl_requested():
        print("[crawl] 手動クロール要求なし。終了")
        return

    rules = get_rules()
    cfg = rules.get("candidates", {})
    sel = rules.get("selection", {})
    keywords = cfg.get("keywords", [])
    nodes = cfg.get("ranking_nodes", [])
    source_urls = cfg.get("source_urls", []) or []
    per_source = cfg.get("per_source", 10)
    max_total = args.limit or cfg.get("max_total", 40)
    season = bool(sel.get("seasonal_boost", True))

    exclude = set() if args.dry else candidates.known_asins()
    sk = amazon_rank.seasonal_keywords() if season else []
    print(f"[crawl] keywords={len(keywords)} nodes={len(nodes)} urls={len(source_urls)} "
          f"季節={','.join(sk) or '無'} 既存除外={len(exclude)}件 …収集中")

    report: list[dict] = []
    found = amazon_rank.collect(keywords=keywords, nodes=nodes, source_urls=source_urls,
                                per_source=per_source, max_total=max_total,
                                exclude_asins=exclude, season=season, report=report)
    print(f"[crawl] 採用 {len(found)} 件 / 選定基準で足切り {len(report)} 件")
    for r in report[:12]:
        print(f"   ⛔ {r['reason']}: {r['title']}")

    if args.dry or not candidates.enabled():
        if not candidates.enabled() and not args.dry:
            print("[crawl] CANDIDATES_WEBHOOK_URL 未設定のため投入せず表示します。")
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return

    if not found:
        print("[crawl] 新規候補はありませんでした（既出のみ）。")
        return
    ok = candidates.push(found)
    print(f"[crawl] 候補プールへ投入: {'OK' if ok else '失敗'}（{len(found)}件）")


if __name__ == "__main__":
    main()
