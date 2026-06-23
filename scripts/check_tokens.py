"""Threadsトークンの疎通確認（必ずXserver=cronと同一IPで実行すること）。

⚠️ ローカル(自宅)から叩くと、cron(大阪)と自宅(沖縄)の2拠点アクセスになり
「不可能な移動／新デバイス」とMetaに判定されAPIブロックを誘発する。
→ 確認は必ずサーバーで:
  ssh ... 'cd ~/afi && .venv/bin/python scripts/check_tokens.py'
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import overrides, threads_pipeline as tp  # noqa: E402

UA = "ouchibase-threads/1.0 (+https://graph.threads.net)"


def _me(tok: str):
    url = ("https://graph.threads.net/v1.0/me?fields=id,username&access_token="
           + urllib.parse.quote(tok))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()[:120]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:120]
    except Exception as e:  # noqa: BLE001
        return "ERR", str(e)[:100]


def main() -> None:
    overrides.load(force=True)
    for acc in tp.accounts():
        aid = acc.get("id")
        try:
            tok = tp.account_token(acc)
        except Exception:  # noqa: BLE001
            print(f"{aid:22} (トークン無し)")
            continue
        if not tok:
            print(f"{aid:22} (トークン無し)")
            continue
        s, b = _me(tok)
        mark = "OK " if s == 200 else "NG!"
        print(f"{mark} {aid:20} {s} {b[:70]}")


if __name__ == "__main__":
    main()
