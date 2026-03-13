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
import re
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
MAX_TEXT_LENGTH = 1500
IMAGE_WIDTH = 1000
PADDING = 60
WATERMARK = "Lim Sovannrady"

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

# ── Emoji detection ───────────────────────────────────────────────────────────

# Unicode ranges that are emoji / emoji-like
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # Misc symbols, emoticons, transport, flags…
    "\U00002600-\U000027BF"   # Misc symbols, dingbats
    "\U0000FE00-\U0000FE0F"   # Variation selectors
    "\U0001F900-\U0001F9FF"   # Supplemental symbols and pictographs
    "\U00002300-\U000023FF"   # Misc technical
    "\U00002B00-\U00002BFF"   # Misc symbols & arrows
    "\U00003000-\U00003300"   # CJK symbols (some overlap)
    "]+",
    flags=re.UNICODE,
)


def _segment(text: str) -> list[tuple[str, bool]]:
    """
    Split *text* into (chunk, is_emoji) pairs so each chunk can be rendered
    with the appropriate font.
    """
    segments: list[tuple[str, bool]] = []
    pos = 0
    for m in _EMOJI_RE.finditer(text):
        if m.start() > pos:
            segments.append((text[pos : m.start()], False))
        segments.append((m.group(), True))
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], False))
    return segments or [("", False)]


# ── Font helpers ──────────────────────────────────────────────────────────────

_here = Path(__file__).parent


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Load the main text font.
    Priority: bundled NotoSansKhmer → system Noto → DejaVu → fallback.
    """
    candidates = [
        str(_here / "fonts" / "NotoSansKhmer.ttf"),
        str(_here / "fonts" / "NotoSans-Regular.ttf"),
        "/usr/share/fonts/noto/NotoSansKhmer-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansKhmer-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/run/current-system/sw/share/X11/fonts/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _load_emoji_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the emoji font (NotoEmoji). Falls back to the main font."""
    candidates = [
        str(_here / "fonts" / "NotoEmoji.ttf"),
        "/usr/share/fonts/noto/NotoEmoji-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return _load_font(size)


# ── Multi-font text measurement ───────────────────────────────────────────────

def _measure_line(
    line: str,
    font_main: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    font_emoji: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    dummy_draw: ImageDraw.ImageDraw,
) -> tuple[int, int]:
    """Return (width, height) of a single line using per-segment fonts."""
    total_w = 0
    max_h = 0
    for chunk, is_emoji in _segment(line):
        if not chunk:
            continue
        font = font_emoji if is_emoji else font_main
        try:
            bbox = dummy_draw.textbbox((0, 0), chunk, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
        except Exception:
            w = len(chunk) * (font_main.size if hasattr(font_main, "size") else 20)  # type: ignore[attr-defined]
            h = font_main.size if hasattr(font_main, "size") else 20               # type: ignore[attr-defined]
        total_w += w
        max_h = max(max_h, h)
    return total_w, max_h


# ── Font-size / wrapping selection ────────────────────────────────────────────

def _wrap_lines(text: str, chars_per_line: int) -> list[str]:
    """Wrap each paragraph independently, preserving blank lines."""
    wrapped: list[str] = []
    for paragraph in text.splitlines():
        if paragraph.strip() == "":
            wrapped.append("")
        else:
            wrapped.extend(textwrap.wrap(paragraph, width=chars_per_line) or [""])
    return wrapped


def _font_size_for_text(
    text: str,
    max_width: int,
    max_height: int,
    min_size: int = 18,
    max_size: int = 56,
) -> tuple[int, list[str]]:
    """
    Binary-search for the largest font size where the wrapped text fits inside
    (max_width × max_height). Returns (chosen_size, wrapped_lines).
    """
    dummy_img = Image.new("RGB", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)

    best_size = min_size
    best_lines: list[str] = text.splitlines() or [""]

    for size in range(max_size, min_size - 1, -2):
        font_main = _load_font(size)
        font_emoji = _load_emoji_font(size)

        # Estimate chars per line
        try:
            bbox = dummy_draw.textbbox((0, 0), "ក", font=font_main)
            char_w = max(bbox[2] - bbox[0], 1)
        except Exception:
            char_w = size // 2
        chars_per_line = max(int(max_width / char_w), 10)

        lines = _wrap_lines(text, chars_per_line)

        line_spacing = int(size * 0.35)
        total_h = 0
        for line in lines:
            _, lh = _measure_line(line, font_main, font_emoji, dummy_draw)
            total_h += max(lh, size) + line_spacing

        if total_h <= max_height:
            best_size = size
            best_lines = lines
            break

    return best_size, best_lines


# ── Multi-font line drawing ───────────────────────────────────────────────────

def _draw_line(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    line: str,
    font_main: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    font_emoji: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int],
    shadow: bool = False,
    shadow_draw: ImageDraw.ImageDraw | None = None,
) -> int:
    """
    Draw a single line with per-segment fonts.
    Returns the line height (px).
    """
    cur_x = x
    line_h = 0
    segments = _segment(line)

    for chunk, is_emoji in segments:
        if not chunk:
            continue
        font = font_emoji if is_emoji else font_main
        try:
            bbox = draw.textbbox((cur_x, y), chunk, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
        except Exception:
            sz = font_main.size if hasattr(font_main, "size") else 20  # type: ignore[attr-defined]
            w = len(chunk) * sz // 2
            h = sz
        if shadow and shadow_draw:
            shadow_draw.text((cur_x + 3, y + 3), chunk, font=font, fill=(0, 0, 0, 60))
        draw.text((cur_x, y), chunk, font=font, fill=fill)
        cur_x += w
        line_h = max(line_h, h)

    return line_h


# ── Image generation ──────────────────────────────────────────────────────────

def _pick_background(text: str) -> tuple[int, int, int]:
    return BACKGROUNDS[sum(ord(c) for c in text) % len(BACKGROUNDS)]


def generate_image(text: str) -> bytes:
    """
    Render *text* onto a clean image and return the PNG as raw bytes.
    Supports Khmer, Latin, emoji, and mixed scripts.
    """
    bg_color = _pick_background(text)
    content_width = IMAGE_WIDTH - 2 * PADDING

    font_size, lines = _font_size_for_text(
        text,
        max_width=content_width,
        max_height=2000,
    )
    font_main  = _load_font(font_size)
    font_emoji = _load_emoji_font(font_size)
    font_wm    = _load_font(max(14, font_size // 3))

    line_spacing = int(font_size * 0.35)

    # ── Calculate canvas height ───────────────────────────────────────────────
    header_bar_h = 8
    top_pad      = PADDING + header_bar_h + 20
    bottom_pad   = PADDING + 30

    dummy_img  = Image.new("RGB", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)

    text_block_h = sum(
        max(_measure_line(l, font_main, font_emoji, dummy_draw)[1], font_size) + line_spacing
        for l in lines
    )
    canvas_h = max(top_pad + text_block_h + bottom_pad, 200)

    # ── Background ────────────────────────────────────────────────────────────
    img  = Image.new("RGB", (IMAGE_WIDTH, canvas_h), color=bg_color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (IMAGE_WIDTH, header_bar_h)], fill=ACCENT_COLOR)

    # ── Shadow layer ──────────────────────────────────────────────────────────
    shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow_draw  = ImageDraw.Draw(shadow_layer)

    cur_y = top_pad
    for line in lines:
        _draw_line(
            shadow_draw, PADDING, cur_y + 3, line,
            font_main, font_emoji,
            fill=(0, 0, 0, 60),
        )
        lh = max(_measure_line(line, font_main, font_emoji, dummy_draw)[1], font_size)
        cur_y += lh + line_spacing

    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=4))
    img = img.convert("RGBA")
    img = Image.alpha_composite(img, shadow_layer)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── Main text ─────────────────────────────────────────────────────────────
    cur_y = top_pad
    for line in lines:
        _draw_line(draw, PADDING, cur_y, line, font_main, font_emoji, fill=TEXT_COLOR)
        lh = max(_measure_line(line, font_main, font_emoji, dummy_draw)[1], font_size)
        cur_y += lh + line_spacing

    # ── Watermark ─────────────────────────────────────────────────────────────
    try:
        wm_bbox = draw.textbbox((0, 0), WATERMARK, font=font_wm)
        wm_w = wm_bbox[2] - wm_bbox[0]
        wm_h = wm_bbox[3] - wm_bbox[1]
    except Exception:
        wm_w = len(WATERMARK) * (font_size // 3)
        wm_h = font_size // 3
    draw.text(
        (IMAGE_WIDTH - PADDING // 2 - wm_w, canvas_h - wm_h - 14),
        WATERMARK, font=font_wm, fill=WATERMARK_COLOR,
    )

    # ── Border ────────────────────────────────────────────────────────────────
    border_color = tuple(max(c - 15, 0) for c in bg_color)
    draw.rectangle([(0, 0), (IMAGE_WIDTH - 1, canvas_h - 1)], outline=border_color, width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to the Text → Image Bot!*\n\n"
        "Send me any text and I'll turn it into a beautiful image for you.\n\n"
        "Use /help to see usage tips.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *How to use this bot*\n\n"
        "1. Simply send any text message.\n"
        "2. The bot renders it as a clean image and sends it back.\n\n"
        "*Tips:*\n"
        "• Use line breaks (Enter key) to split into paragraphs.\n"
        "• Long text is automatically wrapped and resized to fit.\n"
        "• Emojis, Khmer, and Latin text are all supported.\n"
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
        await message.reply_text(
            "Please send some text and I'll turn it into an image."
        )
        return

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

    await context.bot.send_chat_action(chat_id=message.chat_id, action="upload_photo")

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

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is starting — polling for updates …")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
