# بسته اجرای آزمایشی Shad AI در GitHub Actions

این بسته بر اساس `app.py` فعلی ساخته شده و فقط پیام متنی خصوصی را پردازش می‌کند. گروه، عکس و PDF هنوز فعال نیستند.

## نکته اجرا
- Workflow هر ۵ دقیقه شروع می‌شود.
- هر اجرا حداکثر ۲۱۰ ثانیه ربات را فعال نگه می‌دارد و سپس `aioshad.stop()` را صدا می‌زند.
- نشست و پایگاه داده کوچکِ شناسه‌های پردازش‌شده با GitHub Actions Cache بین اجراها نگهداری می‌شوند.
- در پایگاه داده فقط HMAC شناسه پیام ذخیره می‌شود، نه متن پیام یا شناسه خام.
- Cron در GitHub تضمین اجرای دقیق در دقیقه مشخص نمی‌دهد؛ ممکن است با تأخیر اجرا شود.

## فایل‌ها
- `app.py`: نسخه فعلی برنامه؛ بدون تغییر در منطق دریافت پیام.
- `github_runner.py`: اجرای زمان‌بندی‌شده، توقف تمیز و جلوگیری پایدار از پاسخ تکراری.
- `session_bootstrap.py`: بازیابی اولیه فایل Session از Secret.
- `.github/workflows/shad-ai.yml`: Workflow زمان‌بندی‌شده.

## Secrets لازم در GitHub
در Repository → Settings → Secrets and variables → Actions → New repository secret این موارد را اضافه کن:

| نام Secret | مقدار |
|---|---|
| `SHAD_PHONE` | شماره حساب شاد با قالب 09xxxxxxxxx |
| `SHAD_SESSION_KEY` | همان کلید رمزنگاری Session که در `.env` محلی استفاده می‌کنی |
| `SHAD_SESSION_FILENAME` | نام دقیق فایل `.session` داخل پوشه sessions، فقط نام فایل |
| `SHAD_SESSION_B64` | محتوای Base64 فایل Session؛ فقط برای اولین اجرا و اگر Cache موجود نباشد |
| `LLM_API_KEY` | کلید Cloudflare Workers AI |
| `LLM_MODEL` | نام مدل مانند `@cf/google/gemma-4-26b-a4b-it` |
| `LLM_BASE_URL` | Base URL حساب Cloudflare با مسیر `/ai/v1` |

هرگز مقدار Secretها را در کد، فایل README یا گفت‌وگو قرار نده.

## تبدیل فایل Session به Base64 در PowerShell
از پوشه اصلی پروژه محلی اجرا کن:

```powershell
$session = Get-ChildItem .\sessions -Filter *.session -File | Select-Object -First 1
if (-not $session) { throw "No .session file found" }
$bytes = [System.IO.File]::ReadAllBytes($session.FullName)
$b64 = [Convert]::ToBase64String($bytes)
Write-Host "SESSION FILENAME: $($session.Name)"
Write-Host "SESSION BASE64 (copy this directly into GitHub Secret SHAD_SESSION_B64):"
Write-Host $b64
```

خروجی Base64 را برای کسی ارسال نکن؛ آن را مستقیماً در GitHub Secret قرار بده.

## شروع Workflow
بعد از قرار دادن فایل‌ها در مخزن و افزودن Secrets:
1. در GitHub وارد زبانه Actions شو.
2. Workflow با نام `Shad AI Assistant` را انتخاب کن.
3. روی `Run workflow` بزن تا اولین اجرای دستی انجام شود.
4. سپس اجراهای زمان‌بندی‌شده هر ۵ دقیقه فعال خواهند شد.

## هشدارهای محدودیت
- این یک استقرار آزمایشی است، نه تضمین سرویس ۲۴ ساعته.
- GitHub ممکن است اجرای زمان‌بندی‌شده را با تأخیر انجام دهد.
- اجرای هر ۵ دقیقه با Runner عمومی در مخزن عمومی برای استفاده رایگان طراحی شده است؛ این مخزن برای این Workflow باید Public باشد.
- فایل Session مانند رمز عبور حساس است. آن را در Git، ZIP عمومی یا پیام‌رسان منتشر نکن. در مخزن عمومی فقط کد قرار بگیرد؛ Session فقط از طریق GitHub Secret و Cache داخلی Actions بازیابی می‌شود.
- `app.py` هنوز تصاویر را نادیده می‌گیرد. بعد از آنکه ساختار پیوست تصویر شاد را از داده خام تأیید کردیم، پشتیبانی تصویر را جداگانه اضافه می‌کنیم.
- بانک Word و عکس معلم هنوز به ربات متصل نشده است.
