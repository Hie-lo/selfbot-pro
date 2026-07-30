"""
کلاس پایه پلاگین
"""

import logging


class BasePlugin:
    name: str = "base"
    description: str = ""

    def __init__(self, client, user_id: int, db):
        self.client = client
        self.user_id = user_id
        self.db = db
        self.logger = logging.getLogger(
            f"plugin.{self.name}.{user_id}"
        )
        self._handlers = []

    async def start(self):
        pass

    async def stop(self):
        for handler, event in self._handlers:
            self.client.remove_event_handler(handler, event)
        self._handlers.clear()
        self.logger.info("stopped")

    def _add_handler(self, callback, event):
        self.client.add_event_handler(callback, event)
        self._handlers.append((callback, event))