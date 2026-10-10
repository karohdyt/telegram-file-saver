# Telegram File Saver Bot

> فارسی: [README.fa.md](README.fa.md)

Send any file to your Telegram bot and it is downloaded and stored on your server.
The bot is a plain Python script; only the **local Telegram Bot API server** runs in Docker.
That server removes Telegram's size limits (20 MB download / 50 MB upload on the cloud API), so you can save **files up to 2 GB**.

## Features

- 📥 Saves documents, videos, audio, voice messages, photos, GIFs and video notes
- 💾 **Checks free disk space before receiving a file** and refuses politely if it will not fit
- 📂 Each user can choose the save folder (button or `/path /root/movies`), default `/root/savedfile`
- 🖥 `/server` and `/disk`: OS, CPU, RAM, uptime and usage of every disk
- 📋 `/files`: latest saved files
- 🔄 Inline-button menus, live progress messages, refresh buttons
- 🔒 Private: only the Telegram IDs in `ALLOWED_USER_IDS` can use it
- 🛡 File names are sanitized (no `../`), nothing is ever overwritten, half-written files never appear under the final name

## How it works

```
 you ──► Telegram ──► telegram-bot-api (Docker, 127.0.0.1:8081)
                              │ downloads the file into its data folder
                              ▼
        /var/lib/docker/volumes/telegram-bot-api-data/_data
                              │ bot.py (on the host, as root) MOVES the file
                              ▼
                        /root/savedfile
```

Because the bot runs on the host, the file is simply **moved**. When both folders are on the same disk
(the usual case) this is an instant rename: a 1.5 GB file needs 1.5 GB, not 3 GB.

| Situation | Free space needed for a file of size S |
|---|---|
| API data folder and save folder on the same mount (default) | S (instant rename) |
| Same disk, different mounts (bind mount, btrfs subvolume) | 2 x S while copying |
| Two different disks | S on each disk |

The bot checks the right case automatically before it asks Telegram for the file.
Tip: if your save folder is on another disk, point the API data there too (see the comment in `docker-compose.yml`) to get instant moves.

## Deploy on a server (step by step)

Ubuntu / Debian shown; any Linux server with Docker and Python 3.9+ works.

### 1. Install Docker, Python and git

```bash
curl -fsSL https://get.docker.com | sh
apt update && apt install -y python3 python3-venv python3-pip git
docker compose version          # must print a version (Compose v2)
```

### 2. Get the credentials

| What | Where |
|---|---|
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) -> `/newbot` |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | <https://my.telegram.org> -> *API development tools* -> create an app |

### 3. Download the project and configure it

```bash
git clone https://github.com/karohdyt/telegram-file-saver-bot.git /opt/telegram-file-saver-bot
cd /opt/telegram-file-saver-bot
cp .env.example .env
nano .env
```

Fill in `BOT_TOKEN`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`. Leave `ALLOWED_USER_IDS` empty for now.

### 4. Only if this bot token was used before with the official API

A bot that has talked to `api.telegram.org` must be logged out once before a local server accepts it:

```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/logOut"
```

Telegram then blocks that token on the cloud API for 10 minutes - wait, then continue. (A brand-new bot needs no logOut.)

### 5. Start the Bot API server

```bash
docker compose up -d
docker compose ps                                                     # "running"
docker volume inspect telegram-bot-api-data --format '{{ .Mountpoint }}'
```

The last command must print the same path as `API_HOST_DIR` in `.env`
(`/var/lib/docker/volumes/telegram-bot-api-data/_data` by default). If it differs, fix `API_HOST_DIR`.

### 6. Install the bot

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python bot.py          # test run as root - should log: Started as @yourbot
```

Stop it with `Ctrl+C` after you see the "Started" line.

### 7. Allow yourself

Run the bot again, open it in Telegram and send `/id`. Put the number into `ALLOWED_USER_IDS` in `.env`
(several users: `111,222`), then restart the bot. Send `/start`, then any file - it appears in `/root/savedfile`.

### 8. Run it as a service (starts on boot, restarts on crash)

```bash
cp telegram-file-saver.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now telegram-file-saver
systemctl status telegram-file-saver
```

> The bot must run as **root**: the Bot API data folder lives in `/var/lib/docker`, which only root can read.
> If you cloned somewhere other than `/opt/telegram-file-saver-bot`, edit the two paths in the service file.

### Everyday commands

```bash
journalctl -u telegram-file-saver -f                  # bot logs
systemctl restart telegram-file-saver                 # restart the bot (after editing .env)
docker compose logs -f telegram-bot-api               # API server logs
cd /opt/telegram-file-saver-bot && git pull && ./venv/bin/pip install -r requirements.txt && systemctl restart telegram-file-saver   # update the bot
docker compose pull && docker compose up -d           # update the Bot API image
docker compose down                                   # stop the API server (volume and saved files stay)
```

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` | - | Token from @BotFather (required) |
| `ALLOWED_USER_IDS` | empty | Telegram user IDs allowed to use the bot |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | - | Credentials for the Bot API container (required) |
| `TELEGRAM_API_URL` | `http://localhost:8081` | Bot API server; empty = official cloud API (20 MB) |
| `API_CONTAINER_DIR` | `/var/lib/telegram-bot-api` | Data folder as seen inside the container |
| `API_HOST_DIR` | `/var/lib/docker/volumes/telegram-bot-api-data/_data` | The same folder on the server |
| `DEFAULT_SAVE_PATH` | `/root/savedfile` | Default save folder |
| `MIN_FREE_MB` | `200` | Space that must stay free after saving |
| `SAVE_ROOT` | empty | Optional: restrict users to this base folder |
| `MAX_FILE_MB` | `2000` (local) / `20` (cloud) | Largest accepted file |
| `REQUEST_TIMEOUT` | `1800` | HTTP read timeout in seconds |
| `SETTINGS_FILE` | `./settings.json` | Where per-user settings are stored |

## Commands

| Command | Description |
|---|---|
| `/start` | Main menu |
| `/path [dir]` | Show or change the save folder |
| `/files` | Latest saved files |
| `/disk` | Disk usage |
| `/server` | OS, CPU, RAM, uptime, disks |
| `/id` | Your Telegram user ID |
| `/cancel` | Cancel the current action |
| `/help` | Full help |

## Security notes

- **Never commit `.env`** (it is in `.gitignore`). If a token ever leaks, revoke it in @BotFather (`/revoke`).
- The bot runs as root and writes to your disk, so access is limited to `ALLOWED_USER_IDS`; with an empty list nobody can use it.
- System folders (`/etc`, `/usr`, `/proc`, ...) cannot be chosen as a save folder. Set `SAVE_ROOT` to confine users to one folder.
- The Bot API container is published on `127.0.0.1` only - do not expose port 8081 to the internet.

## Troubleshooting

| Problem | Fix |
|---|---|
| Log: *Telegram rejected the token* | Wrong token, or the bot was not logged out of the cloud API (step 4 + 10 min wait) |
| Log: *Bot API not reachable* forever | `docker compose ps` and `docker compose logs telegram-bot-api` - usually wrong `TELEGRAM_API_ID`/`HASH` |
| Bot answers "no access" | Send `/id`, add the number to `ALLOWED_USER_IDS`, restart the bot |
| "file is too big" | The bot is talking to the cloud API - check `TELEGRAM_API_URL` in `.env` |
| "file not found" after download | `API_HOST_DIR` is wrong - compare with `docker volume inspect telegram-bot-api-data` |
| "Permission denied" | Run the bot as root (the systemd unit does) |
| "Not enough disk space" | Free space or choose another folder; see `/disk` for details |
| Disk filling up | `du -sh /var/lib/docker/volumes/telegram-bot-api-data/_data` - leftovers of failed transfers can be deleted |


