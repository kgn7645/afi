"""媒体（メディア）プロファイル。複数サイトへの並行量産（#95）の基盤。

各媒体に「WP接続・コンセプト・E-E-A-T・カテゴリ・クロール語・アフィリタグ」を束ねる。
現状の単一サイト(ouchibase)は media[0] として後方互換で扱う。

設計方針:
- 非機密の媒体設定（名前/コンセプト/クロール語など）は config.yaml の `media:` ＋
  Web設定オーバーライドで編集可。
- 機密（WPアプリパスワード等）は **env** に置く。媒体ごとに env_prefix で分ける:
    media #1: env_prefix=""   → WP_BASE_URL / WP_USERNAME / WP_APP_PASSWORD（既存）
    media #2: env_prefix="WP2_" → WP2_BASE_URL / WP2_USERNAME / WP2_APP_PASSWORD
  （config.yaml は基本コミットされるため、パスワードは置かない）

この時点では本モジュールは「読み取り専用の構成」。実際の投稿先切替（wordpress.py の
媒体対応）や並行バッチは、2サイト目が用意できてから配線する（docs/issue-95-multi-media.md）。
"""
from __future__ import annotations

import contextvars
import os
from dataclasses import dataclass, field

from .config import get_rules, get_settings


@dataclass
class Media:
    id: str
    name: str
    enabled: bool = True
    kind: str = "wp"               # wp | note | x（将来）
    env_prefix: str = ""           # WP機密のenv接頭辞（""=既存のWP_*）
    concept: str = ""              # site_concept（生成のフレーミング）
    eeat: dict = field(default_factory=dict)        # site_name/author/profile_slug等
    candidates: dict = field(default_factory=dict)  # keywords/ranking_nodes/rakuten_genres/source_urls
    affiliate: dict = field(default_factory=dict)   # 媒体別タグ（空なら全体設定を流用）
    note_enabled: bool = False

    # --- WP接続（envから解決。config.yamlには置かない） ---
    @property
    def wp_base_url(self) -> str:
        return os.getenv(f"{self.env_prefix}WP_BASE_URL", "").rstrip("/")

    @property
    def wp_username(self) -> str:
        return os.getenv(f"{self.env_prefix}WP_USERNAME", "")

    @property
    def wp_app_password(self) -> str:
        return os.getenv(f"{self.env_prefix}WP_APP_PASSWORD", "")

    @property
    def wp_ready(self) -> bool:
        return bool(self.wp_base_url and self.wp_username and self.wp_app_password)


def _media1_from_legacy() -> Media:
    """`media:` 未定義時の後方互換: 現状の config.yaml トップレベルから媒体#1を合成。"""
    r = get_rules()
    eeat = r.get("eeat", {}) or {}
    return Media(
        id="ouchibase",
        name=eeat.get("site_name", "おうちベース"),
        enabled=True,
        kind="wp",
        env_prefix="",  # 既存の WP_BASE_URL 等
        concept=eeat.get("site_concept", ""),
        eeat=eeat,
        candidates=r.get("candidates", {}) or {},
        affiliate=r.get("affiliate", {}) or {},
        note_enabled=bool((r.get("note", {}) or {}).get("enabled", False)),
    )


def load_media(*, only_enabled: bool = False) -> list[Media]:
    """媒体一覧。#1は既存のトップレベルconfig(=ouchibase)、#2以降は `media:` の追加定義。

    こうすることで既存設定を二重化せず、`media:` には増やす媒体だけ書けばよい。
    """
    media = [_media1_from_legacy()]  # #1 = 既存サイト（後方互換・常に有効）
    for m in get_rules().get("media") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        media.append(Media(
            id=str(m["id"]),
            name=m.get("name", m["id"]),
            enabled=bool(m.get("enabled", True)),
            kind=m.get("kind", "wp"),
            env_prefix=m.get("env_prefix", ""),
            concept=m.get("concept", ""),
            eeat=m.get("eeat", {}) or {},
            candidates=m.get("candidates", {}) or {},
            affiliate=m.get("affiliate", {}) or {},
            note_enabled=bool(m.get("note_enabled", False)),
        ))
    return [m for m in media if m.enabled] if only_enabled else media


def get_media(media_id: str) -> Media | None:
    for m in load_media():
        if m.id == media_id:
            return m
    return None


# --- アクティブ媒体（contextvar）。投稿先切替の配線で使う（現状はデフォルト=既存設定） ---
_active: contextvars.ContextVar[Media | None] = contextvars.ContextVar("active_media", default=None)


def set_active(media: Media | None) -> None:
    _active.set(media)


def active() -> Media | None:
    return _active.get()


def wp_target() -> tuple[str, str, str]:
    """現在アクティブな媒体のWP接続(base, user, pass)。未設定なら従来の env(get_settings)。"""
    m = _active.get()
    if m is not None and m.wp_ready:
        return m.wp_base_url, m.wp_username, m.wp_app_password
    s = get_settings()
    return s.wp_base_url, s.wp_username, s.wp_app_password
