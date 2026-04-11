from datetime import timedelta
from typing import Literal, Optional

from nonebot import get_plugin_config
from nonebot.log import logger
from pydantic import BaseModel, Field, field_validator


class MemeListImageConfig(BaseModel):
    sort_by: Literal[
        "key", "keywords", "keywords_pinyin", "date_created", "date_modified"
    ] = "keywords_pinyin"
    sort_reverse: bool = False
    text_template: str = "{index}. {keywords}"
    add_category_icon: bool = True
    label_new_timedelta: timedelta = timedelta(days=30)
    label_hot_threshold: int = 21
    label_hot_days: int = 7


class MemeParamsMismatchPolicy(BaseModel):
    too_much_text: Literal["ignore", "prompt", "drop"] = "ignore"
    too_few_text: Literal["ignore", "prompt", "get"] = "ignore"
    too_much_image: Literal["ignore", "prompt", "drop"] = "ignore"
    too_few_image: Literal["ignore", "prompt", "get"] = "ignore"


class MultipleImageConfig(BaseModel):
    direct_send_threshold: int = 10
    send_zip_file: bool = True
    send_forward_msg: bool = False


class MemeDailyLimitConfig(BaseModel):
    mode: Literal["USER", "GROUP", "UIG"] = "UIG"
    max_count: int = -1
    result: Optional[str] = None
    group_max_count: dict[str, int] = Field(default_factory=dict)

    @field_validator("max_count")
    @classmethod
    def validate_max_count(cls, value: int) -> int:
        if value == -1 or value > 0:
            return value
        raise ValueError("max_count must be -1 or greater than 0")

    @field_validator("group_max_count")
    @classmethod
    def validate_group_max_count(cls, value: dict[str, int]) -> dict[str, int]:
        for max_count in value.values():
            if max_count == -1:
                continue
            if max_count <= 0:
                raise ValueError(
                    "group_max_count values must be -1 or greater than 0"
                )
        return value


class Config(BaseModel):
    memes_command_prefixes: Optional[list[str]] = None
    memes_disabled_list: list[str] = []
    memes_check_resources_on_startup: bool = True
    memes_params_mismatch_policy: MemeParamsMismatchPolicy = MemeParamsMismatchPolicy()
    memes_use_sender_when_no_image: bool = False
    memes_use_default_when_no_text: bool = False
    memes_random_meme_show_info: bool = True
    memes_list_image_config: MemeListImageConfig = MemeListImageConfig()
    memes_multiple_image_config: MultipleImageConfig = MultipleImageConfig()
    memes_daily_limit: Optional[MemeDailyLimitConfig] = None


memes_config = get_plugin_config(Config)


if memes_config.memes_check_resources_on_startup:
    from meme_generator.resources import check_resources_in_background
    from nonebot import get_driver

    driver = get_driver()

    @driver.on_startup
    def _():
        logger.info("正在检查资源文件...")
        check_resources_in_background()
