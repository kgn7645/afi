"""
売れ筋カテゴリのカタログ（部門→サブカテゴリ）を手動で取得→共有ストアへ保存。
Xserver(日本IP)で実行。通常は crawl_candidates.py（毎日5am）が週1で自動更新する。

  python scripts/refresh_ranking_catalog.py          # クロール→保存
  python scripts/refresh_ranking_catalog.py --print   # 保存せず一覧表示
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import ranking_catalog  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="売れ筋カテゴリのカタログ更新")
    p.add_argument("--print", action="store_true", dest="dry", help="保存せず表示")
    args = p.parse_args()

    items = ranking_catalog.crawl_catalog()
    by_dept: dict[str, int] = {}
    for it in items:
        by_dept[it["dept"]] = by_dept.get(it["dept"], 0) + 1
    print(f"[catalog] 取得 {len(items)}件")
    for dept, n in by_dept.items():
        print(f"   {dept}: {n}件")

    if args.dry:
        for it in items:
            print(f"   {it['node']:24} {it['name']}")
        return
    print("保存:", "OK" if ranking_catalog.update_store(items) else "見送り（件数不足）")


if __name__ == "__main__":
    main()
