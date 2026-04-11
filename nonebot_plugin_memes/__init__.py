from nonebot import require
from nonebot.plugin import PluginMetadata, inherit_supported_adapters

require("nonebot_plugin_alconna")
require("nonebot_plugin_waiter")
require("nonebot_plugin_uninfo")
require("nonebot_plugin_localstore")
require("nonebot_plugin_orm")

from . import matchers as matchers
from .config import Config, memes_config

memes_prefixes = memes_config.memes_command_prefixes
memes_prefix = memes_prefixes[0] if memes_prefixes else ""


__plugin_meta__ = PluginMetadata(
    name="表情包制作",
    description="制作各种沙雕表情包",
    usage=(
        "## 🖼️ 表情包制作\n\n"
        f"- `{memes_prefix}关键词 + 图片/文字` - 制作表情\n"
        "  支持“自己”、“@某人”、“@id”作为图片，支持回复提取\n"
        "- `随机表情 + 图片/文字` - 随机制作表情\n"
        "- `表情包制作` - 查看表情列表\n"
        "- `表情详情 [关键词]` - 查看详细信息和预览\n"
        "- `表情搜索 [关键词]` - 查找相关的表情\n\n"
        "## 📊 调用统计\n\n"
        "- `[我的][全局]<时间段>表情调用统计 [表情名]` - 获取调用统计图\n"
        "  [时间段] 可选：日/周/月/年\n\n"
        "## ⚙️ 表情管理\n\n"
        "- `启用表情/禁用表情 [关键词]` - 开启或关闭某个表情\n"
        "- `表情包次数 [次数]` - 配置当前群或普通用户私聊的每日次数限制\n"
    ),
    type="application",
    homepage="https://github.com/noneplugin/nonebot-plugin-memes",
    config=Config,
    supported_adapters=inherit_supported_adapters(
        "nonebot_plugin_alconna", "nonebot_plugin_uninfo"
    ),
    extra={
        "author": "noneplugin",
        "version": "unknown",
        "menu_type": "功能",
    },
)
