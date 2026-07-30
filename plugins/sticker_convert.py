"""
پلاگین تبدیل استیکر به عکس/گیف
کامند: .استیکر (ریپلای روی استیکر)
"""

import os
import subprocess
from telethon import events
from plugins.base import BasePlugin
from config import DOWNLOADS_DIR


class StickerConvertPlugin(BasePlugin):
    name = "sticker_convert"
    description = "تبدیل استیکر"
    always_on = True

    async def start(self):

        async def convert_cmd(event):
            if not event.out:
                return
            if not event.is_reply:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ روی استیکر ریپلای کنید.")
                return

            reply = await event.get_reply_message()
            if not reply.sticker:
                await event.delete()
                await self.client.send_message(event.chat_id, "❌ این پیام استیکر نیست.")
                return

            await event.delete()

            folder = os.path.join(DOWNLOADS_DIR, "stickers")
            os.makedirs(folder, exist_ok=True)

            try:
                fp = await reply.download_media(file=folder)
                if not fp:
                    await self.client.send_message(event.chat_id, "❌ دانلود ناموفق.")
                    return

                is_video = fp.endswith(".webm")
                is_webp = fp.endswith(".webp")
                is_animated = fp.endswith(".tgs")

                if is_webp:
                    # استیکر ثابت -> PNG
                    from PIL import Image
                    png_path = fp.replace(".webp", ".png")
                    img = Image.open(fp).convert("RGBA")
                    img.save(png_path, "PNG")

                    await self.client.send_file(
                        event.chat_id, png_path,
                        caption="🖼 تبدیل شد",
                        force_document=False,
                        reply_to=reply.id,
                    )
                    os.remove(png_path)

                elif is_video:
                    # استیکر ویدیویی -> GIF
                    gif_path = fp.replace(".webm", ".gif")

                    # تلاش با ffmpeg
                    try:
                        subprocess.run(
                            [
                                "ffmpeg", "-y",
                                "-i", fp,
                                "-vf", "fps=15,scale=256:-1:flags=lanczos",
                                "-loop", "0",
                                gif_path,
                            ],
                            capture_output=True,
                            timeout=30,
                        )
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        gif_path = None

                    if gif_path and os.path.exists(gif_path) and os.path.getsize(gif_path) > 0:
                        # ارسال به عنوان GIF
                        await self.client.send_file(
                            event.chat_id, gif_path,
                            caption="🎬 تبدیل شد",
                            force_document=False,
                            reply_to=reply.id,
                            attributes=[],
                        )
                        os.remove(gif_path)
                    else:
                        # fallback: ارسال mp4 به عنوان ویدیو گرد (GIF تلگرامی)
                        mp4_path = fp.replace(".webm", ".mp4")
                        try:
                            subprocess.run(
                                [
                                    "ffmpeg", "-y",
                                    "-i", fp,
                                    "-c:v", "libx264",
                                    "-pix_fmt", "yuv420p",
                                    "-an",
                                    mp4_path,
                                ],
                                capture_output=True,
                                timeout=30,
                            )
                        except (FileNotFoundError, subprocess.TimeoutExpired):
                            mp4_path = None

                        if mp4_path and os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 0:
                            await self.client.send_file(
                                event.chat_id, mp4_path,
                                caption="🎬 تبدیل شد",
                                force_document=False,
                                supports_streaming=True,
                                reply_to=reply.id,
                            )
                            os.remove(mp4_path)
                        else:
                            # اگه ffmpeg نبود
                            await self.client.send_file(
                                event.chat_id, fp,
                                caption="📎 فایل استیکر (ffmpeg نصب نیست)",
                                force_document=True,
                                reply_to=reply.id,
                            )

                elif is_animated:
                    await self.client.send_file(
                        event.chat_id, fp,
                        caption="📦 استیکر متحرک TGS",
                        force_document=True,
                        reply_to=reply.id,
                    )

                else:
                    await self.client.send_file(
                        event.chat_id, fp,
                        caption="📎 فایل استیکر",
                        force_document=True,
                        reply_to=reply.id,
                    )

                if os.path.exists(fp):
                    os.remove(fp)

                self.logger.info("Sticker converted")

            except ImportError:
                await self.client.send_message(
                    event.chat_id, "❌ Pillow نصب نیست: `pip install Pillow`"
                )
            except Exception as e:
                self.logger.error(f"Convert error: {e}")
                await self.client.send_message(event.chat_id, f"❌ خطا: {str(e)[:100]}")

        self._add_handler(
            convert_cmd,
            events.NewMessage(pattern=r"^\.استیکر$", outgoing=True),
        )

        self.logger.info("loaded")