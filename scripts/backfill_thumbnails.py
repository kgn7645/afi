"""
アイキャッチ未設定の投稿に、本文の最初の画像をアイキャッチ(featured image)として補完。
一覧/SNSのno-image・OGP既定画像を解消する（#42の過去分対応）。

使い方:
  python scripts/backfill_thumbnails.py            # 実行（下書き含む）
  python scripts/backfill_thumbnails.py --dry-run  # 変更せず対象だけ表示
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import wordpress  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="アイキャッチ未設定投稿のサムネ補完")
    p.add_argument("--dry-run", action="store_true", help="変更せず対象を表示")
    args = p.parse_args()

    ok, msg = wordpress.test_connection()
    print(("✅ " if ok else "❌ ") + msg)
    if not ok:
        return

    posts = wordpress.list_posts(fields="id,title,status,featured_media,content")
    fixed = skipped = 0
    for post in posts:
        if post.get("featured_media"):
            continue  # 既にアイキャッチあり
        title = post["title"]["rendered"][:36]
        src = wordpress.first_image_src(post.get("content", {}).get("raw", ""))
        if not src:
            skipped += 1
            print(f"  skip id={post['id']} 画像なし（再生成推奨）: {title}")
            continue
        if args.dry_run:
            print(f"  [dry] id={post['id']} ← {src[:60]}")
            fixed += 1
            continue
        try:
            media = wordpress.upload_image_from_url(src)
            wordpress.set_featured_media(post["id"], media["id"])
            fixed += 1
            print(f"  set  id={post['id']} アイキャッチ={media['id']}: {title}")
        except Exception as e:  # noqa: BLE001
            skipped += 1
            print(f"  fail id={post['id']} {e}")

    print(f"完了: 設定 {fixed}件 / スキップ {skipped}件"
          + ("（dry-run）" if args.dry_run else ""))


if __name__ == "__main__":
    main()
