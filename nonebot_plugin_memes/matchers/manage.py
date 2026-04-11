from typing import Optional

from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER, Permission
from nonebot_plugin_alconna import Alconna, Args, on_alconna
from nonebot_plugin_uninfo import Uninfo

from ..daily_limit_manager import daily_limit_manager, is_private_scene, is_superuser
from ..manager import MemeMode, meme_manager
from .utils import UserId, find_meme


def _uninfo_role(session: Uninfo) -> bool:
    return session.scene.is_private or bool(
        session.member and session.member.role and session.member.role.level > 1
    )


PERM_EDIT = SUPERUSER | Permission(_uninfo_role)
PERM_GLOBAL = SUPERUSER


def can_manage_daily_limit(session: Uninfo) -> bool:
    if is_superuser(session):
        return True
    if is_private_scene(session):
        return False
    return bool(
        session.member and session.member.role and session.member.role.level > 1
    )


PERM_DAILY_LIMIT = Permission(can_manage_daily_limit)


block_matcher = on_alconna(
    Alconna("禁用表情", Args["meme_name", str]),
    block=True,
    priority=11,
    use_cmd_start=True,
    permission=PERM_EDIT,
)
unblock_matcher = on_alconna(
    Alconna("启用表情", Args["meme_name", str]),
    block=True,
    priority=11,
    use_cmd_start=True,
    permission=PERM_EDIT,
)
block_gl_matcher = on_alconna(
    Alconna("全局禁用表情", Args["meme_name", str]),
    block=True,
    priority=11,
    use_cmd_start=True,
    permission=PERM_GLOBAL,
)
unblock_gl_matcher = on_alconna(
    Alconna("全局启用表情", Args["meme_name", str]),
    block=True,
    priority=11,
    use_cmd_start=True,
    permission=PERM_GLOBAL,
)
daily_limit_matcher = on_alconna(
    Alconna("表情包次数", Args["max_count?", int]),
    aliases={"表情次数", "meme次数", "memes次数"},
    block=True,
    priority=11,
    use_cmd_start=True,
    permission=PERM_DAILY_LIMIT,
)


def get_daily_limit_status_message(session: Uninfo) -> str:
    max_count = daily_limit_manager.get_limit_max_count(session)
    if is_private_scene(session):
        if max_count is None:
            return "当前普通用户私聊未开启表情包次数限制"
        return f"当前普通用户私聊每日可制作 {max_count} 次表情包"

    if max_count is None:
        return "当前群未开启表情包次数限制"
    return f"当前群内每人每日可制作 {max_count} 次表情包"


def is_valid_daily_limit_max_count(max_count: int) -> bool:
    return max_count == -1 or max_count > 0


async def handle_daily_limit_command(
    matcher: Matcher, session: Uninfo, max_count: Optional[int]
):
    if max_count is None:
        await matcher.finish(get_daily_limit_status_message(session))

    if not is_valid_daily_limit_max_count(max_count):
        await matcher.finish("次数必须为 -1 或正整数")

    if is_private_scene(session):
        daily_limit_manager.set_private_max_count(session, max_count)
        if max_count == -1:
            await matcher.finish("已关闭普通用户私聊的表情包次数限制")
        await matcher.finish(f"已设置普通用户私聊每日可制作 {max_count} 次表情包")

    daily_limit_manager.set_group_max_count(session, max_count)
    if max_count == -1:
        await matcher.finish("已关闭当前群的表情包次数限制")
    await matcher.finish(f"已设置当前群内每人每日可制作 {max_count} 次表情包")


@block_matcher.handle()
async def _(matcher: Matcher, user_id: UserId, meme_name: str):
    meme = await find_meme(matcher, meme_name)
    meme_manager.block(user_id, meme.key)
    await matcher.finish(f"表情 {meme.key} 禁用成功")


@unblock_matcher.handle()
async def _(matcher: Matcher, user_id: UserId, meme_name: str):
    meme = await find_meme(matcher, meme_name)
    meme_manager.unblock(user_id, meme.key)
    await matcher.finish(f"表情 {meme.key} 启用成功")


@block_gl_matcher.handle()
async def _(matcher: Matcher, meme_name: str):
    meme = await find_meme(matcher, meme_name)
    meme_manager.change_mode(MemeMode.WHITE, meme.key)
    await matcher.finish(f"表情 {meme.key} 已设为白名单模式")


@unblock_gl_matcher.handle()
async def _(matcher: Matcher, meme_name: str):
    meme = await find_meme(matcher, meme_name)
    meme_manager.change_mode(MemeMode.BLACK, meme.key)
    await matcher.finish(f"表情 {meme.key} 已设为黑名单模式")


@daily_limit_matcher.handle()
async def _(matcher: Matcher, session: Uninfo, max_count: Optional[int] = None):
    await handle_daily_limit_command(matcher, session, max_count)
