# ربات ذخیره‌ساز فایل تلگرام

> English: [README.md](README.md)

هر فایلی برای ربات بفرستید، روی سرور شما دانلود و ذخیره می‌شود.
خود ربات یک اسکریپت ساده‌ی پایتون است و فقط **Bot API سرور لوکال تلگرام** داخل Docker اجرا می‌شود.
این سرور محدودیت‌های حجمی تلگرام (۲۰ مگابایت دانلود / ۵۰ مگابایت آپلود در API رسمی) را برمی‌دارد و می‌توانید **تا ۲ گیگابایت** فایل ذخیره کنید.

## امکانات

- 📥 ذخیره‌ی سند، ویدیو، موزیک، ویس، عکس، گیف و ویدیو‌نوت
- 💾 **بررسی فضای دیسک قبل از دریافت فایل**؛ اگر جا نباشد فایل دریافت نمی‌شود
- 📂 انتخاب پوشه‌ی ذخیره توسط کاربر (با دکمه یا `/path /root/movies`)، پیش‌فرض `/root/savedfile`
- 🖥 دستورهای `/server` و `/disk`: سیستم‌عامل، پردازنده، رم، آپ‌تایم و وضعیت دیسک‌ها
- 📋 `/files`: آخرین فایل‌های ذخیره‌شده
- 🔄 منوی دکمه‌ای، پیام‌های وضعیت زنده و دکمه‌ی بروزرسانی
- 🔒 خصوصی: فقط شناسه‌های داخل `ALLOWED_USER_IDS` اجازه‌ی استفاده دارند
- 🛡 نام فایل پاک‌سازی می‌شود، هیچ فایلی بازنویسی نمی‌شود

## ساختار کار

```
 شما ──► تلگرام ──► telegram-bot-api (داخل Docker، آدرس 127.0.0.1:8081)
                              │ فایل را داخل پوشه‌ی داده‌ی خودش دانلود می‌کند
                              ▼
        /var/lib/docker/volumes/telegram-bot-api-data/_data
                              │ bot.py (روی خود سرور، با root) فایل را «جابه‌جا» می‌کند
                              ▼
                        /root/savedfile
```

چون ربات روی خود سرور اجرا می‌شود، فایل فقط **جابه‌جا** می‌شود. وقتی هر دو پوشه روی یک دیسک باشند (حالت معمول)
این کار یک rename آنی است: فایل ۱٫۵ گیگ فقط ۱٫۵ گیگ فضا می‌خواهد، نه ۳ گیگ.

| وضعیت | فضای خالی لازم برای فایل با حجم S |
|---|---|
| پوشه‌ی داده‌ی API و پوشه‌ی ذخیره روی یک mount (پیش‌فرض) | S (rename آنی) |
| یک دیسک ولی mountهای جدا (bind mount، subvolume در btrfs) | ۲ × S هنگام کپی |
| دو دیسک مختلف | S روی هر دیسک |

ربات قبل از اینکه فایل را از تلگرام بخواهد، حالت درست را خودش تشخیص می‌دهد و بررسی می‌کند.
نکته: اگر پوشه‌ی ذخیره‌ی شما روی دیسک دیگری است، پوشه‌ی داده‌ی API را هم همان‌جا بگذارید
(کامنت داخل `docker-compose.yml`) تا جابه‌جایی آنی باشد.

## آموزش نصب روی سرور (مرحله به مرحله)

مثال برای Ubuntu / Debian است؛ هر سرور لینوکسی با Docker و پایتون ۳٫۹ به بالا کار می‌کند.

### ۱. نصب Docker، پایتون و git

```bash
curl -fsSL https://get.docker.com | sh
apt update && apt install -y python3 python3-venv python3-pip git
docker compose version          # باید یک نسخه چاپ کند (Compose v2)
```

### ۲. گرفتن اطلاعات لازم

| چیست | از کجا |
|---|---|
| `BOT_TOKEN` | از [@BotFather](https://t.me/BotFather) با دستور `/newbot` |
| `TELEGRAM_API_ID` و `TELEGRAM_API_HASH` | ورود به <https://my.telegram.org> ← *API development tools* ← ساخت یک app |

### ۳. دانلود پروژه و تنظیم

```bash
git clone https://github.com/<you>/telegram-file-saver-bot.git /opt/telegram-file-saver-bot
cd /opt/telegram-file-saver-bot
cp .env.example .env
nano .env
```

مقدارهای `BOT_TOKEN`، `TELEGRAM_API_ID` و `TELEGRAM_API_HASH` را پر کنید. فعلاً `ALLOWED_USER_IDS` را خالی بگذارید.

### ۴. فقط اگر قبلاً این توکن با API رسمی تلگرام استفاده شده

رباتی که قبلاً به `api.telegram.org` وصل بوده باید یک بار logOut شود تا سرور لوکال آن را بپذیرد:

```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/logOut"
```

بعد از آن تلگرام ۱۰ دقیقه اجازه‌ی ورود با همان توکن به API رسمی را نمی‌دهد؛ صبر کنید و ادامه دهید.
(رباتِ تازه‌ساخته نیازی به logOut ندارد.)

### ۵. اجرای Bot API سرور

```bash
docker compose up -d
docker compose ps                                                     # باید running باشد
docker volume inspect telegram-bot-api-data --format '{{ .Mountpoint }}'
```

دستور آخر باید همان مسیری را چاپ کند که در `.env` جلوی `API_HOST_DIR` نوشته شده
(پیش‌فرض: `/var/lib/docker/volumes/telegram-bot-api-data/_data`). اگر فرق داشت، `API_HOST_DIR` را اصلاح کنید.

### ۶. نصب ربات

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python bot.py          # اجرای آزمایشی با root - باید بنویسد: Started as @yourbot
```

بعد از دیدن خط «Started» با `Ctrl+C` متوقفش کنید.

### ۷. دادن دسترسی به خودتان

ربات را دوباره اجرا کنید، در تلگرام بازش کنید و `/id` بفرستید. عدد را در `.env` جلوی `ALLOWED_USER_IDS` بگذارید
(چند نفر: `111,222`) و ربات را ری‌استارت کنید. `/start` بزنید و یک فایل بفرستید؛ در `/root/savedfile` ذخیره می‌شود.

### ۸. اجرا به‌صورت سرویس (بعد از ریبوت خودکار بالا می‌آید و اگر کرش کند دوباره اجرا می‌شود)

```bash
cp telegram-file-saver.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now telegram-file-saver
systemctl status telegram-file-saver
```

> ربات باید با **root** اجرا شود، چون پوشه‌ی داده‌ی Bot API داخل `/var/lib/docker` است و فقط root می‌تواند آن را بخواند.
> اگر پروژه را جای دیگری غیر از `/opt/telegram-file-saver-bot` گذاشتید، دو مسیر داخل فایل سرویس را اصلاح کنید.

### دستورهای روزمره

```bash
journalctl -u telegram-file-saver -f                  # لاگ ربات
systemctl restart telegram-file-saver                 # ری‌استارت ربات (بعد از ویرایش .env)
docker compose logs -f telegram-bot-api               # لاگ Bot API سرور
cd /opt/telegram-file-saver-bot && git pull && ./venv/bin/pip install -r requirements.txt && systemctl restart telegram-file-saver   # آپدیت ربات
docker compose pull && docker compose up -d           # آپدیت ایمیج Bot API
docker compose down                                   # توقف Bot API سرور (volume و فایل‌های ذخیره‌شده می‌مانند)
```

## تنظیمات (`.env`)

| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `BOT_TOKEN` | - | توکن ربات (الزامی) |
| `ALLOWED_USER_IDS` | خالی | شناسه‌ی کاربرانی که اجازه‌ی استفاده دارند |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | - | مشخصات کانتینر Bot API (الزامی) |
| `TELEGRAM_API_URL` | `http://localhost:8081` | آدرس Bot API سرور؛ خالی = API رسمی (۲۰ مگابایت) |
| `API_CONTAINER_DIR` | `/var/lib/telegram-bot-api` | پوشه‌ی داده از دید داخل کانتینر |
| `API_HOST_DIR` | `/var/lib/docker/volumes/telegram-bot-api-data/_data` | همان پوشه روی سرور |
| `DEFAULT_SAVE_PATH` | `/root/savedfile` | پوشه‌ی پیش‌فرض ذخیره |
| `MIN_FREE_MB` | `200` | فضایی که بعد از ذخیره باید خالی بماند |
| `SAVE_ROOT` | خالی | اختیاری: محدود کردن کاربران به یک پوشه‌ی پایه |
| `MAX_FILE_MB` | `2000` (لوکال) / `20` (رسمی) | حداکثر حجم فایل |
| `REQUEST_TIMEOUT` | `1800` | تایم‌اوت HTTP به ثانیه |
| `SETTINGS_FILE` | `./settings.json` | محل ذخیره‌ی تنظیمات کاربران |

## دستورها

| دستور | توضیح |
|---|---|
| `/start` | منوی اصلی |
| `/path [dir]` | نمایش یا تغییر پوشه‌ی ذخیره |
| `/files` | آخرین فایل‌های ذخیره‌شده |
| `/disk` | وضعیت دیسک |
| `/server` | سیستم‌عامل، پردازنده، رم، آپ‌تایم، دیسک‌ها |
| `/id` | شناسه‌ی عددی شما |
| `/cancel` | لغو عملیات جاری |
| `/help` | راهنمای کامل |

## نکات امنیتی

- فایل `.env` را **هرگز** در گیت‌هاب نگذارید (در `.gitignore` هست). اگر توکن لو رفت، از @BotFather با `/revoke` باطلش کنید.
- ربات با root اجرا می‌شود و روی دیسک می‌نویسد؛ پس فقط `ALLOWED_USER_IDS` دسترسی دارند و با لیست خالی هیچ‌کس نمی‌تواند استفاده کند.
- مسیرهای سیستمی (`/etc`، `/usr`، `/proc` و ...) قابل انتخاب نیستند. با `SAVE_ROOT` می‌توانید کاربران را به یک پوشه محدود کنید.
- کانتینر Bot API فقط روی `127.0.0.1` منتشر می‌شود؛ پورت ۸۰۸۱ را هرگز به اینترنت باز نکنید.

## رفع اشکال

| مشکل | راه‌حل |
|---|---|
| لاگ: *Telegram rejected the token* | توکن اشتباه است یا ربات از API رسمی logOut نشده (مرحله‌ی ۴ + ۱۰ دقیقه صبر) |
| لاگ: *Bot API not reachable* برای همیشه | `docker compose ps` و `docker compose logs telegram-bot-api` را ببینید؛ معمولاً `TELEGRAM_API_ID`/`HASH` اشتباه است |
| ربات می‌گوید «دسترسی ندارید» | `/id` بزنید، عدد را به `ALLOWED_USER_IDS` اضافه کنید و ربات را ری‌استارت کنید |
| خطای «file is too big» | ربات به API رسمی وصل است؛ `TELEGRAM_API_URL` را در `.env` بررسی کنید |
| «file not found» بعد از دانلود | `API_HOST_DIR` اشتباه است؛ با خروجی `docker volume inspect telegram-bot-api-data` مقایسه کنید |
| «Permission denied» | ربات را با root اجرا کنید (سرویس systemd همین کار را می‌کند) |
| «فضای دیسک کافی نیست» | فضا آزاد کنید یا پوشه‌ی دیگری انتخاب کنید؛ جزئیات در `/disk` |
| پر شدن دیسک | با `du -sh /var/lib/docker/volumes/telegram-bot-api-data/_data` بررسی کنید؛ باقی‌مانده‌ی انتقال‌های ناموفق را می‌توانید پاک کنید |
