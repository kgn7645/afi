"""
カテゴリ比較記事「おすすめN選」を生成（流入B）。Xserverで実行。

  python scripts/make_roundup.py --rakuten 204546 --category "除湿機" --count 5
  python scripts/make_roundup.py --node kitchen/4083001 --category "除湿機"
  python scripts/make_roundup.py --rakuten 402279 --category "タンブラー" --no-wp
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import roundup  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="カテゴリ比較記事（おすすめN選）の生成")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--rakuten", help="楽天ジャンルID")
    g.add_argument("--node", help="Amazon売れ筋ノード（kitchen/4083001 形式）")
    p.add_argument("--category", required=True, help="カテゴリ表示名（タイトルに使う）")
    p.add_argument("--count", type=int, default=5, help="掲載商品数")
    p.add_argument("--no-wp", action="store_true", help="WPへ投稿しない（確認用）")
    p.add_argument("--status", default="draft", choices=["draft", "publish"])
    args = p.parse_args()

    source = {"type": "rakuten", "genre": args.rakuten} if args.rakuten \
        else {"type": "amazon", "node": args.node}
    res = roundup.build_and_post(
        category=args.category, source=source, count=args.count,
        post_to_wp=not args.no_wp, wp_status=args.status)
    if not res.get("ok"):
        print("[roundup] 失敗:", res.get("error"))
        raise SystemExit(1)
    print(f"[roundup] OK: {res['title']}")
    print(f"  商品数: {res['products']}  WP#{res.get('wp_post_id','(未投稿)')}")
    if res.get("edit_link"):
        print("  編集:", res["edit_link"])


if __name__ == "__main__":
    main()
