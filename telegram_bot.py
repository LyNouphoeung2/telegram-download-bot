import os
import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

import yt_dlp
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Load Bot Token ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("FATAL: BOT_TOKEN environment variable is not set.")
    exit()
# ----------------------------------------------------

# Telegram bot API file size limit (50 MB)
FILE_SIZE_LIMIT_MB = 50

# Permanent download directory (ephemeral on Koyeb)
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# --- CRITICAL: Path to ffmpeg on Koyeb ---
# This is still required for merging YouTube videos.
FFMPEG_PATH = "/usr/bin/ffmpeg"

# --- New caption as requested ---
BOT_CAPTION = "ដោនឡូតវីដេអូដោយ @Apple_Downloader_bot"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the /start command is issued."""
    await update.message.reply_text(
        "Send me a video URL from YouTube, TikTok, or Facebook, and I'll download it!"
    )


def run_download_blocking(
    url: str, temp_dir: str, loop, context, chat_id, message_id
) -> Tuple[Optional[Path], dict]:
    """
    Synchronous function to run yt_dlp in a separate thread.
    This will MERGE formats using FFmpeg if needed (e.g., for YouTube).
    """
    temp_path = Path(temp_dir)
    last_update_time = 0
    last_percent = -1

    def progress_hook(d):
        """Hook to send progress updates back to the async loop."""
        nonlocal last_update_time, last_percent
        if d['status'] == 'downloading':
            current_time = time.time()
            percent_str = d.get('_percent_str')
            if not percent_str:
                return

            try:
                percent = float(percent_str.strip().replace('%', ''))
            except ValueError:
                percent = 0.0

            # Throttle updates
            if current_time - last_update_time > 2.5 or abs(percent - last_percent) > 10:
                text = f"Download in progress... {percent_str} ⏳"
                try:
                    coro = context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=text
                    )
                    asyncio.run_coroutine_threadsafe(coro, loop)
                    last_update_time = current_time
                    last_percent = percent
                except Exception as e:
                    logger.warning(f"Error sending progress update: {e}")
        
        elif d['status'] == 'finished':
            # Handle post-processing (merging) message
            if d.get('postprocessor') == 'Merger':
                text = "Download finished. Merging video and audio... 🔄"
                try:
                    coro = context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=text
                    )
                    asyncio.run_coroutine_threadsafe(coro, loop)
                except Exception as e:
                    logger.warning(f"Error sending merge update: {e}")

    # --- Updated ydl_opts for better YouTube compatibility ---
    ydl_opts = {
        "format": "bv[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4][height<=720]/bv+ba/b",
        "outtmpl": str(temp_path / "%(id)s.%(ext)s"),
        "paths": {"home": temp_dir, "temp": temp_dir},
        "ffmpeg_location": FFMPEG_PATH, # Still needed for merging
        "progress_hooks": [progress_hook],
        "postprocessors": [{
            'key': 'FFmpegVideoRemuxer',
            'preferedformat': 'mp4',
        }],
        "nocheckcertificate": True, # Ignore SSL certificate errors
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # Add a small delay
        time.sleep(1) 
        info = ydl.extract_info(url, download=True)
        
        # Find the downloaded file
        video_file = Path(ydl.prepare_filename(info))
        
        if not video_file.exists():
            # Fallback in case of remuxing
            video_file = temp_path / f"{info['id']}.mp4"
            if not video_file.exists():
                logger.error(f"Downloaded file not found. Expected: {video_file}")
                raise FileNotFoundError(f"Could not find downloaded file for id {info['id']}")

        return video_file, info


async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download the video from the URL and send it back to the user."""
    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("Please send a valid URL starting with http:// or https://.")
        return

    status_message = await update.message.reply_text("Fetching video details... 🔄")

    temp_dir = None
    video_file = None
    info = None

    try:
        temp_dir = tempfile.mkdtemp()
        loop = asyncio.get_event_loop()

        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text="Download starting... 0% ⏳",
        )

        video_file, info = await asyncio.to_thread(
            run_download_blocking,
            url,
            temp_dir,
            loop,
            context,
            status_message.chat_id,
            status_message.message_id
        )

        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text="Download finished. Sending video... ✅",
        )

        file_size_mb = video_file.stat().st_size / (1024 * 1024)

        if file_size_mb <= FILE_SIZE_LIMIT_MB:
            logger.info(f"Sending video: {video_file} (size: {file_size_mb:.2f} MB)")

            with open(video_file, "rb") as f:
                # --- CHANGE: Send video with new caption ---
                await update.message.reply_video(
                    video=f,
                    caption=BOT_CAPTION, # Use the new caption
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True,
                    read_timeout=100,
                    write_timeout=100,
                )
            
            # Delete the "Download finished" status message
            await context.bot.delete_message(
                chat_id=status_message.chat_id,
                message_id=status_message.message_id
            )

        else:
            # For videos > 50 MB
            permanent_path = DOWNLOAD_DIR / video_file.name
            shutil.move(video_file, permanent_path)

            await update.message.reply_text(
                f"✅ Download complete, but file is too large to send.\n\n"
                f"**Size:** {file_size_mb:.2f} MB\n"
                f"**Limit:** {FILE_SIZE_LIMIT_MB} MB\n\n"
                f"File saved to bot's server (storage is temporary).",
                parse_mode=ParseMode.MARKDOWN
            )
            await context.bot.delete_message(
                chat_id=status_message.chat_id,
                message_id=status_message.message_id
            )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"DownloadError: {str(e)}")
        error_text = "❌ Error downloading video. The URL might be private or invalid."
        if "confirm you're not a bot" in str(e):
            error_text = "❌ YouTube is blocking the download. Please try a different video."
            
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=error_text
        )
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=f"❌ An unexpected error occurred: {str(e)}. Please try again."
        )
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"Cleanup of temp directory: {temp_dir}")


def main() -> None:
    """Initialize and run the Telegram bot."""
    global DOWNLOAD_DIR
    DOWNLOAD_DIR = Path("downloads")
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    logger.info(f"Using download directory: {DOWNLOAD_DIR.resolve()}")
    
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    # --- CHANGE: Updated handler to call the renamed function ---
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_and_send))
    
    # --- DELETED: CallbackQueryHandler for audio is removed ---

    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
