"""
Issue #16: 自動QA。生成記事の禁止表現（薬機法/誇大）・構成・整形崩れを検査する。

issue = {"level": "error"|"warn", "code": str, "message": str}
- error: 公開を止めるべき重大な問題（アフィリリンク欠落・本文崩壊など）
- warn : 人の確認を促す問題（薬機法疑い・最大級表現・見出し不足など）
"""
from __future__ import annotations

import re

from .config import get_rules
from .models import Article, Product

# 薬機法的にリスクのある効果効能の断定表現（家電/ガジェットでは原則NG）
DEFAULT_NG_KEYWORDS = [
    "治る", "治療", "完治", "病気", "症状が改善", "効能", "医薬品", "医師が推奨",
    "ウイルスを除去", "菌を除去", "除菌効果", "痩せる", "ダイエット効果", "血行が良くなる",
    "肩こりが治", "疲労回復", "免疫力",
]
# 根拠なく使うとリスクの高い最大級・断定表現
DEFAULT_EXAGGERATION = [
    "最高", "日本一", "世界一", "No.1", "ナンバーワン", "業界初", "完全に",
    "絶対に", "100%", "必ず", "唯一",
]
# 構成上あるべき見出しキーワード
DEFAULT_REQUIRED_HEADINGS = ["はじめに", "とは", "レビュー", "比較", "まとめ"]


def _text_from_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "")


def check_article(article: Article, product: Product, rules: dict | None = None) -> list[dict]:
    """記事のQA問題リストを返す（空なら問題なし）。"""
    rules = rules if rules is not None else get_rules()
    qa = rules.get("qa", {})
    ng = qa.get("ng_keywords", DEFAULT_NG_KEYWORDS)
    exa = qa.get("exaggeration_keywords", DEFAULT_EXAGGERATION)
    required = qa.get("required_headings", DEFAULT_REQUIRED_HEADINGS)
    min_chars = rules.get("article", {}).get("min_chars", 6000)

    body = article.body_html or ""
    text = _text_from_html(body)
    issues: list[dict] = []

    # 1) 薬機法・禁止表現（人が確認）
    for kw in ng:
        if kw in text:
            issues.append({"level": "warn", "code": "pharma",
                           "message": f"薬機法リスク表現の疑い: 「{kw}」"})
    # 2) 最大級・断定表現
    for kw in exa:
        if kw in text:
            issues.append({"level": "warn", "code": "exaggeration",
                           "message": f"誇大/最大級表現の疑い: 「{kw}」（根拠が無ければ言い換えを）"})

    # 3) 構成チェック（必須見出し）
    for h in required:
        if h not in body:
            issues.append({"level": "warn", "code": "structure",
                           "message": f"想定の見出しが見当たりません: 「{h}」"})

    # 4) 文字数（下限の8割未満は薄すぎ）
    if len(text) < int(min_chars * 0.8):
        issues.append({"level": "warn", "code": "thin",
                       "message": f"本文が薄い（{len(text)}字 < 目安{min_chars}字の8割）"})

    # 5) アフィリエイトリンクの有無（無ければ収益化されない＝重大）
    #    カード/ボタン/もしも、いずれの形式でも収益導線があればOK
    affiliate_markers = ("amazon-cta-btn", "amazon-card", "affiliate-link", "sponsored")
    if not any(m in body for m in affiliate_markers):
        issues.append({"level": "error", "code": "no_affiliate",
                       "message": "アフィリエイトリンク/ボタンが本文にありません"})

    # 6) 整形崩れ（未変換の強調記号・空タイトル）
    if "**" in body:
        issues.append({"level": "warn", "code": "markdown_leftover",
                       "message": "未変換の強調記号「**」が残っています"})
    if not (article.title or "").strip():
        issues.append({"level": "error", "code": "no_title",
                       "message": "タイトルが空です"})

    return issues


def has_errors(issues: list[dict]) -> bool:
    return any(i.get("level") == "error" for i in issues)


def format_issues(issues: list[dict]) -> list[str]:
    """warnings表示用の文字列リスト。"""
    mark = {"error": "❌", "warn": "⚠"}
    return [f"QA {mark.get(i['level'], '・')} {i['message']}" for i in issues]
