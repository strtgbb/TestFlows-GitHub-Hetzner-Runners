set -euo pipefail
set -x

echo "Install required packages"
apt-get update
apt-get -y install python3-pip
apt-get -y install openssh-client

echo "Create and configure ubuntu user"
if ! id -u ubuntu >/dev/null 2>&1; then
    adduser ubuntu --disabled-password --gecos ""
fi
if ! getent group wheel >/dev/null 2>&1; then
    addgroup wheel
fi
usermod -aG wheel ubuntu
usermod -aG sudo ubuntu

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

echo "Generate SSH key if missing"
sudo -u ubuntu mkdir -p /home/ubuntu/.ssh
if [ ! -f "/home/ubuntu/.ssh/id_rsa" ]; then
    sudo -u ubuntu ssh-keygen -t rsa -q -f "/home/ubuntu/.ssh/id_rsa" -N ""
fi

echo "Create scripts folder"
mkdir -p /home/ubuntu/.tfs-runners/scripts
mkdir -p /home/ubuntu/.tfs-runners/configs

# Ensure the deploy tree is owned by the service user so the controller can scp
# scripts/config in as that user afterwards (on images where the login user is
# 'ubuntu', e.g. AWS, it cannot write into a root-created directory otherwise).
chown -R ubuntu:ubuntu /home/ubuntu/.tfs-runners
