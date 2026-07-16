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

echo "Launch fail2ban"
systemctl enable --now fail2ban
