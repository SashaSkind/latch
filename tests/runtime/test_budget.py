import pytest

from runtime.budget import DeadlinePolicy


@pytest.mark.parametrize(
    ("deadline_ms", "expected_path"),
    [
        (1_200, "full"),
        (1_000, "full"),
        (999, "fast"),
        (300, "fast"),
        (299, "partial"),
        (1, "partial"),
    ],
)
def test_default_deadline_paths_are_deterministic(
    deadline_ms: int,
    expected_path: str,
) -> None:
    assert DeadlinePolicy().select_path(deadline_ms) == expected_path


def test_deadline_thresholds_are_calibratable() -> None:
    policy = DeadlinePolicy(
        full_path_minimum_ms=800,
        fast_path_minimum_ms=200,
    )

    assert policy.select_path(800) == "full"
    assert policy.select_path(799) == "fast"
    assert policy.select_path(199) == "partial"


@pytest.mark.parametrize(
    ("full_threshold", "fast_threshold"),
    [(300, 300), (299, 300), (300, 0)],
)
def test_invalid_thresholds_are_rejected(
    full_threshold: int,
    fast_threshold: int,
) -> None:
    with pytest.raises(ValueError):
        DeadlinePolicy(
            full_path_minimum_ms=full_threshold,
            fast_path_minimum_ms=fast_threshold,
        )


def test_non_positive_deadline_is_rejected() -> None:
    with pytest.raises(ValueError, match="deadline_ms"):
        DeadlinePolicy().select_path(0)
