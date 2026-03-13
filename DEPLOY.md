# Telegram Bot Deployment Guide

This guide covers deploying the text→image Telegram bot to production.

## Prerequisites

1. **Telegram Bot Token**
   - Chat with [@BotFather](https://t.me/botfather) on Telegram
   - Send `/newbot` and follow the prompts
   - Copy the token (e.g., `6734217659:AAGdb_8AHbvoNpeqTOUrFrLGM-EK3lG7VUc`)

2. **Git repo pushed to GitHub**
   - The Dockerfile is in the root of your repo
   - Fonts are in `fonts/` directory

3. **Deployment platform account**
   - [Render.com](https://render.com) (free tier available)
   - Or: Railway, Fly.io, DigitalOcean, AWS, etc.

---

## Option 1: Deploy to Render (Recommended, Free Tier Available)

Render automatically detects and builds the Dockerfile. Zero configuration needed.

### Steps

1. **Sign up** at [render.com](https://render.com) with GitHub

2. **Create a new Web Service**
   - Click "New +" → "Web Service"
   - Connect your GitHub repo
   - Select the repo containing `Dockerfile`
   - Name: `telegram-bot` (or your choice)
   - Region: Any (default is fine)
   - Branch: `main` (or your default branch)

3. **Configure the environment**
   - Under "Environment", click "Add Environment Variable"
   - Key: `BOT_TOKEN`
   - Value: Paste your token from @BotFather
   - Click "Save"

4. **Build settings**
   - **Build Command**: Leave empty (Render auto-detects Dockerfile)
   - **Start Command**: Leave empty (Dockerfile CMD is used)
   - Render will build from the Dockerfile automatically

5. **Deploy**
   - Click "Create Web Service"
   - Render builds and deploys (1–2 minutes)
   - Logs appear in the Render dashboard
   - Once "deployed", the bot is live 🎉

### Monitoring

- **View logs**: Render dashboard → your service → "Logs"
- **Check status**: Green checkmark = running
- **Auto-redeploy**: Pushing to `main` triggers a new build

### Scaling

- **Free tier**: limited to 0.5 CPU, auto-sleeps after 15 min of inactivity
- **Pro tier** ($7/month): dedicated resources, no auto-sleep

---

## Option 2: Deploy to Railway.app

Railway is also free-tier friendly and has good Docker support.

### Steps

1. **Sign up** at [railway.app](https://railway.app) with GitHub

2. **Create a new project**
   - Click "Create Project"
   - Select your GitHub repo
   - Railway auto-detects the Dockerfile

3. **Set environment variables**
   - Go to "Variables" tab
   - Add `BOT_TOKEN = <your_token>`

4. **Deploy**
   - Railway builds and deploys automatically
   - Logs visible in the Railway dashboard
   - Bot is live once the build completes

---

## Option 3: Deploy Locally with Docker

Test the bot locally before deploying.

### Build

```bash
docker build -t telegram-bot:latest .
```

### Run

```bash
docker run \
  --env BOT_TOKEN="your_token_here" \
  telegram-bot:latest
```

The bot should start polling for messages. Test by sending text in the Telegram chat.

### Stop

```bash
docker stop <container_id>
```

---

## Troubleshooting

### Bot doesn't respond to messages

1. **Check the token is correct**
   - Copy from @BotFather again
   - Ensure no extra spaces

2. **View logs**
   - Render: Dashboard → Logs tab
   - Railway: Logs tab
   - Look for errors like `Conflict: terminated by other getUpdates request`

3. **Restart the bot**
   - Render: Manual redeploy in dashboard
   - Railway: Restart in dashboard

### Image rendering errors

1. **Check ImageMagick is installed**
   - Dockerfile includes `imagemagick` — should be automatic
   - If missing, check Docker build logs

2. **Font not found**
   - Fonts are copied in the Dockerfile from `fonts/`
   - Ensure `fonts/NotoSansKhmer.ttf` and `fonts/NotoEmoji.ttf` exist locally

3. **Pango errors**
   - Dockerfile installs `libpango-1.0-0` and registers fonts via `fc-cache`
   - This is automatic; no user action needed

### Memory/CPU limits exceeded

- Free tier services have limits (Render: 0.5 CPU, Railway: limited)
- Image generation is lightweight; if you hit limits, upgrade the plan

---

## Updating the Bot

1. **Make changes locally**
   - Edit `bot.py`, commit to git
   - Push to `main` branch

2. **Trigger redeploy**
   - Render/Railway auto-detect the push
   - Build and deploy automatically (1–2 minutes)
   - No manual steps needed

---

## Monitoring & Logs

### Render

```
Dashboard → Your Service → Logs
```

Look for:
- `Bot starting — polling …` = Bot is ready
- `ERROR` messages = Check token, network, or image rendering
- `Traceback` = Code error (check logs carefully)

### Railway

```
Dashboard → Logs tab
```

Same log patterns as Render.

---

## Example Deployment Output

**Successful startup (Render logs):**
```
2026-03-13 18:50:46 [INFO] __main__: Bot starting — polling …
2026-03-13 18:50:47 [INFO] Application started
```

**Bot received a text message:**
```
2026-03-13 18:51:12 [INFO] __main__: Generating image  user=123456789  len=42
```

**Image sent successfully:**
```
No logs — just works!
```

---

## Support

- **Telegram bot issues**: Check token in @BotFather, ensure bot is /start'd
- **Deployment issues**: Check service provider logs (Render/Railway dashboard)
- **ImageMagick/Pango issues**: Check Dockerfile is present and build logs look clean
