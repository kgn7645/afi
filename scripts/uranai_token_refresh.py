"""superuniverseoracle の Threads 長命トークンを自動更新（cron・週次）。

Threads長命トークン(60日)は `th_refresh_token` で期限を60日リセットできる
（条件: トークンが24時間以上経過かつ未期限切れ）。本スクリプトは overrides の
account['token'] を読んで更新し、**新トークンを overrides へ書き戻す**（publishの読み元）。
週次で回せば期限切れは起こらない。標準ライブラリのみ＝Xserver pip更新不要。

  python scripts/uranai_token_refresh.py
  python scripts/uranai_token_refresh.py --force   # 残日数に関わらず更新を試みる

出力: 新しい有効期限（日数）。24h未満で更新拒否された場合はその旨ログして正常終了。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import overrides, threads_pipeline as tp  # noqa: E402

ACCOUNT = "superuniverseoracle"
JST = timezone(timedelta(hours=9))
REFRESH_URL = "https://graph.threads.net/refresh_access_token"
# これ以上の残日数があれば更新スキップ（無駄な更新を避ける）。週次cron想定で 50日。
SKIP_IF_DAYS_LEFT_OVER = 50


def _http_get(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=30) as r:  # noqa: S310
        return json.loads(r.read().decode())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    ov = overrides.load(force=True)
    accs = (ov.get("threads", {}) or {}).get("accounts") or []
    acc = next((a for a in accs if a.get("id") == ACCOUNT), None)
    if acc is None:
        print(f"[token] overrides に {ACCOUNT} が無い。中止")
        sys.exit(1)
    tok = (acc.get("token") or "").strip()
    if not tok:
        print(f"[token] {ACCOUNT} のトークン未設定。中止")
        sys.exit(1)

    # 残日数を確認（任意・スキップ判定用）。debug_token不要、refreshのexpires_inで足りる。
    try:
        res = _http_get(REFRESH_URL, {"grant_type": "th_refresh_token", "access_token": tok})
    except Exception as e:  # noqa: BLE001
        print(f"[token] 更新リクエスト失敗: {e}")
        sys.exit(1)

    if "access_token" not in res:
        # 24h未満などの拒否。エラー内容をログして正常終了（cronを赤くしない）
        err = (res.get("error") or {}).get("message", res)
        print(f"[token] 更新されず（まだ早い/一時的の可能性）: {err}")
        sys.exit(0)

    new_tok = res["access_token"]
    exp_s = int(res.get("expires_in", 0))
    exp_date = (datetime.now(JST) + timedelta(seconds=exp_s)).strftime("%Y-%m-%d")
    if new_tok == tok and not args.force:
        print(f"[token] トークン変化なし（残 {exp_s // 86400}日・期限 {exp_date}）")
        sys.exit(0)

    acc["token"] = new_tok
    if overrides.save(ov):
        print(f"[token] ✅ 更新成功 → overrides書き戻し。残 {exp_s // 86400}日（期限 {exp_date}）")
    else:
        print("[token] ✗ overrides保存に失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
