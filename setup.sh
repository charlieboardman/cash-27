#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SETUP_RENDER_ONLY="${CASH_SETUP_RENDER_ONLY:-0}"
readonly SETUP_BOOTSTRAPPED="${CASH_SETUP_BOOTSTRAPPED:-0}"
readonly OPERATOR_CONFIG="${CASH_OPERATOR_CONFIG:-/etc/cash-27/operator.conf}"
readonly SERVICE_USER_REQUESTED="${CASH_SERVICE_USER:-cash27}"

readonly CONFIG_DIR="${CASH_SETUP_CONFIG_DIR:-${HOME}/.config/cash-27}"
readonly DATA_ROOT="${CASH_SETUP_DATA_ROOT:-${HOME}/.local/share/cash-27}"
readonly USER_SYSTEMD_DIR="${CASH_SETUP_USER_SYSTEMD_DIR:-${HOME}/.config/systemd/user}"
readonly ENV_FILE="${CONFIG_DIR}/cash-27.env"
readonly INSTALL_FILE="${CONFIG_DIR}/install.conf"
readonly COMPOSE_ENV_FILE="${CONFIG_DIR}/compose.env"
readonly REFERENCE_DIR="${CONFIG_DIR}/reference"
readonly INSTALLED_ACCOUNTS_CSV="${REFERENCE_DIR}/accounts.csv"
readonly INSTALLED_CLASSES_CSV="${REFERENCE_DIR}/classes.csv"
readonly COMPOSE_SERVICE_FILE="${USER_SYSTEMD_DIR}/cash-27-compose.service"
readonly BACKUP_STATE_DIR="${DATA_ROOT}/backups/local"
readonly CADDY_FILE="${CASH_SETUP_CADDY_FILE:-${CONFIG_DIR}/Caddyfile}"
readonly CADDY_DATA="${DATA_ROOT}/caddy/data"
readonly CADDY_CONFIG="${DATA_ROOT}/caddy/config"

DOMAIN="${CASH_SETUP_DOMAIN:-}"
ADMIN_PASSWORD="${CASH_SETUP_ADMIN_PASSWORD:-}"
USER_PASSWORD="${CASH_SETUP_USER_PASSWORD:-}"
ACCOUNTS_SOURCE="${CASH_SETUP_ACCOUNTS_CSV:-}"
CLASSES_SOURCE="${CASH_SETUP_CLASSES_CSV:-}"
SECRET_KEY=""
CSV_RESULT=""
IMAGE=""

say() {
    printf '%s\n' "$*"
}

privileged() {
    if [[ "${EUID}" -eq 0 ]]; then
        "$@"
    else
        sudo "$@"
    fi
}

fail() {
    printf 'Setup stopped: %s\n' "$*" >&2
    exit 1
}

read_env_value() {
    local name="$1"
    local file="$2"
    local line

    [[ -f "${file}" ]] || return 0
    line="$(grep -m1 -E "^${name}=" "${file}" || true)"
    printf '%s' "${line#*=}"
}

random_hex_32() {
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}

valid_domain() {
    local candidate="$1"
    [[ "${candidate}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]
}

normalize_domain() {
    local candidate="$1"
    candidate="${candidate#http://}"
    candidate="${candidate#https://}"
    candidate="${candidate%%/*}"
    candidate="${candidate%.}"
    printf '%s' "${candidate,,}"
}

prompt_domain() {
    local existing=""
    local answer=""

    existing="$(read_env_value CASH_DOMAIN "${INSTALL_FILE}")"
    if [[ -n "${DOMAIN}" ]]; then
        DOMAIN="$(normalize_domain "${DOMAIN}")"
        valid_domain "${DOMAIN}" || fail "CASH_SETUP_DOMAIN must be a public domain name, such as cash.example.org."
        return
    fi

    while true; do
        if [[ -n "${existing}" ]]; then
            read -r -p "Public domain [${existing}]: " answer
            answer="${answer:-${existing}}"
        else
            read -r -p "Public domain (for example, cash.example.org): " answer
        fi
        answer="$(normalize_domain "${answer}")"
        if valid_domain "${answer}"; then
            DOMAIN="${answer}"
            return
        fi
        say "Please enter a domain name without a URL path or port."
    done
}

password_is_valid() {
    local password="$1"
    [[ -n "${password}" ]] || return 1
    [[ "${password}" != *$'\n'* && "${password}" != *$'\r'* ]] || return 1
    [[ ! "${password}" =~ ^[[:space:]] ]] || return 1
    [[ ! "${password}" =~ [[:space:]]$ ]] || return 1
}

prompt_new_password() {
    local first=""
    local second=""

    while true; do
        read -r -s -p "Administrator password: " first
        printf '\n'
        read -r -s -p "Repeat administrator password: " second
        printf '\n'
        if [[ "${first}" != "${second}" ]]; then
            say "Those passwords did not match. Try again."
            continue
        fi
        if ! password_is_valid "${first}"; then
            say "The password cannot be empty or begin or end with whitespace."
            continue
        fi
        ADMIN_PASSWORD="${first}"
        return
    done
}

prompt_password() {
    local existing=""
    local change=""

    existing="$(read_env_value CASH_ADMIN_PASSWORD "${ENV_FILE}")"
    if [[ -n "${ADMIN_PASSWORD}" ]]; then
        password_is_valid "${ADMIN_PASSWORD}" || fail "CASH_SETUP_ADMIN_PASSWORD cannot be empty, multiline, or have surrounding whitespace."
        return
    fi

    if [[ -n "${existing}" ]]; then
        read -r -p "Keep the existing administrator password? [Y/n]: " change
        case "${change}" in
            n|N|no|NO|No) prompt_new_password ;;
            *) ADMIN_PASSWORD="${existing}" ;;
        esac
    else
        prompt_new_password
    fi
}

prompt_new_user_password() {
    local first=""
    local second=""
    while true; do
        read -r -s -p "Shared employee password: " first
        printf '\n'
        read -r -s -p "Repeat shared employee password: " second
        printf '\n'
        if [[ "${first}" != "${second}" ]]; then
            say "Those passwords did not match. Try again."
            continue
        fi
        if ! password_is_valid "${first}"; then
            say "The password cannot be empty or begin or end with whitespace."
            continue
        fi
        USER_PASSWORD="${first}"
        return
    done
}

prompt_user_password() {
    local existing=""
    local change=""
    existing="$(read_env_value CASH_USER_PASSWORD "${ENV_FILE}")"
    if [[ -n "${USER_PASSWORD}" ]]; then
        password_is_valid "${USER_PASSWORD}" || fail "CASH_SETUP_USER_PASSWORD cannot be empty, multiline, or have surrounding whitespace."
        return
    fi
    if [[ -n "${existing}" ]]; then
        read -r -p "Keep the existing shared employee password? [Y/n]: " change
        case "${change}" in
            n|N|no|NO|No) prompt_new_user_password ;;
            *) USER_PASSWORD="${existing}" ;;
        esac
    else
        prompt_new_user_password
    fi
}

resolve_csv_source() {
    local candidate="$1"
    local label="$2"
    local parent header

    [[ -n "${candidate}" ]] || fail "${label} CSV path is required."
    if [[ "${candidate}" != /* ]]; then
        candidate="${PROJECT_ROOT}/${candidate}"
    fi
    [[ -f "${candidate}" && -r "${candidate}" ]] \
        || fail "${label} CSV is not a readable file: ${candidate}"
    parent="$(cd -- "$(dirname -- "${candidate}")" && pwd -P)"
    candidate="${parent}/$(basename -- "${candidate}")"
    header="$(sed -n '1{s/\r$//;p;}' "${candidate}")"
    [[ "${header}" == "name,code" ]] \
        || fail "${label} CSV must begin with exactly: name,code"
    CSV_RESULT="${candidate}"
}

prompt_csv_source() {
    local label="$1"
    local source_variable="$2"
    local installed_file="$3"
    local configured answer

    configured="${!source_variable}"
    if [[ -n "${configured}" ]]; then
        resolve_csv_source "${configured}" "${label}"
        printf -v "${source_variable}" '%s' "${CSV_RESULT}"
        return
    fi

    if [[ -f "${installed_file}" ]]; then
        read -r -p "Keep the installed ${label,,} CSV? [Y/n]: " answer
        case "${answer}" in
            n|N|no|NO|No) ;;
            *) printf -v "${source_variable}" '%s' "${installed_file}"; return ;;
        esac
    fi

    read -r -p "Path to ${label,,} CSV (name,code): " answer
    resolve_csv_source "${answer}" "${label}"
    printf -v "${source_variable}" '%s' "${CSV_RESULT}"
}

prompt_reference_data() {
    prompt_csv_source "Accounts" ACCOUNTS_SOURCE "${INSTALLED_ACCOUNTS_CSV}"
    prompt_csv_source "Classes" CLASSES_SOURCE "${INSTALLED_CLASSES_CSV}"
}


require_runtime_user() {
    if [[ "${EUID}" -eq 0 ]]; then
        fail "the root bootstrap did not delegate setup to the runtime user."
    fi
    command -v systemctl >/dev/null 2>&1 || fail "this installer requires a systemd-based Linux VM."
    command -v loginctl >/dev/null 2>&1 || fail "systemd-loginctl is required for automatic startup."
    if [[ "${SETUP_BOOTSTRAPPED}" == "1" ]]; then
        [[ "$(id -un)" == "${SERVICE_USER_REQUESTED}" ]] \
            || fail "setup was delegated to the wrong runtime user."
    else
        command -v sudo >/dev/null 2>&1 \
            || fail "sudo is required when setup is not bootstrapped by root."
    fi
}

install_host_packages() {
    local -a packages=()

    command -v podman >/dev/null 2>&1 || packages+=(podman)
    command -v podman-compose >/dev/null 2>&1 || packages+=(podman-compose)
    command -v curl >/dev/null 2>&1 || packages+=(curl)
    if ! command -v swapon >/dev/null 2>&1 \
            || ! command -v mkswap >/dev/null 2>&1 \
            || ! command -v blkid >/dev/null 2>&1 \
            || ! command -v flock >/dev/null 2>&1; then
        packages+=(util-linux)
    fi

    if (( ${#packages[@]} == 0 )); then
        return
    fi

    say "Installing required host packages: ${packages[*]}"
    if [[ "${EUID}" -ne 0 ]]; then
        sudo -v
    fi
    if command -v apt-get >/dev/null 2>&1; then
        privileged apt-get update
        privileged env DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
    elif command -v dnf >/dev/null 2>&1; then
        privileged dnf install -y "${packages[@]}"
    else
        fail "automatic package installation supports apt or dnf; install ${packages[*]} and run setup.sh again."
    fi

    command -v podman >/dev/null 2>&1 || fail "Podman was not installed successfully."
    command -v curl >/dev/null 2>&1 || fail "curl was not installed successfully."
    command -v podman-compose >/dev/null 2>&1 \
        || fail "podman-compose was not installed successfully."
}

validate_host() {
    local cgroups_version
    if ! cgroups_version="$(podman info --format '{{.Host.CgroupsVersion}}')"; then
        fail "rootless Podman could not start for $(id -un). Check that the account has subordinate UID/GID ranges and rerun setup."
    fi
    [[ "${cgroups_version}" == "v2" ]] || fail "The rootless Compose deployment requires cgroup v2; this host reports ${cgroups_version}."
}

configure_host_swap() {
    # Paths are arguments so provisioning can be exercised against disposable
    # files. Real setup always uses these defaults; never format an old file.
    privileged bash -s -- "${1:-/swapfile.cash-27}" "${2:-/etc/fstab}" <<'SWAP'
set -euo pipefail
umask 077
swap_file="$1"
fstab_file="$2"
temporary=""
trap 'if [[ -n "${temporary}" ]]; then rm -f -- "${temporary}"; fi' EXIT
stop() { printf 'Swap setup stopped: %s\n' "$*" >&2; exit 1; }

exec 9>"${swap_file}.setup.lock"
flock -x 9
active="$(swapon --show=NAME --noheadings)"
if [[ -L "${swap_file}" ]]; then
    stop "${swap_file} is a symlink; it will not be used."
elif [[ -e "${swap_file}" ]]; then
    [[ -f "${swap_file}" && "$(stat -c %h -- "${swap_file}")" == 1 ]] \
        || stop "${swap_file} must be a regular file with one link."
    [[ "$(blkid -p -s TYPE -o value -- "${swap_file}" || true)" == swap ]] \
        || stop "${swap_file} is not initialized swap; it will not be overwritten."
elif [[ -n "${active}" ]] || awk '
    $0 !~ /^[[:space:]]*#/ && $3 == "swap" { found=1 }
    END { exit !found }
' "${fstab_file}"; then
    printf 'Existing host swap found; leaving its configuration unchanged.\n'
    exit 0
else
    available_kib="$(df -Pk -- "$(dirname -- "${swap_file}")" | awk 'END {print $4}')"
    [[ "${available_kib}" =~ ^[0-9]+$ ]] || stop "cannot determine free disk space."
    (( available_kib >= 3 * 1024 * 1024 )) \
        || stop "need at least 3 GiB free to create 2 GiB swap and retain 1 GiB disk space."
    printf 'Creating a 2 GiB host swap file at %s...\n' "${swap_file}"
    temporary="$(mktemp "${swap_file}.XXXXXX")"
    # Real allocated blocks, not a sparse file or filesystem-dependent preallocation.
    dd if=/dev/zero of="${temporary}" bs=1M count=2048 conv=fsync status=none
    chmod 0600 -- "${temporary}"
    chown root:root -- "${temporary}"
    mkswap "${temporary}"
    mv -- "${temporary}" "${swap_file}"
    temporary=""
fi

chmod 0600 -- "${swap_file}"
chown root:root -- "${swap_file}"
if ! printf '%s\n' "${active}" | grep -Fxq -- "${swap_file}"; then
    swapon "${swap_file}" || stop "could not enable ${swap_file}; fstab was not changed."
fi
swapon --show=NAME --noheadings | grep -Fxq -- "${swap_file}" \
    || stop "${swap_file} is not active; fstab was not changed."
if ! awk -v file="${swap_file}" '
    $0 !~ /^[[:space:]]*#/ && $1 == file && $3 == "swap" { found=1 }
    END { exit !found }
' "${fstab_file}"; then
    printf '\n%s none swap sw 0 0\n' "${swap_file}" >>"${fstab_file}"
fi
printf 'Host swap is active and configured for reboot: %s\n' "${swap_file}"
SWAP
}

configure_rootless_ports() {
    local current temporary
    current="$(sysctl -n net.ipv4.ip_unprivileged_port_start)"
    if (( current <= 80 )); then
        return
    fi
    say "Allowing the rootless Caddy container to bind public ports 80 and 443..."
    temporary="$(mktemp)"
    printf 'net.ipv4.ip_unprivileged_port_start=80\n' >"${temporary}"
    privileged install -m 0644 -- "${temporary}" \
        /etc/sysctl.d/90-cash-27-rootless-ports.conf
    rm -f -- "${temporary}"
    privileged sysctl --load /etc/sysctl.d/90-cash-27-rootless-ports.conf >/dev/null
}

configure_host_firewall() {
    if command -v ufw >/dev/null 2>&1 && privileged ufw status | grep -q '^Status: active'; then
        say "Allowing public HTTP and HTTPS through UFW..."
        privileged ufw allow 80/tcp
        privileged ufw allow 443/tcp
    fi

    if command -v firewall-cmd >/dev/null 2>&1 \
        && privileged firewall-cmd --state >/dev/null 2>&1; then
        say "Allowing public HTTP and HTTPS through firewalld..."
        privileged firewall-cmd --permanent --add-service=http
        privileged firewall-cmd --permanent --add-service=https
        privileged firewall-cmd --reload
    fi
}

valid_service_user() {
    [[ "$1" =~ ^[a-z_][a-z0-9_-]*[$]?$ && "$1" != "root" ]]
}

service_user_home() {
    getent passwd "$1" | cut -d: -f6
}

validate_operator_configuration() {
    local service_user="$1" existing existing_project
    [[ -f "${OPERATOR_CONFIG}" ]] || return 0
    existing="$(grep -m1 '^CASH_SERVICE_USER=' "${OPERATOR_CONFIG}" || true)"
    existing="${existing#*=}"
    [[ -z "${existing}" || "${existing}" == "${service_user}" ]] \
        || fail "${OPERATOR_CONFIG} belongs to runtime user ${existing}; remove it deliberately before choosing another user."
    existing_project="$(grep -m1 '^CASH_PROJECT_ROOT=' "${OPERATOR_CONFIG}" || true)"
    existing_project="${existing_project#*=}"
    [[ -z "${existing_project}" || "${existing_project}" == "${PROJECT_ROOT}" ]] \
        || fail "cash-27 is already installed from ${existing_project}; run setup there."
}

write_operator_configuration() {
    local service_user="$1" service_home="$2" directory temporary
    directory="$(dirname -- "${OPERATOR_CONFIG}")"
    mkdir -p -- "${directory}"
    temporary="$(mktemp "${directory}/.operator.conf.XXXXXX")"
    {
        printf 'CASH_SERVICE_USER=%s\n' "${service_user}"
        printf 'CASH_SERVICE_HOME=%s\n' "${service_home}"
        printf 'CASH_PROJECT_ROOT=%s\n' "${PROJECT_ROOT}"
    } >"${temporary}"
    chmod 0644 -- "${temporary}"
    mv -f -- "${temporary}" "${OPERATOR_CONFIG}"
}

bootstrap_as_root() {
    local service_user service_home service_uid service_group owner_uid created

    command -v useradd >/dev/null 2>&1 || fail "useradd is required."
    command -v getent >/dev/null 2>&1 || fail "getent is required."
    command -v runuser >/dev/null 2>&1 || fail "runuser is required."
    command -v systemctl >/dev/null 2>&1 \
        || fail "this installer requires a systemd-based Linux VM."
    command -v loginctl >/dev/null 2>&1 \
        || fail "systemd-loginctl is required for automatic startup."

    service_user="${SERVICE_USER_REQUESTED}"
    created="0"
    valid_service_user "${service_user}" \
        || fail "CASH_SERVICE_USER must be a non-root system username."
    validate_operator_configuration "${service_user}"
    if ! getent passwd "${service_user}" >/dev/null; then
        say "Creating the unprivileged ${service_user} runtime account..."
        useradd --create-home --shell /bin/bash "${service_user}"
        passwd --lock "${service_user}" >/dev/null
        created="1"
    else
        say "Using the existing ${service_user} runtime account."
    fi
    service_home="$(service_user_home "${service_user}")"
    service_uid="$(id -u "${service_user}")"
    service_group="$(id -gn "${service_user}")"
    [[ -n "${service_home}" && "${service_home}" == /* ]] \
        || fail "runtime user ${service_user} needs an absolute home directory."
    [[ "${service_uid}" != "0" ]] \
        || fail "the runtime account may not be root."
    if [[ "${created}" == "1" ]] \
            && { ! cut -d: -f1 /etc/subuid | grep -Fxq "${service_user}" \
                 || ! cut -d: -f1 /etc/subgid | grep -Fxq "${service_user}"; }; then
        fail "the new runtime account did not receive subordinate UID/GID ranges required by rootless Podman."
    fi

    owner_uid="$(stat -c '%u' -- "${PROJECT_ROOT}")"
    [[ "${owner_uid}" == "0" ]] \
        || fail "the production checkout must be root-owned. Clone it as root under /opt/cash-27."
    chown -R "root:${service_group}" -- "${PROJECT_ROOT}"
    chmod -R u+rwX,g+rX,g-w,o-rwx -- "${PROJECT_ROOT}"
    find "${PROJECT_ROOT}" -type d -exec chmod g+s {} +
    if ! runuser -u "${service_user}" -- test -r "${PROJECT_ROOT}/compose.yaml" \
            -a -x "${PROJECT_ROOT}/setup.sh"; then
        fail "${service_user} cannot read this checkout. Clone it under /opt/cash-27 rather than under /root."
    fi

    install_host_packages
    configure_host_swap
    configure_rootless_ports
    configure_host_firewall
    loginctl enable-linger "${service_user}"
    systemctl start "user@${service_uid}.service"
    [[ -d "/run/user/${service_uid}" ]] \
        || fail "systemd did not create the runtime directory for ${service_user}."

    write_operator_configuration "${service_user}" "${service_home}"
    "${PROJECT_ROOT}/deploy/as-runtime-user.sh" \
        git config --global --add safe.directory "${PROJECT_ROOT}"

    say "Continuing setup as ${service_user}; long-running containers will not run as root."
    export CASH_SETUP_BOOTSTRAPPED=1
    export CASH_SERVICE_USER="${service_user}"
    exec "${PROJECT_ROOT}/deploy/as-runtime-user.sh" "${PROJECT_ROOT}/setup.sh"
}

write_private_configuration() {
    local existing_secret=""
    local env_tmp install_tmp compose_tmp name value

    existing_secret="$(read_env_value CASH_SECRET_KEY "${ENV_FILE}")"
    SECRET_KEY="${existing_secret:-$(random_hex_32)}"

    mkdir -p -- "${CONFIG_DIR}" "${DATA_ROOT}" "${USER_SYSTEMD_DIR}" "${CADDY_DATA}" "${CADDY_CONFIG}" "${BACKUP_STATE_DIR}"
    chmod 0700 -- "${CONFIG_DIR}" "${DATA_ROOT}" "${USER_SYSTEMD_DIR}" "${CADDY_DATA}" "${CADDY_CONFIG}" "${BACKUP_STATE_DIR}"

    env_tmp="$(mktemp "${CONFIG_DIR}/.cash-27.env.XXXXXX")"
    install_tmp="$(mktemp "${CONFIG_DIR}/.install.conf.XXXXXX")"
    compose_tmp="$(mktemp "${CONFIG_DIR}/.compose.env.XXXXXX")"
    trap 'rm -f -- "${env_tmp:-}" "${install_tmp:-}" "${compose_tmp:-}"' RETURN

    {
        printf 'CASH_ADMIN_PASSWORD=%s\n' "${ADMIN_PASSWORD}"
        printf 'CASH_USER_PASSWORD=%s\n' "${USER_PASSWORD}"
        printf 'CASH_SECRET_KEY=%s\n' "${SECRET_KEY}"
        printf 'CASH_FY_START_MONTH=9\n'
        printf 'CASH_TIMEZONE=America/Los_Angeles\n'
    } >"${env_tmp}"
    {
        printf 'CASH_DOMAIN=%s\n' "${DOMAIN}"
    } >"${install_tmp}"
    {
        printf 'CASH_APP_IMAGE=%s\n' "$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh" app)"
        printf 'CASH_DATA_ROOT=%s\n' "${DATA_ROOT}"
        printf 'CASH_ACCOUNTS_CSV=%s\n' "${INSTALLED_ACCOUNTS_CSV}"
        printf 'CASH_CLASSES_CSV=%s\n' "${INSTALLED_CLASSES_CSV}"
        printf 'CASH_APP_ENV_FILE=%s\n' "${ENV_FILE}"
        printf 'CASH_CADDY_FILE=%s\n' "${CADDY_FILE}"
        printf 'CASH_CADDY_DATA=%s\n' "${CADDY_DATA}"
        printf 'CASH_CADDY_CONFIG=%s\n' "${CADDY_CONFIG}"
        # Preserve optional local-archive tuning on later setup runs.
        for name in CASH_LOCAL_BACKUP_INTERVAL_SECONDS CASH_LOCAL_BACKUP_RETRY_SECONDS \
                CASH_LOCAL_BACKUP_KEEP CASH_LOCAL_BACKUP_UPGRADE_KEEP CASH_LOCAL_BACKUP_MANUAL_KEEP; do
            value="${!name:-}"
            value="${value:-$(read_env_value "${name}" "${COMPOSE_ENV_FILE}")}"
            if [[ -n "${value}" ]]; then
                [[ "${value}" =~ ^[1-9][0-9]*$ ]] || fail "${name} must be a positive integer."
                printf '%s=%s\n' "${name}" "${value}"
            fi
        done
    } >"${compose_tmp}"
    chmod 0600 -- "${env_tmp}" "${install_tmp}" "${compose_tmp}"
    mv -f -- "${env_tmp}" "${ENV_FILE}"
    mv -f -- "${install_tmp}" "${INSTALL_FILE}"
    mv -f -- "${compose_tmp}" "${COMPOSE_ENV_FILE}"
    trap - RETURN
}

install_private_csv() {
    local source="$1"
    local destination="$2"
    local temporary

    if [[ "${source}" == "${destination}" ]]; then
        chmod 0600 -- "${destination}"
        return
    fi
    temporary="$(mktemp "${REFERENCE_DIR}/.$(basename -- "${destination}").XXXXXX")"
    trap 'rm -f -- "${temporary:-}"' RETURN
    cp -- "${source}" "${temporary}"
    chmod 0600 -- "${temporary}"
    mv -f -- "${temporary}" "${destination}"
    trap - RETURN
}

install_reference_data() {
    mkdir -p -- "${REFERENCE_DIR}"
    chmod 0700 -- "${REFERENCE_DIR}"
    install_private_csv "${ACCOUNTS_SOURCE}" "${INSTALLED_ACCOUNTS_CSV}"
    install_private_csv "${CLASSES_SOURCE}" "${INSTALLED_CLASSES_CSV}"
}

write_compose_service() {
    local temporary
    temporary="$(mktemp "${USER_SYSTEMD_DIR}/.cash-27-compose.service.XXXXXX")"
    trap 'rm -f -- "${temporary:-}"' RETURN
    cat >"${temporary}" <<EOF
[Unit]
Description=Cash 27 Compose application stack
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${PROJECT_ROOT}
Environment="CASH_CONFIG_DIR=${CONFIG_DIR}"
ExecStart="${PROJECT_ROOT}/deploy/compose.sh" up --detach --remove-orphans
ExecStop="${PROJECT_ROOT}/deploy/compose.sh" down
TimeoutStartSec=1200
TimeoutStopSec=180

[Install]
WantedBy=default.target
EOF
    chmod 0600 -- "${temporary}"
    mv -f -- "${temporary}" "${COMPOSE_SERVICE_FILE}"
    trap - RETURN
}


write_caddy_body() {
    local destination="$1"
    mkdir -p -- "$(dirname -- "${destination}")"
    cat >"${destination}" <<EOF
# Managed by cash-27/setup.sh. Rerun setup.sh to change the domain.
${DOMAIN} {
    encode zstd gzip
    reverse_proxy app:8000

    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "same-origin"
    }
}
EOF
}

render_only() {
    mkdir -p -- "$(dirname -- "${CADDY_FILE}")"
    write_caddy_body "${CADDY_FILE}"
    say "Rendered deployment configuration without installing or starting services."
}

validate_caddy_configuration() {
    say "Validating the Caddy configuration..."
    CASH_CONFIG_DIR="${CONFIG_DIR}" "${PROJECT_ROOT}/deploy/compose.sh" \
        run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile \
        || fail "Caddy rejected ${CADDY_FILE}."
}

validate_reference_configuration() {
    say "Validating all account and class CSV rows..."
    # Use the built image's parser, without credentials, network, or a database
    # mount. Bad reference data must fail before starting a restart loop.
    podman run --rm --network none --read-only --cap-drop ALL \
        --security-opt no-new-privileges --entrypoint python \
        --volume "${INSTALLED_ACCOUNTS_CSV}:/config/accounts.csv:ro,z" \
        --volume "${INSTALLED_CLASSES_CSV}:/config/classes.csv:ro,z" \
        "${IMAGE}" -m cash.reference_data /config/accounts.csv /config/classes.csv \
        || fail "reference CSV validation failed. Correct the file and rerun setup, choosing its path again."
}

start_application() {
    local username
    username="$(id -un)"

    if [[ "${SETUP_BOOTSTRAPPED}" != "1" ]]; then
        sudo loginctl enable-linger "${username}"
    fi
    say "Building application image ${IMAGE} (also used for local archives)..."
    "${PROJECT_ROOT}/deploy/podman-build.sh"
    validate_reference_configuration
    validate_caddy_configuration

    systemctl --user daemon-reload
    systemctl --user enable cash-27-compose.service
    systemctl --user restart cash-27-compose.service
}

wait_for_health() {
    local attempt
    for attempt in {1..30}; do
        if curl --silent --show-error --fail --max-time 2 \
            http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
            return
        fi
        sleep 1
    done
    systemctl --user --no-pager status cash-27-compose.service || true
    CASH_CONFIG_DIR="${CONFIG_DIR}" "${PROJECT_ROOT}/deploy/compose.sh" ps || true
    fail "the container did not become healthy on localhost:8000."
}

main() {
    cd -- "${PROJECT_ROOT}"

    if [[ "${SETUP_RENDER_ONLY}" != "1" && "${EUID}" -eq 0 ]]; then
        bootstrap_as_root
    fi
    IMAGE="$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh")"
    if [[ "${SETUP_RENDER_ONLY}" != "1" ]]; then
        require_runtime_user
    fi

    say "cash-27 production setup"
    say "The application will use ${DATA_ROOT} for persistent data."
    say ""
    prompt_domain
    prompt_password
    prompt_user_password
    prompt_reference_data

    if [[ "${SETUP_RENDER_ONLY}" != "1" && "${SETUP_BOOTSTRAPPED}" != "1" ]]; then
        install_host_packages
    fi

    write_private_configuration
    install_reference_data
    write_compose_service
    write_caddy_body "${CADDY_FILE}"

    if [[ "${SETUP_RENDER_ONLY}" == "1" ]]; then
        render_only
        return
    fi

    validate_host
    if [[ "${SETUP_BOOTSTRAPPED}" != "1" ]]; then
        configure_host_swap
        configure_rootless_ports
        configure_host_firewall
    fi
    start_application
    wait_for_health

    say ""
    say "cash-27 is running."
    say "Open: https://${DOMAIN}/login"
    say "Application image: ${IMAGE}"
    say "Persistent data: ${DATA_ROOT}"
    say "Account and class CSV copies: ${REFERENCE_DIR}"
    say "Services: app, local archives, and Caddy (Podman Compose)"
    say "Local recovery archives: ${BACKUP_STATE_DIR}"
    say "Enable managed Droplet backups in DigitalOcean; setup does not enable or verify them."
    say "Keep persistent data on the Droplet disk: attached Volumes are not included in Droplet backups."
    say ""
    say "Caddy will obtain HTTPS automatically once DNS points ${DOMAIN} to this VM"
    say "and the cloud firewall permits inbound TCP ports 80 and 443."
    say "For an upgrade: ./upgrade.sh"
    say "For local archive recovery: ./restore-local.sh (see README for a DO restore rehearsal)"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
