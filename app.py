"""Shad private-chat educational assistant using Cloudflare Workers AI via HTTPX.

This version intentionally uses the same direct HTTP endpoint and request shape
as the successful test_cloudflare_v2.py test, instead of the OpenAI SDK.
Groups, images, and PDFs are deliberately disabled in this text-only version.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Deque, Any

import httpx
from dotenv import load_dotenv
from aioshad import Client, filters

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env", override=True)


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("REPLACE_"):
        raise RuntimeError(f"Please set {name} in the .env file")
    return value


SHAD_PHONE = required_env("SHAD_PHONE")
SHAD_SESSION_KEY = required_env("SHAD_SESSION_KEY")
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
ENABLE_GROUPS = os.getenv("ENABLE_GROUPS", "false").strip().lower() == "true"

if bool(LLM_API_KEY) != bool(LLM_MODEL) or bool(LLM_API_KEY) != bool(LLM_BASE_URL):
    raise RuntimeError("Set LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL together in .env, or leave all three empty")

CHAT_COMPLETIONS_URL = ""
if LLM_API_KEY and LLM_BASE_URL:
    if LLM_BASE_URL.endswith("/chat/completions"):
        CHAT_COMPLETIONS_URL = LLM_BASE_URL
    else:
        CHAT_COMPLETIONS_URL = LLM_BASE_URL + "/chat/completions"

shad = Client(
    SHAD_PHONE,
    session_directory=str(APP_DIR / "sessions"),
    session_encryption_key=SHAD_SESSION_KEY,
)

SYSTEM_PROMPT = """تو یک دستیار آموزشی فارسی‌زبان و مهربان برای دانش‌آموزان دبستانی هستی.
با زبان ساده، روشن، صبور و متناسب با سن کودک پاسخ بده.
در سؤال‌های درسی، راه‌حل را مرحله‌به‌مرحله توضیح بده و فقط جواب نهایی را نده.
برای سؤال‌های ساده پاسخ کوتاه و دقیق بده.
اگر سؤال مبهم یا ناقص است، فقط یک سؤال روشن‌کننده بپرس.
اگر مطمئن نیستی، صادقانه بگو و پاسخ حدسی را قطعی بیان نکن.
اطلاعات شخصی، شماره تماس، رمز، کد ورود یا نشانی دانش‌آموز را درخواست نکن.
از موضوعات نامناسب برای کودکان دوری کن.
پاسخ را تا حد امکان در چند جمله کوتاه و خوانا بنویس."""

histories: dict[tuple[str, str], Deque[dict[str, str]]] = {}
last_reply_at: dict[tuple[str, str], float] = {}
seen_message_ids: dict[str, float] = {}
api_semaphore = asyncio.Semaphore(3)
http_client: httpx.AsyncClient | None = None
COOLDOWN_SECONDS = 4.0
MAX_REPLY_CHARS = 2400


def clean_for_match(text: str) -> str:
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    return " ".join(text.lower().split())


def greeting_reply(text: str) -> str | None:
    normalized = clean_for_match(text)
    greetings = {"سلام", "درود", "صبح بخیر", "عصر بخیر", "شب بخیر", "سلام خوبی", "سلام چطوری"}
    if normalized in greetings:
        return "سلام! 😊 خوش اومدی. سؤالت رو بفرست تا با هم بررسیش کنیم."
    return None


def extract_answer(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(p for p in parts if p).strip()
    return ""


def safe_api_error(data: Any) -> str:
    """Return a brief provider error without printing credentials or user prompts."""
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                code = first.get("code", "")
                message = first.get("message", "")
                return f"code={code}; message={str(message)[:300]}"
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or "Provider error")[:300]
    return "Provider returned an unsuccessful response"


async def generate_answer(chat_key: tuple[str, str], user_text: str) -> str:
    if not LLM_API_KEY or not LLM_MODEL or not CHAT_COMPLETIONS_URL:
        return "اتصال شاد برقرار است، اما تنظیمات هوش مصنوعی کامل نیست."
    if http_client is None:
        raise RuntimeError("HTTP client is not initialized")

    history = histories.setdefault(chat_key, deque(maxlen=12))
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *list(history),
        {"role": "user", "content": user_text[:4000]},
    ]
    payload: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": messages,
        "chat_template_kwargs": {"enable_thinking": False, "clear_thinking": True},
        "max_completion_tokens": 512,
        "temperature": 0.2,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    async with api_semaphore:
        response = await http_client.post(CHAT_COMPLETIONS_URL, headers=headers, json=payload)

    try:
        data = response.json()
    except ValueError:
        data = {}

    if not response.is_success:
        print(f"Cloudflare API HTTP {response.status_code}: {safe_api_error(data)}")
        raise RuntimeError(f"Cloudflare API returned HTTP {response.status_code}")

    answer = extract_answer(data if isinstance(data, dict) else {})
    if not answer:
        choices = (data or {}).get("choices") or [] if isinstance(data, dict) else []
        finish = (choices[0] or {}).get("finish_reason") if choices else "unknown"
        print(f"Cloudflare API returned no final text (finish_reason={finish})")
        raise RuntimeError("Model returned no final text")

    history.append({"role": "user", "content": user_text[:4000]})
    history.append({"role": "assistant", "content": answer[:MAX_REPLY_CHARS]})
    return answer[:MAX_REPLY_CHARS]


def is_own_message(message: Any) -> bool:
    raw = getattr(message, "raw", None)
    if isinstance(raw, dict) and raw.get("is_mine") is True:
        return True
    # Some aioshad versions keep sender metadata only in the raw nested message.
    inner = raw.get("message") if isinstance(raw, dict) else None
    if isinstance(inner, dict) and inner.get("is_mine") is True:
        return True
    return False


async def process_private_message(message: Any) -> None:
    if is_own_message(message):
        return

    text = (getattr(message, "text", None) or "").strip()
    if not text:
        return  # This text-only version intentionally ignores media messages.

    message_id = str(getattr(message, "id", "") or "")
    if message_id:
        now_seen = time.monotonic()
        if message_id in seen_message_ids:
            return
        seen_message_ids[message_id] = now_seen
        # Simple bounded cleanup.
        if len(seen_message_ids) > 1000:
            cutoff = now_seen - 600
            for key, timestamp in list(seen_message_ids.items()):
                if timestamp < cutoff:
                    seen_message_ids.pop(key, None)

    author_guid = str(getattr(message, "author_guid", "") or "unknown")
    chat_guid = str(getattr(message, "chat_guid", "") or "unknown")
    chat_key = (chat_guid, author_guid)

    now = time.monotonic()
    previous = last_reply_at.get(chat_key, 0.0)
    if now - previous < COOLDOWN_SECONDS:
        return
    last_reply_at[chat_key] = now

    quick_reply = greeting_reply(text)
    if quick_reply is not None:
        await message.reply(quick_reply)
        return

    try:
        answer = await generate_answer(chat_key, text)
        await message.reply(answer)
    except httpx.TimeoutException:
        print("Cloudflare API request timed out")
        await message.reply("پاسخ کمی طول کشید و زمان درخواست تمام شد. لطفاً دوباره تلاش کن. ⏳")
    except httpx.RequestError as exc:
        print(f"Cloudflare connection error: {type(exc).__name__}")
        await message.reply("ارتباط با سرویس پاسخ‌گویی برقرار نشد. لطفاً کمی بعد دوباره تلاش کن.")
    except Exception as exc:
        print(f"AI request failed: {type(exc).__name__}: {str(exc)[:250]}")
        await message.reply("فعلاً در پاسخ‌گویی مشکل فنی دارم. لطفاً کمی بعد دوباره تلاش کن.")


@shad.on_message(filters.private & filters.text)
async def private_chat_handler(message: Any) -> None:
    await process_private_message(message)


async def main() -> None:
    global http_client
    if LLM_API_KEY and LLM_MODEL and CHAT_COMPLETIONS_URL:
        http_client = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0))
    print("Starting Shad educational assistant (text-only mode)...")
    print("AI mode: enabled" if http_client is not None else "AI mode: disabled")
    print("Private chats only. Groups, photos, and PDFs are disabled.")
    try:
        await shad.start()
    finally:
        if http_client is not None:
            await http_client.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopping...")
