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
import time
import socket
import ipaddress
import subprocess
import signal
import shlex

from datetime import datetime, timezone
from collections import namedtuple

from hcloud.servers.client import BoundServer
from hcloud.servers.domain import Server, PublicNetwork, IPv4Address, IPv6Network
from hcloud.primary_ips.domain import PrimaryIP

from .actions import Action
from .shell import shell
from .constants import runner_name_prefix

ServerAge = namedtuple("ServerAge", "days hours minutes seconds")


class MockServer(Server):
    """Mock server class for direct SSH connections."""

    def __init__(self, name: str, public_net: dict):
        """Initialize mock server.

        Args:
            name: Server name
            public_net: Dictionary containing public network info with ipv4/ipv6
        """
        ipv4_ip = public_net.get("ipv4", {}).get("ip")
        ipv6_ip = public_net.get("ipv6", {}).get("ip")

        ipv4_addr = (
            IPv4Address(ip=ipv4_ip, blocked=False, dns_ptr="") if ipv4_ip else None
        )
        ipv6_addr = (
            IPv6Network(ip=f"{ipv6_ip}/64", blocked=False, dns_ptr=[])
            if ipv6_ip
            else None
        )

        primary_ipv4 = PrimaryIP(ip=ipv4_ip) if ipv4_ip else None
        primary_ipv6 = PrimaryIP(ip=ipv6_ip) if ipv6_ip else None

        public_network = PublicNetwork(
            ipv4=ipv4_addr,
            ipv6=ipv6_addr,
            primary_ipv4=primary_ipv4,
            primary_ipv6=primary_ipv6,
            floating_ips=[],
        )

        # Convert datetime to ISO format string
        created_dt = datetime.now(timezone.utc)
        created_iso = created_dt.isoformat()

        super().__init__(
            id=0,
            name=name,
            status=Server.STATUS_RUNNING,
            created=created_iso,  # Pass ISO format string instead of datetime object
            public_net=public_network,
            server_type={"name": "unknown"},
            labels={},
        )


def age(server: Server):
    """Return server's age."""
    now = datetime.now(timezone.utc)
    used = now - server.created
    days = used.days
    hours, remainder = divmod(used.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    return ServerAge(days=days, hours=hours, minutes=minutes, seconds=seconds)


def ip_address(server):
    """Return IPv4 (default) or IPv6 address of the server."""
    from .cloud_provider import ProviderServer

    if isinstance(server, ProviderServer):
        if server.public_ipv4 is not None:
            return server.public_ipv4
        if server.public_ipv6 is not None:
            return ipaddress.IPv6Network(
                server.public_ipv6, strict=False
            ).network_address + 1
        raise ValueError(f"Server {server.name} has no public IPv4 or IPv6 address")
    # Legacy path: hcloud Server / MockServer objects.
    if server.public_net.primary_ipv4 is not None:
        return server.public_net.primary_ipv4.ip
    return (
        ipaddress.IPv6Network(
            server.public_net.primary_ipv6.ip, strict=False
        ).network_address
        + 1
    )


def wait_ssh(server, timeout: float):
    """Wait until SSH connection is ready."""
    ip = ip_address(server=server)

    attempt = -1
    start_time = time.time()

    while True:
        attempt += 1
        with Action(
            f"Trying to connect to {server.name}@{ip}...{attempt}",
            ignore_fail=True,
            stacklevel=3,
            server_name=server.name,
        ):
            returncode = ssh(server, "hostname", check=False, stacklevel=4)
            if returncode == 0:
                break
        if time.time() - start_time >= timeout:
            ssh(server, "hostname")
        else:
            time.sleep(5)


def ssh_command(server, options: str = ""):
    """Return ssh command."""
    from .cloud_provider import ProviderServer

    ip = ip_address(server=server)
    user = server.ssh_user if isinstance(server, ProviderServer) else "root"
    port_option = ""
    if isinstance(server, ProviderServer) and getattr(server, "ssh_port", None):
        port_option = f'-p {server.ssh_port} '
    identity_option = ""
    if isinstance(server, ProviderServer) and getattr(server, "ssh_key_path", None):
        identity_option = f'-i {shlex.quote(server.ssh_key_path)} '
    # No user (direct --host without one): let ssh resolve it, e.g. from
    # ~/.ssh/config for a host alias, instead of forcing a wrong login.
    destination = f"{user}@{ip}" if user else f"{ip}"
    return (
        f'ssh -q -o "StrictHostKeyChecking no" -o "UserKnownHostsFile=/dev/null" '
        f"{port_option}{identity_option}{options}{' ' if options else ''}{destination}"
    )


def ssh(server, cmd: str, *args, stacklevel=3, **kwargs):
    """Execute command over SSH."""
    return shell(
        f"{ssh_command(server=server)} {cmd}",
        *args,
        **kwargs,
        server_name=server.name,
        stacklevel=stacklevel,
    )


def scp(source: str, destination: str, *args, server=None, **kwargs):
    """Execute copy over SSH."""
    port_option = ""
    identity_option = ""
    if server is not None:
        if getattr(server, "ssh_port", None):
            port_option = f"-P {server.ssh_port} "
        if getattr(server, "ssh_key_path", None):
            identity_option = f"-i {shlex.quote(server.ssh_key_path)} "
    scp_command = (
        'scp -q -o "StrictHostKeyChecking no" -o "UserKnownHostsFile=/dev/null" '
        f"{port_option}{identity_option}{source} {destination}"
    )
    return shell(f"{scp_command}", *args, **kwargs)


def wait_ready(server: BoundServer, timeout: float, action: Action = None):
    """Wait for server to be ready."""
    start_time = time.time()

    while True:
        status = server.status
        if action:
            action.note(f"{server.name} {status}", stacklevel=4)
        if status == server.STATUS_RUNNING:
            break
        if time.time() - start_time >= timeout:
            raise TimeoutError("waiting for server to start running")
        time.sleep(1)
        server.reload()


def is_port_available(port: int) -> bool:
    """Check if a local port is available.

    Args:
        port: The port number to check

    Returns:
        bool: True if port is available, False if it's in use
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.25)  # Short timeout for connection attempt
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        return result != 0  # Port is available if connection fails
    except Exception:
        return False


class ssh_tunnel:
    """Context manager for establishing SSH tunnels."""

    def __init__(
        self,
        server: BoundServer,
        local_port: int,
        remote_port: int,
        action: Action = None,
    ):
        """Initialize SSH tunnel.

        Args:
            server: Hetzner server to connect to
            local_port: Local port to bind the tunnel to
            remote_port: Remote port to forward to
            action: Optional Action context for logging
        """
        self.server = server
        self.local_port = local_port
        self.remote_port = remote_port
        self.process = None
        self.action = action

        if not is_port_available(self.local_port):
            raise ValueError(f"Port {self.local_port} is already in use")

    def __enter__(self):
        # SSH tunnel options:
        # -N: don't execute remote command
        # -L: local port forwarding
        options = f"-N -L {self.local_port}:localhost:{self.remote_port}"
        full_cmd = f"{ssh_command(server=self.server, options=options)}"

        if self.action:
            self.action.note(
                f"Establishing SSH tunnel: {full_cmd}",
                stacklevel=4,
            )

        self.process = subprocess.Popen(
            full_cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setpgrp,  # Create new process group
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.process:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass

    def wait_ready(self, timeout: float = 10.0, check_interval: float = 1):
        """Wait for the SSH tunnel to be ready by attempting to connect to the local port.

        Args:
            timeout: Maximum time to wait in seconds
            check_interval: Time between connection attempts in seconds

        Returns:
            True if tunnel is ready, False if timeout occurred
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            if not is_port_available(self.local_port):
                return True
            time.sleep(check_interval)

        return False


def get_runner_server_name(runner_name: str) -> str:
    """Determine runner's server name.

    Default runners are named ``<server-name>-<type>-<location>``; the server
    name is the first five dash fields (the trailing ``-<type>-<location>`` is
    dropped, which also tolerates hyphenated AWS locations like ``us-east-1a``
    that sit past field five).

    Dedicated-static runners register under their bare, stable name with no
    ``-<type>-<location>`` suffix, and that name may contain hyphens in the
    group (e.g. ``github-runner-static-my-group-<hash>``). For them the runner
    name *is* the server name, so return it unchanged — mirroring the prefix
    guard used by get_runner_server_type for the same class of issue.
    """
    if runner_name.startswith(f"{runner_name_prefix}static-"):
        return runner_name
    return "-".join(runner_name.split("-")[:5])
