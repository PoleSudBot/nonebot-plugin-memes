from pathlib import Path
from typing import Any, Optional

import yaml
from nonebot import get_driver
from nonebot.compat import model_dump, type_validate_python
from nonebot.log import logger
from nonebot_plugin_localstore import get_config_file
from pydantic import BaseModel, Field, field_validator

config_path = get_config_file("nonebot_plugin_memes", "daily_limit.yml")
LEGACY_PRIVATE_MAX_COUNT_KEY = "__legacy__"


class DailyLimitConfig(BaseModel):
    private_max_count: dict[str, int] = Field(default_factory=dict)
    group_max_count: dict[str, int] = Field(default_factory=dict)

    @field_validator("private_max_count", mode="before")
    @classmethod
    def validate_private_max_count(cls, value: Any) -> dict[str, int]:
        if value is None:
            return {}
        if isinstance(value, int):
            if value == -1:
                return {}
            if value > 0:
                return {LEGACY_PRIVATE_MAX_COUNT_KEY: value}
            raise ValueError("private_max_count values must be -1 or greater than 0")

        for max_count in value.values():
            if max_count == -1 or max_count > 0:
                continue
            raise ValueError("private_max_count values must be -1 or greater than 0")
        return value

    @field_validator("group_max_count")
    @classmethod
    def validate_group_max_count(cls, value: dict[str, int]) -> dict[str, int]:
        for max_count in value.values():
            if max_count == -1 or max_count > 0:
                continue
            raise ValueError("group_max_count values must be -1 or greater than 0")
        return value


def scope_value(scope: Any) -> Any:
    return getattr(scope, "value", scope)


def is_superuser(session: Any) -> bool:
    user = getattr(session, "user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    return str(user_id) in get_driver().config.superusers


def is_private_scene(session: Any) -> bool:
    scene = getattr(session, "scene", None)
    if scene is None:
        return False
    if hasattr(scene, "is_private"):
        return bool(scene.is_private)
    scene_type = getattr(scene, "type", None)
    return getattr(scene_type, "value", None) == "private"


def get_scene_key(session: Any) -> str:
    scene_path = getattr(session, "scene_path", None)
    if scene_path:
        return f"{scope_value(session.scope)}_{session.self_id}_{scene_path}"

    scene_type = getattr(session.scene.type, "value", session.scene.type)
    return (
        f"{scope_value(session.scope)}_{session.self_id}_"
        f"{scene_type}_{session.scene.id}"
    )


def get_daily_limit_notice_key(session: Any) -> str:
    scene_type = getattr(session.scene.type, "value", session.scene.type)
    return (
        f"{scope_value(session.scope)}:{session.self_id}:"
        f"scene:{scene_type}:{session.scene.id}:user:{session.user.id}"
    )


def normalize_max_count(max_count: Optional[int]) -> Optional[int]:
    if max_count in (None, -1):
        return None
    return max_count


def get_private_key(session: Any) -> str:
    return f"{scope_value(session.scope)}_{session.self_id}"


class DailyLimitManager:
    def __init__(self, path: Path = config_path):
        self.__path = path
        self.__config = DailyLimitConfig()
        self.__load()
        self.__dump()

    def get_private_max_count(self, session: Any) -> Optional[int]:
        key = get_private_key(session)
        if key in self.__config.private_max_count:
            return normalize_max_count(self.__config.private_max_count[key])

        return normalize_max_count(
            self.__config.private_max_count.get(LEGACY_PRIVATE_MAX_COUNT_KEY)
        )

    def set_private_max_count(self, session: Any, max_count: int):
        self.__config.private_max_count[get_private_key(session)] = max_count
        self.__dump()

    def get_group_max_count(self, session: Any) -> Optional[int]:
        max_count = self.__config.group_max_count.get(get_scene_key(session))
        return normalize_max_count(max_count)

    def set_group_max_count(self, session: Any, max_count: int):
        key = get_scene_key(session)
        if max_count == -1:
            self.__config.group_max_count.pop(key, None)
        else:
            self.__config.group_max_count[key] = max_count
        self.__dump()

    def get_limit_max_count(self, session: Any) -> Optional[int]:
        if is_private_scene(session):
            return self.get_private_max_count(session)
        return self.get_group_max_count(session)

    def __load(self):
        raw_data: dict[str, Any] = {}
        if self.__path.exists():
            with self.__path.open("r", encoding="utf-8") as f:
                try:
                    raw_data = yaml.safe_load(f) or {}
                except Exception:
                    logger.warning("表情包次数限制配置解析失败，将重新生成")

        try:
            self.__config = type_validate_python(DailyLimitConfig, raw_data)
        except Exception:
            self.__config = DailyLimitConfig()
            logger.warning("表情包次数限制配置解析失败，将重新生成")

    def __dump(self):
        self.__path.parent.mkdir(parents=True, exist_ok=True)
        with self.__path.open("w", encoding="utf-8") as f:
            yaml.dump(model_dump(self.__config), f, allow_unicode=True)


daily_limit_manager = DailyLimitManager()
