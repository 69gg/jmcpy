"""重试与端点切换的决策逻辑（纯逻辑，不涉及网络）。"""

from __future__ import annotations

import random

import pytest

from jmcpy.enums import RetryMode
from jmcpy.errors import ConfigurationError
from jmcpy.transport.retry import Attempt, RetryPlanner


def make_planner(
    endpoints: tuple[str, ...] = ("a.example", "b.example", "c.example"),
    *,
    mode: RetryMode = RetryMode.RETRY_FIRST,
    retry_times: int = 1,
    backoff_base: float = 0.5,
    backoff_max: float = 8.0,
    jitter: float = 0.0,
    seed: int = 7,
) -> RetryPlanner:
    return RetryPlanner(
        endpoints,
        mode=mode,
        retry_times=retry_times,
        backoff_base=backoff_base,
        backoff_max=backoff_max,
        backoff_jitter=jitter,
        rng=random.Random(seed),
    )


def drain(planner: RetryPlanner) -> list[Attempt]:
    attempts: list[Attempt] = []
    while (attempt := planner.plan()) is not None:
        attempts.append(attempt)
    return attempts


def test_retry_first_exhausts_one_endpoint_before_switching() -> None:
    attempts = drain(make_planner(mode=RetryMode.RETRY_FIRST, retry_times=1))

    assert [item.endpoint for item in attempts] == [
        "a.example",
        "a.example",
        "b.example",
        "b.example",
        "c.example",
        "c.example",
    ]
    assert [item.round for item in attempts] == [1, 2, 1, 2, 1, 2]
    assert [item.index for item in attempts] == [1, 2, 3, 4, 5, 6]


def test_rotate_first_visits_every_endpoint_each_round() -> None:
    attempts = drain(make_planner(mode=RetryMode.ROTATE_FIRST, retry_times=1))

    assert [item.endpoint for item in attempts] == [
        "a.example",
        "b.example",
        "c.example",
        "a.example",
        "b.example",
        "c.example",
    ]
    assert [item.round for item in attempts] == [1, 1, 1, 2, 2, 2]


@pytest.mark.parametrize("mode", [RetryMode.RETRY_FIRST, RetryMode.ROTATE_FIRST])
def test_single_endpoint_degrades_to_plain_retry(mode: RetryMode) -> None:
    attempts = drain(make_planner(("only.example",), mode=mode, retry_times=3))

    assert [item.endpoint for item in attempts] == ["only.example"] * 4
    assert [item.index for item in attempts] == [1, 2, 3, 4]


@pytest.mark.parametrize("mode", [RetryMode.RETRY_FIRST, RetryMode.ROTATE_FIRST])
def test_zero_retry_times_means_single_pass(mode: RetryMode) -> None:
    attempts = drain(make_planner(mode=mode, retry_times=0))

    assert [item.endpoint for item in attempts] == ["a.example", "b.example", "c.example"]


def test_total_attempts_matches_plan_length() -> None:
    planner = make_planner(retry_times=2)

    assert planner.total_attempts == 3 * 3


def test_first_attempt_has_no_delay_then_exponential_backoff_with_cap() -> None:
    planner = make_planner(("only.example",), retry_times=5, backoff_base=0.5, backoff_max=2.0)

    delays = [item.delay for item in drain(planner)]

    assert delays[0] == 0.0
    assert delays[1:] == [0.5, 1.0, 2.0, 2.0, 2.0]


def test_jitter_stays_within_configured_ratio() -> None:
    planner = make_planner(("only.example",), retry_times=4, backoff_base=1.0, jitter=0.5)

    delays = [item.delay for item in drain(planner)][1:]

    for index, delay in enumerate(delays):
        nominal = 1.0 * (2.0**index)
        assert nominal * 0.5 <= delay <= nominal * 1.5


def test_plan_returns_none_after_exhaustion_and_is_idempotent() -> None:
    planner = make_planner(("only.example",), retry_times=0)

    assert planner.plan() is not None
    assert planner.plan() is None
    assert planner.plan() is None


def test_skip_endpoint_jumps_to_next_endpoint_without_waiting() -> None:
    planner = make_planner(retry_times=3)
    first = planner.plan()
    assert first is not None

    planner.skip_endpoint(first.endpoint_index)
    second = planner.plan()

    assert second is not None
    assert second.endpoint == "b.example", "应跳到下一个端点"
    assert second.delay == 0.0, "换端点不再叠加退避"


def test_skip_endpoint_removes_it_from_all_rounds() -> None:
    planner = make_planner(mode=RetryMode.ROTATE_FIRST, retry_times=2)
    first = planner.plan()
    assert first is not None

    planner.skip_endpoint(first.endpoint_index)
    remaining = [item.endpoint for item in drain(planner)]

    assert "a.example" not in remaining


def test_record_collects_attempt_failures() -> None:
    planner = make_planner(("only.example",), retry_times=1)
    first = planner.plan()
    assert first is not None
    planner.record(first, "https://only.example/x", RuntimeError("boom"))

    assert planner.attempts_made == 1
    assert len(planner.failures) == 1
    failure = planner.failures[0]
    assert failure.attempt == 1
    assert failure.endpoint == "only.example"
    assert failure.url == "https://only.example/x"
    assert "boom" in str(failure)


def test_record_without_attempt_still_counts() -> None:
    planner = make_planner(retry_times=0)

    planner.record(None, "https://x/y", RuntimeError("early"))

    assert planner.failures[0].attempt == 1
    assert planner.failures[0].endpoint is None


def test_empty_endpoints_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="端点列表为空"):
        make_planner(())


def test_negative_retry_times_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="不能为负数"):
        make_planner(retry_times=-1)
