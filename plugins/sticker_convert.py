"""
پلاگین تبدیل استیکر به عکس/گیف
کامند: .استیکر (ریپلای روی استیکر)
"""

import os
from telethon import events
from telethon.tl.types import DocumentAttributeSticker
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
                # دانلود استیکر
                fp = await reply.download_media(file=folder)
                if not fp:
                    await self.client.send_message(event.chat_id, "❌ دانلود ناموفق.")
                    return

                # تشخیص نوع
                is_animated = fp.endswith(".tgs")
                is_video = fp.endswith(".webm")
                is_webp = fp.endswith(".webp")

                if is_webp:
                    # استیکر ثابت -> عکس PNG
                    from PIL import Image
                    png_path = fp.replace(".webp", ".png")
                    img = Image.open(fp).convert("RGBA")
                    img.save(png_path, "PNG")
                    await self.client.send_file(
                        event.chat_id, png_path,
                        caption="🖼 تبدیل شد",
                        force_document=False,
                    )
                    os.remove(png_path)

                elif is_video:
                    # استیکر ویدیویی -> GIF/MP4
                    await self.client.send_file(
                        event.chat_id, fp,
                        caption="🎬 تبدیل شد",
                        force_document=False,
                        supports_streaming=True,
                    )

                elif is_animated:
                    # TGS -> فعلاً فایل خام
                    await self.client.send_file(
                        event.chat_id, fp,
                        caption="📦 استیکر متحرک (TGS)",
                        force_document=True,
                    )

                else:
                    await self.client.send_file(
                        event.chat_id, fp,
                        caption="📎 فایل استیکر",
                        force_document=True,
                    )

                # پاکسازی
                if os.path.exists(fp):
                    os.remove(fp)

                self.logger.info("Sticker converted")

            except ImportError:
                await self.client.send_message(
                    event.chat_id,
                    "❌ کتابخانه Pillow نصب نیست.\n`pip install Pillow`",
                )
            except Exception as e:
                self.logger.error(f"Convert error: {e}")
                await self.client.send_message(event.chat_id, f"❌ خطا: {str(e)[:100]}")

        self._add_handler(
            convert_cmd,
            events.NewMessage(pattern=r"^\.استیکر$", outgoing=True),
        )

        self.logger.info("loaded")