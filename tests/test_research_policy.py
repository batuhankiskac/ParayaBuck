"""Fail-closed tests for research test classification and assurance tiers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_POLICY_SPEC = importlib.util.spec_from_file_location(
    "parayabuck_test_conftest",
    Path(__file__).with_name("conftest.py"),
)
assert _POLICY_SPEC is not None and _POLICY_SPEC.loader is not None
policy = importlib.util.module_from_spec(_POLICY_SPEC)
_POLICY_SPEC.loader.exec_module(policy)


class _Item:
    def __init__(
        self,
        nodeid: str,
        marks: tuple[object, ...] = (),
        *,
        lifecycle: dict[str, tuple[object, ...]] | None = None,
    ) -> None:
        self.nodeid = nodeid
        self.path = Path("tests/test_research_policy.py")
        self.fspath = self.path
        self._marks = marks
        self._lifecycle = {} if lifecycle is None else lifecycle

    def iter_markers(self, name: str):
        if name == "research_deep":
            return self._marks
        return self._lifecycle.get(name, ())


def _deep_mark(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(kwargs=kwargs)


def test_research_deep_requires_a_non_empty_reason() -> None:
    for mark in (_deep_mark(), _deep_mark(reason="  "), _deep_mark(reason=42)):
        errors = policy._validate_research_deep_marker(_Item("policy::missing", (mark,)), policy._CURRENT_RESEARCH)
        assert errors == ["research_deep requires a non-empty reason: policy::missing"]


def test_research_deep_accepts_an_explicit_reason() -> None:
    item = _Item("policy::explicit", (_deep_mark(reason="periodic assurance"),))

    assert policy._validate_research_deep_marker(item, policy._CURRENT_RESEARCH) == []


def test_historical_research_cannot_receive_a_deep_marker() -> None:
    item = _Item("historical::closed", (_deep_mark(reason="closed replay"),))

    errors = policy._validate_research_deep_marker(item, policy._HISTORICAL_RESEARCH)

    assert errors == ["historical research cannot be research_deep: historical::closed"]


def test_product_or_unclassified_tests_cannot_receive_a_deep_marker() -> None:
    item = _Item("product::misclassified", (_deep_mark(reason="periodic assurance"),))

    errors = policy._validate_research_deep_marker(item, policy._PRODUCT)

    assert errors == ["non-current research cannot be research_deep: product::misclassified"]


@pytest.mark.parametrize("marker", ("research", "historical_research"))
def test_product_tests_cannot_receive_research_lifecycle_markers(marker: str) -> None:
    item = _Item("product::misclassified", lifecycle={marker: (_deep_mark(),)})

    errors = policy._validate_research_lifecycle_markers(item, policy._PRODUCT)

    assert errors == ["product test cannot be marked research or historical_research: product::misclassified"]


def test_current_research_cannot_receive_historical_marker() -> None:
    item = _Item("current::misclassified", lifecycle={"historical_research": (_deep_mark(),)})

    errors = policy._validate_research_lifecycle_markers(item, policy._CURRENT_RESEARCH)

    assert errors == ["current research cannot be marked historical_research: current::misclassified"]


def test_r46_pin_always_cannot_receive_a_deep_marker() -> None:
    item = _Item("r46::pinned", (_deep_mark(reason="change-sensitive"),))

    errors = policy._validate_research_deep_marker(
        item,
        policy._CURRENT_RESEARCH,
        r46_tier=policy._R46_PIN_ALWAYS,
    )

    assert errors == ["R46 PIN_ALWAYS test cannot be research_deep: r46::pinned"]


def test_broad_r46_collection_fails_closed_when_a_registered_selector_is_stale(monkeypatch) -> None:
    monkeypatch.setattr(policy, "_R46_TEST_FILES", frozenset())
    monkeypatch.setattr(policy, "_R46_TEST_NODEIDS", frozenset({"tests/research/test_r46.py::test_missing"}))
    config = SimpleNamespace(
        option=SimpleNamespace(keyword="", deselect=()),
        args=(str(policy._REPO_ROOT),),
    )

    with pytest.raises(pytest.UsageError, match="stale R46 tier selectors"):
        policy._apply_r46_tiers_and_validate(config, [])


@pytest.mark.parametrize(
    "relative_path",
    (
        "tests/test_research_unknown.py",
        "tests/research/test_r47_example.py",
        "tests/research/r47/test_contract.py",
    ),
)
def test_unclassified_research_file_fails_closed(monkeypatch, tmp_path: Path, relative_path: str) -> None:
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    monkeypatch.setattr(policy, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(policy, "_CURRENT_RESEARCH_TEST_PATHS", ())
    monkeypatch.setattr(policy, "_HISTORICAL_RESEARCH_TEST_PATHS", ())
    monkeypatch.setattr(policy, "_CURRENT_RESEARCH_TESTS", frozenset())
    monkeypatch.setattr(policy, "_HISTORICAL_RESEARCH_TESTS", frozenset())
    monkeypatch.setattr(policy, "_R46_TEST_FILES", frozenset())

    with pytest.raises(pytest.UsageError, match="unclassified research tests"):
        policy._validate_research_classification()


def test_registered_historical_research_is_skipped_before_import() -> None:
    config = SimpleNamespace(getoption=lambda name: False)
    path = policy._REPO_ROOT / "tests/research/test_all_34_closure_2026_09_09.py"

    assert policy.pytest_ignore_collect(path, config) is True
