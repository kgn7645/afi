"""設定の読み込み（.env と config.yaml を統合）。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Settings:
    """環境変数（.env）由来の設定。"""

    def __init__(self) -> None:
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

        self.wp_base_url = os.getenv("WP_BASE_URL", "").rstrip("/")
        self.wp_username = os.getenv("WP_USERNAME", "")
        self.wp_app_password = os.getenv("WP_APP_PASSWORD", "")
        self.wp_default_status = os.getenv("WP_DEFAULT_STATUS", "draft")

        self.moshimo_placeholder = os.getenv(
            "MOSHIMO_PLACEHOLDER",
            "<!-- START MoshimoAffiliateEasyLink -->\nリンク\n<!-- MoshimoAffiliateEasyLink END -->",
        ).replace("\\n", "\n")

        # もしもアフィリエイトの成果ID（かんたんリンク自動生成に使用）
        _aid = os.getenv("MOSHIMO_AID", "").strip()
        self.moshimo_aid = int(_aid) if _aid.isdigit() else None
        # 楽天ウェブサービス（新OpenAPI）。applicationId(UUID)とaccessKey(pk_)の両方が必要
        self.rakuten_app_id = os.getenv("RAKUTEN_APP_ID", "").strip()
        self.rakuten_access_key = os.getenv("RAKUTEN_ACCESS_KEY", "").strip()
        self.rakuten_affiliate_id = os.getenv("RAKUTEN_AFFILIATE_ID", "").strip()

        # IndexNow（Bing/Yandex等への即時インデックス通知）用キー
        self.indexnow_key = os.getenv("INDEXNOW_KEY", "").strip()
        # キーファイルの設置URL（未指定なら https://<host>/<key>.txt を仮定）
        self.indexnow_key_location = os.getenv("INDEXNOW_KEY_LOCATION", "").strip()

        self.app_host = os.getenv("APP_HOST", "127.0.0.1")
        self.app_port = int(os.getenv("APP_PORT", "8000"))

    @property
    def gemini_ready(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def wordpress_ready(self) -> bool:
        return bool(self.wp_base_url and self.wp_username and self.wp_app_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_rules() -> dict:
    """config.yaml の生成ルールを読む。"""
    path = ROOT / "config.yaml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
