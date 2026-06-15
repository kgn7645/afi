"""設定の読み込み（.env と config.yaml を統合）。"""
from __future__ import annotations

import copy
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
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        # 枠切れ(429)時のフォールバック用 追加キー（別プロジェクト=別の無料枠）
        _extra = [os.getenv("GEMINI_API_KEY_2", "").strip(),
                  os.getenv("GEMINI_API_KEY_3", "").strip()]
        self.gemini_api_keys = [k for k in [self.gemini_api_key, *_extra] if k]
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

        # キュー: Googleスプレッドシートの「ウェブに公開(CSV)」URL（設定時はこちらを優先）
        self.queue_sheet_csv_url = os.getenv("QUEUE_SHEET_CSV_URL", "").strip()
        # 生成実績の書き戻し先: Google Apps Script Web App の URL（Issue #4）
        self.sheet_log_webhook_url = os.getenv("SHEET_LOG_WEBHOOK_URL", "").strip()
        # 運用通知（Slack/Discord等のIncoming Webhook）。失敗/完了を通知（Issue #21）
        self.notify_webhook_url = os.getenv("NOTIFY_WEBHOOK_URL", "").strip()
        self.notify_on_success = os.getenv("NOTIFY_ON_SUCCESS", "true").lower() != "false"
        # 商品候補プール（クローラ書込＋スワイプUI読み書きの共有先 / Issue #3/#12）
        self.candidates_webhook_url = os.getenv("CANDIDATES_WEBHOOK_URL", "").strip()

        # note 非公式API用のセッションCookie（_note_session_v5 の値 / Issue #2）
        self.note_session = os.getenv("NOTE_SESSION", "").strip()
        # Amazonアソシエイトのタグ（例 chance274-22）。note Amazonカード等に使用
        self.amazon_associate_tag = os.getenv("AMAZON_ASSOCIATE_TAG", "").strip()

        # Canva Connect API（アイキャッチ生成 / Issue #5）。OAuthのリフレッシュトークン運用。
        self.canva_client_id = os.getenv("CANVA_CLIENT_ID", "").strip()
        self.canva_client_secret = os.getenv("CANVA_CLIENT_SECRET", "").strip()
        self.canva_refresh_token = os.getenv("CANVA_REFRESH_TOKEN", "").strip()

        # IndexNow（Bing/Yandex等への即時インデックス通知）用キー
        self.indexnow_key = os.getenv("INDEXNOW_KEY", "").strip()
        # キーファイルの設置URL（未指定なら https://<host>/<key>.txt を仮定）
        self.indexnow_key_location = os.getenv("INDEXNOW_KEY_LOCATION", "").strip()

        self.app_host = os.getenv("APP_HOST", "127.0.0.1")
        self.app_port = int(os.getenv("APP_PORT", "8000"))

        # 承認Webアプリ（Issue #12）。REVIEW_PASSWORD未設定なら承認画面は無効。
        self.review_password = os.getenv("REVIEW_PASSWORD", "").strip()
        self.session_secret = os.getenv("SESSION_SECRET", "").strip()
        # 設定/プロンプトの外部オーバーライド（WP保存）。CONFIG_OVERRIDES=0 で無効。
        self.config_overrides = os.getenv("CONFIG_OVERRIDES", "1").lower() not in ("0", "false", "no")

    @property
    def gemini_ready(self) -> bool:
        return bool(self.gemini_api_keys)

    @property
    def wordpress_ready(self) -> bool:
        return bool(self.wp_base_url and self.wp_username and self.wp_app_password)

    @property
    def note_ready(self) -> bool:
        return bool(self.note_session)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def _yaml_rules() -> dict:
    """config.yaml の生成ルール（プロセス内キャッシュ）。"""
    path = ROOT / "config.yaml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_rules() -> dict:
    """config.yaml（デフォルト）＋ Web編集のオーバーライドを deep-merge して返す。

    オーバーライドはWP非公開ページに保存（core/overrides.py・60秒キャッシュ）。
    未設定/取得失敗時は config.yaml のみ。
    """
    base = copy.deepcopy(_yaml_rules())
    try:
        from . import overrides  # 遅延import（循環回避）
        ov = overrides.load()
    except Exception:  # noqa: BLE001
        ov = {}
    return _deep_merge(base, ov) if ov else base
