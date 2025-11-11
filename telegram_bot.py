import os
import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple, List

import yt_dlp
from telegram import Update, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# កំណត់កម្រិត logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- កំណត់តម្លៃថេរ ដើម្បីងាយស្រួលកែប្រែ ---

# Bot Token (ផ្លាស់ប្តូរនៅទីនេះបើចាំបាច់)
BOT_TOKEN_ENV = "BOT_TOKEN"

# កំណត់ទំហំឯកសារអតិបរមា (MB)
FILE_SIZE_LIMIT_MB = 50

# ផ្លូវទៅកាន់ ffmpeg (សម្រាប់ Koyeb ឬស្រដៀងគ្នា)
FFMPEG_PATH = "/usr/bin/ffmpeg"

# ចំណងជើងសម្រាប់វីដេអូ ឬរូបភាព (ប្រើ HTML ដើម្បីធ្វើឲ្យ @username អាចចុចបាន)
BOT_CAPTION = "ដោនឡូតវីដេអូដោយ <a href=\"https://t.me/Apple_Downloader_bot\">@Apple_Downloader_bot</a>"

# វេទិកាដែលគាំទ្រ
SUPPORTED_PLATFORMS = ['tiktok', 'instagram']

# សារស្វាគមន៍សម្រាប់ /start
WELCOME_MESSAGE = "សូមផ្ញើ Link (TikTok, Instagram) មកខ្ញុំ💚 ខ្ញុំនឹងទាញយកវីដេអូ ឬរូបភាព យ៉ាងច្បាស់ជូនអ្នក!"

# សារប្រាប់ថាតំណមិនត្រឹមត្រូវ
INVALID_URL_MESSAGE = "សូមផ្ញើតំណដែលត្រឹមត្រូវចាប់ផ្តើមដោយ http:// ឬ https://។"

# សារប្រាប់ថាមិនគាំទ្រវេទិកា
UNSUPPORTED_PLATFORM_MESSAGE = "សូមអភ័យទោស ខ្ញុំអាចទាញយកបានតែវីដេអូ និងរូបភាពពី TikTok និង Instagram ប៉ុណ្ណោះ"

# សារស្ថានភាព
FETCHING_INFO_MESSAGE = "កំពុងទាញយកព័ត៌មាន... 🔄"
DOWNLOAD_START_MESSAGE = "កំពុងចាប់ផ្តើមទាញយក... 0% ⏳"
DOWNLOAD_FINISHED_MESSAGE = "ទាញយករួចរាល់។ កំពុងផ្ញើ... ✅"

# សារជោគជ័យសម្រាប់វីដេអូ
VIDEO_SUCCESS_MESSAGE = "វីដេអូមានគុណភាពខ្ពស់របស់អ្នកត្រូវបានទាញយកជោគជ័យហើយ💚💚"

# សារជោគជ័យសម្រាប់រូបភាព
IMAGE_SUCCESS_MESSAGE = "រូបភាពមានគុណភាពខ្ពស់របស់អ្នកត្រូវបានទាញយកជោគជ័យហើយ💚💚"

# សារស្នើសុំតំណបន្ទាប់
NEXT_DOWNLOAD_MESSAGE = "បើអ្នកចង់ទាញយកវីដេអូ/រូបភាព ផ្សេងទៀត សូមផ្ញើរ Link មកខ្ញុំ💚💚"

# សារសម្រាប់ឯកសារធំពេក
FILE_TOO_LARGE_MESSAGE = "✅ ទាញយករួចរាល់ ប៉ុន្តែឯកសារធំពេកដើម្បីផ្ញើ។\n\n**ទំហំ:** {size:.2f} MB\n**កំណត់:** {limit} MB\n\n(Bot មិនអាចផ្ញើឯកសារធំជាង 50MB បានទេ)"

# សារកំហុសទូទៅ
DEFAULT_ERROR_MESSAGE = "❌ កំហុសក្នុងការទាញយក។ តំណអាចជាឯកជន មិនត្រឹមត្រូវ ឬត្រូវបានលុប។"
BLOCKED_ERROR_MESSAGE = "❌ Platform កំពុងរារាំងការទាញយក។ សូមព្យាយាមវីដេអូផ្សេង ឬរង់ចាំបន្តិច។"
PRIVATE_ERROR_MESSAGE = "❌ វីដេអូ/រូបភាព នេះជាឯកជន មានកំណត់អាយុ ឬមិនអាចប្រើបាន។"
RATE_LIMIT_ERROR_MESSAGE = "❌ ត្រូវបានកំណត់អត្រា (Rate Limit)។ សូមរង់ចាំ 5-10 នាទី ហើយព្យាយាមម្តងទៀត។"
UNEXPECTED_ERROR_MESSAGE = "❌ កំហុសមិនរំពឹងទុកបានកើតឡើង: {error}។ សូមព្យាយាមម្តងទៀត។"

# ទ្រង់ទ្រាយសម្រាប់វីដេអូ (កែប្រែគុណភាពនៅទីនេះ)
VIDEO_FORMAT = 'bestvideo[height>=1080][fps>=30][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height>=720][fps>=30][ext=mp4]+bestaudio[ext=m4a]/best'

# --- មុខងារដើម ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_MESSAGE)


# *** អនុគមន៍នេះត្រូវបានធ្វើបច្ចុប្បន្នភាពទាំងស្រុង ***
def run_download_blocking(
    url: str, temp_dir: str, loop, context, chat_id, message_id
) -> Tuple[Optional[Path], List[Path], dict]:
    temp_path = Path(temp_dir)
    last_update_time = 0
    last_percent = -1

    def progress_hook(d):
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

            # ធ្វើបច្ចុប្បន្នភាពរៀងរាល់ 2.5 វិនាទី ឬ 10%
            if current_time - last_update_time > 2.5 or abs(percent - last_percent) > 10:
                text = f"កំពុងទាញយក... {percent_str} ⏳"
                try:
                    coro = context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=text
                    )
                    asyncio.run_coroutine_threadsafe(coro, loop)
                    last_update_time = current_time
                    last_percent = percent
                except Exception as e:
                    logger.warning(f"កំហុសក្នុងការផ្ញើការធ្វើបច្ចុប្បន្នភាពវឌ្ឍនភាព: {e}")
        
        elif d['status'] == 'finished':
            if d.get('postprocessor') == 'Merger':
                text = "ទាញយករួចរាល់។ កំពុងបញ្ចូលវីដេអូនិងសំឡេង... 🔄"
                try:
                    coro = context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=text
                    )
                    asyncio.run_coroutine_threadsafe(coro, loop)
                except Exception as e:
                    logger.warning(f"កំហុសក្នុងការផ្ញើការធ្វើបច្ចុប្បន្នភាពបញ្ចូល: {e}")

    common_opts = {
        'nocheckcertificate': True,
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'extractor_retries': 5,
        'retry_sleep': 5,
        'sleep_interval': 1,
        'max_sleep_interval': 5,
        'socket_timeout': 30,
        'fragment_retries': 10,
        'paths': {"home": temp_dir, "temp": temp_dir},
    }

    ydl_opts_info = common_opts.copy()
    with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
        time.sleep(1) # បន្ថែមការផ្អាកបន្តិច
        info = ydl.extract_info(url, download=False)

    # --- តក្កវិជ្ជាថ្មីសម្រាប់ពិនិត្យមើលប្រភេទ Post ---
    is_video = False
    
    # Check 1: ប្រសិនបើវាមាន 'entries' វាជា slideshow រូបភាព
    if info.get('entries'):
        is_video = False
        logger.info(f"បានរកឃើញ Post ប្រភេទរូបភាព (slideshow) សម្រាប់ {url}")
    
    # Check 2: ប្រសិនបើគ្មាន 'entries' សូមពិនិត្យមើល 'formats' សម្រាប់វីដេអូ
    elif any(f.get('vcodec', 'none') != 'none' for f in info.get('formats', [])):
        is_video = True
        logger.info(f"បានរកឃើញ Post ប្រភេទវីដេអូ សម្រាប់ {url}")
    
    # Check 3: បើមិនដូច្នេះទេ វាជារូបភាពតែមួយ (ឧ. Instagram)
    else:
        is_video = False
        logger.info(f"បានរកឃើញ Post ប្រភេទរូបភាពតែមួយ សម្រាប់ {url}")
    # --- ចប់តក្កវិជ្ជាថ្មី ---

    if is_video:
        # --- ការទាញយកវីដេអូ (មិនផ្លាស់ប្តូរ) ---
        ydl_opts = common_opts.copy()
        ydl_opts.update({
            'format': VIDEO_FORMAT,
            'outtmpl': str(temp_path / "%(id)s.%(ext)s"),
            'ffmpeg_location': FFMPEG_PATH,
            'progress_hooks': [progress_hook],
            'postprocessors': [{
                'key': 'FFmpegVideoRemuxer',
                'preferedformat': 'mp4',
            }],
        })
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        video_files = list(temp_path.glob('*.mp4'))
        video_file = video_files[0] if video_files else None
        images = []

    else:
        # --- ការទាញយករូបភាព (បានកែតម្រូវ) ---
        ydl_opts = common_opts.copy()
        ydl_opts.update({
            # ប្រើ %(autonumber)s ដើម្បីរាប់លេខរូបភាព ក្នុងករណី slideshow
            'outtmpl': str(temp_path / "%(id)s_%(autonumber)s.%(ext)s"),
            'progress_hooks': [progress_hook],
            'skip_download': False # ត្រូវប្រាកដថាយើងទាញយករូបភាព
        })
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # ប្រមូលរូបភាពទាំងអស់ (បន្ថែម .webp ព្រោះ TikTok ប្រើវា)
        images = list(temp_path.glob('*.jpg')) + \
                 list(temp_path.glob('*.jpeg')) + \
                 list(temp_path.glob('*.png')) + \
                 list(temp_path.glob('*.webp')) # បានបន្ថែម .webp
        
        images.sort(key=lambda p: p.name) # តម្រៀបតាមឈ្មោះ (e.g., ..._1, ..._2)
        video_file = None

    if video_file is None and not images:
        logger.warning(f"yt-dlp download finished but no files found for {url}")
        raise FileNotFoundError("No video or images found after download")

    return video_file, images, info


async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text(INVALID_URL_MESSAGE)
        return

    lower_url = url.lower()
    if not any(platform in lower_url for platform in SUPPORTED_PLATFORMS):
        await update.message.reply_text(UNSUPPORTED_PLATFORM_MESSAGE)
        return

    status_message = await update.message.reply_text(FETCHING_INFO_MESSAGE)

    temp_dir = None
    video_file = None
    images = []
    info = None

    try:
        temp_dir = tempfile.mkdtemp()
        loop = asyncio.get_event_loop()

        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=DOWNLOAD_START_MESSAGE,
        )

        video_file, images, info = await asyncio.to_thread(
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
            text=DOWNLOAD_FINISHED_MESSAGE,
        )

        if video_file:
            file_size_mb = video_file.stat().st_size / (1024 * 1024)

            if file_size_mb <= FILE_SIZE_LIMIT_MB:
                logger.info(f"កំពុងផ្ញើវីដេអូ: {video_file} (ទំហំ: {file_size_mb:.2f} MB)")

                await update.message.reply_text(VIDEO_SUCCESS_MESSAGE)

                with open(video_file, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        caption=BOT_CAPTION,
                        parse_mode=ParseMode.HTML,
                        supports_streaming=True,
                        read_timeout=100,
                        write_timeout=100,
                    )

                await update.message.reply_text(NEXT_DOWNLOAD_MESSAGE)
            
            else:
                # មិនរក្សាទុកឯកសារធំៗ (កូដនេះត្រឹមត្រូវពីមុន)
                await update.message.reply_text(
                    FILE_TOO_LARGE_MESSAGE.format(size=file_size_mb, limit=FILE_SIZE_LIMIT_MB),
                    parse_mode=ParseMode.MARKDOWN
                )

        elif images:
            logger.info(f"កំពុងផ្ញើរូបភាព {len(images)} សន្លឹក សម្រាប់ {url}")
            await update.message.reply_text(IMAGE_SUCCESS_MESSAGE)

            media_group = []
            for i, img_path in enumerate(images):
                try:
                    with open(img_path, 'rb') as f:
                        # អាន file bytes ចូលទៅក្នុង memory
                        # នេះគឺចាំបាច់ព្រោះ `finally` block នឹងលុប temp_dir
                        # មុនពេល `reply_media_group` អាចបញ្ចប់ការផ្ញើ
                        img_bytes = f.read()
                    
                    caption = BOT_CAPTION if i == 0 else None
                    media_group.append(InputMediaPhoto(img_bytes, caption=caption, parse_mode=ParseMode.HTML if caption else None))
                except Exception as e:
                    logger.warning(f"មិនអាចដំណើរការរូបភាព {img_path}: {e}")

            # ផ្ញើរូបភាពជាក្រុម (albums)
            # Telegram ដាក់កម្រិត 10 រូបភាពក្នុងមួយក្រុម
            for i in range(0, len(media_group), 10):
                chunk = media_group[i:i + 10]
                try:
                    await update.message.reply_media_group(media=chunk)
                except Exception as e:
                    logger.error(f"មិនអាចផ្ញើ media group: {e}")
                    await update.message.reply_text("❌ មានបញ្ហាក្នុងការផ្ញើស្លាយរូបភាពមួយចំនួន។")


            await update.message.reply_text(NEXT_DOWNLOAD_MESSAGE)

        # លុបសារ "កំពុងផ្ញើ..." បន្ទាប់ពីជោគជ័យ
        await context.bot.delete_message(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id
        )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"DownloadError: {str(e)} សម្រាប់ {url}")
        error_text = DEFAULT_ERROR_MESSAGE
        error_msg = str(e).lower()
        if "confirm you're not a bot" in error_msg:
            error_text = BLOCKED_ERROR_MESSAGE
        elif "private video" in error_msg or "unavailable" in error_msg:
            error_text = PRIVATE_ERROR_MESSAGE
        elif "rate limit" in error_msg or "too many requests" in error_msg:
            error_text = RATE_LIMIT_ERROR_MESSAGE
            
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=error_text
        )
    except Exception as e:
        logger.error(f"កំហុសមិនរំពឹងទុក: {str(e)} សម្រាប់ {url}")
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=UNEXPECTED_ERROR_MESSAGE.format(error=str(e))
        )
    finally:
        # សម្អាតថតបណ្តោះអាសន្នជានិច្ច មិនថាជោគជ័យ ឬបរាជ័យ
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"សម្អាតថតបណ្តោះអាសន្ន: {temp_dir}")


def main() -> None:
    token = os.environ.get(BOT_TOKEN_ENV)
    if not token:
        logger.critical(f"មិនអាចរកឃើញ {BOT_TOKEN_ENV}! សូមតັ້ງ Environment Variable។")
        return

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_and_send))

    logger.info("កំពុងចាប់ផ្តើម bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
