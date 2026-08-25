"""Tests for the volume CLI selector (volumes._select_volumes).

The selector is pure once the volume list is fetched: it filters and dedups by
the union of criteria, mirroring servers._select.
"""
from types import SimpleNamespace

from testflows.core import *

from testflows.github.runners.volumes import _select_volumes


def _vol(vid, name):
    return SimpleNamespace(id=vid, name=name)


@TestScenario
def select_all_returns_everything(self):
    vols = [_vol("1", "cache"), _vol("2", "data")]
    result = _select_volumes(
        vols, names=None, volume_names=None, ids=None, select_all=True
    )
    assert result == vols, result


@TestScenario
def no_criteria_selects_nothing(self):
    """The delete/resize guardrail: no criteria and not --all selects nothing."""
    vols = [_vol("1", "cache")]
    result = _select_volumes(
        vols, names=None, volume_names=None, ids=None, select_all=False
    )
    assert result == [], result


@TestScenario
def overlapping_criteria_select_a_volume_once(self):
    """A volume matching two criteria is selected once, not twice.

    Regression: the old per-criterion accumulation double-selected it, so delete
    detached/deleted it twice and the second call raised, aborting the rest.
    """
    v = _vol("42", "cache")
    vols = [v, _vol("43", "data")]
    result = _select_volumes(
        vols, names=None, volume_names=["cache"], ids=["42"], select_all=False
    )
    assert result == [v], result


@TestScenario
def union_of_distinct_criteria(self):
    a, b, c = _vol("1", "a"), _vol("2", "b"), _vol("3", "c")
    result = _select_volumes(
        [a, b, c], names=None, volume_names=["a"], ids=["3"], select_all=False
    )
    assert result == [a, c], result


@TestFeature
@Name("volumes cli")
def feature(self):
    for scenario in loads(current_module(), Scenario):
        scenario()
