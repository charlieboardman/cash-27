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
readonly BACKUP_CONFIG_FILE="${CONFIG_DIR}/backup.env"
readonly COMPOSE_ENV_FILE="${CONFIG_DIR}/compose.env"
readonly REFERENCE_DIR="${CONFIG_DIR}/reference"
readonly INSTALLED_ACCOUNTS_CSV="${REFERENCE_DIR}/accounts.csv"
readonly INSTALLED_CLASSES_CSV="${REFERENCE_DIR}/classes.csv"
readonly COMPOSE_SERVICE_FILE="${USER_SYSTEMD_DIR}/cash-27-compose.service"
readonly BACKUP_STATE_DIR="${DATA_ROOT}/backups/drive"
readonly BACKUP_HOST_STATUS="${BACKUP_STATE_DIR}/host.conf"
readonly CADDY_FILE="${CASH_SETUP_CADDY_FILE:-${CONFIG_DIR}/Caddyfile}"
readonly CADDY_DATA="${DATA_ROOT}/caddy/data"
readonly CADDY_CONFIG="${DATA_ROOT}/caddy/config"

DOMAIN="${CASH_SETUP_DOMAIN:-}"
ADMIN_PASSWORD="${CASH_SETUP_ADMIN_PASSWORD:-}"
USER_PASSWORD="${CASH_SETUP_USER_PASSWORD:-}"
ACCOUNTS_SOURCE="${CASH_SETUP_ACCOUNTS_CSV:-}"
CLASSES_SOURCE="${CASH_SETUP_CLASSES_CSV:-}"
BACKUP_REMOTE="${CASH_SETUP_RCLONE_REMOTE:-}"
BACKUP_PATH="${CASH_SETUP_RCLONE_PATH:-}"
BACKUP_CONFIGURED="0"
RCLONE_CONFIG_FILE="${CASH_SETUP_RCLONE_CONFIG:-}"
SECRET_KEY=""
BACKUP_CORE_TOKEN=""
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

valid_rclone_remote() {
    [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]]
}

valid_backup_path() {
    local value="$1"
    [[ -n "${value}" && "${value}" != *$'\n'* && "${value}" != *$'\r'* \
        && "${value}" != *$'\t'* ]]
}

configure_render_backup() {
    if [[ -n "${BACKUP_REMOTE}" ]]; then
        valid_rclone_remote "${BACKUP_REMOTE}" \
            || fail "CASH_SETUP_RCLONE_REMOTE contains invalid characters."
        BACKUP_PATH="${BACKUP_PATH:-Cash 27 Backups}"
        valid_backup_path "${BACKUP_PATH}" \
            || fail "CASH_SETUP_RCLONE_PATH is invalid."
        BACKUP_CONFIGURED="1"
    else
        BACKUP_PATH="${BACKUP_PATH:-Cash 27 Backups}"
    fi
}

configure_google_drive_backup() {
    local existing_remote existing_path existing_configured answer
    local -a remotes=()
    existing_remote="$(read_env_value CASH_BACKUP_REMOTE "${BACKUP_CONFIG_FILE}")"
    existing_path="$(read_env_value CASH_BACKUP_PATH "${BACKUP_CONFIG_FILE}")"
    existing_configured="$(read_env_value CASH_BACKUP_CONFIGURED "${BACKUP_CONFIG_FILE}")"

    if [[ -z "${BACKUP_REMOTE}" && "${existing_configured}" == "1" \
            && -n "${existing_remote}" ]] \
            && rclone listremotes | grep -Fxq "${existing_remote}:"; then
        read -r -p "Keep the existing Google Drive backup destination (${existing_remote}:${existing_path})? [Y/n]: " answer
        case "${answer}" in
            n|N|no|NO|No) ;;
            *)
                BACKUP_REMOTE="${existing_remote}"
                BACKUP_PATH="${existing_path:-Cash 27 Backups}"
                ;;
        esac
    fi

    if [[ -z "${BACKUP_REMOTE}" ]]; then
        read -r -p "Configure automatic Google Drive backups now? [Y/n]: " answer
        case "${answer}" in
            n|N|no|NO|No)
                BACKUP_PATH="${existing_path:-Cash 27 Backups}"
                say "Google Drive backup is not configured. Cash 27 will show an administrator warning."
                return
                ;;
        esac

        mapfile -t remotes < <(rclone listremotes | sed 's/:$//')
        if (( ${#remotes[@]} == 0 )); then
            say ""
            say "rclone needs a Google Drive remote. Its configuration wizard will now open."
            say "Create a remote using the Google Drive storage type, then quit the wizard."
            say "Google now requires your own OAuth client ID; rclone will explain where to enter it."
            rclone config
            mapfile -t remotes < <(rclone listremotes | sed 's/:$//')
        fi
        (( ${#remotes[@]} > 0 )) \
            || fail "rclone has no configured remote. Run setup.sh again after configuring one."
        say "Available rclone remotes: ${remotes[*]}"
        read -r -p "Google Drive remote name [${remotes[0]}]: " BACKUP_REMOTE
        BACKUP_REMOTE="${BACKUP_REMOTE:-${remotes[0]}}"
    fi

    valid_rclone_remote "${BACKUP_REMOTE}" \
        || fail "the rclone remote name may contain only letters, numbers, underscores, and hyphens."
    rclone listremotes | grep -Fxq "${BACKUP_REMOTE}:" \
        || fail "rclone remote ${BACKUP_REMOTE} does not exist. Run rclone config and rerun setup.sh."
    rclone config show "${BACKUP_REMOTE}" | grep -Eq '^type[[:space:]]*=[[:space:]]*drive$' \
        || fail "rclone remote ${BACKUP_REMOTE} is not a Google Drive remote."

    if [[ -z "${BACKUP_PATH}" ]]; then
        read -r -p "Folder in Google Drive [${existing_path:-Cash 27 Backups}]: " BACKUP_PATH
        BACKUP_PATH="${BACKUP_PATH:-${existing_path:-Cash 27 Backups}}"
    fi
    valid_backup_path "${BACKUP_PATH}" \
        || fail "the Google Drive backup folder is invalid."

    say "Checking ${BACKUP_REMOTE}:${BACKUP_PATH}..."
    rclone mkdir "${BACKUP_REMOTE}:${BACKUP_PATH}" \
        || fail "rclone could not create or open the Google Drive backup folder."
    rclone lsf "${BACKUP_REMOTE}:${BACKUP_PATH}" --max-depth 1 >/dev/null \
        || fail "rclone could not read the Google Drive backup folder."
    BACKUP_CONFIGURED="1"
}

resolve_rclone_config() {
    local reported
    if [[ -z "${RCLONE_CONFIG_FILE}" ]]; then
        reported="$(rclone config file 2>/dev/null | tail -n 1)"
        RCLONE_CONFIG_FILE="${reported:-${HOME}/.config/rclone/rclone.conf}"
    fi
    case "${RCLONE_CONFIG_FILE}" in
        /*) ;;
        *) fail "rclone's configuration path must be absolute." ;;
    esac
    mkdir -p -- "$(dirname -- "${RCLONE_CONFIG_FILE}")"
    touch "${RCLONE_CONFIG_FILE}"
    chmod 0600 -- "${RCLONE_CONFIG_FILE}"
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
    command -v rclone >/dev/null 2>&1 || packages+=(rclone)

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
    command -v rclone >/dev/null 2>&1 || fail "rclone was not installed successfully."
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
    local existing_backup_token=""
    local env_tmp install_tmp backup_tmp compose_tmp

    existing_secret="$(read_env_value CASH_SECRET_KEY "${ENV_FILE}")"
    SECRET_KEY="${existing_secret:-$(random_hex_32)}"
    existing_backup_token="$(read_env_value CASH_BACKUP_CORE_TOKEN "${BACKUP_CONFIG_FILE}")"
    BACKUP_CORE_TOKEN="${existing_backup_token:-$(random_hex_32)}"
    RCLONE_CONFIG_FILE="${RCLONE_CONFIG_FILE:-${CONFIG_DIR}/rclone.conf}"

    mkdir -p -- "${CONFIG_DIR}" "${DATA_ROOT}" "${USER_SYSTEMD_DIR}" "${CADDY_DATA}" "${CADDY_CONFIG}" "${BACKUP_STATE_DIR}"
    chmod 0700 -- "${CONFIG_DIR}" "${DATA_ROOT}" "${USER_SYSTEMD_DIR}" "${CADDY_DATA}" "${CADDY_CONFIG}" "${BACKUP_STATE_DIR}"
    touch "${RCLONE_CONFIG_FILE}"
    chmod 0600 -- "${RCLONE_CONFIG_FILE}"

    env_tmp="$(mktemp "${CONFIG_DIR}/.cash-27.env.XXXXXX")"
    install_tmp="$(mktemp "${CONFIG_DIR}/.install.conf.XXXXXX")"
    backup_tmp="$(mktemp "${CONFIG_DIR}/.backup.env.XXXXXX")"
    compose_tmp="$(mktemp "${CONFIG_DIR}/.compose.env.XXXXXX")"
    trap 'rm -f -- "${env_tmp:-}" "${install_tmp:-}" "${backup_tmp:-}" "${compose_tmp:-}"' RETURN

    {
        printf 'CASH_ADMIN_PASSWORD=%s\n' "${ADMIN_PASSWORD}"
        printf 'CASH_USER_PASSWORD=%s\n' "${USER_PASSWORD}"
        printf 'CASH_SECRET_KEY=%s\n' "${SECRET_KEY}"
        printf 'CASH_FY_START_MONTH=9\n'
        printf 'CASH_BACKUP_EXPECTED=1\n'
    } >"${env_tmp}"
    {
        printf 'CASH_DOMAIN=%s\n' "${DOMAIN}"
        printf 'CASH_BACKUP_CONFIGURED=%s\n' "${BACKUP_CONFIGURED}"
        printf 'CASH_BACKUP_REMOTE=%s\n' "${BACKUP_REMOTE}"
        printf 'CASH_BACKUP_PATH=%s\n' "${BACKUP_PATH}"
    } >"${install_tmp}"
    {
        printf 'CASH_BACKUP_CONFIGURED=%s\n' "${BACKUP_CONFIGURED}"
        printf 'CASH_BACKUP_REMOTE=%s\n' "${BACKUP_REMOTE}"
        printf 'CASH_BACKUP_PATH=%s\n' "${BACKUP_PATH}"
        printf 'CASH_BACKUP_CORE_TOKEN=%s\n' "${BACKUP_CORE_TOKEN}"
        printf 'CASH_BACKUP_CORE_URL=http://backup-core:8090/archive\n'
        printf 'CASH_BACKUP_INTERVAL_SECONDS=86400\n'
        printf 'CASH_BACKUP_RETRY_SECONDS=3600\n'
    } >"${backup_tmp}"
    {
        printf 'CASH_APP_IMAGE=%s\n' "$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh" app)"
        printf 'CASH_BACKUP_IMAGE=%s\n' "$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh" backup)"
        printf 'CASH_DATA_ROOT=%s\n' "${DATA_ROOT}"
        printf 'CASH_ACCOUNTS_CSV=%s\n' "${INSTALLED_ACCOUNTS_CSV}"
        printf 'CASH_CLASSES_CSV=%s\n' "${INSTALLED_CLASSES_CSV}"
        printf 'CASH_APP_ENV_FILE=%s\n' "${ENV_FILE}"
        printf 'CASH_BACKUP_ENV_FILE=%s\n' "${BACKUP_CONFIG_FILE}"
        printf 'CASH_RCLONE_CONFIG=%s\n' "${RCLONE_CONFIG_FILE}"
        printf 'CASH_CADDY_FILE=%s\n' "${CADDY_FILE}"
        printf 'CASH_CADDY_DATA=%s\n' "${CADDY_DATA}"
        printf 'CASH_CADDY_CONFIG=%s\n' "${CADDY_CONFIG}"
    } >"${compose_tmp}"
    chmod 0600 -- "${env_tmp}" "${install_tmp}" "${backup_tmp}" "${compose_tmp}"
    mv -f -- "${env_tmp}" "${ENV_FILE}"
    mv -f -- "${install_tmp}" "${INSTALL_FILE}"
    mv -f -- "${backup_tmp}" "${BACKUP_CONFIG_FILE}"
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

write_backup_host_status() {
    local temporary destination
    mkdir -p -- "${BACKUP_STATE_DIR}"
    chmod 0700 -- "${BACKUP_STATE_DIR}"
    destination=""
    if [[ "${BACKUP_CONFIGURED}" == "1" ]]; then
        destination="${BACKUP_REMOTE}:${BACKUP_PATH}"
    fi
    temporary="$(mktemp "${BACKUP_STATE_DIR}/.host.conf.XXXXXX")"
    {
        printf 'rclone_configured=%s\n' "${BACKUP_CONFIGURED}"
        printf 'service_installed=1\n'
        printf 'destination=%s\n' "${destination}"
        printf 'schedule=At startup and every 24 hours\n'
    } >"${temporary}"
    chmod 0600 -- "${temporary}"
    mv -f -- "${temporary}" "${BACKUP_HOST_STATUS}"
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

start_application() {
    local username
    username="$(id -un)"

    if [[ "${SETUP_BOOTSTRAPPED}" != "1" ]]; then
        sudo loginctl enable-linger "${username}"
    fi
    say "Building application and backup images for ${IMAGE}..."
    "${PROJECT_ROOT}/deploy/podman-build.sh"
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

    if [[ "${SETUP_RENDER_ONLY}" == "1" ]]; then
        configure_render_backup
    else
        if [[ "${SETUP_BOOTSTRAPPED}" != "1" ]]; then
            install_host_packages
        fi
        configure_google_drive_backup
        resolve_rclone_config
    fi

    write_private_configuration
    install_reference_data
    write_compose_service
    write_backup_host_status
    write_caddy_body "${CADDY_FILE}"

    if [[ "${SETUP_RENDER_ONLY}" == "1" ]]; then
        render_only
        return
    fi

    validate_host
    if [[ "${SETUP_BOOTSTRAPPED}" != "1" ]]; then
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
    say "Services: app, archive builder, Drive adapter, and Caddy (Podman Compose)"
    if [[ "${BACKUP_CONFIGURED}" == "1" ]]; then
        say "Google Drive backups: ${BACKUP_REMOTE}:${BACKUP_PATH}"
    else
        say "Google Drive backups: not configured (rerun setup.sh to configure them)"
    fi
    say ""
    say "Caddy will obtain HTTPS automatically once DNS points ${DOMAIN} to this VM"
    say "and the cloud firewall permits inbound TCP ports 80 and 443."
    say "For an upgrade: ./upgrade.sh"
    say "For disaster recovery: ./restore-from-drive.sh"
}

main "$@"
