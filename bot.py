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
   pip install "python-telegram-bot[all]>=21.0" Pillow python-dotenv

3. Set your token:
   - Create a .env file with:  BOT_TOKEN=your_token_here
   - OR set the environment variable:  export BOT_TOKEN=your_token_here

4. Run the bot:
   python bot.py
"""

from __future__ import annotations

import io
import logging
import os
import textwrap
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

# ── Load environment variables from .env (if present) ──────────────────────
load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_TEXT_LENGTH = 1500          # Characters — reject anything longer than this
IMAGE_WIDTH = 1000              # Fixed canvas width in pixels
PADDING = 60                    # Horizontal & vertical padding in pixels
WATERMARK = "Made with Telegram Text→Image Bot"

# Soft pastel / light-gray background colours to cycle through per message
BACKGROUNDS: list[tuple[int, int, int]] = [
    (245, 245, 248),   # near-white gray
    (240, 245, 255),   # pale sky blue
    (245, 255, 245),   # pale mint
    (255, 248, 240),   # pale warm peach
    (248, 240, 255),   # pale lavender
]

TEXT_COLOR = (30, 30, 30)         # Very dark gray (almost black)
ACCENT_COLOR = (100, 120, 200)    # Soft indigo for the header bar
WATERMARK_COLOR = (160, 160, 170) # Muted gray


# ── Font helpers ──────────────────────────────────────────────────────────────

def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try common system fonts; fall back to the built-in bitmap font.

    Priority order:
    1. Bundled fonts/ directory (Noto Sans Khmer — supports Latin + Khmer + many scripts)
    2. Common system paths
    3. PIL built-in bitmap font (last resort)
    """
    # Resolve bundled fonts directory relative to this script
    _here = Path(__file__).parent
    candidates = [
        # ── Bundled fonts (highest priority — Khmer + Latin support) ─────────
        str(_here / "fonts" / "NotoSansKhmer.ttf"),
        str(_here / "fonts" / "NotoSans-Regular.ttf"),
        # ── System Noto fonts ─────────────────────────────────────────────────
        "/usr/share/fonts/noto/NotoSansKhmer-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansKhmer-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        # ── DejaVu (available on most Linux/NixOS systems) ────────────────────
        "/run/current-system/sw/share/X11/fonts/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        # ── Liberation / FreeSans ─────────────────────────────────────────────
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        # ── macOS ─────────────────────────────────────────────────────────────
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        # ── Windows ───────────────────────────────────────────────────────────
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Ultimate fallback — PIL built-in bitmap font (no size argument)
    return ImageFont.load_default()


def _font_size_for_text(
    text: str,
    max_width: int,
    max_height: int,
    min_size: int = 18,
    max_size: int = 56,
) -> tuple[int, list[str]]:
    """
    Binary-search for the largest font size where the wrapped text fits inside
    (max_width × max_height).  Returns (chosen_size, wrapped_lines).
    """
    best_size = min_size
    best_lines: list[str] = text.splitlines() or [""]

    for size in range(max_size, min_size - 1, -2):
        font = _load_font(size)
        # Estimate characters per line from font metrics
        try:
            bbox = font.getbbox("A")
            char_w = max(bbox[2] - bbox[0], 1)
        except AttributeError:
            char_w = size // 2

        chars_per_line = max(int(max_width / char_w), 10)

        # Wrap each paragraph independently, preserving blank lines
        wrapped: list[str] = []
        for paragraph in text.splitlines():
            if paragraph.strip() == "":
                wrapped.append("")
            else:
                wrapped.extend(
                    textwrap.wrap(paragraph, width=chars_per_line) or [""]
                )

        # Measure actual rendered height
        dummy = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(dummy)
        try:
            bbox = draw.textbbox((0, 0), "\n".join(wrapped), font=font)
            total_h = bbox[3] - bbox[1]
        except AttributeError:
            total_h = len(wrapped) * (size + 4)

        if total_h <= max_height:
            best_size = size
            best_lines = wrapped
            break

    return best_size, best_lines


# ── Image generation ──────────────────────────────────────────────────────────

def _pick_background(text: str) -> tuple[int, int, int]:
    """Deterministically pick a background colour based on text content."""
    return BACKGROUNDS[sum(ord(c) for c in text) % len(BACKGROUNDS)]


def generate_image(text: str) -> bytes:
    """
    Render *text* onto a clean image and return the PNG as raw bytes.

    Layout
    ------
    - Fixed 1000-px-wide canvas, height adapts to content
    - Soft pastel background
    - Accent bar at the top
    - Main text centred horizontally, with generous padding
    - Subtle drop-shadow on the text block
    - Small watermark in the bottom-right corner
    """
    bg_color = _pick_background(text)
    content_width = IMAGE_WIDTH - 2 * PADDING

    # ── Work out font size and wrapped lines ─────────────────────────────────
    # Temporarily assume a tall canvas so we can measure freely
    font_size, lines = _font_size_for_text(
        text,
        max_width=content_width,
        max_height=2000,
    )
    font_main = _load_font(font_size)
    font_watermark = _load_font(max(14, font_size // 3))

    line_spacing = int(font_size * 0.35)
    line_height = font_size + line_spacing

    # ── Calculate canvas height ───────────────────────────────────────────────
    header_bar_h = 8
    top_pad = PADDING + header_bar_h + 20
    bottom_pad = PADDING + 30   # room for watermark

    text_block_h = len(lines) * line_height
    canvas_h = max(top_pad + text_block_h + bottom_pad, 200)

    # ── Draw background ───────────────────────────────────────────────────────
    img = Image.new("RGB", (IMAGE_WIDTH, canvas_h), color=bg_color)
    draw = ImageDraw.Draw(img)

    # Accent bar at top
    draw.rectangle(
        [(0, 0), (IMAGE_WIDTH, header_bar_h)],
        fill=ACCENT_COLOR,
    )

    # ── Drop-shadow layer ─────────────────────────────────────────────────────
    shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)

    text_x = PADDING
    text_y = top_pad
    joined = "\n".join(lines)

    # Draw shadow slightly offset
    shadow_draw.text(
        (text_x + 3, text_y + 3),
        joined,
        font=font_main,
        fill=(0, 0, 0, 60),
        spacing=line_spacing,
    )
    # Blur the shadow
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=4))
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, shadow_layer)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── Main text ─────────────────────────────────────────────────────────────
    draw.text(
        (text_x, text_y),
        joined,
        font=font_main,
        fill=TEXT_COLOR,
        spacing=line_spacing,
    )

    # ── Watermark ─────────────────────────────────────────────────────────────
    try:
        wm_bbox = draw.textbbox((0, 0), WATERMARK, font=font_watermark)
        wm_w = wm_bbox[2] - wm_bbox[0]
        wm_h = wm_bbox[3] - wm_bbox[1]
    except AttributeError:
        wm_w = len(WATERMARK) * (font_size // 3)
        wm_h = font_size // 3

    wm_x = IMAGE_WIDTH - PADDING // 2 - wm_w
    wm_y = canvas_h - wm_h - 14
    draw.text((wm_x, wm_y), WATERMARK, font=font_watermark, fill=WATERMARK_COLOR)

    # ── Thin border ───────────────────────────────────────────────────────────
    border_color = tuple(max(c - 15, 0) for c in bg_color)
    draw.rectangle(
        [(0, 0), (IMAGE_WIDTH - 1, canvas_h - 1)],
        outline=border_color,
        width=2,
    )

    # ── Encode as PNG ─────────────────────────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    await update.message.reply_text(
        "👋 *Welcome to the Text → Image Bot!*\n\n"
        "Send me any text and I'll turn it into a beautiful image for you.\n\n"
        "Use /help to see usage tips.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /help command."""
    await update.message.reply_text(
        "📖 *How to use this bot*\n\n"
        "1. Simply send any text message.\n"
        "2. The bot renders it as a clean image and sends it back.\n\n"
        "*Tips:*\n"
        "• Use line breaks (Enter key) to split into paragraphs.\n"
        "• Long text is automatically wrapped and resized to fit.\n"
        f"• Maximum length: {MAX_TEXT_LENGTH} characters.\n\n"
        "*Commands:*\n"
        "/start — welcome message\n"
        "/help  — show this help",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Convert incoming text to an image and send it back."""
    message = update.message
    text: str = (message.text or "").strip()

    # ── Guard: empty text ─────────────────────────────────────────────────────
    if not text:
        await message.reply_text(
            "Please send some text and I'll turn it into an image."
        )
        return

    # ── Guard: text too long ──────────────────────────────────────────────────
    if len(text) > MAX_TEXT_LENGTH:
        await message.reply_text(
            f"⚠️ Your message is too long ({len(text)} chars).\n"
            f"Please keep it under {MAX_TEXT_LENGTH} characters."
        )
        return

    logger.info(
        "Generating image for user=%s  text_len=%d",
        message.from_user.id if message.from_user else "unknown",
        len(text),
    )

    # ── Show "uploading photo…" action indicator ──────────────────────────────
    await context.bot.send_chat_action(
        chat_id=message.chat_id, action="upload_photo"
    )

    # ── Generate and send ─────────────────────────────────────────────────────
    try:
        image_bytes = generate_image(text)
        buf = io.BytesIO(image_bytes)
        buf.name = "image.png"
        await message.reply_photo(photo=buf)
    except Exception as exc:
        logger.exception("Image generation failed: %s", exc)
        await message.reply_text(
            "❌ Something went wrong while generating your image. Please try again."
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "BOT_TOKEN environment variable is not set.\n"
            "Create a .env file with:  BOT_TOKEN=<your_token>\n"
            "or export it:  export BOT_TOKEN=<your_token>"
        )

    app = (
        Application.builder()
        .token(token)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    logger.info("Bot is starting — polling for updates …")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
