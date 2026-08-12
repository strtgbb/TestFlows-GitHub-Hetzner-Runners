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
import os
import sys

from github import Auth, Github
from github.Repository import Repository
from github.SelfHostedActionsRunner import SelfHostedActionsRunner

from .actions import Action
from .config import Config, provider_factory
from .cloud_provider import CloudProvider, ProviderServer
from .server import ssh_command
from .scale_up import runner_name_prefix, get_volume_name
from .request import request

runner_status_icon = {
    "online": "🟢",
    "offline": "🔴",
    "unknown": "❓",
}

server_status_icon = {
    CloudProvider.STATUS_STARTING: "🚀",
    CloudProvider.STATUS_RUNNING: "🟢",
    CloudProvider.STATUS_OFF: "🔴",
    CloudProvider.STATUS_STOPPING: "🛑",
    CloudProvider.STATUS_REBUILDING: "🛠️",
    CloudProvider.STATUS_MIGRATING: "📦",
    CloudProvider.STATUS_DELETING: "🗑️",
    CloudProvider.STATUS_UNKNOWN: "❓",
}


def _github_runners(config: Config) -> list[SelfHostedActionsRunner]:
    """Return this controller's self-hosted runners registered with GitHub."""
    with Action("Logging in to GitHub"):
        github = Github(auth=Auth.Token(config.github_token))

    with Action(f"Getting repository {config.github_repository}"):
        repo: Repository = github.get_repo(config.github_repository)

    with Action("Getting list of self-hosted runners"):
        runners = repo.get_self_hosted_runners()
        return [r for r in runners if r.name.startswith(runner_name_prefix)]


def _runner_servers(config: Config) -> list[tuple[ProviderServer, CloudProvider]]:
    """(server, provider) for every active runner server across all providers."""
    with Action("Getting a list of servers"):
        pairs = []
        for provider in provider_factory(config):
            for server in provider.list_runner_servers():
                pairs.append((server, provider))
        return pairs


def _select(pairs, runners, *, names, server_names, ids, select_all):
    """Filter (server, provider) pairs and runners.

    ``select_all`` takes everything. Otherwise selection is the union of:
    ``names`` (name-prefix match on both servers and runners), ``server_names``
    (exact server name), and ``ids`` (server id compared as a string, so
    string ids like AWS ``i-...`` work). A server matched by server-name or id
    also pulls the runner that shares its name. Results are deduplicated, so
    overlapping filters never select the same server or runner twice.
    """
    if select_all:
        return list(pairs), list(runners)

    sel_pairs, seen_servers = [], set()
    sel_runners, seen_runners = [], set()

    def take_server(server, provider):
        key = (provider.name, server.id)
        if key not in seen_servers:
            seen_servers.add(key)
            sel_pairs.append((server, provider))

    def take_runner(runner):
        if runner.id not in seen_runners:
            seen_runners.add(runner.id)
            sel_runners.append(runner)

    for prefix in names or []:
        for server, provider in pairs:
            if server.name.startswith(prefix):
                take_server(server, provider)
        for runner in runners:
            if runner.name.startswith(prefix):
                take_runner(runner)

    id_set = {str(i) for i in (ids or [])}
    matched_names = set()
    for server, provider in pairs:
        if (server_names and server.name in server_names) or (
            id_set and str(server.id) in id_set
        ):
            take_server(server, provider)
            matched_names.add(server.name)

    for runner in runners:
        if runner.name in matched_names:
            take_runner(runner)

    return sel_pairs, sel_runners


def _delete_servers(pairs):
    """Delete each server through the provider that owns it."""
    for server, provider in pairs:
        with Action(
            f"🗑️  Deleting server {server.name} ({provider.name}) "
            f"id {server.id} in {server.location}"
        ):
            provider.delete_server(server)


def _find_server(config: Config, server_name: str) -> ProviderServer | None:
    """Find an active runner server by name across all configured providers."""
    for server, _ in _runner_servers(config):
        if server.name == server_name:
            return server
    return None


def _print_runners(runners, *, no_labels):
    print("Runners:" if runners else "No runners", file=sys.stdout)
    if not runners:
        return

    print("  ", f"{'status':11}", "name,", "id,", "os,", "busy", file=sys.stdout)
    for runner in runners:
        icon = runner_status_icon.get(runner.status, "❓")
        print(
            f"{icon} {runner.status:11}",
            f"{runner.name},",
            f"{runner.id},",
            f"{runner.os},",
            f"{'busy' if runner.busy else 'free'}",
            file=sys.stdout,
        )
        if no_labels:
            continue
        indent = " " * 17
        print(f"{indent}labels:", file=sys.stdout)
        labels = []
        for label in runner.labels:
            value = str(label["name"]).lower()
            if len(value) > 64:
                value = value[:64] + "..."
            labels.append(value)
        if not labels:
            print(f"{indent}  no labels", file=sys.stdout)
        else:
            print(f"{indent}  {', '.join(labels)}", file=sys.stdout)


def _print_servers(pairs, *, no_labels, no_volumes):
    print("Servers:" if pairs else "No servers", file=sys.stdout)
    if not pairs:
        return

    print(
        "  ",
        f"{'provider':10}",
        f"{'status':12}",
        "name,",
        "id,",
        "ip,",
        "type,",
        "location",
        file=sys.stdout,
    )
    indent = " " * 17
    for server, provider in pairs:
        icon = server_status_icon.get(server.status, "❓")
        ip = server.public_ipv4 or server.public_ipv6 or "-"
        print(
            icon,
            f"{provider.name:10}",
            f"{server.status:12}",
            f"{server.name},",
            f"{server.id},",
            f"{ip},",
            f"{server.server_type},",
            f"{server.location}",
            file=sys.stdout,
        )
        if not no_labels:
            print(f"{indent}labels:", file=sys.stdout)
            if not server.labels:
                print(f"{indent}  no labels", file=sys.stdout)
            else:
                for k, v in server.labels.items():
                    value = str(v)
                    if len(value) > 64:
                        value = value[:64] + "..."
                    print(f"{indent}  {k}={value}", file=sys.stdout)

        if not no_volumes:
            print(f"{indent}volumes:", file=sys.stdout)
            if not server.volumes:
                print(f"{indent}  no volumes", file=sys.stdout)
            for volume in server.volumes:
                print(
                    f"{indent}  {get_volume_name(volume.name)}, {volume.name}, "
                    f"{volume.size}GB, {volume.location}",
                    file=sys.stdout,
                )


def list_servers(args, config: Config):
    """List all current runner servers across every configured provider."""
    config.check()

    runners = _github_runners(config)
    pairs = _runner_servers(config)

    if not runners and not pairs:
        print("No runners or servers found", file=sys.stderr)
        return

    select_all = args.list_all or not (
        args.list_name or args.list_server_name or args.list_id
    )
    sel_pairs, sel_runners = _select(
        pairs,
        runners,
        names=args.list_name,
        server_names=args.list_server_name,
        ids=args.list_id,
        select_all=select_all,
    )

    if not sel_runners and not sel_pairs:
        print("No runners or servers selected", file=sys.stderr)
        return

    _print_runners(sel_runners, no_labels=args.no_labels)
    _print_servers(sel_pairs, no_labels=args.no_labels, no_volumes=args.no_volumes)


def delete(args, config: Config):
    """Delete runners and their servers across every configured provider.

    Unlike ``list``, delete never defaults to everything: with no filter and no
    ``--all`` it selects nothing.
    """
    config.check()

    runners = _github_runners(config)
    pairs = _runner_servers(config)

    if not runners and not pairs:
        print("No runners or servers found", file=sys.stdout)
        return

    sel_pairs, sel_runners = _select(
        pairs,
        runners,
        names=args.delete_name,
        server_names=args.delete_server_name,
        ids=args.delete_id,
        select_all=args.delete_all,
    )

    if not sel_runners and not sel_pairs:
        print("No runners or servers selected", file=sys.stderr)
        return

    for runner in sel_runners:
        with Action(f"🗑️  Deleting runner {runner.name}") as action:
            _, resp = request(
                f"https://api.github.com/repos/{config.github_repository}/actions/runners/{runner.id}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {config.github_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                method="DELETE",
                data={},
            )
            action.note(f"   {resp.status}")

    _delete_servers(sel_pairs)


def ssh_client(
    args, config: Config, server_name: str = None, server: ProviderServer = None
):
    """Open ssh client to the server."""
    if server is None:
        config.check()

        if server_name is None:
            server_name = args.name

        with Action(f"Getting server {server_name}"):
            server = _find_server(config, server_name)

            if server is None:
                raise ValueError("server not found")

            if server.status != CloudProvider.STATUS_RUNNING:
                raise ValueError(f"server status is {server.status}")

    with Action("Opening SSH client"):
        os.system(ssh_command(server=server))


def ssh_client_command(
    args, config: Config, server_name: str = None, server: ProviderServer = None
):
    """Return ssh command to connect server."""
    if server is None:
        config.check()

        if server_name is None:
            server_name = args.name

        with Action(f"Getting server {server_name}"):
            server = _find_server(config, server_name)

            if server is None:
                raise ValueError("server not found")

    print(ssh_command(server=server), file=sys.stdout)
