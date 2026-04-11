from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nonebot
from pydantic import ValidationError
import pytest

nonebot.init()

from nonebot_plugin_memes.config import MemeDailyLimitConfig
from nonebot_plugin_memes.matchers import command as command_matcher
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


def build_session(scene_id: str, scene_type: str, user_id: str):
    return SimpleNamespace(
        self_id="bot",
        scope="qq",
        scene=SimpleNamespace(id=scene_id, type=SimpleNamespace(value=scene_type)),
        user=SimpleNamespace(id=user_id),
    )


@pytest.fixture(autouse=True)
def reset_daily_limit_notice_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(command_matcher, "_daily_limit_notice_date", "")
    monkeypatch.setattr(command_matcher, "_daily_limit_notice_keys", set())


@pytest.fixture(autouse=True)
def reset_superusers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(command_matcher.get_driver().config, "superusers", set())


def clause_columns(session, id_type: SessionIdType) -> set[str]:
    return {str(clause.left) for clause in filter_statement(session, id_type)}


@pytest.mark.parametrize(
    ("id_type", "expected_columns", "unexpected_columns"),
    [
        (
            SessionIdType.USER,
            {
                "nonebot_plugin_uninfo_botmodel.self_id",
                "nonebot_plugin_uninfo_botmodel.scope",
                "nonebot_plugin_uninfo_usermodel.user_id",
            },
            {
                "nonebot_plugin_uninfo_scenemodel.scene_id",
                "nonebot_plugin_uninfo_scenemodel.scene_type",
            },
        ),
        (
            SessionIdType.GROUP,
            {
                "nonebot_plugin_uninfo_botmodel.self_id",
                "nonebot_plugin_uninfo_botmodel.scope",
                "nonebot_plugin_uninfo_scenemodel.scene_id",
                "nonebot_plugin_uninfo_scenemodel.scene_type",
            },
            {
                "nonebot_plugin_uninfo_usermodel.user_id",
            },
        ),
        (
            SessionIdType.GROUP_USER,
            {
                "nonebot_plugin_uninfo_botmodel.self_id",
                "nonebot_plugin_uninfo_botmodel.scope",
                "nonebot_plugin_uninfo_scenemodel.scene_id",
                "nonebot_plugin_uninfo_scenemodel.scene_type",
                "nonebot_plugin_uninfo_usermodel.user_id",
            },
            set(),
        ),
    ],
)
def test_filter_statement_uses_expected_scope_dimensions(
    id_type: SessionIdType,
    expected_columns: set[str],
    unexpected_columns: set[str],
):
    session = build_session("group-1", "group", "user-1")

    columns = clause_columns(session, id_type)

    assert expected_columns.issubset(columns)
    assert columns.isdisjoint(unexpected_columns)


def test_filter_statement_keeps_private_session_scope_for_group_modes():
    private_session = build_session("private-1", "private", "user-1")

    group_columns = clause_columns(private_session, SessionIdType.GROUP)
    uig_columns = clause_columns(private_session, SessionIdType.GROUP_USER)

    assert "nonebot_plugin_uninfo_scenemodel.scene_id" in group_columns
    assert "nonebot_plugin_uninfo_scenemodel.scene_type" in group_columns
    assert "nonebot_plugin_uninfo_usermodel.user_id" not in group_columns

    assert "nonebot_plugin_uninfo_scenemodel.scene_id" in uig_columns
    assert "nonebot_plugin_uninfo_scenemodel.scene_type" in uig_columns
    assert "nonebot_plugin_uninfo_usermodel.user_id" in uig_columns


@pytest.mark.parametrize("invalid_limit", [0, -2])
def test_daily_limit_config_rejects_invalid_group_override_values(invalid_limit: int):
    with pytest.raises(ValidationError):
        MemeDailyLimitConfig(
            mode="UIG",
            max_count=2,
            group_max_count={"group-1": invalid_limit},
        )


def test_daily_limit_config_accepts_negative_one_default_max_count():
    config = MemeDailyLimitConfig(mode="UIG", max_count=-1)

    assert config.max_count == -1


@pytest.mark.parametrize("invalid_limit", [0, -2])
def test_daily_limit_config_rejects_invalid_default_max_count(invalid_limit: int):
    with pytest.raises(ValidationError):
        MemeDailyLimitConfig(mode="UIG", max_count=invalid_limit)


@pytest.mark.asyncio
async def test_check_daily_limit_skips_query_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    count_mock = AsyncMock(return_value=99)

    monkeypatch.setattr(command_matcher.memes_config, "memes_daily_limit", None)
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", count_mock)

    assert await command_matcher.check_daily_limit(
        build_session("group-1", "group", "user-1")
    )
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_daily_limit_skips_query_for_superuser(
    monkeypatch: pytest.MonkeyPatch,
):
    count_mock = AsyncMock(return_value=99)

    monkeypatch.setattr(command_matcher.get_driver().config, "superusers", {"user-1"})
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode="UIG", max_count=2),
    )
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", count_mock)

    assert await command_matcher.check_daily_limit(
        build_session("group-1", "group", "user-1")
    )
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_id_type"),
    [
        ("USER", SessionIdType.USER),
        ("GROUP", SessionIdType.GROUP),
        ("UIG", SessionIdType.GROUP_USER),
    ],
)
async def test_check_daily_limit_uses_mode_and_local_day_window(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_id_type: SessionIdType,
):
    fixed_now = datetime(2026, 4, 10, 15, 30, 45, tzinfo=timezone(timedelta(hours=8)))
    captured: dict[str, object] = {}

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now
            return fixed_now.astimezone(tz)

    async def fake_get_count(session, id_type, **kwargs):
        captured["session"] = session
        captured["id_type"] = id_type
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(command_matcher, "datetime", FixedDateTime)
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode=mode, max_count=2),
    )
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", fake_get_count)

    session = build_session("group-1", "group", "user-1")

    assert await command_matcher.check_daily_limit(session) is True
    assert captured["session"] is session
    assert captured["id_type"] == expected_id_type
    assert captured["time_start"] == fixed_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert captured["time_stop"] == fixed_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    assert captured["time_stop_inclusive"] is False


@pytest.mark.parametrize("mode", ["USER", "GROUP", "UIG"])
def test_get_daily_limit_max_count_returns_none_when_default_max_count_is_unlimited(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode=mode, max_count=-1),
    )

    assert (
        command_matcher.get_daily_limit_max_count(
            build_session("group-1", "group", "user-1")
        )
        is None
    )


@pytest.mark.parametrize("mode", ["GROUP", "UIG"])
def test_get_daily_limit_max_count_uses_group_override_for_group_modes(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(
            mode=mode,
            max_count=9,
            group_max_count={"group-1": 5},
        ),
    )

    assert command_matcher.get_daily_limit_max_count(
        build_session("group-1", "group", "user-1")
    ) == 5


def test_get_daily_limit_max_count_ignores_group_override_for_user_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(
            mode="USER",
            max_count=9,
            group_max_count={"group-1": 5},
        ),
    )

    assert (
        command_matcher.get_daily_limit_max_count(
            build_session("group-1", "group", "user-1")
        )
        == 9
    )


@pytest.mark.parametrize("mode", ["GROUP", "UIG"])
def test_get_daily_limit_max_count_ignores_group_override_in_private_session(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(
            mode=mode,
            max_count=9,
            group_max_count={"private-1": 1},
        ),
    )

    assert (
        command_matcher.get_daily_limit_max_count(
            build_session("private-1", "private", "user-1")
        )
        == 9
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["USER", "GROUP", "UIG"])
async def test_check_daily_limit_skips_query_when_default_max_count_is_unlimited(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    count_mock = AsyncMock(return_value=999)

    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode=mode, max_count=-1),
    )
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", count_mock)

    assert await command_matcher.check_daily_limit(
        build_session("group-1", "group", "user-1")
    )
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["GROUP", "UIG"])
async def test_check_daily_limit_uses_group_override_when_default_max_count_is_unlimited(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    count_mock = AsyncMock(return_value=3)

    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(
            mode=mode,
            max_count=-1,
            group_max_count={"group-1": 3},
        ),
    )
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", count_mock)

    assert (
        await command_matcher.check_daily_limit(
            build_session("group-1", "group", "user-1")
        )
        is False
    )
    count_mock.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["GROUP", "UIG"])
async def test_check_daily_limit_skips_query_in_private_session_when_default_is_unlimited(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    count_mock = AsyncMock(return_value=999)

    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(
            mode=mode,
            max_count=-1,
            group_max_count={"private-1": 1},
        ),
    )
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", count_mock)

    assert await command_matcher.check_daily_limit(
        build_session("private-1", "private", "user-1")
    )
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["GROUP", "UIG"])
async def test_check_daily_limit_skips_query_when_group_override_is_unlimited(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    count_mock = AsyncMock(return_value=999)

    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(
            mode=mode,
            max_count=9,
            group_max_count={"group-1": -1},
        ),
    )
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", count_mock)

    assert await command_matcher.check_daily_limit(
        build_session("group-1", "group", "user-1")
    )
    count_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["GROUP", "UIG"])
async def test_check_daily_limit_uses_group_override_max_count(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
):
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(
            mode=mode,
            max_count=9,
            group_max_count={"group-1": 3},
        ),
    )
    monkeypatch.setattr(
        command_matcher,
        "get_meme_generation_count",
        AsyncMock(return_value=3),
    )

    assert (
        await command_matcher.check_daily_limit(
            build_session("group-1", "group", "user-1")
        )
        is False
    )


@pytest.mark.asyncio
async def test_check_daily_limit_blocks_when_today_count_reaches_max(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode="USER", max_count=2),
    )
    monkeypatch.setattr(
        command_matcher,
        "get_meme_generation_count",
        AsyncMock(return_value=2),
    )

    assert (
        await command_matcher.check_daily_limit(
            build_session("group-1", "group", "user-1")
        )
        is False
    )


@pytest.mark.asyncio
async def test_process_rejects_when_daily_limit_reached(
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
        MemeDailyLimitConfig(mode="UIG", max_count=2),
    )

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
            matcher=matcher,
            session=session,
            meme=meme,
            images=[],
            texts=[],
            options={},
        )

    assert matcher.messages == [command_matcher.DAILY_LIMIT_REACHED_MSG]
    record_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_rejects_with_custom_daily_limit_result(
    monkeypatch: pytest.MonkeyPatch,
):
    matcher = FakeMatcher()

    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(
            mode="UIG",
            max_count=2,
            result="这个群今天的表情包额度已经用完了",
        ),
    )

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
            matcher=matcher,
            session=session,
            meme=meme,
            images=[],
            texts=[],
            options={},
        )

    assert matcher.messages == ["这个群今天的表情包额度已经用完了"]


@pytest.mark.asyncio
async def test_process_daily_limit_notice_only_once_per_user_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode="USER", max_count=2),
    )
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
async def test_process_daily_limit_notice_scope_is_per_group_for_group_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode="GROUP", max_count=2),
    )
    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=False)
    )

    first = FakeMatcher()
    second_same_group = FakeMatcher()
    third_other_group = FakeMatcher()
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
            matcher=first,
            session=build_session("group-1", "group", "user-1"),
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
            matcher=second_same_group,
            session=build_session("group-1", "group", "user-2"),
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
            matcher=third_other_group,
            session=build_session("group-2", "group", "user-2"),
            meme=meme,
            images=[],
            texts=[],
            options={},
        )

    assert first.messages == [command_matcher.DAILY_LIMIT_REACHED_MSG]
    assert second_same_group.messages == [None]
    assert third_other_group.messages == [command_matcher.DAILY_LIMIT_REACHED_MSG]


@pytest.mark.asyncio
async def test_process_daily_limit_notice_scope_is_per_group_user_for_uig_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode="UIG", max_count=2),
    )
    monkeypatch.setattr(
        command_matcher, "check_daily_limit", AsyncMock(return_value=False)
    )

    first = FakeMatcher()
    second_same_group_user = FakeMatcher()
    third_same_group_other_user = FakeMatcher()
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
            matcher=first,
            session=build_session("group-1", "group", "user-1"),
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
            matcher=second_same_group_user,
            session=build_session("group-1", "group", "user-1"),
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
            matcher=third_same_group_other_user,
            session=build_session("group-1", "group", "user-2"),
            meme=meme,
            images=[],
            texts=[],
            options={},
        )

    assert first.messages == [command_matcher.DAILY_LIMIT_REACHED_MSG]
    assert second_same_group_user.messages == [None]
    assert third_same_group_other_user.messages == [
        command_matcher.DAILY_LIMIT_REACHED_MSG
    ]


@pytest.mark.asyncio
async def test_process_daily_limit_notice_resets_on_next_day(
    monkeypatch: pytest.MonkeyPatch,
):
    days = iter(["2026-04-11", "2026-04-11", "2026-04-12"])
    monkeypatch.setattr(command_matcher, "get_today_str", lambda: next(days))
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode="USER", max_count=2),
    )
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
async def test_process_records_only_after_successful_send(
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
    monkeypatch.setattr(command_matcher.memes_config, "memes_daily_limit", None)

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

    assert order == ["send", "record"]


@pytest.mark.asyncio
async def test_process_superuser_does_not_record_generation(
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

    monkeypatch.setattr(command_matcher.get_driver().config, "superusers", {"user-1"})
    monkeypatch.setattr(command_matcher, "UniMessage", OrderedUniMessage)
    monkeypatch.setattr(command_matcher, "record_meme_generation", fake_record)
    monkeypatch.setattr(command_matcher, "run_sync", lambda _func: fake_generate)
    monkeypatch.setattr(command_matcher, "get_meme_generation_count", AsyncMock())
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode="UIG", max_count=2),
    )

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
async def test_process_does_not_record_when_default_limit_is_unlimited(
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
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(mode="UIG", max_count=-1),
    )

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
async def test_process_records_when_group_override_enables_limit(
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
    monkeypatch.setattr(
        command_matcher.memes_config,
        "memes_daily_limit",
        MemeDailyLimitConfig(
            mode="UIG",
            max_count=-1,
            group_max_count={"group-1": 2},
        ),
    )

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

    assert order == ["send", "record"]


@pytest.mark.asyncio
async def test_process_does_not_record_when_generation_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    record_mock = AsyncMock()
    matcher = FakeMatcher()

    class DummyFeedback:
        def __init__(self, feedback: str):
            self.feedback = feedback

    async def fake_generate(*_args, **_kwargs):
        return DummyFeedback("生成失败")

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
            session=build_session("group-1", "group", "user-1"),
            meme=meme,
            images=[],
            texts=[],
            options={},
        )

    assert matcher.messages == ["生成失败"]
    record_mock.assert_not_awaited()
