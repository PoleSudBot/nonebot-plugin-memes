from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
import pytest

nonebot.init()

from nonebot_plugin_memes.config import MemeDailyLimitConfig
from nonebot_plugin_memes.daily_limit_manager import DailyLimitManager
from nonebot_plugin_memes.matchers import command as command_matcher
from nonebot_plugin_memes.matchers import manage as manage_matcher
from nonebot_plugin_memes.recorder import SessionIdType, filter_statement


@pytest.fixture(scope="session", autouse=True)
async def nonebug_init():
    return None


class FinishCalled(Exception):
    pass


class FakeMatcher:
    def __init__(self):
        self.messages: list[str | None] = []

    async def finish(self, message: str | None = None):
        self.messages.append(message)
        raise FinishCalled


class FakeUniMessage:
    def __init__(self):
        self.parts: list[object] = []

    @staticmethod
    def image(*, raw: bytes):
        return ("image", raw)

    def __iadd__(self, item: object):
        self.parts.append(item)
        return self

    async def send(self):
        return None


def build_session(
    scene_id: str,
    scene_type: str,
    user_id: str,
    *,
    role_level: int = 1,
    self_id: str = "bot",
    scope: str = "qq",
):
    is_private = scene_type == "private"
    member = None
    if not is_private:
        member = SimpleNamespace(role=SimpleNamespace(level=role_level), nick=None)
    return SimpleNamespace(
        self_id=self_id,
        scope=scope,
        scene=SimpleNamespace(
            id=scene_id,
            type=SimpleNamespace(value=scene_type),
            is_private=is_private,
        ),
        scene_path=f"{scene_type}:{scene_id}",
        user=SimpleNamespace(id=user_id, avatar=None, nick=None, name=user_id),
        member=member,
    )


@pytest.fixture(autouse=True)
def reset_daily_limit_notice_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(command_matcher, "_daily_limit_notice_date", "")
    monkeypatch.setattr(command_matcher, "_daily_limit_notice_keys", set())


@pytest.fixture(autouse=True)
def reset_superusers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(command_matcher.get_driver().config, "superusers", set())


@pytest.fixture(autouse=True)
def reset_daily_limit_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(command_matcher.memes_config, "memes_daily_limit", None)


@pytest.fixture(autouse=True)
def fresh_daily_limit_manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    manager = DailyLimitManager(tmp_path / "daily_limit.yml")
    monkeypatch.setattr(command_matcher, "daily_limit_manager", manager)
    monkeypatch.setattr(manage_matcher, "daily_limit_manager", manager)
    return manager


def clause_columns(session, id_type: SessionIdType) -> set[str]:
    return {str(clause.left) for clause in filter_statement(session, id_type)}


def test_filter_statement_uses_group_user_scope_dimensions():
    session = build_session("group-1", "group", "user-1")

    columns = clause_columns(session, SessionIdType.GROUP_USER)

    assert {
        "nonebot_plugin_uninfo_botmodel.self_id",
        "nonebot_plugin_uninfo_botmodel.scope",
        "nonebot_plugin_uninfo_scenemodel.scene_id",
        "nonebot_plugin_uninfo_scenemodel.scene_type",
        "nonebot_plugin_uninfo_usermodel.user_id",
    }.issubset(columns)


def test_filter_statement_keeps_private_session_scope_for_group_user_mode():
    private_session = build_session("private-1", "private", "user-1")

    columns = clause_columns(private_session, SessionIdType.GROUP_USER)

    assert "nonebot_plugin_uninfo_scenemodel.scene_id" in columns
    assert "nonebot_plugin_uninfo_scenemodel.scene_type" in columns
    assert "nonebot_plugin_uninfo_usermodel.user_id" in columns


def test_daily_limit_manager_persists_private_and_group_limits(tmp_path: Path):
    path = tmp_path / "daily_limit.yml"
    manager = DailyLimitManager(path)
    group_session = build_session("group-1", "group", "user-1")
    private_session = build_session("private-1", "private", "user-1")

    assert manager.get_limit_max_count(group_session) is None
    assert manager.get_limit_max_count(private_session) is None

    manager.set_group_max_count(group_session, 5)
    manager.set_private_max_count(private_session, 3)

    reloaded = DailyLimitManager(path)
    assert reloaded.get_limit_max_count(group_session) == 5
    assert reloaded.get_limit_max_count(private_session) == 3

    reloaded.set_group_max_count(group_session, -1)
    assert reloaded.get_limit_max_count(group_session) is None


def test_daily_limit_manager_group_limits_are_isolated(
    fresh_daily_limit_manager: DailyLimitManager,
):
    first_group = build_session("group-1", "group", "user-1")
    second_group = build_session("group-2", "group", "user-1")

    fresh_daily_limit_manager.set_group_max_count(first_group, 2)

    assert fresh_daily_limit_manager.get_limit_max_count(first_group) == 2
    assert fresh_daily_limit_manager.get_limit_max_count(second_group) is None


def test_daily_limit_manager_private_limits_are_isolated_by_bot(
    fresh_daily_limit_manager: DailyLimitManager,
):
    first_private = build_session(
        "private-1", "private", "user-1", self_id="bot-a", scope="qq"
    )
    second_private = build_session(
        "private-1", "private", "user-1", self_id="bot-b", scope="qq"
    )

    fresh_daily_limit_manager.set_private_max_count(first_private, 2)

    assert fresh_daily_limit_manager.get_limit_max_count(first_private) == 2
    assert fresh_daily_limit_manager.get_limit_max_count(second_private) is None


def test_daily_limit_manager_reads_legacy_private_scalar(tmp_path: Path):
    path = tmp_path / "daily_limit.yml"
    path.write_text("private_max_count: 4\ngroup_max_count: {}\n", encoding="utf-8")
    manager = DailyLimitManager(path)
    session = build_session("private-1", "private", "user-1")

    assert manager.get_limit_max_count(session) == 4


@pytest.mark.asyncio
async def test_check_daily_limit_skips_query_when_group_limit_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    count_mock = AsyncMock(return_value=99)
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", count_mock)

    assert await command_matcher.check_daily_limit(
        build_session("group-1", "group", "user-1")
    )
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_daily_limit_skips_query_when_private_limit_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    count_mock = AsyncMock(return_value=99)
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", count_mock)

    assert await command_matcher.check_daily_limit(
        build_session("private-1", "private", "user-1")
    )
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_daily_limit_skips_query_for_superuser(
    monkeypatch: pytest.MonkeyPatch,
    fresh_daily_limit_manager: DailyLimitManager,
):
    session = build_session("group-1", "group", "user-1")
    count_mock = AsyncMock(return_value=99)

    fresh_daily_limit_manager.set_group_max_count(session, 2)
    monkeypatch.setattr(command_matcher.get_driver().config, "superusers", {"user-1"})
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", count_mock)

    assert await command_matcher.check_daily_limit(session)
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_daily_limit_uses_group_user_and_local_day_window_for_group_limit(
    monkeypatch: pytest.MonkeyPatch,
    fresh_daily_limit_manager: DailyLimitManager,
):
    fixed_now = datetime(2026, 4, 10, 15, 30, 45, tzinfo=timezone(timedelta(hours=8)))
    captured: dict[str, object] = {}
    session = build_session("group-1", "group", "user-1")

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now
            return fixed_now.astimezone(tz)

    async def fake_get_count(session_arg, id_type, **kwargs):
        captured["session"] = session_arg
        captured["id_type"] = id_type
        captured.update(kwargs)
        return 1

    fresh_daily_limit_manager.set_group_max_count(session, 2)
    monkeypatch.setattr(command_matcher, "datetime", FixedDateTime)
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", fake_get_count)

    assert await command_matcher.check_daily_limit(session) is True
    assert captured["session"] is session
    assert captured["id_type"] == SessionIdType.GROUP_USER
    assert captured["time_start"] == fixed_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert captured["time_stop"] == fixed_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    assert captured["time_stop_inclusive"] is False


@pytest.mark.asyncio
async def test_check_daily_limit_uses_private_limit(
    monkeypatch: pytest.MonkeyPatch,
    fresh_daily_limit_manager: DailyLimitManager,
):
    session = build_session("private-1", "private", "user-1")

    fresh_daily_limit_manager.set_private_max_count(session, 2)
    monkeypatch.setattr(
        command_matcher,
        "get_meme_generation_count",
        AsyncMock(return_value=2),
    )

    assert await command_matcher.check_daily_limit(session) is False


def test_can_manage_daily_limit_allows_group_admin_and_superuser(
    monkeypatch: pytest.MonkeyPatch,
):
    group_admin = build_session("group-1", "group", "user-1", role_level=2)
    private_superuser = build_session("private-1", "private", "user-2")

    monkeypatch.setattr(command_matcher.get_driver().config, "superusers", {"user-2"})

    assert manage_matcher.can_manage_daily_limit(group_admin) is True
    assert manage_matcher.can_manage_daily_limit(private_superuser) is True


def test_can_manage_daily_limit_rejects_group_member_and_private_non_superuser():
    group_member = build_session("group-1", "group", "user-1", role_level=1)
    private_user = build_session("private-1", "private", "user-2")

    assert manage_matcher.can_manage_daily_limit(group_member) is False
    assert manage_matcher.can_manage_daily_limit(private_user) is False


@pytest.mark.asyncio
async def test_handle_daily_limit_command_queries_group_status(
    fresh_daily_limit_manager: DailyLimitManager,
):
    matcher = FakeMatcher()

    with pytest.raises(FinishCalled):
        await manage_matcher.handle_daily_limit_command(
            matcher,
            build_session("group-1", "group", "user-1", role_level=2),
            None,
        )

    assert matcher.messages == ["当前群未开启表情包次数限制"]


@pytest.mark.asyncio
async def test_handle_daily_limit_command_sets_group_limit(
    fresh_daily_limit_manager: DailyLimitManager,
):
    matcher = FakeMatcher()
    session = build_session("group-1", "group", "user-1", role_level=2)

    with pytest.raises(FinishCalled):
        await manage_matcher.handle_daily_limit_command(matcher, session, 5)

    assert fresh_daily_limit_manager.get_limit_max_count(session) == 5
    assert matcher.messages == ["已设置当前群内每人每日可制作 5 次表情包"]


@pytest.mark.asyncio
async def test_handle_daily_limit_command_closes_group_limit(
    fresh_daily_limit_manager: DailyLimitManager,
):
    matcher = FakeMatcher()
    session = build_session("group-1", "group", "user-1", role_level=2)
    fresh_daily_limit_manager.set_group_max_count(session, 5)

    with pytest.raises(FinishCalled):
        await manage_matcher.handle_daily_limit_command(matcher, session, -1)

    assert fresh_daily_limit_manager.get_limit_max_count(session) is None
    assert matcher.messages == ["已关闭当前群的表情包次数限制"]


@pytest.mark.asyncio
async def test_handle_daily_limit_command_queries_private_status(
    fresh_daily_limit_manager: DailyLimitManager,
):
    matcher = FakeMatcher()
    session = build_session("private-1", "private", "user-1")
    fresh_daily_limit_manager.set_private_max_count(session, 3)

    with pytest.raises(FinishCalled):
        await manage_matcher.handle_daily_limit_command(matcher, session, None)

    assert matcher.messages == ["当前普通用户私聊每日可制作 3 次表情包"]


@pytest.mark.asyncio
async def test_handle_daily_limit_command_sets_private_limit(
    fresh_daily_limit_manager: DailyLimitManager,
):
    matcher = FakeMatcher()
    session = build_session("private-1", "private", "user-1")

    with pytest.raises(FinishCalled):
        await manage_matcher.handle_daily_limit_command(matcher, session, 4)

    assert fresh_daily_limit_manager.get_limit_max_count(session) == 4
    assert matcher.messages == ["已设置普通用户私聊每日可制作 4 次表情包"]


@pytest.mark.asyncio
@pytest.mark.parametrize("max_count", [0, -2])
async def test_handle_daily_limit_command_rejects_invalid_count(
    max_count: int,
):
    matcher = FakeMatcher()

    with pytest.raises(FinishCalled):
        await manage_matcher.handle_daily_limit_command(
            matcher,
            build_session("group-1", "group", "user-1", role_level=2),
            max_count,
        )

    assert matcher.messages == ["次数必须为 -1 或正整数"]


@pytest.mark.asyncio
async def test_process_rejects_when_daily_limit_reached_with_custom_result(
    monkeypatch: pytest.MonkeyPatch,
):
    matcher = FakeMatcher()
    record_mock = AsyncMock()

    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(command_matcher, "record_meme_generation", record_mock)
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(result="这个群今天的表情包额度已经用完了"),
    )

    meme = SimpleNamespace(
        key="test",
        generate=lambda *_args, **_kwargs: None,
        info=SimpleNamespace(keywords=["测试"]),
    )

    with pytest.raises(FinishCalled):
        await command_matcher.process(
            bot=SimpleNamespace(),
            event=SimpleNamespace(),
            state={},
            matcher=matcher,
            session=build_session("group-1", "group", "user-1"),
            meme=meme,
            images=[],
            texts=[],
            options={},
        )

    assert matcher.messages == ["这个群今天的表情包额度已经用完了"]
    record_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_daily_limit_notice_only_once_per_same_group_user(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=False)
    )

    first = FakeMatcher()
    second = FakeMatcher()
    meme = SimpleNamespace(
        key="test",
        generate=lambda *_args, **_kwargs: None,
        info=SimpleNamespace(keywords=["测试"]),
    )
    session = build_session("group-1", "group", "user-1")

    with pytest.raises(FinishCalled):
        await command_matcher.process(
            bot=SimpleNamespace(),
            event=SimpleNamespace(),
            state={},
            matcher=first,
            session=session,
            meme=meme,
            images=[],
            texts=[],
            options={},
        )

    with pytest.raises(FinishCalled):
        await command_matcher.process(
            bot=SimpleNamespace(),
            event=SimpleNamespace(),
            state={},
            matcher=second,
            session=session,
            meme=meme,
            images=[],
            texts=[],
            options={},
        )

    assert first.messages == [command_matcher.DAILY_LIMIT_REACHED_MSG]
    assert second.messages == [None]


@pytest.mark.asyncio
async def test_process_daily_limit_notice_resets_on_next_day(
    monkeypatch: pytest.MonkeyPatch,
):
    days = iter(["2026-04-11", "2026-04-11", "2026-04-12"])
    monkeypatch.setattr(command_matcher, "get_today_str", lambda: next(days))
    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=False)
    )

    first = FakeMatcher()
    second = FakeMatcher()
    third = FakeMatcher()
    meme = SimpleNamespace(
        key="test",
        generate=lambda *_args, **_kwargs: None,
        info=SimpleNamespace(keywords=["测试"]),
    )
    session = build_session("group-1", "group", "user-1")

    for matcher in [first, second, third]:
        with pytest.raises(FinishCalled):
            await command_matcher.process(
                bot=SimpleNamespace(),
                event=SimpleNamespace(),
                state={},
                matcher=matcher,
                session=session,
                meme=meme,
                images=[],
                texts=[],
                options={},
            )

    assert first.messages == [command_matcher.DAILY_LIMIT_REACHED_MSG]
    assert second.messages == [None]
    assert third.messages == [command_matcher.DAILY_LIMIT_REACHED_MSG]


@pytest.mark.asyncio
async def test_process_records_only_after_successful_send_when_group_limit_enabled(
    monkeypatch: pytest.MonkeyPatch,
    fresh_daily_limit_manager: DailyLimitManager,
):
    order: list[str] = []
    session = build_session("group-1", "group", "user-1")

    class OrderedUniMessage(FakeUniMessage):
        async def send(self):
            order.append("send")

    async def fake_record(_session, _meme_key):
        order.append("record")

    async def fake_generate(*_args, **_kwargs):
        return b"generated-image"

    fresh_daily_limit_manager.set_group_max_count(session, 2)
    monkeypatch.setattr(command_matcher, "UniMessage", OrderedUniMessage)
    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(command_matcher, "record_meme_generation", fake_record)
    monkeypatch.setattr(command_matcher, "run_sync", lambda _func: fake_generate)

    meme = SimpleNamespace(
        key="test",
        generate=lambda *_args, **_kwargs: None,
        info=SimpleNamespace(keywords=["测试"]),
    )

    await command_matcher.process(
        bot=SimpleNamespace(),
        event=SimpleNamespace(),
        state={},
        matcher=FakeMatcher(),
        session=session,
        meme=meme,
        images=[],
        texts=[],
        options={},
    )

    assert order == ["send", "record"]


@pytest.mark.asyncio
async def test_process_does_not_record_when_group_limit_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    order: list[str] = []

    class OrderedUniMessage(FakeUniMessage):
        async def send(self):
            order.append("send")

    async def fake_record(_session, _meme_key):
        order.append("record")

    async def fake_generate(*_args, **_kwargs):
        return b"generated-image"

    monkeypatch.setattr(command_matcher, "UniMessage", OrderedUniMessage)
    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(command_matcher, "record_meme_generation", fake_record)
    monkeypatch.setattr(command_matcher, "run_sync", lambda _func: fake_generate)

    meme = SimpleNamespace(
        key="test",
        generate=lambda *_args, **_kwargs: None,
        info=SimpleNamespace(keywords=["测试"]),
    )

    await command_matcher.process(
        bot=SimpleNamespace(),
        event=SimpleNamespace(),
        state={},
        matcher=FakeMatcher(),
        session=build_session("group-1", "group", "user-1"),
        meme=meme,
        images=[],
        texts=[],
        options={},
    )

    assert order == ["send"]


@pytest.mark.asyncio
async def test_process_records_in_private_when_private_limit_enabled(
    monkeypatch: pytest.MonkeyPatch,
    fresh_daily_limit_manager: DailyLimitManager,
):
    order: list[str] = []
    session = build_session("private-1", "private", "user-1")

    class OrderedUniMessage(FakeUniMessage):
        async def send(self):
            order.append("send")

    async def fake_record(_session, _meme_key):
        order.append("record")

    async def fake_generate(*_args, **_kwargs):
        return b"generated-image"

    fresh_daily_limit_manager.set_private_max_count(session, 2)
    monkeypatch.setattr(command_matcher, "UniMessage", OrderedUniMessage)
    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(command_matcher, "record_meme_generation", fake_record)
    monkeypatch.setattr(command_matcher, "run_sync", lambda _func: fake_generate)

    meme = SimpleNamespace(
        key="test",
        generate=lambda *_args, **_kwargs: None,
        info=SimpleNamespace(keywords=["测试"]),
    )

    await command_matcher.process(
        bot=SimpleNamespace(),
        event=SimpleNamespace(),
        state={},
        matcher=FakeMatcher(),
        session=session,
        meme=meme,
        images=[],
        texts=[],
        options={},
    )

    assert order == ["send", "record"]


@pytest.mark.asyncio
async def test_process_does_not_record_when_private_limit_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    order: list[str] = []

    class OrderedUniMessage(FakeUniMessage):
        async def send(self):
            order.append("send")

    async def fake_record(_session, _meme_key):
        order.append("record")

    async def fake_generate(*_args, **_kwargs):
        return b"generated-image"

    monkeypatch.setattr(command_matcher, "UniMessage", OrderedUniMessage)
    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(command_matcher, "record_meme_generation", fake_record)
    monkeypatch.setattr(command_matcher, "run_sync", lambda _func: fake_generate)

    meme = SimpleNamespace(
        key="test",
        generate=lambda *_args, **_kwargs: None,
        info=SimpleNamespace(keywords=["测试"]),
    )

    await command_matcher.process(
        bot=SimpleNamespace(),
        event=SimpleNamespace(),
        state={},
        matcher=FakeMatcher(),
        session=build_session("private-1", "private", "user-1"),
        meme=meme,
        images=[],
        texts=[],
        options={},
    )

    assert order == ["send"]


@pytest.mark.asyncio
async def test_process_superuser_does_not_record_generation(
    monkeypatch: pytest.MonkeyPatch,
    fresh_daily_limit_manager: DailyLimitManager,
):
    order: list[str] = []
    session = build_session("group-1", "group", "user-1")

    class OrderedUniMessage(FakeUniMessage):
        async def send(self):
            order.append("send")

    async def fake_record(_session, _meme_key):
        order.append("record")

    async def fake_generate(*_args, **_kwargs):
        return b"generated-image"

    fresh_daily_limit_manager.set_group_max_count(session, 2)
    monkeypatch.setattr(command_matcher.get_driver().config, "superusers", {"user-1"})
    monkeypatch.setattr(command_matcher, "UniMessage", OrderedUniMessage)
    monkeypatch.setattr(command_matcher, "record_meme_generation", fake_record)
    monkeypatch.setattr(command_matcher, "run_sync", lambda _func: fake_generate)
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", AsyncMock())

    meme = SimpleNamespace(
        key="test",
        generate=lambda *_args, **_kwargs: None,
        info=SimpleNamespace(keywords=["测试"]),
    )

    await command_matcher.process(
        bot=SimpleNamespace(),
        event=SimpleNamespace(),
        state={},
        matcher=FakeMatcher(),
        session=session,
        meme=meme,
        images=[],
        texts=[],
        options={},
    )

    assert order == ["send"]


@pytest.mark.asyncio
async def test_process_does_not_record_when_generation_fails(
    monkeypatch: pytest.MonkeyPatch,
    fresh_daily_limit_manager: DailyLimitManager,
):
    record_mock = AsyncMock()
    matcher = FakeMatcher()
    session = build_session("group-1", "group", "user-1")

    class DummyFeedback:
        def __init__(self, feedback: str):
            self.feedback = feedback

    async def fake_generate(*_args, **_kwargs):
        return DummyFeedback("生成失败")

    fresh_daily_limit_manager.set_group_max_count(session, 2)
    monkeypatch.setattr(command_matcher, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(command_matcher, "MemeFeedback", DummyFeedback)
    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(command_matcher, "record_meme_generation", record_mock)
    monkeypatch.setattr(command_matcher, "run_sync", lambda _func: fake_generate)

    meme = SimpleNamespace(
        key="test",
        generate=lambda *_args, **_kwargs: None,
        info=SimpleNamespace(keywords=["测试"]),
    )

    with pytest.raises(FinishCalled):
        await command_matcher.process(
            bot=SimpleNamespace(),
            event=SimpleNamespace(),
            state={},
            matcher=matcher,
            session=session,
            meme=meme,
            images=[],
            texts=[],
            options={},
        )

    assert matcher.messages == ["生成失败"]
    record_mock.assert_not_awaited()
