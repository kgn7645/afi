"""Gemini（Google AI Studio 無料枠）クライアント。簡易レート制御つき。"""
from __future__ import annotations

import time

from .config import get_settings

# 無料枠のRPM(1分あたりリクエスト)上限に当たらないための最小間隔(秒)
_MIN_INTERVAL = 4.5


class GeminiClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._last_call = 0.0
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            if not self.settings.gemini_ready:
                raise RuntimeError(
                    "GEMINI_API_KEY が未設定です。.env に設定してください。"
                )
            from google import genai  # 遅延import（未インストールでも他機能は動く）

            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)
        self._last_call = time.time()

    def generate(self, prompt: str, *, temperature: float = 0.8, max_retries: int = 3) -> str:
        client = self._ensure_client()
        last_err: Exception | None = None
        for attempt in range(max_retries):
            self._throttle()
            try:
                resp = client.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=prompt,
                    config={"temperature": temperature},
                )
                return (resp.text or "").strip()
            except Exception as e:  # noqa: BLE001
                last_err = e
                # レート/一時エラーは指数バックオフで再試行
                wait = 10 * (attempt + 1)
                if attempt < max_retries - 1:
                    time.sleep(wait)
        raise RuntimeError(f"Gemini生成に失敗しました: {last_err}")

    def generate_grounded(self, prompt: str, *, temperature: float = 0.4,
                          max_retries: int = 2) -> str:
        """Google検索グラウンディング付き生成（Issue #15: 誤生成対策）。

        グラウンディング不可（SDK/モデル非対応・レート等）の場合は
        通常生成にフォールバックする。
        """
        client = self._ensure_client()
        try:
            from google.genai import types

            tool = types.Tool(google_search=types.GoogleSearch())
            config = types.GenerateContentConfig(temperature=temperature, tools=[tool])
            last_err: Exception | None = None
            for attempt in range(max_retries):
                self._throttle()
                try:
                    resp = client.models.generate_content(
                        model=self.settings.gemini_model, contents=prompt, config=config,
                    )
                    return (resp.text or "").strip()
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if attempt < max_retries - 1:
                        time.sleep(10 * (attempt + 1))
            raise last_err if last_err else RuntimeError("grounding失敗")
        except Exception:  # noqa: BLE001
            # 検索グラウンディングが使えない環境では通常生成にフォールバック
            return self.generate(prompt, temperature=temperature)
