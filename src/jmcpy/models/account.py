"""账号模型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Account"]


@dataclass(frozen=True, slots=True)
class Account:
    """登录用户资料。"""

    uid: str = ""
    username: str = ""
    email: str | None = None
    email_verified: bool = False
    avatar_url: str | None = None
    gender: str | None = None
    message: str | None = None
    coins: int | None = None
    favorites: int | None = None
    favorites_limit: int | None = None
    level: int | None = None
    level_name: str | None = None
    experience: int | None = None
    experience_percent: float | None = None
    next_level_experience: int | None = None
    ad_free: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.username}({self.uid})"
