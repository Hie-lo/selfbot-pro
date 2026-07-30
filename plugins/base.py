"""
کلاس پایه پلاگین
"""

import logging


class BasePlugin:
    """هر پلاگین از این ارث‌بری می‌کند"""

    name: str = "base"
    description: str = ""
    always_on: bool = False  # اگر True باشه نیاز به toggle نداره

    def __init__(self, client, user_id: int):
        self.client = client
        self.user_id = user_id
        self.logger = logging.getLogger(f"plugin.{self.name}.{user_id}")
        self._handlers = []

    async def start(self):
        """ثبت هندلرها — هر پلاگین override می‌کنه"""
        pass

    async def stop(self):
        """حذف هندلرها"""
        for callback, event in self._handlers:
            self.client.remove_event_handler(callback, event)
        self._handlers.clear()
        self.logger.info("stopped")

    def _add_handler(self, callback, event):
        """ثبت هندلر با نگهداری رفرنس برای حذف بعدی"""
        self.client.add_event_handler(callback, event)
        self._handlers.append((callback, event))