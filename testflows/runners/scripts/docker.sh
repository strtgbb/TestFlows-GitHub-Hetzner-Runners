set -euo pipefail
set -x

echo "Create and configure ubuntu user"
if ! id -u ubuntu >/dev/null 2>&1; then
    adduser ubuntu --disabled-password --gecos ""
fi
if ! getent group wheel >/dev/null 2>&1; then
    addgroup wheel
fi
if ! getent group docker >/dev/null 2>&1; then
    addgroup docker
fi
usermod -aG wheel ubuntu
usermod -aG sudo ubuntu
usermod -aG docker ubuntu

SUDOERS_FILE="/etc/sudoers.d/90-tfs-runners-wheel"
SUDOERS_LINE="%wheel ALL=(ALL:ALL) NOPASSWD:ALL"
if [ ! -f "${SUDOERS_FILE}" ] || ! grep -Fxq "${SUDOERS_LINE}" "${SUDOERS_FILE}"; then
    printf "%s\n" "${SUDOERS_LINE}" > "${SUDOERS_FILE}"
    chmod 0440 "${SUDOERS_FILE}"
fi

echo "Install fail2ban"
apt-get update
apt-get install --yes --no-install-recommends fail2ban
systemctl enable --now fail2ban

echo "Install Docker Engine"
apt-get -y update
apt-get -y install ca-certificates curl gnupg

CACHE_DIR="${CACHE_DIR:-/mnt/cache}"
if [ -d "$CACHE_DIR" ]; then
    CACHE_DIR_DOCKER="$CACHE_DIR/docker"
    mkdir -p "$CACHE_DIR_DOCKER"
    echo "Using cache directory: $CACHE_DIR_DOCKER"
else
    CACHE_DIR_DOCKER=""
    echo "No cache directory available, proceeding without caching"
fi

echo "Add Docker's official GPG key"
install -m 0755 -d /etc/apt/keyrings
DOCKER_GPG_PATH="/etc/apt/keyrings/docker.asc"

if [ -n "$CACHE_DIR_DOCKER" ] && [ -f "$CACHE_DIR_DOCKER/docker.gpg" ]; then
    cp "$CACHE_DIR_DOCKER/docker.gpg" "$DOCKER_GPG_PATH"
else
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o "$DOCKER_GPG_PATH"
    if [ -n "$CACHE_DIR_DOCKER" ]; then
        cp "$DOCKER_GPG_PATH" "$CACHE_DIR_DOCKER/docker.gpg"
    fi
fi
chmod a+r "$DOCKER_GPG_PATH"

echo "Set up Docker's repository"
DOCKER_LIST_PATH="/etc/apt/sources.list.d/docker.list"
DOCKER_REPO_LINE="deb [arch=$(dpkg --print-architecture) signed-by=$DOCKER_GPG_PATH] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable"
if [ -f "$DOCKER_LIST_PATH" ] && grep -Fqx "$DOCKER_REPO_LINE" "$DOCKER_LIST_PATH"; then
    :
elif [ -n "$CACHE_DIR_DOCKER" ] && [ -f "$CACHE_DIR_DOCKER/docker.list" ]; then
    cp "$CACHE_DIR_DOCKER/docker.list" "$DOCKER_LIST_PATH"
else
    printf "%s\n" "$DOCKER_REPO_LINE" > "$DOCKER_LIST_PATH"
    if [ -n "$CACHE_DIR_DOCKER" ]; then
        cp "$DOCKER_LIST_PATH" "$CACHE_DIR_DOCKER/docker.list"
    fi
fi

echo "Install Docker Engine and containerd"
apt-get -y update
apt-get -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "Add ubuntu user to docker group"
usermod -aG docker ubuntu
