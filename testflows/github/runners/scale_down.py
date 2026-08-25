# Copyright 2023 Katteli Inc.
# TestFlows.com Open-Source Software Testing Framework (http://testflows.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import math
import time
import copy
import queue
import random
import logging
import threading

from dataclasses import dataclass
from datetime import datetime, timezone

from .actions import Action
from . import metrics
from .constants import (
    runner_name_prefix,
    standby_runner_name_prefix,
    recycle_server_name_prefix,
    recycle_timestamp_label,
    powered_off_since_label,
)
from .recycling import powered_off_retire_action
from .scale_up import (
    StandbyRunner,
    ScaleUpFailureMessage,
)
from .config import Config
from .cloud_provider import (
    CloudProvider,
    ProviderServer,
    RetirementResult,
)
from .provider_hooks import run_after_scale_down_hooks, run_before_scale_down_hooks
from .ordered_set import OrderedSet as set

from github import Auth, Github
from github.Repository import Repository
from github.SelfHostedActionsRunner import SelfHostedActionsRunner


@dataclass
class ScaleUpFailure:
    """Scale up server failure."""

    time: float
    labels: list[str]
    server_name: str
    exception: Exception
    count: int
    observed_interval: float


@dataclass
class ScaleDownFailureMessage:
    """Scale down server failure."""

    time: float
    labels: set[str]
    server_name: str
    exception: Exception


@dataclass
class ZombieServer:
    """Zombie server."""

    time: float
    server: ProviderServer
    observed_interval: float


@dataclass
class UnusedRunner:
    """Unused self-hosted runner."""

    time: float
    runner: SelfHostedActionsRunner
    observed_interval: float


def _server_age_components(server: ProviderServer) -> tuple[int, int, int, int]:
    """Return server age as (days, hours, minutes, seconds)."""
    created = server.created
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - created
    total_seconds = max(int(delta.total_seconds()), 0)
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    return days, hours, minutes, seconds


def unused_runner_action(
    runner_server: ProviderServer | None, runner_status: str
) -> str:
    """Decide what to do with an aged-out unused runner.

    found -> "recycle" its server; not found + offline -> "deregister" the dead
    runner; not found + online -> "leave" it (idle/waiting, or another
    controller's).
    """
    if runner_server is not None:
        return "recycle"
    if runner_status == "offline":
        return "deregister"
    return "leave"


def delete_recyclable_server(
    server_name,
    recyclable_servers: list[tuple[ProviderServer, CloudProvider]],
    provider_prices: dict[str, dict[str, dict[str, float]]],
    recycle_grace_period: int | None = None,
    stack_level=2,
):
    """Deleting recycle server either randomly or the cheapest if server prices are available.

    :param server_name: name of the server that we are trying to create
    :param recyclable_servers: list of recyclable servers
    :param provider_prices: dictionary of provider prices
    """
    if not recyclable_servers:
        return

    recyclable_servers = [
        (server, provider)
        for server, provider in recyclable_servers
        if not provider.is_recycle_claimed(server)
    ]
    if not recyclable_servers:
        return

    if recycle_grace_period and recycle_grace_period > 0:
        now_ts = int(time.time())
        eligible_servers: list[tuple[ProviderServer, CloudProvider]] = []
        for server, provider in recyclable_servers:
            recycle_ts_raw = provider.get_server_tag(server, recycle_timestamp_label)
            try:
                recycle_ts = int(recycle_ts_raw)
            except (TypeError, ValueError):
                recycle_ts = 0

            if recycle_ts <= 0:
                with Action(
                    f"Setting recycle timestamp for {server.name} before deletion",
                    stacklevel=stack_level + 1,
                    level=logging.DEBUG,
                    server_name=server_name,
                ):
                    provider.set_server_tags(
                        server, {recycle_timestamp_label: str(now_ts)}
                    )
                continue

            # Respect a per-provider grace override; fall back to the passed
            # (global) grace. The pool can span providers, so this is decided
            # per candidate rather than once at the call site.
            effective_grace = (
                provider.recycle_grace_period
                if provider.recycle_grace_period is not None
                else recycle_grace_period
            )
            time_since_recycle = now_ts - recycle_ts
            if time_since_recycle < effective_grace:
                with Action(
                    f"Skipping recyclable server {server.name} deletion "
                    f"as it has only been recycled for {time_since_recycle}s "
                    f"(need {effective_grace}s)",
                    stacklevel=stack_level + 1,
                    level=logging.DEBUG,
                    server_name=server_name,
                ):
                    continue

            eligible_servers.append((server, provider))

        recyclable_servers = eligible_servers

    if not recyclable_servers:
        return

    picking = "randomly picked"
    candidate_currencies = {
        provider_prices.get(provider.name, {}).get("currency")
        for _, provider in recyclable_servers
        if provider_prices.get(provider.name, {}).get("currency")
    }
    if not provider_prices or len(candidate_currencies) > 1:
        random.shuffle(recyclable_servers)
    else:
        picking = "the cheapest"

        def sorting_key(recyclable_server: tuple[ProviderServer, CloudProvider]):
            server, provider = recyclable_server
            server_type_name = server.server_type
            server_location_name = server.location
            _, _, minutes, _ = _server_age_components(server)
            provider_server_prices = (provider_prices or {}).get(provider.name, {}).get(
                "prices", {}
            )
            try:
                return (60 - minutes) - provider_server_prices[server_type_name][
                    server_location_name
                ] / 60
            except KeyError:
                with Action(
                    f"price for {server_type_name} at {server_location_name} is missing",
                    level=logging.ERROR,
                    stacklevel=stack_level + 1,
                    server_name=server_name,
                ):
                    return math.inf

        recyclable_servers.sort(key=sorting_key, reverse=True)

    recyclable_server = recyclable_provider = None
    while recyclable_servers:
        candidate_server, candidate_provider = recyclable_servers.pop()
        if candidate_provider.reserve_recycled_server(candidate_server):
            recyclable_server = candidate_server
            recyclable_provider = candidate_provider
            break
    if recyclable_server is None:
        return

    deletion_submitted = False
    try:
        with Action(
            f"Deleting {picking} recyclable server {recyclable_server.name} with type "
            f"{recyclable_server.server_type}",
            stacklevel=stack_level,
            ignore_fail=True,
            server_name=server_name,
        ):
            recyclable_provider.delete_server(recyclable_server)
            deletion_submitted = True
    finally:
        if deletion_submitted:
            recyclable_provider.mark_recycled_server_deleting(recyclable_server)
        else:
            recyclable_provider.release_recycled_server(recyclable_server)

    return recyclable_server.name


def recycle_server(
    reason: str,
    server: ProviderServer,
    provider: CloudProvider,
    ssh_key_names: set[str],
    end_of_life: int,
    recycle_grace_period: int,
    recycle_enabled: bool = True,
):
    """Delegate the retirement transition to the owning provider."""
    try:
        result = provider.retire_runner_server(
            server,
            reason=reason,
            recycle_enabled=recycle_enabled,
            ssh_key_names=ssh_key_names,
            end_of_life=end_of_life,
            recycle_grace_period=recycle_grace_period,
        )
    except Exception as exc:
        metrics.record_scale_down_failure(
            error_type=f"retire_{reason}_failed",
            server_name=server.name,
            server_type=server.server_type,
            server_location=server.location,
            error_details={
                "error": str(exc),
                "server_type": server.server_type,
                "location": server.location,
                "labels": ",".join(provider.get_runner_labels(server)),
                "timestamp": time.time(),
            },
        )
        with Action(
            f"Could not retire {reason} server {server.name}: {exc}",
            ignore_fail=True,
            server_name=server.name,
        ):
            raise
        return RetirementResult("failed", server.name)

    if result.action == "unmanaged":
        # A server this controller does not own by SSH key is left untouched
        # (never renamed/recycled/deleted). That is normal for other controllers'
        # servers in a shared project, but a FLEET-WIDE unmanaged result means the
        # controller no longer recognizes its own servers (e.g. its resolved SSH
        # key name drifted from what the servers were tagged with) — which silently
        # strands them and, on Scaleway, leaks their SBS volumes until quota fills.
        # Log the mismatch at DEBUG so that failure mode is diagnosable instead of
        # silent (kept at DEBUG to avoid noise from legitimately-foreign servers).
        with Action(
            f"Not retiring {reason} server {server.name}: unmanaged "
            f"(stored ssh-key {provider.get_server_ssh_key_name(server)!r} not in "
            f"this controller's owned keys {sorted(ssh_key_names)})",
            level=logging.DEBUG,
            ignore_fail=True,
            server_name=server.name,
        ):
            pass

    return result


def scale_down(
    terminate: threading.Event,
    mailbox: queue.Queue,
    ssh_keys: dict[str, list] | None,
    config: Config,
    providers: list[CloudProvider] = None,
):
    """Scale down service by deleting any powered off server,
    any server that has unused runner, or any server that failed to register its
    runner (zombie server).
    """
    debug: bool = config.debug
    standby_runners: list[StandbyRunner] = config.standby_runners
    interval_period: int = config.scale_down_interval
    scaleup_interval_period: int = config.scale_up_interval
    github_token: str = config.github_token
    github_repository: str = config.github_repository
    recycle: bool = config.recycle
    recycle_grace_period: int = config.recycle_grace_period
    end_of_life: int = config.end_of_life
    def _effective_end_of_life(provider):
        """Return the provider's end_of_life if set, otherwise the global value."""
        if provider is not None and provider.end_of_life is not None:
            return provider.end_of_life
        return end_of_life

    def _effective_recycle(provider):
        """Return the provider's recycle toggle if set, otherwise the global value."""
        if provider is not None and provider.recycle is not None:
            return provider.recycle
        return recycle

    def _effective_recycle_grace(provider):
        """Return the provider's recycle grace period if set, otherwise the global value."""
        if provider is not None and provider.recycle_grace_period is not None:
            return provider.recycle_grace_period
        return recycle_grace_period
    max_powered_off_time: int = config.max_powered_off_time
    max_unused_runner_time: int = config.max_unused_runner_time
    max_runner_registration_time: int = config.max_runner_registration_time
    provider_prices: dict[str, dict] = config.server_prices or {}
    unused_runners: dict[str, UnusedRunner] = {}
    zombie_servers: dict[str, ZombieServer] = {}
    scaleup_failures: dict[str, ScaleUpFailure] = {}
    interval: int = -1

    if not providers:
        raise ValueError(
            "scale_down requires at least one configured cloud provider"
        )

    provider_ssh_key_names: dict[str, set[str]] = {}
    for provider_name, keys in (ssh_keys or {}).items():
        provider_ssh_key_names[provider_name] = {
            key.name for key in keys if hasattr(key, "name")
        }

    with Action("Logging in to GitHub"):
        github = Github(auth=Auth.Token(github_token), per_page=100)

    with Action(f"Getting repository {github_repository}"):
        repo: Repository = github.get_repo(github_repository)

    while True:
        interval += 1
        current_interval = time.time()
        recyclable_servers: dict[str, tuple[ProviderServer, CloudProvider]] = {}

        if terminate.is_set():
            with Action("Terminating scale down service", interval=interval):
                break

        with Action(
            "Scale down cycle", level=logging.DEBUG, ignore_fail=True, interval=interval
        ) as scale_down_cycle:

            with Action(
                "Getting list of self-hosted runners",
                level=logging.DEBUG,
                interval=interval,
            ):
                runners: list[SelfHostedActionsRunner] = repo.get_self_hosted_runners()

            managed_runner_names = {
                runner.name
                for runner in runners
                if runner.name.startswith(runner_name_prefix)
            }
            provider_selection = run_before_scale_down_hooks(
                providers,
                sequence=interval,
                managed_runner_names=managed_runner_names,
            )
            cycle_providers = list(provider_selection)

            with Action(
                "Getting list of servers", level=logging.DEBUG, interval=interval
            ):
                server_providers: dict[str, CloudProvider] = {}
                servers: list[ProviderServer] = []
                for _lp in cycle_providers:
                    for _ps in _lp.list_runner_servers(claim=True):
                        servers.append(_ps)
                        server_providers[_ps.name] = _lp

            with Action(
                "Getting runner labels for each server",
                level=logging.DEBUG,
                interval=interval,
            ):
                servers_labels = {}
                for ps in servers:
                    _sp = server_providers.get(ps.name)
                    servers_labels[ps.name] = (
                        _sp.get_runner_labels(ps) if _sp is not None else set()
                    )

            with Action(
                "Looking for recyclable servers",
                level=logging.DEBUG,
                interval=interval,
            ):
                for ps in servers:
                    if ps.status == CloudProvider.STATUS_OFF:
                        if ps.name.startswith(recycle_server_name_prefix):
                            _sp = server_providers.get(ps.name)
                            if _sp is not None and _effective_recycle(_sp) and _sp.is_recycled_server(ps):
                                if ps.name not in recyclable_servers:
                                    recyclable_servers[ps.name] = (ps, _sp)

            with Action(
                "Looking for zombie servers",
                level=logging.DEBUG,
                interval=interval,
            ):
                for ps in servers:
                    if ps.status == CloudProvider.STATUS_RUNNING:
                        if not any(
                            [
                                runner.name
                                for runner in runners
                                if runner.name.startswith(ps.name)
                            ]
                        ):
                            if ps.name not in zombie_servers:
                                with Action(
                                    f"Found new potential zombie server {ps.name}",
                                    server_name=ps.name,
                                    interval=interval,
                                ):
                                    zombie_servers[ps.name] = ZombieServer(
                                        time=current_interval,
                                        server=ps,
                                        observed_interval=current_interval,
                                    )
                            zombie_servers[ps.name].server = ps
                            zombie_servers[ps.name].observed_interval = (
                                current_interval
                            )

                        else:
                            zombie_servers.pop(ps.name, None)

            with Action(
                "Looking for unused runners", level=logging.DEBUG, interval=interval
            ):
                _standby_runners = copy.deepcopy(standby_runners)
                for runner in runners:
                    if (runner.status == "online" and not runner.busy) or (
                        runner.status == "offline"
                    ):
                        if runner.name.startswith(runner_name_prefix):
                            # skip any specified standby runners
                            if runner.name.startswith(standby_runner_name_prefix):
                                found = False
                                for standby_runner in _standby_runners:
                                    if set(standby_runner.labels).issubset(
                                        set(
                                            [
                                                label["name"].lower()
                                                for label in runner.labels
                                            ]
                                        )
                                    ):
                                        standby_runner.count -= 1
                                        # check if we have too many
                                        if standby_runner.count > -1:
                                            found = True
                                        break
                                if found:
                                    continue
                            if runner.name not in unused_runners:
                                with Action(
                                    f"Found new unused runner {runner.name}",
                                    server_name=runner.name,
                                    interval=interval,
                                ):
                                    unused_runners[runner.name] = UnusedRunner(
                                        time=current_interval,
                                        runner=runner,
                                        observed_interval=current_interval,
                                    )
                            unused_runners[runner.name].runner = runner
                            unused_runners[runner.name].observed_interval = (
                                current_interval
                            )

            # Update zombie, unused runner, and recycled server metrics
            with Action(
                "Updating zombie, unused runner, and recycled server metrics",
                level=logging.DEBUG,
                interval=interval,
            ):
                # Map runner name -> its server via provider naming so unused-runner
                # metrics carry real type/location, not values parsed from the name.
                runner_servers = {}
                for ps in servers:
                    _p = server_providers.get(ps.name)
                    if _p is not None:
                        runner_servers[_p.build_runner_name(ps)] = ps
                metrics.update_zombie_servers(zombie_servers)
                metrics.update_unused_runners(unused_runners, runner_servers)
                metrics.update_recycled_servers(servers)

            with Action(
                "Checking for scale up failures", level=logging.DEBUG, interval=interval
            ):
                while not mailbox.empty():
                    try:
                        scaleup_failure: ScaleUpFailureMessage = mailbox.get(
                            block=False
                        )

                        if scaleup_failure.server_name not in scaleup_failures:
                            with Action(
                                f"Found new scale up failure for {scaleup_failure.server_name}",
                                server_name=scaleup_failure.server_name,
                                interval=interval,
                            ):
                                scaleup_failures[scaleup_failure.server_name] = (
                                    ScaleUpFailure(
                                        time=scaleup_failure.time,
                                        labels=scaleup_failure.labels,
                                        server_name=scaleup_failure.server_name,
                                        exception=scaleup_failure.exception,
                                        count=1,
                                        observed_interval=current_interval,
                                    )
                                )
                        else:
                            scaleup_failures[scaleup_failure.server_name].exception = (
                                scaleup_failure.exception
                            )
                            scaleup_failures[scaleup_failure.server_name].count += 1
                            scaleup_failures[
                                scaleup_failure.server_name
                            ].observed_interval = current_interval

                    except queue.Empty:
                        continue

            with Action(
                "Retiring powered off servers past their grace",
                level=logging.DEBUG,
                interval=interval,
            ):
                # Grace is anchored to an authoritative powered_off_since tag on
                # the server (set on first sighting), not to in-memory
                # observation, so a missed listing or a controller restart never
                # resets it. Recycle-named servers are handled by the recyclable
                # path above.
                now_ts = int(time.time())
                for ps in servers:
                    if ps.status != CloudProvider.STATUS_OFF:
                        continue
                    if ps.name.startswith(recycle_server_name_prefix):
                        continue
                    _sp = server_providers.get(ps.name)
                    if _sp is None:
                        continue
                    decision = powered_off_retire_action(
                        _sp, ps, now_ts, max_powered_off_time
                    )
                    if decision == "stamp":
                        with Action(
                            f"Recording powered-off time for {ps.name}",
                            server_name=ps.name,
                            interval=interval,
                        ):
                            _sp.set_server_tags(
                                ps, {powered_off_since_label: str(now_ts)}
                            )
                    elif decision == "retire":
                        result = recycle_server(
                            reason="powered_off",
                            server=ps,
                            provider=_sp,
                            ssh_key_names=provider_ssh_key_names.get(_sp.name, set()),
                            end_of_life=_effective_end_of_life(_sp),
                            recycle_grace_period=_effective_recycle_grace(_sp),
                            recycle_enabled=_effective_recycle(_sp),
                        )
                        if result.action == "deleted":
                            metrics.record_server_deletion(
                                server_type=ps.server_type,
                                location=ps.location,
                                reason="powered_off",
                            )

            with Action(
                "Checking which zombie servers need to be deleted",
                level=logging.DEBUG,
                interval=interval,
            ):
                for server_name in list(zombie_servers.keys()):
                    zombie_server = zombie_servers[server_name]

                    if zombie_server.observed_interval != current_interval:
                        with Action(
                            f"Forgetting about zombie server {server_name}",
                            server_name=server_name,
                            interval=interval,
                        ):
                            zombie_servers.pop(server_name)

                    else:
                        if (
                            current_interval - zombie_server.time
                            > max_runner_registration_time
                        ):
                            age_intervals = current_interval - zombie_server.time
                            _sp = server_providers.get(zombie_server.server.name)
                            with Action(
                                "Scale-down decision for zombie server",
                                level=logging.DEBUG,
                                server_name=server_name,
                                interval=interval,
                            ) as action:
                                action.note(
                                    f"age_intervals={age_intervals}, threshold={max_runner_registration_time}, "
                                    f"provider={(getattr(_sp, 'name', 'unknown') if _sp is not None else 'none')}, "
                                    f"recycle={recycle}"
                                )
                            if _sp is not None:
                                result = recycle_server(
                                    reason="zombie",
                                    server=zombie_server.server,
                                    provider=_sp,
                                    ssh_key_names=provider_ssh_key_names.get(_sp.name, set()),
                                    end_of_life=_effective_end_of_life(_sp),
                                    recycle_grace_period=_effective_recycle_grace(_sp),
                                    recycle_enabled=_effective_recycle(_sp),
                                )
                                if result.action == "deleted":
                                    metrics.record_server_deletion(
                                        server_type=zombie_server.server.server_type,
                                        location=zombie_server.server.location,
                                        reason="zombie",
                                    )
                            zombie_servers.pop(server_name)

            with Action(
                "Checking which unused runners need to be removed",
                level=logging.DEBUG,
                interval=interval,
            ):
                for runner_name in list(unused_runners.keys()):
                    unused_runner = unused_runners[runner_name]

                    if unused_runner.observed_interval != current_interval:
                        with Action(
                            f"Forgetting about unused runner {runner_name}",
                            server_name=runner_name,
                            interval=interval,
                        ):
                            unused_runners.pop(runner_name)

                    else:
                        if (
                            current_interval - unused_runner.time
                            > max_unused_runner_time
                        ):
                            age_intervals = current_interval - unused_runner.time
                            runner_server: ProviderServer | None = None
                            runner_server_provider: CloudProvider | None = None
                            with Action(
                                "Scale-down decision for unused runner",
                                level=logging.DEBUG,
                                server_name=runner_name,
                                interval=interval,
                            ) as action:
                                action.note(
                                    f"runner={runner_name}, age_intervals={age_intervals}, "
                                    f"threshold={max_unused_runner_time}"
                                )

                            with Action(
                                f"Try to find server for the runner {runner_name}",
                                ignore_fail=True,
                                server_name=runner_name,
                                interval=interval,
                            ):
                                # Match by the provider's own build_runner_name over
                                # already-listed servers; reverse-parsing the runner
                                # name broke for standby-/recycle- names.
                                for _ps in servers:
                                    _p = server_providers.get(_ps.name)
                                    if (
                                        _p is not None
                                        and _p.build_runner_name(_ps) == runner_name
                                    ):
                                        runner_server = _ps
                                        runner_server_provider = _p
                                        break

                            with Action(
                                "Unused runner resolution result",
                                level=logging.DEBUG,
                                server_name=runner_name,
                                interval=interval,
                            ) as action:
                                provider_lookup_summary = []
                                for _p in cycle_providers:
                                    _matched = any(
                                        server_providers.get(s.name) is _p
                                        and _p.build_runner_name(s) == runner_name
                                        for s in servers
                                    )
                                    provider_lookup_summary.append(
                                        f"{_p.name}:{'hit' if _matched else 'miss'}"
                                    )
                                action.note(
                                    f"runner_server_found={runner_server is not None}, "
                                    f"provider={(runner_server_provider.name if runner_server_provider is not None else 'none')}"
                                )
                                action.note(
                                    "provider_lookups="
                                    + ", ".join(provider_lookup_summary)
                                )
                                action.note(
                                    f"runner_state=status:{unused_runner.runner.status}, "
                                    f"busy:{unused_runner.runner.busy}, "
                                    f"labels:{','.join(label['name'].lower() for label in unused_runner.runner.labels)}"
                                )

                            decision = unused_runner_action(
                                runner_server, unused_runner.runner.status
                            )
                            if decision == "recycle":
                                result = recycle_server(
                                    reason="unused_runner",
                                    server=runner_server,
                                    provider=runner_server_provider,
                                    ssh_key_names=provider_ssh_key_names.get(
                                        runner_server_provider.name, set()
                                    ),
                                    end_of_life=_effective_end_of_life(runner_server_provider),
                                    recycle_grace_period=_effective_recycle_grace(runner_server_provider),
                                    recycle_enabled=_effective_recycle(runner_server_provider),
                                )
                                if result.action == "deleted":
                                        metrics.record_server_deletion(
                                            server_type=runner_server.server_type,
                                            location=runner_server.location,
                                            reason="unused",
                                        )
                                unused_runners.pop(runner_name, None)
                            elif decision == "deregister":
                                # Dead: ephemeral runner killed before completing
                                # never self-deregisters, and GitHub's offline
                                # cleanup takes weeks. Online runners are left alone.
                                with Action(
                                    f"Removing offline runner {runner_name} with no server",
                                    ignore_fail=True,
                                    server_name=runner_name,
                                    interval=interval,
                                ):
                                    repo.remove_self_hosted_runner(
                                        unused_runner.runner
                                    )
                                    unused_runners.pop(runner_name, None)

            with Action(
                "Checking which recyclable servers need to be deleted",
                level=logging.DEBUG,
                interval=interval,
            ):
                for server_name in list(recyclable_servers.keys()):
                    if terminate.is_set():
                        break
                    recyclable_server, recyclable_provider = recyclable_servers[server_name]
                    result = recycle_server(
                        reason="unused_recyclable",
                        server=recyclable_server,
                        provider=recyclable_provider,
                        ssh_key_names=provider_ssh_key_names.get(
                            recyclable_provider.name, set()
                        ),
                        end_of_life=_effective_end_of_life(recyclable_provider),
                        recycle_grace_period=_effective_recycle_grace(recyclable_provider),
                        recycle_enabled=_effective_recycle(recyclable_provider),
                    )
                    # Keep still-alive (pooled/claimed) servers in the pool so the
                    # failure block below can evict one to free capacity; only drop
                    # the ones actually deleted.
                    if result.action == "deleted":
                        recyclable_servers.pop(server_name)

            with Action(
                "Checking which recyclable servers need to be deleted to try to resolve scale up failures",
                level=logging.DEBUG,
                interval=interval,
            ):
                process_failures = []

                for server_name in list(scaleup_failures.keys()):
                    scaleup_failure: ScaleUpFailure = scaleup_failures[server_name]

                    if terminate.is_set():
                        break

                    forget_reason = ""
                    forget_failure = False
                    for labels in servers_labels.values():
                        if set(scaleup_failure.labels).issubset(labels):
                            forget_reason = " at least one server could match labels"
                            forget_failure = True

                    if scaleup_failure.count < 2 and (
                        current_interval - scaleup_failure.time
                        > 2 * scaleup_interval_period
                    ):
                        forget_reason = " sporadic fail"
                        forget_failure = True

                    if not recyclable_servers:
                        forget_reason = " no recyclable servers to delete"
                        forget_failure = True

                    if forget_failure:
                        with Action(
                            f"Forgetting about scale up failure for {server_name}{forget_reason}",
                            server_name=server_name,
                            interval=interval,
                        ):
                            scaleup_failures.pop(server_name)
                    else:
                        process_failures.append(scaleup_failure)

                if recyclable_servers:
                    for scaleup_failure in process_failures:
                        if scaleup_failure.count > 2 and (
                            current_interval - scaleup_failure.time
                            > 2 * scaleup_interval_period
                        ):
                            with Action(
                                f"Picking recyclable server to be deleted to resolve scale up failure for {scaleup_failure.server_name}",
                                server_name=scaleup_failure.server_name,
                                interval=interval,
                            ):
                                deleted_recyclable_server_name = (
                                    delete_recyclable_server(
                                        recyclable_servers=list(
                                            recyclable_servers.values()
                                        ),
                                        provider_prices=provider_prices,
                                        recycle_grace_period=recycle_grace_period,
                                        stack_level=3,
                                        server_name=scaleup_failure.server_name,
                                    )
                                )
                                if deleted_recyclable_server_name is not None:
                                    recyclable_servers.pop(
                                        deleted_recyclable_server_name
                                    )
                                    scaleup_failures.pop(scaleup_failure.server_name)

            run_after_scale_down_hooks(
                cycle_providers,
                sequence=interval,
            )

        # Check if there were any exceptions in the scale down cycle
        if scale_down_cycle.exc_value is None:
            # Record successful scale down cycle
            with Action(
                "Recording successful scale down cycle",
                ignore_fail=True,
                level=logging.DEBUG,
            ):
                metrics.record_scale_down_failure(
                    error_type="success",
                    server_name=None,
                    server_type=None,
                    server_location=None,
                    error_details=None,
                )
        else:
            # Only record if it's not a failure that was already logged
            if not hasattr(scale_down_cycle.exc_value, "failure_logged"):
                error_details = {
                    "error": str(scale_down_cycle.exc_value),
                    "server_type": "unknown",
                    "location": "unknown",
                    "labels": "",
                    "timestamp": time.time(),
                }

                metrics.record_scale_down_failure(
                    error_type="scale_down_cycle_failed",
                    server_name="unknown",
                    server_type="unknown",
                    server_location="unknown",
                    error_details=error_details,
                )

        with Action(
            f"Sleeping until next interval {interval_period}s",
            level=logging.DEBUG,
            interval=interval,
        ):
            time.sleep(interval_period)
