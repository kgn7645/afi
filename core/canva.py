"""
Issue #5: Canva Connect API でアイキャッチを生成。
ブランドテンプレートに「キャッチコピー＋商品画像」を Autofill → PNG書き出し → DL。

仕組み（公式 Connect API）:
  1. リフレッシュトークンでアクセストークン取得（トークンは回転するため保存）
  2. 商品画像を Asset としてアップロード（非同期→ポーリング）
  3. Autofill ジョブでテンプレに差し込み（非同期→ポーリング）→ design_id
  4. Export ジョブで PNG 書き出し（非同期→ポーリング）→ URL → DL

設定が未完/失敗時は None を返し、呼び出し側は Pillow(eyecatch) にフォールバックする。
セットアップ手順は docs/canva-setup.md を参照。
"""
from __future__ import annotations

import base64
import json
import time

import requests

from .config import ROOT, get_rules, get_settings

_API = "https://api.canva.com/rest/v1"
_TOKEN_URL = f"{_API}/oauth/token"
_TOKEN_STORE = ROOT / "data" / "canva_token.json"
_POLL_INTERVAL = 2.0
_POLL_MAX = 30  # 最大 ~60秒/ジョブ


def _cfg() -> dict:
    return get_rules().get("canva", {})


def available() -> bool:
    """Canva連携が有効かつ必要な設定が揃っていれば True。"""
    s = get_settings()
    return bool(
        _cfg().get("enabled", False)
        and s.canva_client_id and s.canva_client_secret
        and (s.canva_refresh_token or _stored_refresh_token())
        and _cfg().get("brand_template_id")
    )


def _stored_refresh_token() -> str:
    try:
        return json.loads(_TOKEN_STORE.read_text()).get("refresh_token", "")
    except Exception:  # noqa: BLE001
        return ""


def _save_refresh_token(token: str) -> None:
    try:
        _TOKEN_STORE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_STORE.write_text(json.dumps({"refresh_token": token}))
    except Exception:  # noqa: BLE001
        pass


def _access_token() -> str:
    """リフレッシュトークンからアクセストークンを取得（回転トークンは保存）。"""
    s = get_settings()
    refresh = _stored_refresh_token() or s.canva_refresh_token
    basic = base64.b64encode(
        f"{s.canva_client_id}:{s.canva_client_secret}".encode()).decode()
    resp = requests.post(
        _TOKEN_URL,
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("refresh_token"):
        _save_refresh_token(data["refresh_token"])  # 回転に追従
    return data["access_token"]


def _poll(url: str, headers: dict, ok_key: str = "job") -> dict:
    """非同期ジョブを完了までポーリング。完了オブジェクトを返す。"""
    for _ in range(_POLL_MAX):
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        body = r.json()
        job = body.get(ok_key, body)
        status = job.get("status")
        if status in ("success", "completed"):
            return job
        if status in ("failed", "error"):
            raise RuntimeError(f"Canvaジョブ失敗: {job}")
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError("Canvaジョブがタイムアウトしました")


def _upload_asset(token: str, image: bytes, name: str = "product") -> str:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
        "Asset-Upload-Metadata": json.dumps(
            {"name_base64": base64.b64encode(name.encode()).decode()}),
    }
    r = requests.post(f"{_API}/asset-uploads", headers=headers, data=image, timeout=60)
    r.raise_for_status()
    job_id = r.json()["job"]["id"]
    job = _poll(f"{_API}/asset-uploads/{job_id}",
                {"Authorization": f"Bearer {token}"})
    return job["asset"]["id"]


def _autofill(token: str, asset_id: str, headline: str) -> str:
    c = _cfg()
    data = {
        c.get("text_field", "headline"): {"type": "text", "text": headline},
        c.get("image_field", "product"): {"type": "image", "asset_id": asset_id},
    }
    r = requests.post(
        f"{_API}/autofills",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"brand_template_id": c["brand_template_id"], "data": data}, timeout=30,
    )
    r.raise_for_status()
    job_id = r.json()["job"]["id"]
    job = _poll(f"{_API}/autofills/{job_id}", {"Authorization": f"Bearer {token}"})
    return job["result"]["design"]["id"]


def _export_png(token: str, design_id: str) -> bytes:
    r = requests.post(
        f"{_API}/exports",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"design_id": design_id, "format": {"type": "png"}}, timeout=30,
    )
    r.raise_for_status()
    job_id = r.json()["job"]["id"]
    job = _poll(f"{_API}/exports/{job_id}", {"Authorization": f"Bearer {token}"})
    url = job["urls"][0]
    return requests.get(url, timeout=60).content


def build_eyecatch(catch_copy: str, product_image: bytes,
                   *, brand: str = "", site_name: str = "") -> bytes | None:
    """Canvaでアイキャッチを生成。未設定/失敗時は None（呼び出し側でPillowへ）。"""
    if not available() or not (catch_copy or "").strip():
        return None
    try:
        token = _access_token()
        asset_id = _upload_asset(token, product_image, name=brand or "product")
        design_id = _autofill(token, asset_id, catch_copy)
        return _export_png(token, design_id)
    except Exception:  # noqa: BLE001
        return None
