"""
مدیریت پلاگین‌ها برای هر کاربر
"""

import logging
from telethon import TelegramClient
from database import db

logger = logging.getLogger("plugin_manager")


def _safe_import(module: str, cls_name: str):
    """
    ایمپورت پلاگین‌ها به‌صورت ایمن: اگر یک پلاگین (یا وابستگی‌اش) خراب باشد،
    بقیه‌ی ربات باید بالا بیاید و فقط همان قابلیت غیرفعال شود — قبلاً یک
    خطای ایمپورت، کل برنامه را از کار می‌انداخت.
    """
    try:
        mod = __import__(module, fromlist=[cls_name])
        return getattr(mod, cls_name)
    except Exception as e:                      # noqa: BLE001
        logger.error(f"plugin import failed: {module}.{cls_name}: {type(e).__name__}: {e}")
        return None


_PLUGIN_IMPORTS = {
    "DicePlugin": ("plugins.dice", "DicePlugin"),
    "TypingPlugin": ("plugins.typing", "TypingPlugin"),
    "AiReplyPlugin": ("plugins.ai_reply", "AiReplyPlugin"),
    "HeartPlugin": ("plugins.heart", "HeartPlugin"),
    "PanelPlugin": ("plugins.panel", "PanelPlugin"),
    "SaveFromLinkPlugin": ("plugins.save_from_link", "SaveFromLinkPlugin"),
    "ForwardChannelPlugin": ("plugins.forward_channel", "ForwardChannelPlugin"),
    "StickerConvertPlugin": ("plugins.sticker_convert", "StickerConvertPlugin"),
    "BannerPlugin": ("plugins.banner", "BannerPlugin"),
    "TimedSaverPlugin": ("plugins.timed_saver", "TimedSaverPlugin"),
    "AntiDeletePlugin": ("plugins.anti_delete", "AntiDeletePlugin"),
    "AntiEditPlugin": ("plugins.anti_edit", "AntiEditPlugin"),
    "AutoResponsePlugin": ("plugins.auto_response", "AutoResponsePlugin"),
    "ChannelMonitorPlugin": ("plugins.channel_monitor", "ChannelMonitorPlugin"),
}

_loaded = {name: _safe_import(*path) for name, path in _PLUGIN_IMPORTS.items()}
globals().update(_loaded)

ALWAYS_ON_PLUGINS = {
    "dice": DicePlugin,
    "heart_animation": HeartPlugin,
    "save_from_link": SaveFromLinkPlugin,
    "forward_channel": ForwardChannelPlugin,
    "sticker_convert": StickerConvertPlugin,
    "panel": PanelPlugin,
    "typing_animation": TypingPlugin,
}


TOGGLEABLE_PLUGINS = {
    "banner": BannerPlugin,
    "timed_saver": TimedSaverPlugin,
    "anti_delete": AntiDeletePlugin,
    "anti_edit": AntiEditPlugin,
    "auto_response": AutoResponsePlugin,
    "channel_monitor": ChannelMonitorPlugin,
    "ai_reply": AiReplyPlugin,
}

# پلاگین‌هایی که ایمپورت نشدند کنار گذاشته می‌شوند (به‌جای کرش برنامه)
ALWAYS_ON_PLUGINS = {k: v for k, v in ALWAYS_ON_PLUGINS.items() if v is not None}
TOGGLEABLE_PLUGINS = {k: v for k, v in TOGGLEABLE_PLUGINS.items() if v is not None}

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
            try:
                plugin = PluginClass(client, user_db_id)
                await plugin.start()
            except Exception as e:
                logger.error(f"User {user_db_id}: plugin {name} failed to start: "
                             f"{type(e).__name__}: {e}")
                continue
            _active_plugins[user_db_id][name] = plugin
            loaded.append(name)

    # 2) toggleable: فقط اگه در DB روشن باشه
    features = await db.get_features(user_db_id)
    enabled_set = {f["feature_name"] for f in features if f["is_enabled"]}

    for name, PluginClass in TOGGLEABLE_PLUGINS.items():
        if name in enabled_set and name not in _active_plugins[user_db_id]:
            try:
                plugin = PluginClass(client, user_db_id)
                await plugin.start()
            except Exception as e:
                logger.error(f"User {user_db_id}: plugin {name} failed to start: "
                             f"{type(e).__name__}: {e}")
                continue
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

    # قابلیت‌های پولی فقط با اشتراک معتبر (دفاع در عمق — هر مسیری که
    # پلاگین را روشن کند، از جمله `.روشن` داخل چت، از اینجا رد می‌شود)
    from core.access import eligible
    user = await db.get_user_by_db_id(user_db_id)
    if not eligible(user)[0]:
        logger.warning(f"User {user_db_id}: enable {feature_name} denied (no subscription)")
        return False

    if user_db_id not in _active_plugins:
        _active_plugins[user_db_id] = {}

    # اگه قبلاً لود شده
    if feature_name in _active_plugins[user_db_id]:
        return True

    try:
        plugin = PluginClass(client, user_db_id)
        await plugin.start()
    except Exception as e:
        logger.error(f"User {user_db_id}: enable {feature_name} failed: "
                     f"{type(e).__name__}: {e}")
        return False
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