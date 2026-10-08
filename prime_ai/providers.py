"""HTTP adapters and bounded request policy; no guild, database or Discord state."""

from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import quote

import aiohttp

from .errors import AIProviderUnavailable

PROVIDER_NAME = "Google Gemini"
PROVIDER_MODEL = "gemini-3.8-flash"
FALLBACK_PROVIDER_MODEL = "gemini-3.1-flash-lite"
RETRYABLE_PROVIDER_STATUSES = frozenset({408, 425, 500, 502, 503, 504})
FALLBACK_PROVIDER_STATUSES = frozenset({429, 500, 502, 503, 504})


class PollinationsProvider:
    """URL adapter for the existing image command."""

    image_endpoint = "https://image.pollinations.ai/prompt"

    @staticmethod
    def image_url(prompt: str, *, width: int = 800, height: int = 600) -> str:
        value = str(prompt or "").strip()
        if not value:
            raise ValueError("invalid_image_prompt")
        if (
            isinstance(width, bool) or not isinstance(width, int)
            or not 64 <= width <= 2048 or isinstance(height, bool)
            or not isinstance(height, int) or not 64 <= height <= 2048
        ):
            raise ValueError("invalid_image_dimensions")
        return (
            f"{PollinationsProvider.image_endpoint}/{quote(value, safe='')}"
            f"?width={width}&height={height}&nologo=true"
        )


class GeminiProvider:
    """Gemini REST adapter preserving PRIME's messages and generation settings."""

    api_endpoint = "https://generativelanguage.googleapis.com/v1beta/models/"

    @staticmethod
    def _request_body(payload: dict) -> dict:
        messages = payload.get("messages", [])
        system_text = "\n\n".join(
            str(item.get("content", "")).strip()
            for item in messages
            if item.get("role") == "system" and str(item.get("content", "")).strip()
        )
        contents = []
        for item in messages:
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            text = str(item.get("content", "")).strip()
            if not text:
                continue
            gemini_role = "model" if role == "assistant" else "user"
            if contents and contents[-1]["role"] == gemini_role:
                contents[-1]["parts"][0]["text"] += "\n" + text
            else:
                contents.append({"role": gemini_role, "parts": [{"text": text}]})
        body = {
            "contents": contents,
            "generationConfig": {
                "temperature": payload.get("temperature", 0.7),
                "maxOutputTokens": payload.get("max_tokens", 1200),
                "thinkingConfig": {
                    "thinkingLevel": str(payload.get("thinking_level", "medium")).lower(),
                },
            },
        }
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}
        return body

    @staticmethod
    def _text_parts(data: dict) -> list[str]:
        candidates = data.get("candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("invalid_candidates")
        if not candidates:
            return []
        parts = candidates[0].get("content", {}).get("parts", [])
        return [
            part["text"] for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]

    async def complete(
        self, session: aiohttp.ClientSession, payload: dict,
        *, timeout_seconds: int,
    ) -> tuple[str, int | None]:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise AIProviderUnavailable("gemini_api_key_missing")
        model = str(payload.get("model") or PROVIDER_MODEL).strip()
        if not model or len(model) > 100:
            raise AIProviderUnavailable("gemini_model_invalid")
        streaming = bool(payload.get("stream"))
        action = "streamGenerateContent" if streaming else "generateContent"
        url = f"{self.api_endpoint}{quote(model, safe='-._')}:{action}"
        options = {
            "json": self._request_body(payload),
            "headers": {
                "Accept": "text/event-stream" if streaming else "application/json",
                "x-goog-api-key": api_key,
            },
            "timeout": aiohttp.ClientTimeout(
                total=timeout_seconds, connect=min(8, timeout_seconds),
            ),
        }
        if streaming:
            options["params"] = {"alt": "sse"}
        async with session.post(url, **options) as response:
            if response.status != 200:
                raise AIProviderUnavailable(
                    f"gemini_status_{response.status}",
                    retryable=response.status in RETRYABLE_PROVIDER_STATUSES,
                    status_code=response.status,
                )
            try:
                tokens_used = None
                if streaming:
                    chunks = []
                    while True:
                        raw_line = await response.content.readline()
                        if not raw_line:
                            break
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line.startswith("data:"):
                            continue
                        content = line[5:].strip()
                        if content == "[DONE]":
                            break
                        try:
                            event = json.loads(content)
                            chunks.extend(self._text_parts(event))
                            usage = event.get("usageMetadata", {})
                            if isinstance(usage, dict):
                                tokens_used = usage.get("totalTokenCount", tokens_used)
                        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                            # Ignore malformed SSE events, not transport failures.
                            continue
                    answer = "".join(chunks)
                else:
                    data = await response.json(content_type=None)
                    if not isinstance(data, dict):
                        raise AIProviderUnavailable("gemini_invalid_response")
                    candidates = data.get("candidates", [])
                    if not isinstance(candidates, list) or not candidates:
                        raise AIProviderUnavailable("gemini_no_text_candidate")
                    answer = "".join(self._text_parts(data))
                    usage = data.get("usageMetadata", {})
                    if isinstance(usage, dict):
                        tokens_used = usage.get("totalTokenCount")
            except (AIProviderUnavailable, aiohttp.ClientError):
                raise
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
                raise AIProviderUnavailable("gemini_invalid_response") from error
        return answer.strip(), tokens_used


async def complete_with_retries(
    session, payload: dict, *, provider: GeminiProvider,
    timeout_seconds: int, retry_count: int,
) -> tuple[str, int | None]:
    retries = max(0, min(int(retry_count), 1))
    for attempt in range(retries + 1):
        try:
            return await provider.complete(
                session, payload, timeout_seconds=timeout_seconds,
            )
        except AIProviderUnavailable as error:
            if not error.retryable or attempt >= retries:
                raise
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt >= retries:
                raise
        await asyncio.sleep(min(0.5 * (2 ** attempt), 2.0))
    raise AIProviderUnavailable("provider_request_failed")


async def request_with_fallback(
    session, payload: dict, *, timeout_seconds: float, retry_count: int,
    complete, logger,
) -> tuple[str, int | None]:
    """One total deadline for primary, retry backoff and same-provider fallback."""
    async with asyncio.timeout(timeout_seconds):
        try:
            return await complete(
                session, payload, timeout_seconds=timeout_seconds,
                retry_count=retry_count,
            )
        except AIProviderUnavailable as primary_error:
            if (
                primary_error.status_code not in FALLBACK_PROVIDER_STATUSES
                or payload["model"] == FALLBACK_PROVIDER_MODEL
            ):
                raise
            logger.warning(
                "[AI] Gemini model %s stayed unavailable; retrying with same-provider model %s.",
                payload["model"], FALLBACK_PROVIDER_MODEL,
            )
            payload["model"] = FALLBACK_PROVIDER_MODEL
            return await complete(
                session, payload, timeout_seconds=timeout_seconds, retry_count=0,
            )
