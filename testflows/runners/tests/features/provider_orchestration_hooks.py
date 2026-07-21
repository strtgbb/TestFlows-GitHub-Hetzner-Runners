"""Tests for provider-neutral orchestration hooks."""

from unittest.mock import MagicMock

from testflows.core import *

from testflows.runners.cloud_provider import (
    CloudProvider,
)
from testflows.runners.provider_hooks import (
    run_after_scale_down_hooks,
    run_before_scale_down_hooks,
    run_before_scale_up_hooks,
)


@TestScenario
def failing_provider_is_isolated_for_one_cycle(self):
    failing = MagicMock()
    failing.name = "failing"
    failing.before_scale_up.side_effect = RuntimeError("maintenance failed")
    healthy = MagicMock()
    healthy.name = "healthy"

    selection = run_before_scale_up_hooks(
        [failing, healthy],
        sequence=7,
        managed_runner_names={"runner-a"},
    )

    assert selection.providers == (healthy,)
    assert selection.failed == (failing,)
    assert selection.inventory_complete is False
    for provider in selection.providers:
        provider.list_runner_servers()
    failing.list_runner_servers.assert_not_called()
    healthy.list_runner_servers.assert_called_once()


@TestScenario
def isolated_provider_is_retried_next_cycle(self):
    provider = MagicMock()
    provider.name = "retry"
    provider.before_scale_down.side_effect = [RuntimeError("temporary"), None]

    first = run_before_scale_down_hooks(
        [provider],
        sequence=1,
        managed_runner_names=set(),
    )
    second = run_before_scale_down_hooks(
        [provider],
        sequence=2,
        managed_runner_names=set(),
    )

    assert first.providers == ()
    assert second.providers == (provider,)
    assert provider.before_scale_down.call_count == 2


@TestScenario
def hook_context_is_typed_and_immutable(self):
    provider = MagicMock()
    provider.name = "provider"
    run_before_scale_down_hooks(
        [provider],
        sequence=3,
        managed_runner_names={"runner-a"},
    )
    context = provider.before_scale_down.call_args.args[0]
    assert context == frozenset({"runner-a"})
    provider.before_scale_up.assert_not_called()


@TestScenario
def default_hooks_are_noops(self):
    provider = MagicMock()
    managed_runner_names = frozenset()
    CloudProvider.before_scale_up(provider, managed_runner_names)
    CloudProvider.before_scale_down(provider, managed_runner_names)
    CloudProvider.after_scale_down(provider)
    CloudProvider.after_server_setup(
        provider,
        MagicMock(),
        None,
    )


@TestScenario
def after_scale_down_hooks_are_best_effort(self):
    failing = MagicMock()
    failing.name = "failing"
    failing.after_scale_down.side_effect = RuntimeError("cleanup failed")
    healthy = MagicMock()
    healthy.name = "healthy"

    run_after_scale_down_hooks([failing, healthy], sequence=9)

    failing.after_scale_down.assert_called_once()
    healthy.after_scale_down.assert_called_once()


@TestFeature
@Name("provider orchestration hooks")
def feature(self):
    for scenario in loads(current_module(), Scenario):
        scenario()
