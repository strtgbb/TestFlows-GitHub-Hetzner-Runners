"""Provider-neutral orchestration hook dispatch."""

import logging
from dataclasses import dataclass

from .actions import Action
from .cloud_provider import CloudProvider


@dataclass(frozen=True)
class ProviderCycleSelection:
    """Providers available after pre-cycle hooks have run."""

    providers: tuple[CloudProvider, ...]
    failed: tuple[CloudProvider, ...]

    @property
    def inventory_complete(self) -> bool:
        return not self.failed


def _run_hooks(
    providers: list[CloudProvider],
    *,
    hook_name: str,
    sequence: int,
    managed_runner_names: set[str],
) -> ProviderCycleSelection:
    """Run provider hooks independently and isolate failures for this cycle."""
    frozen_runner_names = frozenset(managed_runner_names)
    healthy = []
    failed = []
    for provider in providers:
        try:
            with Action(
                f"Running {hook_name} hook for {provider.name}",
                level=logging.DEBUG,
                interval=sequence,
            ):
                getattr(provider, hook_name)(frozen_runner_names)
        except Exception:
            failed.append(provider)
        else:
            healthy.append(provider)
    return ProviderCycleSelection(tuple(healthy), tuple(failed))


def run_before_scale_up_hooks(
    providers: list[CloudProvider],
    *,
    sequence: int,
    managed_runner_names: set[str],
) -> ProviderCycleSelection:
    return _run_hooks(
        providers,
        hook_name="before_scale_up",
        sequence=sequence,
        managed_runner_names=managed_runner_names,
    )


def run_before_scale_down_hooks(
    providers: list[CloudProvider],
    *,
    sequence: int,
    managed_runner_names: set[str],
) -> ProviderCycleSelection:
    return _run_hooks(
        providers,
        hook_name="before_scale_down",
        sequence=sequence,
        managed_runner_names=managed_runner_names,
    )


def run_after_scale_down_hooks(
    providers: list[CloudProvider],
    *,
    sequence: int,
) -> None:
    """Run best-effort post-cycle hooks for healthy scale-down providers."""
    for provider in providers:
        try:
            with Action(
                f"Running after_scale_down hook for {provider.name}",
                level=logging.DEBUG,
                interval=sequence,
            ):
                provider.after_scale_down()
        except Exception as exc:
            with Action(
                f"Provider {provider.name} after_scale_down hook failed: {exc}",
                ignore_fail=True,
                interval=sequence,
            ):
                pass
