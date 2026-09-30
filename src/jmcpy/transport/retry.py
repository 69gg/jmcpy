"""重试与多端点切换的决策器。

这里只有纯逻辑：决定「下一次该向哪个端点、等多久再发」。真正的 I/O 与 sleep
由 :mod:`jmcpy.transport.session` 负责，因此本模块可以完全离线测试。

两种模式：

``RETRY_FIRST``
    逐个端点推进。对每个端点连续尝试 ``retry_times + 1`` 次，用尽后再换下一个端点。

``ROTATE_FIRST``
    按轮推进。每轮把全部端点各试一次，整轮都失败后进入下一轮，共 ``retry_times + 1`` 轮。
    端点较多时这种模式能更快绕开单点故障。

退避时长只与「这是第几次尝试」有关（第 1 次不等，之后按 ``min(backoff_max, base * 2 ** (n - 1))``
递增），再乘以 ``1 ± backoff_jitter`` 的随机系数。
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from ..enums import RetryMode
from ..errors import AttemptFailure, ConfigurationError

__all__ = ["Attempt", "RetryPlanner"]


@dataclass(frozen=True, slots=True)
class Attempt:
    """一次尝试的计划。"""

    #: 全局递增的尝试序号，从 1 开始
    index: int
    #: 目标端点
    endpoint: str
    #: 端点下标
    endpoint_index: int
    #: ``RETRY_FIRST`` 下是该端点内的第几次尝试，``ROTATE_FIRST`` 下是第几轮，均从 1 开始
    round: int
    #: 发送前需要等待的秒数
    delay: float


class RetryPlanner:
    """按 :class:`~jmcpy.settings.Settings` 的配置生成尝试序列。"""

    def __init__(
        self,
        endpoints: Sequence[str],
        *,
        mode: RetryMode,
        retry_times: int,
        backoff_base: float,
        backoff_max: float,
        backoff_jitter: float = 0.0,
        rng: random.Random | None = None,
    ) -> None:
        if not endpoints:
            raise ConfigurationError("端点列表为空，无法发起请求")
        if retry_times < 0:
            raise ConfigurationError("retry_times 不能为负数")

        self._endpoints = tuple(endpoints)
        self._mode = mode
        self._rng = rng if rng is not None else random.Random()
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._backoff_jitter = backoff_jitter
        self._schedule = self._build_schedule(retry_times)
        self._cursor = 0
        self._skipped: set[int] = set()
        self._skip_delay = False
        self._failures: list[AttemptFailure] = []

    @property
    def endpoints(self) -> tuple[str, ...]:
        return self._endpoints

    @property
    def total_attempts(self) -> int:
        """本次请求最多会尝试多少次。"""
        return len(self._schedule)

    @property
    def attempts_made(self) -> int:
        return self._cursor

    @property
    def failures(self) -> tuple[AttemptFailure, ...]:
        return tuple(self._failures)

    def plan(self) -> Attempt | None:
        """取出下一次尝试；返回 ``None`` 表示已无可用尝试。"""
        while self._cursor < len(self._schedule) and self._schedule[self._cursor][0] in self._skipped:
            self._cursor += 1
        if self._cursor >= len(self._schedule):
            return None

        endpoint_index, round_index = self._schedule[self._cursor]
        wait = self._cursor > 0 and not self._skip_delay
        attempt = Attempt(
            index=self._cursor + 1,
            endpoint=self._endpoints[endpoint_index],
            endpoint_index=endpoint_index,
            round=round_index,
            delay=self._backoff(self._cursor) if wait else 0.0,
        )
        self._cursor += 1
        self._skip_delay = False
        return attempt

    def skip_endpoint(self, endpoint_index: int) -> None:
        """跳过该端点剩余的全部尝试。

        用于「这个端点根本不可用」的情况（例如被反爬验证页拦截、地区封锁）：
        在这些端点上重试没有意义，直接推进到下一个端点；下一次尝试也不等待——
        换线路本身就是补救手段，等待并不会让被拦的线路变得可用。
        """
        self._skipped.add(endpoint_index)
        self._skip_delay = True

    def record(self, attempt: Attempt | None, url: str, error: Exception) -> None:
        """登记一次失败。"""
        self._failures.append(
            AttemptFailure(
                attempt=attempt.index if attempt is not None else len(self._failures) + 1,
                endpoint=attempt.endpoint if attempt is not None else None,
                url=url,
                error=error,
            )
        )

    # ------------------------------------------------------------------ 内部
    def _build_schedule(self, retry_times: int) -> list[tuple[int, int]]:
        rounds = retry_times + 1
        if self._mode is RetryMode.ROTATE_FIRST:
            return [
                (index, round_index) for round_index in range(1, rounds + 1) for index in range(len(self._endpoints))
            ]
        return [(index, attempt + 1) for index in range(len(self._endpoints)) for attempt in range(rounds)]

    def _backoff(self, failures: int) -> float:
        if failures <= 0:
            return 0.0
        delay = min(self._backoff_max, self._backoff_base * (2.0 ** (failures - 1)))
        if self._backoff_jitter:
            spread = delay * self._backoff_jitter
            delay += self._rng.uniform(-spread, spread)
        return max(0.0, delay)
