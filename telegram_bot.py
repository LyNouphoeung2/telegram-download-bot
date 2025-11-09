import os
import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode

# Configure logging for better debugging and monitoring
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- NO CHANGE NEEDED HERE ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("FATAL: BOT_TOKEN environment variable is not set.")
    exit()
# ----------------------------------------------------

# Telegram bot API file size limit (50 MB for videos and documents sent by bots)
FILE_SIZE_LIMIT_MB = 50

# Permanent download directory for large files
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Define the callback data prefix for audio downloads
AUDIO_CALLBACK_PREFIX = "download_audio|"

# --- FIX 1: Define the FFmpeg path for Koyeb ---
# Koyeb's buildpack installs ffmpeg here
FFMPEG_PATH = "/usr/bin/ffmpeg"


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
    This will now MERGE formats using FFmpeg if needed (e.g., for YouTube).
    Includes a progress hook to update the user.
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


    ydl_opts = {
        # --- FIX 2: Update format to allow merging (fixes YouTube) ---
        "format": "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best",
        "outtmpl": str(temp_path / "%(id)s.%(ext)s"),
        "paths": {"home": temp_dir, "temp": temp_dir},
        # --- FIX 3: Remove "no_merge" and add ffmpeg_location ---
        "ffmpeg_location": FFMPEG_PATH, # Tell yt-dlp where ffmpeg is
        "progress_hooks": [progress_hook],
        "postprocessors": [{
            'key': 'FFmpegVideoRemuxer',
            'preferedformat': 'mp4',
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        # --- FIX 4: More reliable file finding after download/merge ---
        # ydl.prepare_filename(info) gives the final expected filename
        video_file = Path(ydl.prepare_filename(info))
        
        if not video_file.exists():
            # Fallback in case prepare_filename is wrong (e.g., ext changed)
            video_file = temp_path / f"{info['id']}.mp4"
            if not video_file.exists():
                logger.error(f"Downloaded file not found. Expected: {video_file}")
                raise FileNotFoundError(f"Could not find downloaded file for id {info['id']}")

        return video_file, info


async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download the video from the provided URL and send it back to the user."""
    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("Please send a valid URL starting with http:// or https://.")
        return

    status_message = await update.message.reply_text("Fetching video details and preparing download... 🔄")

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
            text="Download finished. Checking file size... ✅",
        )

        file_size_mb = video_file.stat().st_size / (1024 * 1024)
        title = info.get('title', 'Video Download')

        if file_size_mb <= FILE_SIZE_LIMIT_MB:
            logger.info(f"Sending video: {video_file} (size: {file_size_mb:.2f} MB)")

            with open(video_file, "rb") as f:
                sent_message = await update.message.reply_video(
                    video=f,
                    caption=f"✅ **{title}**\n\n*Size: {file_size_mb:.2f} MB*",
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True,
                    read_timeout=100,
                    write_timeout=100,
                )

            await context.bot.delete_message(
                chat_id=status_message.chat_id,
                message_id=status_message.message_id
            )

            callback_data = f"{AUDIO_CALLBACK_PREFIX}{url}"
            keyboard = [[
                InlineKeyboardButton("🎧 Download as Voice Message", callback_data=callback_data)
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await sent_message.edit_reply_markup(reply_markup=reply_markup)

        else:
            # --- This is for videos > 50 MB ---
            permanent_path = DOWNLOAD_DIR / video_file.name
            shutil.move(video_file, permanent_path)

            await update.message.reply_text(
                f"✅ Download complete, but file is too large to send.\n\n"
                f"**File:** `{title}`\n"
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
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=f"❌ Error downloading video. The URL might be private or invalid.\n\n`{str(e)}`"
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


def run_audio_download_blocking(url: str, temp_dir: str) -> Tuple[Path, dict]:
    """
    Synchronous function to run yt_dlp to extract audio.
    Converts to Ogg/Opus for use as a Telegram Voice Message.
    REQUIRES FFMPEG.
    """
    temp_path = Path(temp_dir)
    ydl_opts = {
        "format": "bestaudio/best",
        # --- FIX 5: Change outtmpl to just id. Post-processor will add .opus ---
        "outtmpl": str(temp_path / "%(id)s"),
        "quiet": True,
        "no_warnings": True,
        "paths": {"home": temp_dir, "temp": temp_dir},
        # --- FIX 6: Tell yt-dlp (and post-processor) where ffmpeg is ---
        "ffmpeg_location": FFMPEG_PATH,
        "postprocessors": [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'opus',
            'preferredquality': '64',
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        # --- FIX 7: More reliable audio file finding ---
        # The post-processor will create a file named "[id].opus"
        audio_file = temp_path / f"{info['id']}.opus"

        if not audio_file.exists():
            logger.error(f"Could not find .opus file. Files: {list(temp_path.glob('*'))}")
            raise FileNotFoundError("Downloaded audio file (.opus) not found. Check 'ffmpeg' log.")

        return audio_file, info


async def download_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the callback query for downloading the audio as a voice message."""
    query = update.callback_query
    await query.answer("Starting audio conversion for voice message...")

    try:
        url = query.data.split(AUDIO_CALLBACK_PREFIX, 1)[1]
    except IndexError:
        await query.edit_message_caption(caption="Error: Could not retrieve URL.", parse_mode=ParseMode.MARKDOWN)
        return

    status_message = await query.message.reply_text("Converting to voice message format... 🎧")

    temp_dir = None
    audio_file = None

    try:
        temp_dir = tempfile.mkdtemp()
        audio_file, info = await asyncio.to_thread(run_audio_download_blocking, url, temp_dir)

        file_size_mb = audio_file.stat().st_size / (1024 * 1024)
        title = info.get('title', 'Audio Track')

        if file_size_mb <= FILE_SIZE_LIMIT_MB:
            await context.bot.edit_message_text(
                chat_id=status_message.chat_id,
                message_id=status_message.message_id,
                text="Sending voice message... 📤",
            )

            with open(audio_file, "rb") as f:
                await query.message.reply_voice(
                    voice=f,
                    caption=f"🎧 **{title}** (Voice)\n\n*Size: {file_size_mb:.2f} MB*",
                    parse_mode=ParseMode.MARKDOWN,
                    read_timeout=100,
                    write_timeout=100,
                )
        else:
            await query.message.reply_text(
                f"Audio file is also too large ({file_size_mb:.2f} MB) to send."
            )

        await context.bot.delete_message(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id
        )

    except Exception as e:
        logger.error(f"Audio download error: {str(e)}")
        # Send a more specific error based on the log
        if "ffprobe and ffmpeg not found" in str(e):
             error_text = "❌ Error: The bot server setup is missing 'ffmpeg'. Please contact the bot admin."
        else:
             error_text = f"❌ An error occurred during audio conversion: {str(e)}"
             
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=error_text
        )
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> None:
    """Initialize and run the Telegram bot."""
    global DOWNLOAD_DIR
    DOWNLOAD_DIR = Path("downloads")
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    logger.info(f"Using download directory: {DOWNLOAD_DIR.resolve()}")
    
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_and_send))
    application.add_handler(CallbackQueryHandler(download_audio, pattern=f"^{AUDIO_CALLBACK_PREFIX}"))

    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
