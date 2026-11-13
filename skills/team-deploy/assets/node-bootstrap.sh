#!/bin/bash
# Canonical node bootstrap. Sourceable for dependency-policy tests.
version_at_least() {
    local value=${1#v} floor=$2 a b
    [[ $value =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?([-.+][a-zA-Z0-9.-]+)?$ ]] || return 1
    IFS=. read -r a b _ <<< "$value"
    local x y
    IFS=. read -r x y _ <<< "$floor"
    ((10#$a > 10#$x || (10#$a == 10#$x && 10#$b >= 10#$y)))
}
stage() { printf 'CTL_STAGE %s\n' "$1" >&3; }
fail() { printf 'CTL_ERROR %s\n' "$1" >&3; exit 1; }
check_docker() {
    command -v docker >/dev/null 2>&1 || return 2
    local engine compose
    engine=$(docker version --format '{{.Server.Version}}') || fail docker_unavailable
    version_at_least "$engine" 20.10 || fail docker_old
    compose=$(docker compose version --short) || fail compose_old
    version_at_least "$compose" 2.30 || fail compose_old
    docker info >/dev/null || fail docker_unavailable
}
supported_apt() {
    case "$ID:$VERSION_ID" in
        ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|ubuntu:26.10|debian:12|debian:13) return 0;;
        *) return 1;;
    esac
}
apt_install() { DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 -o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 install -y "$@"; }
main() {
    set -Eeuo pipefail
    exec 3>&1
    [[ $# == 3 ]] || fail invalid_arguments
    local server=$1 cli=$2 token=$3
    [[ $EUID == 0 ]] || fail root_required
    # The SSH wrapper uses a private temporary directory; logs never contain stdin.
    local logfile
    logfile=$(mktemp /var/log/ctl-node-install.XXXXXXXX.log)
    chmod 600 "$logfile"
    exec >>"$logfile" 2>&1
    trap 'fail installation_failed' ERR
    stage checking
    [[ $(uname -s) == Linux && -d /run/systemd/system ]] || fail systemd_required
    command -v systemctl >/dev/null || fail systemd_required
    # An existing installation must be maintained through its existing identity/updater.
    [[ ! -e /var/lib/ctl-agent/node.json && ! -L /var/lib/ctl-agent/node.json ]] || fail agent_exists
    [[ ! -e /etc/systemd/system/ctl-agent.service && ! -L /etc/systemd/system/ctl-agent.service ]] || fail agent_exists
    [[ ! -e /usr/local/bin/deployctl && ! -L /usr/local/bin/deployctl && ! -e /usr/local/bin/ctl && ! -L /usr/local/bin/ctl ]] || fail cli_exists
    # /etc/os-release is root-owned operating system metadata.
    ID= VERSION_ID= VERSION_CODENAME=
    source /etc/os-release
    local missing_docker=false
    if ! command -v docker >/dev/null 2>&1; then
        missing_docker=true
        supported_apt || fail unsupported_os
        for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc docker-ce; do
            if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q '^install ok installed$'; then fail package_conflict; fi
        done
    else
        check_docker
    fi
    if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(sys.version_info < (3,10))'; then
        supported_apt || fail python_required
        stage python
        apt-get -o Acquire::Retries=2 update
        apt_install python3
        python3 -c 'import sys; sys.exit(sys.version_info < (3,10))' || fail python_required
    fi
    if [[ $missing_docker == true ]]; then
        stage docker
        # Refuse conflicting repository configuration instead of rewriting it.
        if grep -Rqs 'download.docker.com' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then fail repository_exists; fi
        apt-get -o Acquire::Retries=2 update
        apt_install ca-certificates curl
        install -m 0755 -d /etc/apt/keyrings
        [[ ! -e /etc/apt/keyrings/ctl-docker.asc && ! -L /etc/apt/keyrings/ctl-docker.asc && ! -e /etc/apt/sources.list.d/ctl-docker.sources && ! -L /etc/apt/sources.list.d/ctl-docker.sources ]] || fail repository_exists
        curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --max-time 60 "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/ctl-docker.asc
        chmod 0644 /etc/apt/keyrings/ctl-docker.asc
        printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/ctl-docker.asc\n' "$ID" "$VERSION_CODENAME" "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/ctl-docker.sources
        apt-get -o Acquire::Retries=2 update
        apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        systemctl enable --now docker
        check_docker
    fi
    stage agent
    python3 - "$cli" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from deployctl.bootstrap import install
install(artifact=sys.argv[1], install_dir='/usr/local/bin')
PY
    /usr/local/bin/ctl agent install --server "$server" --token-file "$token" --executable /usr/local/bin/ctl
    stage heartbeat
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
