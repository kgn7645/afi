"""
公開済み記事の IndexNow 送信（Issue #17）。
前回実行以降に公開された記事を検知し、IndexNowへ通知する。cronで定期実行する想定。

セットアップ（初回のみ）:
  python index_submit.py --setup     # キー生成＋WPにキーファイル設置＋.envへ追記案内

通常運用:
  python index_submit.py             # 新規公開分をIndexNowへ送信
  python index_submit.py --all       # 直近の公開記事を再送信（状態を無視）

cron例（30分毎）:
  */30 * * * *  cd /path/to/affiliate-automation && .venv/bin/python index_submit.py >> data/index.log 2>&1
"""
from __future__ import annotations

import argparse
import json

from core import indexnow, wordpress
from core.config import ROOT, get_settings

STATE_PATH = ROOT / "data" / "index_state.json"


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"last_date_gmt": ""}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def setup() -> None:
    """IndexNowキーを生成し、ルート設置用のキーファイルを用意する。

    重要: IndexNowはキーファイルの場所より下の階層のURLしか認証しない。
    記事URLはサイト直下のため、キーファイルは必ず「サイトのルート」に置く。
    （WP REST APIではルート直下に置けないため、ここではファイルを用意して案内する）
    """
    s = get_settings()
    key = s.indexnow_key or indexnow.generate_key()
    host = indexnow.host_of(s.wp_base_url)
    loc = indexnow.default_key_location(host, key)

    # 手動アップロード用にローカルへキーファイルを書き出す
    key_file = ROOT / "data" / f"{key}.txt"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(indexnow.key_file_content(key), encoding="utf-8")

    print(f"IndexNowキー: {key}")
    print(f"キーファイル（ローカル）: {key_file}")
    print("\n■ キーファイルを『サイトのルート』に設置してください（必須）:")
    print(f"  {loc}  ← ここで中身 {key} が表示される状態にする")
    print("  方法: サーバーのFTP/ファイルマネージャでドメイン直下に上記txtを置く")
    print("\n■ もしくは（推奨・非属人）: Rank Math等のSEOプラグインのIndexNow機能を有効化")
    print("  → キー設置と公開時の自動送信をプラグインが代行（本スクリプトは不要になる）")
    print("\n.env に以下を追記:")
    print(f"INDEXNOW_KEY={key}")
    print(f"INDEXNOW_KEY_LOCATION={loc}")


def run(submit_all: bool = False) -> None:
    s = get_settings()
    if not s.indexnow_key:
        print("INDEXNOW_KEY 未設定。まず `python index_submit.py --setup` を実行してください。")
        return

    state = _load_state()
    after = "" if submit_all else state.get("last_date_gmt", "")
    posts = wordpress.list_published_since(after)
    if not posts:
        print("送信対象なし（新規公開記事なし）。")
        return

    urls = [p["link"] for p in posts]
    res = indexnow.submit(urls, key=s.indexnow_key, key_location=s.indexnow_key_location)
    print(f"IndexNow送信: {res['count']}件 host={res['host']} status={res['status']} ok={res.get('ok')}")
    for p in posts:
        print(f"  - {p['link']}")

    if res.get("ok"):
        state["last_date_gmt"] = posts[-1]["date_gmt"]
        _save_state(state)
        print(f"状態更新: last_date_gmt={state['last_date_gmt']}")
    else:
        print("⚠ 受理されませんでした（キーファイル設置を確認）:", res.get("body", ""))


def main() -> None:
    p = argparse.ArgumentParser(description="公開記事のIndexNow送信")
    p.add_argument("--setup", action="store_true", help="キー生成＋キーファイル設置")
    p.add_argument("--all", action="store_true", help="状態を無視して直近公開分を再送信")
    args = p.parse_args()
    if args.setup:
        setup()
    else:
        run(submit_all=args.all)


if __name__ == "__main__":
    main()
