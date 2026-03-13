"""
Telegram Text → Image Bot
=========================

SETUP INSTRUCTIONS:
-------------------
1. Get your BOT_TOKEN from @BotFather on Telegram:
   - Start a chat with @BotFather
   - Send /newbot and follow the prompts
   - Copy the token you receive

2. Install dependencies:
   pip install "python-telegram-bot[all]>=21.0" Pillow python-dotenv Wand

3. System requirements (provided by Replit):
   - ImageMagick 7+ with Pango support
   - NotoSansKhmer.ttf + NotoEmoji.ttf in ~/.fonts/ (auto-setup on first run)

4. Set your token:
   - Create a .env file with:  BOT_TOKEN=your_token_here
   - OR:  export BOT_TOKEN=your_token_here

5. Run:
   python bot.py
"""

from __future__ import annotations

import html
import io
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ── Environment ───────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_TEXT_LENGTH = 1500
IMAGE_WIDTH     = 1000
PADDING         = 60
WATERMARK       = "Lim Sovannrady"

BACKGROUNDS: list[tuple[int, int, int]] = [
    (245, 245, 248),
    (240, 245, 255),
    (245, 255, 245),
    (255, 248, 240),
    (248, 240, 255),
]

TEXT_COLOR      = (30, 30, 30)
ACCENT_COLOR    = (100, 120, 200)
WATERMARK_COLOR = (160, 160, 170)

_here = Path(__file__).parent
_FONTS_SETUP_DONE = False


# ── One-time font setup ───────────────────────────────────────────────────────

def _setup_fonts() -> None:
    """Copy bundled fonts to ~/.fonts so Pango/fontconfig can find them."""
    global _FONTS_SETUP_DONE
    if _FONTS_SETUP_DONE:
        return
    user_fonts = Path.home() / ".fonts"
    user_fonts.mkdir(parents=True, exist_ok=True)
    fonts_dir = _here / "fonts"
    copied = []
    for ttf in fonts_dir.glob("*.ttf"):
        dest = user_fonts / ttf.name
        if not dest.exists():
            import shutil
            shutil.copy2(ttf, dest)
            copied.append(ttf.name)
    if copied:
        subprocess.run(["fc-cache", "-f", str(user_fonts)], capture_output=True)
        logger.info("Registered fonts: %s", copied)
    _FONTS_SETUP_DONE = True


# ── Pango text rendering via ImageMagick ──────────────────────────────────────

def _pango_render_text(
    text: str,
    font_size: int,
    text_color: tuple[int, int, int],
    max_width_px: int,
) -> Image.Image:
    """
    Render *text* using ImageMagick's Pango backend.
    Returns a PIL RGBA image of the rendered text (transparent background).
    Pango + HarfBuzz handles Khmer, Latin, emoji, and all complex scripts.
    """
    r, g, b = text_color
    color_hex = f"#{r:02x}{g:02x}{b:02x}"

    # Escape text for Pango XML markup
    safe = html.escape(text)

    # Pango markup: set font family and size; let Pango handle script detection
    markup = (
        f"<span font='Noto Sans Khmer, Noto Emoji {font_size}' "
        f"color='{color_hex}'>{safe}</span>"
    )

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = [
            "magick",
            "-size", f"{max_width_px}x",
            "-background", "none",
            f"pango:{markup}",
            "-trim",         # remove excess transparent border
            "+repage",
            tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env={**os.environ, "HOME": str(Path.home())})
        if result.returncode != 0:
            logger.error("magick pango error (rc=%d): stderr=%s stdout=%s", result.returncode, result.stderr, result.stdout)
            raise RuntimeError(f"ImageMagick pango failed (rc {result.returncode}): {result.stderr[:200]}")
        img = Image.open(tmp_path).convert("RGBA")
        logger.debug("pango rendered text: %s → %sx%s", text[:30], img.width, img.height)
        return img
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _pick_font_size(text: str, max_width: int, max_height: int) -> int:
    """Find the largest font size where rendered text fits within the box."""
    for size in range(56, 17, -2):
        img = _pango_render_text(text, size, (0, 0, 0), max_width)
        if img.height <= max_height:
            return size
    return 18


# ── PIL watermark + fallback font ────────────────────────────────────────────

def _pil_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        str(Path.home() / ".fonts" / "NotoSansKhmer.ttf"),
        str(_here / "fonts" / "NotoSansKhmer.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ── Full image composition ────────────────────────────────────────────────────

def _pick_background(text: str) -> tuple[int, int, int]:
    return BACKGROUNDS[sum(ord(c) for c in text) % len(BACKGROUNDS)]


def generate_image(text: str) -> bytes:
    """
    Render *text* into a styled PNG image and return the bytes.

    Pipeline:
    1. ImageMagick + Pango renders the text with correct Khmer shaping.
    2. Pillow composites the text over a styled background with border/watermark.
    """
    _setup_fonts()

    bg_color = _pick_background(text)
    content_width = IMAGE_WIDTH - 2 * PADDING
    HEADER_H = 8

    # ── Find best font size ───────────────────────────────────────────────────
    font_size = _pick_font_size(text, max_width=content_width, max_height=2000)

    # ── Render text layer ─────────────────────────────────────────────────────
    text_img = _pango_render_text(text, font_size, TEXT_COLOR, content_width)
    text_w, text_h = text_img.size

    # ── Canvas size ───────────────────────────────────────────────────────────
    top_pad   = PADDING + HEADER_H + 20
    bot_pad   = PADDING + 30
    canvas_h  = max(top_pad + text_h + bot_pad, 200)

    # ── Background ────────────────────────────────────────────────────────────
    img  = Image.new("RGB", (IMAGE_WIDTH, canvas_h), color=bg_color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (IMAGE_WIDTH, HEADER_H)], fill=ACCENT_COLOR)

    # ── Drop shadow ───────────────────────────────────────────────────────────
    shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    # Create a darkened version of the text as shadow
    shadow_img = Image.new("RGBA", (text_w, text_h), (0, 0, 0, 0))
    # Darken pixels: keep alpha, set RGB to black
    r_ch, g_ch, b_ch, a_ch = text_img.split()
    shadow_a = a_ch.point(lambda p: int(p * 0.25))
    black_rgb = Image.new("RGB", text_img.size, (0, 0, 0))
    shadow_img = Image.merge("RGBA", (black_rgb.split()[0], black_rgb.split()[1], black_rgb.split()[2], shadow_a))
    shadow_layer.paste(shadow_img, (PADDING + 3, top_pad + 3), mask=shadow_img)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=5))
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, shadow_layer)

    # ── Paste text layer ──────────────────────────────────────────────────────
    img.paste(text_img, (PADDING, top_pad), mask=text_img)
    img = img.convert("RGB")

    # ── Watermark ─────────────────────────────────────────────────────────────
    wm_font = _pil_font(max(12, font_size // 3))
    draw = ImageDraw.Draw(img)
    try:
        wm_bbox = draw.textbbox((0, 0), WATERMARK, font=wm_font)
        wm_w = wm_bbox[2] - wm_bbox[0]
        wm_h = wm_bbox[3] - wm_bbox[1]
    except AttributeError:
        wm_w, wm_h = len(WATERMARK) * 8, 14
    draw.text(
        (IMAGE_WIDTH - PADDING // 2 - wm_w, canvas_h - wm_h - 14),
        WATERMARK, font=wm_font, fill=WATERMARK_COLOR,
    )

    # ── Border ────────────────────────────────────────────────────────────────
    border_color = tuple(max(c - 15, 0) for c in bg_color)
    draw.rectangle(
        [(0, 0), (IMAGE_WIDTH - 1, canvas_h - 1)],
        outline=border_color, width=2,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


# ── Telegram handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to the Text → Image Bot!*\n\n"
        "Send me any text and I'll turn it into a beautiful image.\n\n"
        "Use /help for tips.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *How to use this bot*\n\n"
        "1. Simply send any text message.\n"
        "2. The bot renders it as a clean image and sends it back.\n\n"
        "*Tips:*\n"
        "• Khmer, Latin, emoji and mixed text are all supported.\n"
        "• Use line breaks to split into paragraphs.\n"
        "• Long text is auto-wrapped and resized to fit.\n"
        f"• Maximum length: {MAX_TEXT_LENGTH} characters.\n\n"
        "*Commands:*\n"
        "/start — welcome message\n"
        "/help  — show this help",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    text: str = (message.text or "").strip()

    if not text:
        await message.reply_text("Please send some text and I'll turn it into an image.")
        return

    if len(text) > MAX_TEXT_LENGTH:
        await message.reply_text(
            f"⚠️ Your message is too long ({len(text)} chars). "
            f"Please keep it under {MAX_TEXT_LENGTH} characters."
        )
        return

    logger.info(
        "Generating image  user=%s  len=%d",
        message.from_user.id if message.from_user else "?",
        len(text),
    )
    await context.bot.send_chat_action(chat_id=message.chat_id, action="upload_photo")

    try:
        image_bytes = generate_image(text)
        buf = io.BytesIO(image_bytes)
        buf.name = "image.png"
        await message.reply_photo(photo=buf)
        logger.info("Image sent successfully")
    except subprocess.TimeoutExpired:
        logger.exception("ImageMagick timeout (too much text or system slow)")
        await message.reply_text(
            "⏱️ Image rendering took too long. Try a shorter text."
        )
    except FileNotFoundError as e:
        logger.exception("Missing resource: %s", e)
        await message.reply_text(
            "❌ System error: missing resource. Contact admin."
        )
    except Exception as exc:
        logger.exception("Image generation failed: %s", exc)
        await message.reply_text(
            "❌ Something went wrong generating your image. Please try again."
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "BOT_TOKEN is not set. "
            "Create a .env with  BOT_TOKEN=<token>  or export it."
        )
    _setup_fonts()   # warm up font registration at startup

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot starting — polling …")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
