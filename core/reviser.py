"""
差し戻し（リライト）機能。レビュー画面で修正項目をチェック→差し戻し→記事をリライト。

修正項目（チェックボックス）はQA指摘とも連動（薬機法/薄い/構成 等を推奨チェック）。
リライトはGeminiで本文だけ改善し、商品カード/画像/アフィリンクは保持する。
安全基準（アフィリ要素・画像が減っていない/極端に短くない）を満たした時だけWPを更新。
"""
from __future__ import annotations

import re

from . import prompts, qa, wordpress
from .models import Article, Product

# (key, ラベル, AIへの指示)。10項目＋自由記入。
REVISE_OPTIONS: list[tuple[str, str, str]] = [
    ("pharma", "薬機法・誇大表現の修正",
     "薬機法・景表法に触れる効果効能の断定（治る/シミが消える/必ず/No.1等）を削除し、認められた範囲＋個人の感想に言い換える"),
    ("longer", "内容を厚くする（増量）",
     "薄いセクションに具体例・数字・生活シーンを足して内容を厚くし、本文の文字数下限を満たす"),
    ("reviews", "口コミを自然・具体的に",
     "良い/気になる口コミを、使用期間・場面・具体的な数字感を含む実体験風に書き直す"),
    ("intro", "導入を魅力的に",
     "冒頭の導入を、読者の悩み・生活シーンから入る引き込む書き出しに直す（比喩を1つ）"),
    ("compare", "大手比較を充実",
     "大手メーカーとの比較を、価格・機能・サポート等の具体的な観点で厚くする"),
    ("merit", "メリット/デメリットを具体化",
     "メリット・デメリットを根拠とともに掘り下げる。デメリットも正直に書いて信頼を得る"),
    ("persona", "おすすめ層を明確化",
     "どんな人に向くか／避けるべきかを言い切る（ペルソナを明確に）"),
    ("dedup", "冗長・重複を整理",
     "重複・冗長な表現や未変換の記号（**等）を削り、読みやすく整える"),
    ("headings", "見出し構成を整える",
     "見出しの粒度・順序を整え構成を分かりやすくする。想定の見出しが欠けていれば補う"),
    ("summary", "まとめを明確に",
     "まとめで『買うべき人／避けるべき人』を明確に提示して締める"),
]

# QA指摘コード → 推奨チェックするキー
_QA_TO_KEY = {
    "pharma": "pharma", "exaggeration": "pharma", "thin": "longer",
    "structure": "headings", "markdown_leftover": "dedup",
}

_MARKERS = ("amazon-cta-btn", "amazon-card", "affiliate-link", "msmaflink")


def recommended_keys(qa_issues: list[dict] | None) -> set[str]:
    """QA指摘から、最初からチェックしておく修正キーを返す。"""
    keys: set[str] = set()
    for it in (qa_issues or []):
        k = _QA_TO_KEY.get(it.get("code", ""))
        if k:
            keys.add(k)
    return keys


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _is_safe(orig: str, new: str) -> tuple[bool, str]:
    """リライト結果がアフィリ要素/画像を壊していないか検査。"""
    if len(new) < len(orig) * 0.4:
        return False, "本文が大幅に短くなりました"
    for m in _MARKERS:
        if orig.count(m) > new.count(m):
            return False, f"アフィリエイト要素が減少（{m}）"
    if orig.count("<img") > new.count("<img"):
        return False, "画像が減少しました"
    return True, ""


def revise_post(post_id: int, keys: set[str], note: str = "") -> tuple[bool, str]:
    """選択した修正項目で記事をリライトし、WP下書きを更新。 (ok, メッセージ)。"""
    from .gemini_client import GeminiClient

    instrs = [opt[2] for opt in REVISE_OPTIONS if opt[0] in keys]
    if note and note.strip():
        instrs.append(note.strip())

    try:
        p = wordpress.get_post(post_id, fields="id,title,content")
        title = ((p.get("title", {}) or {}).get("raw")
                 or (p.get("title", {}) or {}).get("rendered", ""))
        body = (p.get("content", {}) or {}).get("raw", "")
    except Exception as e:  # noqa: BLE001
        return False, f"記事の取得に失敗: {e}"
    if not body:
        return False, "本文が空のためリライトできません"

    # 現在のQA指摘を“必ず解消する”修正対象として明示的に渡す（汎用指示だけだと直らない）
    qa_msgs = [i["message"] for i in qa.check_article(Article(title=title, body_html=body), Product())]
    if not instrs and not qa_msgs:
        return False, "修正項目が選択されていません"

    blocks = []
    if instrs:
        blocks.append("【指定の修正方針】\n" + "\n".join(f"- {x}" for x in instrs))
    if qa_msgs:
        blocks.append("【QAで検出された問題（必ず解消すること）】\n"
                      + "\n".join(f"- {m}" for m in qa_msgs))
    instr_text = "\n\n".join(blocks)
    try:
        gem = GeminiClient()
        out = gem.generate(
            prompts.revise_article_prompt(title, body, instr_text), temperature=0.7)
    except Exception as e:  # noqa: BLE001
        return False, f"リライトに失敗（Gemini未設定/枠切れ等）: {e}"

    out = _strip_fences(out)
    ok, why = _is_safe(body, out)
    if not ok:
        return False, f"リライト結果が安全基準を満たさず中止（元のまま）: {why}"

    try:
        wordpress.update_post_content(post_id, out)
        wordpress.set_post_status(post_id, "draft")
    except Exception as e:  # noqa: BLE001
        return False, f"WP更新に失敗: {e}"
    return True, f"リライトしました（{len(instrs)}項目）。内容を再確認してください"
