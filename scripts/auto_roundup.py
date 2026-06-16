"""
比較記事「おすすめN選」の自動生成（流入B）。cronで毎日回し、refresh_daysで間引く。
config.yaml の roundup.categories を対象に、最近作っていないものから per_run 本生成。

  python scripts/auto_roundup.py            # 設定に従い自動生成
  python scripts/auto_roundup.py --force    # refresh_days無視で1本だけ作る（確認用）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import roundup  # noqa: E402
from core.config import ROOT, get_rules  # noqa: E402

_LOG = ROOT / "data" / ".roundup_log.json"   # {category_key: last_unix_ts}


def _load_log() -> dict:
    try:
        return json.loads(_LOG.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_log(d: dict) -> None:
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        _LOG.write_text(json.dumps(d, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


def _key(c: dict) -> str:
    return f"{c.get('type')}:{c.get('id') or c.get('node')}"


def main() -> None:
    p = argparse.ArgumentParser(description="比較記事の自動生成")
    p.add_argument("--force", action="store_true", help="refresh_days無視で1本だけ作る")
    args = p.parse_args()

    rc = get_rules().get("roundup", {})
    cats = rc.get("categories", []) or []
    if not args.force and not rc.get("enabled", False):
        print("[roundup] 自動生成は無効（roundup.enabled=false）")
        return
    if not cats:
        print("[roundup] 対象カテゴリ未設定（roundup.categories）")
        return

    count = int(rc.get("count", 5))
    per_run = 1 if args.force else int(rc.get("per_run", 1))
    refresh = int(rc.get("refresh_days", 14)) * 86400
    status = rc.get("status", "draft")
    log = _load_log()
    now = time.time()

    # 最後に作ってから古い順に並べ、due なものを per_run 本
    due = sorted(cats, key=lambda c: log.get(_key(c), 0))
    made = 0
    for c in due:
        if made >= per_run:
            break
        if not args.force and now - log.get(_key(c), 0) < refresh:
            continue  # まだ新しい
        source = ({"type": "rakuten", "genre": c["id"]} if c.get("type") == "rakuten"
                  else {"type": "amazon", "node": c["node"]})
        try:
            res = roundup.build_and_post(category=c["name"], source=source,
                                         count=count, post_to_wp=True, wp_status=status)
        except Exception as e:  # noqa: BLE001
            print(f"[roundup] {c['name']}: 失敗 {e}")
            continue
        if res.get("ok"):
            print(f"[roundup] {c['name']}: WP#{res.get('wp_post_id')} {res.get('title','')}")
            log[_key(c)] = now
            made += 1
        else:
            print(f"[roundup] {c['name']}: 失敗 {res.get('error')}")
    _save_log(log)
    print(f"[roundup] 生成 {made} 本")


if __name__ == "__main__":
    main()
