"""
مدیریت پلاگین‌ها برای هر کاربر
"""

import logging
from telethon import TelegramClient
from database import db

from plugins.dice import DicePlugin
from plugins.heart import HeartPlugin
from plugins.save_from_link import SaveFromLinkPlugin
from plugins.sticker_convert import StickerConvertPlugin
from plugins.banner import BannerPlugin
from plugins.timed_saver import TimedSaverPlugin
from plugins.anti_delete import AntiDeletePlugin
from plugins.anti_edit import AntiEditPlugin
from plugins.auto_response import AutoResponsePlugin

logger = logging.getLogger("plugin_manager")
from plugins.channel_monitor import ChannelMonitorPlugin

ALWAYS_ON_PLUGINS = {
    "dice": DicePlugin,
    "heart_animation": HeartPlugin,
    "save_from_link": SaveFromLinkPlugin,
    "sticker_convert": StickerConvertPlugin,
}

TOGGLEABLE_PLUGINS = {
    "banner": BannerPlugin,
    "timed_saver": TimedSaverPlugin,
    "anti_delete": AntiDeletePlugin,
    "anti_edit": AntiEditPlugin,
    "auto_response": AutoResponsePlugin,
    # "channel_monitor": ChannelMonitorPlugin,
}

# پلاگین‌های فعال هر کاربر
# key: user_db_id, value: {feature_name: plugin_instance}
_active_plugins: dict[int, dict[str, object]] = {}


# ═══════ Load / Unload ═══════


async def load_plugins_for_user(user_db_id: int, client: TelegramClient):
    """
    بارگذاری پلاگین‌ها بعد از login یا reconnect
    """
    if user_db_id not in _active_plugins:
        _active_plugins[user_db_id] = {}

    loaded = []

    # 1) همیشه روشن‌ها
    for name, PluginClass in ALWAYS_ON_PLUGINS.items():
        if name not in _active_plugins[user_db_id]:
            plugin = PluginClass(client, user_db_id)
            await plugin.start()
            _active_plugins[user_db_id][name] = plugin
            loaded.append(name)

    # 2) toggleable: فقط اگه در DB روشن باشه
    features = await db.get_features(user_db_id)
    enabled_set = {f["feature_name"] for f in features if f["is_enabled"]}

    for name, PluginClass in TOGGLEABLE_PLUGINS.items():
        if name in enabled_set and name not in _active_plugins[user_db_id]:
            plugin = PluginClass(client, user_db_id)
            await plugin.start()
            _active_plugins[user_db_id][name] = plugin
            loaded.append(name)

    if loaded:
        logger.info(f"User {user_db_id}: loaded [{', '.join(loaded)}]")


async def enable_plugin(user_db_id: int, feature_name: str, client: TelegramClient) -> bool:
    """روشن کردن یک پلاگین toggleable"""
    if feature_name in ALWAYS_ON_PLUGINS:
        return True  # همیشه روشنه

    PluginClass = TOGGLEABLE_PLUGINS.get(feature_name)
    if not PluginClass:
        return False

    if user_db_id not in _active_plugins:
        _active_plugins[user_db_id] = {}

    # اگه قبلاً لود شده
    if feature_name in _active_plugins[user_db_id]:
        return True

    plugin = PluginClass(client, user_db_id)
    await plugin.start()
    _active_plugins[user_db_id][feature_name] = plugin

    logger.info(f"User {user_db_id}: enabled {feature_name}")
    return True


async def disable_plugin(user_db_id: int, feature_name: str) -> bool:
    """خاموش کردن یک پلاگین toggleable"""
    if feature_name in ALWAYS_ON_PLUGINS:
        return False  # نمیشه خاموش کرد

    if user_db_id not in _active_plugins:
        return True

    plugin = _active_plugins[user_db_id].pop(feature_name, None)
    if plugin:
        await plugin.stop()
        logger.info(f"User {user_db_id}: disabled {feature_name}")

    return True


async def unload_all_for_user(user_db_id: int):
    """حذف تمام پلاگین‌های یک کاربر"""
    plugins = _active_plugins.pop(user_db_id, {})
    for name, plugin in plugins.items():
        try:
            await plugin.stop()
        except Exception as e:
            logger.error(f"Error stopping {name} for user {user_db_id}: {e}")

    if plugins:
        logger.info(f"User {user_db_id}: unloaded all ({len(plugins)} plugins)")


async def unload_all():
    """حذف همه پلاگین‌های همه کاربران"""
    for uid in list(_active_plugins.keys()):
        await unload_all_for_user(uid)


def get_active_plugins(user_db_id: int) -> dict:
    """لیست پلاگین‌های فعال یک کاربر"""
    return dict(_active_plugins.get(user_db_id, {}))