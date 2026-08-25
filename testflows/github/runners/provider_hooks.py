"""Provider-neutral orchestration hook dispatch."""

import logging

from .actions import Action
from .cloud_provider import CloudProvider


def _run_hooks(
    providers: list[CloudProvider],
    *,
    hook_name: str,
    sequence: int,
    managed_runner_names: set[str],
) -> list[CloudProvider]:
    """Run provider hooks independently, returning the providers that succeeded.

    A provider whose hook raises is dropped for this cycle (failure isolation).
    """
    frozen_runner_names = frozenset(managed_runner_names)
    healthy = []
    for provider in providers:
        try:
            with Action(
                f"Running {hook_name} hook for {provider.name}",
                level=logging.DEBUG,
                interval=sequence,
            ):
                getattr(provider, hook_name)(frozen_runner_names)
        except Exception:
            continue
        healthy.append(provider)
    return healthy


def run_before_scale_up_hooks(
    providers: list[CloudProvider],
    *,
    sequence: int,
    managed_runner_names: set[str],
) -> list[CloudProvider]:
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
) -> list[CloudProvider]:
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
