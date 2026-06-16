"""
Issue #3: Amazon売れ筋/検索から商品候補をクロールし、候補プール(スプレッドシート)へ投入。
Xserverのcronで定期実行する想定（日本IPでないとAmazonにブロックされやすい）。

実行状況は設定オーバーライド(WP)の `_crawl_status` に書き出し、Web UI(Render)から
「実行中／完了(採用N件)／失敗」が見えるようにする（Issue: クロール状況の見える化）。

使い方:
  python scripts/crawl_candidates.py            # config.yaml の keywords/nodes をクロール→投入
  python scripts/crawl_candidates.py --print    # 投入せず候補をJSON表示（動作確認）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import amazon_rank, candidates, overrides, ranking_catalog  # noqa: E402
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


def _set_status(**kw) -> None:
    """クロール状況を共有ストア(WP)へ記録。Web UIが /crawl/status で読む。失敗は無視。"""
    try:
        overrides.update({"_crawl_status": kw})
    except Exception:  # noqa: BLE001
        pass


def _reason_label(reason: str) -> str:
    for key in ("価格", "在庫切れ", "大手ブランド", "除外カテゴリ"):
        if key in reason[:8]:
            return key
    return (reason.split(" ")[0] or reason)[:10]


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
    rakuten_genres = cfg.get("rakuten_genres", []) or []
    per_source = cfg.get("per_source", 10)
    max_total = args.limit or cfg.get("max_total", 40)
    season = bool(sel.get("seasonal_boost", True))

    exclude = set() if args.dry else candidates.known_asins()
    started = int(time.time())
    kw_disp = "/".join(keywords) if keywords else "（絞り込み無し）"
    print(f"[crawl] Amazonランキング{len(nodes)}＋参照元{len(source_urls)}＋楽天ジャンル"
          f"{len(rakuten_genres)} を巡回 / 絞り込みKW={kw_disp} 既存除外={len(exclude)}件 …収集中")

    if not args.dry:
        _set_status(state="running", started_at=started, finished_at=0,
                    keywords=len(keywords), nodes=len(nodes), urls=len(source_urls),
                    kept=0, screened=0, pushed=0, message="収集中…")

    report: list[dict] = []
    try:
        found = amazon_rank.collect(
            keywords=keywords, nodes=nodes, source_urls=source_urls,
            rakuten_genres=rakuten_genres,
            per_source=per_source, max_total=max_total,
            exclude_asins=exclude, season=season, report=report)
    except Exception as e:  # noqa: BLE001
        print(f"[crawl] 🔥 失敗: {e}")
        if not args.dry:
            _set_status(state="error", started_at=started, finished_at=int(time.time()),
                        keywords=len(keywords), nodes=len(nodes), urls=len(source_urls),
                        kept=0, screened=len(report), pushed=0, message=str(e)[:200])
        raise

    tally = Counter(_reason_label(r["reason"]) for r in report)
    top_reasons = [f"{k}×{v}" for k, v in tally.most_common(5)]
    print(f"[crawl] 採用 {len(found)} 件 / 選定基準で足切り {len(report)} 件 {top_reasons}")
    for r in report[:12]:
        print(f"   ⛔ {r['reason']}: {r['title']}")

    if args.dry or not candidates.enabled():
        if not candidates.enabled() and not args.dry:
            print("[crawl] CANDIDATES_WEBHOOK_URL 未設定のため投入せず表示します。")
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return

    pushed = 0
    if found:
        ok = candidates.push(found)
        pushed = len(found) if ok else 0
        print(f"[crawl] 候補プールへ投入: {'OK' if ok else '失敗'}（{len(found)}件）")
    else:
        print("[crawl] 新規候補はありませんでした（既出のみ）。")

    _set_status(
        state="done" if pushed else "empty",
        started_at=started, finished_at=int(time.time()),
        keywords=len(keywords), nodes=len(nodes), urls=len(source_urls),
        kept=len(found), screened=len(report), pushed=pushed,
        top_reasons=top_reasons,
        message=(f"{pushed}件を候補プールへ追加" if pushed
                 else "新規候補なし（既出または全て足切り）"))

    # 売れ筋カテゴリのカタログを週1で更新（毎日5amのフル実行に相乗り）
    if not args.if_requested and ranking_catalog.age_days() >= 7:
        try:
            items = ranking_catalog.crawl_catalog()
            if ranking_catalog.update_store(items):
                print(f"[crawl] 売れ筋カタログを更新: {len(items)}件")
            else:
                print("[crawl] カタログ取得が少なく更新見送り（既存維持）")
        except Exception as e:  # noqa: BLE001
            print(f"[crawl] カタログ更新でエラー（継続）: {e}")


if __name__ == "__main__":
    main()
