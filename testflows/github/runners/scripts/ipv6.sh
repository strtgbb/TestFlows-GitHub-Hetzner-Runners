set -euo pipefail
set -x

echo "Configuring IPv6 DNS by ensuring required entries are at top of /etc/resolv.conf"
DNS_ENTRIES=(
    "nameserver 2a01:4f9:c010:3f02::1"
    "nameserver 2a00:1098:2b::1"
    "nameserver 2a00:1098:2c::1"
)

TEMP_FILE=$(mktemp)
for dns in "${DNS_ENTRIES[@]}"; do
    printf "%s\n" "$dns" >> "$TEMP_FILE"
done
while IFS= read -r line; do
    skip=false
    for dns in "${DNS_ENTRIES[@]}"; do
        if [ "$line" = "$dns" ]; then
            skip=true
            break
        fi
    done
    if [ "$skip" = false ]; then
        printf "%s\n" "$line" >> "$TEMP_FILE"
    fi
done < /etc/resolv.conf
mv "$TEMP_FILE" /etc/resolv.conf
chmod 644 /etc/resolv.conf

echo "Testing IPv6 DNS resolution"
curl -6 https://github.com >/dev/null 2>&1 || true

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

SUDOERS_FILE="/etc/sudoers.d/90-tfs-github-runners-wheel"
SUDOERS_LINE="%wheel ALL=(ALL:ALL) NOPASSWD:ALL"
if [ ! -f "${SUDOERS_FILE}" ] || ! grep -Fxq "${SUDOERS_LINE}" "${SUDOERS_FILE}"; then
    printf "%s\n" "${SUDOERS_LINE}" > "${SUDOERS_FILE}"
    chmod 0440 "${SUDOERS_FILE}"
fi

echo "Install fail2ban"
apt-get update
apt-get install --yes --no-install-recommends fail2ban
systemctl enable --now fail2ban
