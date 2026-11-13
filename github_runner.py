# -*- coding: utf-8 -*-
"""Timed GitHub Actions runner for the existing Shad AI assistant.

The original app.py remains the message-processing implementation. This wrapper
starts its dispatcher in the background, runs for a bounded window, and stops
cleanly so a scheduled GitHub Actions job can finish.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx
import app as bot

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".shad_state"
DB_PATH = STATE_DIR / "processed.sqlite3"
RUN_SECONDS = max(60, min(int(os.getenv("BOT_RUN_SECONDS", "210")), 230))

# Keep the same student-friendly style across every run. No diagnosis or label
# is assigned to any student. The current version is text-only; the question
# bank and image pipeline will be added after the Shad media format is verified.
bot.SYSTEM_PROMPT = """تو یک دستیار آموزشی مهربان و صبور برای دانش‌آموزان پایه‌های اول تا سوم ابتدایی هستی.

قواعد ضروری:
- فقط از فارسی روان، واژه‌های ساده و جمله‌های کوتاه استفاده کن.
- هر بار فقط یک نکته را آموزش بده؛ توضیح را به گام‌های کوچک تقسیم کن.
- در ریاضی، اول یک مثال خیلی ساده و آشنا بزن و بعد پاسخ را مرحله‌به‌مرحله توضیح بده.
- بدون درخواست معلم یا دانش‌آموز، سؤال سخت، چندمرحله‌ای یا گمراه‌کننده مطرح نکن.
- اگر دانش‌آموز درخواست تمرین کرد ولی بانک سؤال تأییدشده در اختیار تو نبود، وانمود نکن که سؤال از فایل معلم است؛ ساده بگو بانک تمرین هنوز در دسترس نیست.
- اگر سؤال یا تصویر (در نسخه فعلی تصویر هنوز پشتیبانی نمی‌شود) مبهم بود، فقط یک پرسش روشن‌کننده بپرس.
- اگر از جواب مطمئن نیستی، حدس را به‌عنوان جواب قطعی ارائه نکن.
- اشتباه دانش‌آموز را با مهربانی اصلاح کن؛ سرزنش، مقایسه یا برچسب‌زدن ممنوع است.
- اطلاعات خصوصی مانند شماره تلفن، نشانی، رمز و کد ورود را نخواه.
- به درخواست‌های نامناسب برای کودک پاسخ محتوایی نده و محترمانه موضوع را عوض کن.
- پاسخ معمولاً حداکثر ۳ تا ۵ جمله کوتاه باشد. برای حل تمرین، هر گام را در یک خط بنویس.
"""


def _db_connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS processed ("
        "digest TEXT PRIMARY KEY, processed_at INTEGER NOT NULL)"
    )
    con.commit()
    return con


def _message_digest(message: Any) -> str | None:
    message_id = str(getattr(message, "id", "") or "").strip()
    if not message_id:
        return None
    chat_guid = str(getattr(message, "chat_guid", "") or "")
    author_guid = str(getattr(message, "author_guid", "") or "")
    # Store only a keyed digest, not raw Shad IDs or student text.
    secret = os.getenv("SHAD_SESSION_KEY", "").encode("utf-8")
    material = f"{chat_guid}|{author_guid}|{message_id}".encode("utf-8")
    return hmac.new(secret, material, hashlib.sha256).hexdigest()


def _already_processed(digest: str) -> bool:
    with _db_connect() as con:
        row = con.execute(
            "SELECT 1 FROM processed WHERE digest = ?", (digest,)
        ).fetchone()
    return row is not None


def _mark_processed(digest: str) -> None:
    now = int(time.time())
    with _db_connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO processed(digest, processed_at) VALUES (?, ?)",
            (digest, now),
        )
        # Keep the state database bounded (approximately the newest 5,000 IDs).
        con.execute(
            "DELETE FROM processed WHERE digest IN ("
            "SELECT digest FROM processed ORDER BY processed_at DESC LIMIT -1 OFFSET 5000)"
        )
        con.commit()


_original_process = bot.process_private_message


async def _process_with_persistent_dedup(message: Any) -> None:
    digest = _message_digest(message)
    if digest and _already_processed(digest):
        return
    await _original_process(message)
    # The original handler sends either a reply or a friendly error reply.
    # Mark only after it returns successfully.
    if digest:
        _mark_processed(digest)


# The registered handler resolves this global function at call time.
bot.process_private_message = _process_with_persistent_dedup


async def main() -> None:
    if bot.LLM_API_KEY and bot.LLM_MODEL and bot.CHAT_COMPLETIONS_URL:
        bot.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(75.0, connect=15.0),
            follow_redirects=True,
        )

    print(f"Shad AI scheduled run starting; window={RUN_SECONDS}s")
    print("Mode: private text messages only; groups and media are disabled.")
    started = False
    try:
        await bot.shad.start_in_background()
        started = True
        print("Shad dispatcher started.")
        await asyncio.sleep(RUN_SECONDS)
    finally:
        if started:
            try:
                await asyncio.wait_for(bot.shad.stop(), timeout=25)
                print("Shad client stopped cleanly; session save requested.")
            except Exception as exc:
                print(f"Shad stop warning: {type(exc).__name__}")
        if bot.http_client is not None:
            try:
                await bot.http_client.aclose()
            except Exception:
                pass
        print("Scheduled run finished.")


if __name__ == "__main__":
    asyncio.run(main())
