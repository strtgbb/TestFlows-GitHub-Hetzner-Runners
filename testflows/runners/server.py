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

from .actions import Action
from .shell import shell

def ip_address(server):
    """Return IPv4 (default) or IPv6 address of the server."""
    if server.public_ipv4 is not None:
        return server.public_ipv4
    if server.public_ipv6 is not None:
        return (
            ipaddress.IPv6Network(server.public_ipv6, strict=False).network_address
            + 1
        )
    raise ValueError(f"Server {server.name} has no public IPv4 or IPv6 address")


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
        server,
        local_port: int,
        remote_port: int,
        action: Action = None,
    ):
        """Initialize SSH tunnel.

        Args:
            server: server to connect to
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
