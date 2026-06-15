"""
Canva Connect API のリフレッシュトークンを取得する対話ヘルパー（Issue #5）。
PKCE付き認可コードフローをローカルで実行し、トークンを取得して保存する。

前提:
  - Canva Developer Portal で Integration を作成済み
  - .env に CANVA_CLIENT_ID / CANVA_CLIENT_SECRET を設定済み
  - Integration の「Redirect URL」に下記と同じURLを登録済み
    （既定: http://127.0.0.1:8080/callback）

使い方（ブラウザが開ける手元のターミナルで実行）:
  python scripts/canva_auth.py
  → ブラウザでCanvaの認可画面 → 許可 → 自動でトークン取得・保存

取得後: refresh_token を .env の CANVA_REFRESH_TOKEN に貼るか、
        data/canva_token.json に保存されたものがそのまま使われる。
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ROOT, get_settings  # noqa: E402

AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
REDIRECT_URI = "http://127.0.0.1:8080/callback"
SCOPES = (
    "asset:write design:content:write design:content:read "
    "design:meta:read brandtemplate:meta:read brandtemplate:content:read"
)
TOKEN_STORE = ROOT / "data" / "canva_token.json"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


class _Handler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    state_expected: str = ""

    def do_GET(self) -> None:  # noqa: N802
        q = urllib.parse.urlparse(self.path)
        if not q.path.startswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(q.query)
        _Handler.code = params.get("code", [None])[0]
        ok = _Handler.code and params.get("state", [""])[0] == _Handler.state_expected
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = "認可に成功しました。ターミナルに戻ってください。" if ok else "認可に失敗しました。"
        self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode())

    def log_message(self, *a) -> None:  # 出力を抑制
        pass


def main() -> None:
    s = get_settings()
    if not (s.canva_client_id and s.canva_client_secret):
        print("❌ .env に CANVA_CLIENT_ID / CANVA_CLIENT_SECRET を設定してください。")
        return

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    _Handler.state_expected = state
    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": s.canva_client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })

    server = http.server.HTTPServer(("127.0.0.1", 8080), _Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    print("ブラウザでCanvaの認可画面を開きます。表示されない場合は以下を手動で開いてください:\n")
    print(auth_url + "\n")
    webbrowser.open(auth_url)
    print("認可待ち... (ブラウザで「許可」を押してください)")

    # handle_request はワンショット。コードが入るまで少し待つ
    import time
    for _ in range(180):
        if _Handler.code:
            break
        time.sleep(1)
    if not _Handler.code:
        print("❌ タイムアウト。Redirect URLの登録（" + REDIRECT_URI + "）を確認してください。")
        return

    basic = base64.b64encode(
        f"{s.canva_client_id}:{s.canva_client_secret}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "code": _Handler.code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"❌ トークン取得失敗 HTTP {resp.status_code}: {resp.text[:300]}")
        return
    data = resp.json()
    refresh = data.get("refresh_token", "")
    TOKEN_STORE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_STORE.write_text(json.dumps({"refresh_token": refresh}))
    print("\n✅ 取得成功！")
    print(f"  refresh_token を {TOKEN_STORE} に保存しました（このまま利用可能）。")
    print("  .env にも残す場合は次を追加:")
    print(f"    CANVA_REFRESH_TOKEN={refresh}")
    print("\n次に config.yaml の canva.enabled: true と brand_template_id を設定してください。")


if __name__ == "__main__":
    main()
