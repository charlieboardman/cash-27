#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly IMAGE="$(CASH_PROJECT_ROOT="${PROJECT_ROOT}" "${PROJECT_ROOT}/deploy/image-ref.sh")"
readonly CONTAINER_NAME="cash-27"
readonly SETUP_RENDER_ONLY="${CASH_SETUP_RENDER_ONLY:-0}"

readonly CONFIG_DIR="${CASH_SETUP_CONFIG_DIR:-${HOME}/.config/cash-27}"
readonly DATA_ROOT="${CASH_SETUP_DATA_ROOT:-${HOME}/.local/share/cash-27}"
readonly QUADLET_DIR="${CASH_SETUP_QUADLET_DIR:-${HOME}/.config/containers/systemd}"
readonly USER_SYSTEMD_DIR="${CASH_SETUP_USER_SYSTEMD_DIR:-${HOME}/.config/systemd/user}"
readonly ENV_FILE="${CONFIG_DIR}/cash-27.env"
readonly INSTALL_FILE="${CONFIG_DIR}/install.conf"
readonly BACKUP_CONFIG_FILE="${CONFIG_DIR}/backup.conf"
readonly REFERENCE_DIR="${CONFIG_DIR}/reference"
readonly INSTALLED_ACCOUNTS_CSV="${REFERENCE_DIR}/accounts.csv"
readonly INSTALLED_CLASSES_CSV="${REFERENCE_DIR}/classes.csv"
readonly QUADLET_FILE="${QUADLET_DIR}/cash-27.container"
readonly BACKUP_SERVICE_FILE="${USER_SYSTEMD_DIR}/cash-27-backup.service"
readonly BACKUP_TIMER_FILE="${USER_SYSTEMD_DIR}/cash-27-backup.timer"
readonly BACKUP_STATE_DIR="${DATA_ROOT}/backups/drive"
readonly BACKUP_HOST_STATUS="${BACKUP_STATE_DIR}/host.conf"
readonly CADDY_FILE="${CASH_SETUP_CADDY_FILE:-/etc/caddy/conf.d/cash-27.caddy}"
readonly CADDY_MAIN_FILE="${CASH_SETUP_CADDY_MAIN_FILE:-/etc/caddy/Caddyfile}"

DOMAIN="${CASH_SETUP_DOMAIN:-}"
ADMIN_PASSWORD="${CASH_SETUP_ADMIN_PASSWORD:-}"
USER_PASSWORD="${CASH_SETUP_USER_PASSWORD:-}"
ACCOUNTS_SOURCE="${CASH_SETUP_ACCOUNTS_CSV:-}"
CLASSES_SOURCE="${CASH_SETUP_CLASSES_CSV:-}"
BACKUP_REMOTE="${CASH_SETUP_RCLONE_REMOTE:-}"
BACKUP_PATH="${CASH_SETUP_RCLONE_PATH:-}"
BACKUP_CONFIGURED="0"
SECRET_KEY=""
CSV_RESULT=""

say() {
    printf '%s\n' "$*"
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
    [[ ${#password} -ge 12 ]] || return 1
    [[ ! "${password}" =~ ^[[:space:]] ]] || return 1
    [[ ! "${password}" =~ [[:space:]]$ ]] || return 1
}

prompt_new_password() {
    local first=""
    local second=""

    while true; do
        read -r -s -p "Administrator password (at least 12 characters): " first
        printf '\n'
        read -r -s -p "Repeat administrator password: " second
        printf '\n'
        if [[ "${first}" != "${second}" ]]; then
            say "Those passwords did not match. Try again."
            continue
        fi
        if ! password_is_valid "${first}"; then
            say "Use at least 12 characters, with no whitespace at the beginning or end."
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
        password_is_valid "${ADMIN_PASSWORD}" || fail "CASH_SETUP_ADMIN_PASSWORD must contain at least 12 characters and have no surrounding whitespace."
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
        read -r -s -p "Shared employee password (at least 12 characters): " first
        printf '\n'
        read -r -s -p "Repeat shared employee password: " second
        printf '\n'
        if [[ "${first}" != "${second}" ]]; then
            say "Those passwords did not match. Try again."
            continue
        fi
        if ! password_is_valid "${first}"; then
            say "Use at least 12 characters, with no whitespace at the beginning or end."
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
        password_is_valid "${USER_PASSWORD}" || fail "CASH_SETUP_USER_PASSWORD must contain at least 12 characters and have no surrounding whitespace."
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

require_normal_user() {
    if [[ "${EUID}" -eq 0 ]]; then
        fail "run this as the normal account that will operate cash-27, not as root (the script will use sudo when needed)."
    fi
    command -v sudo >/dev/null 2>&1 || fail "sudo is required on the production VM."
    command -v systemctl >/dev/null 2>&1 || fail "this installer requires a systemd-based Linux VM."
    command -v loginctl >/dev/null 2>&1 || fail "systemd-loginctl is required for automatic startup."
}

install_host_packages() {
    local -a packages=()

    command -v podman >/dev/null 2>&1 || packages+=(podman)
    command -v caddy >/dev/null 2>&1 || packages+=(caddy)
    command -v curl >/dev/null 2>&1 || packages+=(curl)
    command -v rclone >/dev/null 2>&1 || packages+=(rclone)

    if (( ${#packages[@]} == 0 )); then
        return
    fi

    say "Installing required host packages: ${packages[*]}"
    sudo -v
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y "${packages[@]}"
    else
        fail "automatic package installation supports apt or dnf; install ${packages[*]} and run setup.sh again."
    fi

    command -v podman >/dev/null 2>&1 || fail "Podman was not installed successfully."
    command -v caddy >/dev/null 2>&1 || fail "Caddy was not installed successfully."
    command -v curl >/dev/null 2>&1 || fail "curl was not installed successfully."
    command -v rclone >/dev/null 2>&1 || fail "rclone was not installed successfully."
}

validate_host() {
    local cgroups_version
    cgroups_version="$(podman info --format '{{.Host.CgroupsVersion}}')"
    [[ "${cgroups_version}" == "v2" ]] || fail "Podman Quadlet requires cgroup v2; this host reports ${cgroups_version}."
}

configure_host_firewall() {
    if command -v ufw >/dev/null 2>&1 && sudo ufw status | grep -q '^Status: active'; then
        say "Allowing public HTTP and HTTPS through UFW..."
        sudo ufw allow 80/tcp
        sudo ufw allow 443/tcp
    fi

    if command -v firewall-cmd >/dev/null 2>&1 \
        && sudo firewall-cmd --state >/dev/null 2>&1; then
        say "Allowing public HTTP and HTTPS through firewalld..."
        sudo firewall-cmd --permanent --add-service=http
        sudo firewall-cmd --permanent --add-service=https
        sudo firewall-cmd --reload
    fi
}

write_private_configuration() {
    local existing_secret=""
    local env_tmp install_tmp backup_tmp

    existing_secret="$(read_env_value CASH_SECRET_KEY "${ENV_FILE}")"
    SECRET_KEY="${existing_secret:-$(random_hex_32)}"

    mkdir -p -- "${CONFIG_DIR}" "${DATA_ROOT}" "${QUADLET_DIR}" \
        "${USER_SYSTEMD_DIR}"
    chmod 0700 -- "${CONFIG_DIR}" "${DATA_ROOT}" "${QUADLET_DIR}" \
        "${USER_SYSTEMD_DIR}"

    env_tmp="$(mktemp "${CONFIG_DIR}/.cash-27.env.XXXXXX")"
    install_tmp="$(mktemp "${CONFIG_DIR}/.install.conf.XXXXXX")"
    backup_tmp="$(mktemp "${CONFIG_DIR}/.backup.conf.XXXXXX")"
    trap 'rm -f -- "${env_tmp:-}" "${install_tmp:-}" "${backup_tmp:-}"' RETURN

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
        printf 'CASH_DATA_ROOT=%s\n' "${DATA_ROOT}"
        printf 'CASH_BACKUP_CONFIGURED=%s\n' "${BACKUP_CONFIGURED}"
        printf 'CASH_BACKUP_REMOTE=%s\n' "${BACKUP_REMOTE}"
        printf 'CASH_BACKUP_PATH=%s\n' "${BACKUP_PATH}"
    } >"${backup_tmp}"
    chmod 0600 -- "${env_tmp}" "${install_tmp}" "${backup_tmp}"
    mv -f -- "${env_tmp}" "${ENV_FILE}"
    mv -f -- "${install_tmp}" "${INSTALL_FILE}"
    mv -f -- "${backup_tmp}" "${BACKUP_CONFIG_FILE}"
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

write_quadlet() {
    local quadlet_tmp
    local uid gid
    uid="$(id -u)"
    gid="$(id -g)"
    quadlet_tmp="$(mktemp "${QUADLET_DIR}/.cash-27.container.XXXXXX")"
    trap 'rm -f -- "${quadlet_tmp:-}"' RETURN

    cat >"${quadlet_tmp}" <<EOF
[Unit]
Description=cash-27 cash ledger
Wants=network-online.target
After=network-online.target

[Container]
Image=${IMAGE}
ContainerName=${CONTAINER_NAME}
Pull=never
UserNS=keep-id
User=${uid}
Group=${gid}
DropCapability=all
NoNewPrivileges=true
ReadOnly=true
Tmpfs=/tmp:rw,nosuid,nodev,noexec,size=134217728
PidsLimit=256
PublishPort=127.0.0.1:8000:8000
Volume=${DATA_ROOT}:/data:Z
Volume=${INSTALLED_ACCOUNTS_CSV}:/config/accounts.csv:ro,Z
Volume=${INSTALLED_CLASSES_CSV}:/config/classes.csv:ro,Z
EnvironmentFile=${ENV_FILE}
Environment=CASH_DATA_ROOT=/data
Environment=CASH_ACCOUNTS_CSV=/config/accounts.csv
Environment=CASH_CLASSES_CSV=/config/classes.csv
Environment=HOME=/tmp

[Service]
Restart=on-failure
RestartSec=5
TimeoutStartSec=900

[Install]
WantedBy=default.target
EOF
    chmod 0600 -- "${quadlet_tmp}"
    mv -f -- "${quadlet_tmp}" "${QUADLET_FILE}"
    trap - RETURN
}

write_backup_units() {
    local service_tmp timer_tmp
    service_tmp="$(mktemp "${USER_SYSTEMD_DIR}/.cash-27-backup.service.XXXXXX")"
    timer_tmp="$(mktemp "${USER_SYSTEMD_DIR}/.cash-27-backup.timer.XXXXXX")"
    trap 'rm -f -- "${service_tmp:-}" "${timer_tmp:-}"' RETURN

    cat >"${service_tmp}" <<EOF
[Unit]
Description=Create and upload a verified cash-27 backup
Wants=network-online.target
After=network-online.target cash-27.service

[Service]
Type=oneshot
Environment="CASH_BACKUP_CONFIG_DIR=${CONFIG_DIR}"
ExecStart="${PROJECT_ROOT}/deploy/backup-to-drive.sh"
TimeoutStartSec=7200
EOF
    cat >"${timer_tmp}" <<'EOF'
[Unit]
Description=Nightly cash-27 backup check

[Timer]
OnCalendar=*-*-* 02:00:00
RandomizedDelaySec=30m
Persistent=true
AccuracySec=5m
Unit=cash-27-backup.service

[Install]
WantedBy=timers.target
EOF
    chmod 0600 -- "${service_tmp}" "${timer_tmp}"
    mv -f -- "${service_tmp}" "${BACKUP_SERVICE_FILE}"
    mv -f -- "${timer_tmp}" "${BACKUP_TIMER_FILE}"
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
        printf 'timer_installed=1\n'
        printf 'destination=%s\n' "${destination}"
        printf 'schedule=Nightly around 2:00 AM\n'
    } >"${temporary}"
    chmod 0600 -- "${temporary}"
    mv -f -- "${temporary}" "${BACKUP_HOST_STATUS}"
}

write_caddy_body() {
    local destination="$1"
    cat >"${destination}" <<EOF
# Managed by cash-27/setup.sh. Rerun setup.sh to change the domain.
${DOMAIN} {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000

    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "same-origin"
    }
}
EOF
}

install_caddy_configuration() {
    local snippet_tmp main_tmp previous_tmp had_previous
    snippet_tmp="$(mktemp)"
    main_tmp="$(mktemp)"
    previous_tmp="$(mktemp)"
    had_previous=0
    trap 'rm -f -- "${snippet_tmp:-}" "${main_tmp:-}" "${previous_tmp:-}"' RETURN
    write_caddy_body "${snippet_tmp}"

    sudo install -d -m 0755 -- "$(dirname -- "${CADDY_FILE}")"
    if sudo test -f "${CADDY_FILE}"; then
        sudo cat "${CADDY_FILE}" >"${previous_tmp}"
        had_previous=1
    fi
    sudo install -m 0644 -- "${snippet_tmp}" "${CADDY_FILE}"

    if sudo test -f "${CADDY_MAIN_FILE}"; then
        sudo cat "${CADDY_MAIN_FILE}" >"${main_tmp}"
    fi
    if ! grep -Eq '^[[:space:]]*import[[:space:]]+conf\.d/\*[[:space:]]*$' "${main_tmp}"; then
        {
            cat "${main_tmp}"
            printf '\n# cash-27 sites\nimport conf.d/*\n'
        } >"${main_tmp}.new"
        mv -f -- "${main_tmp}.new" "${main_tmp}"
        sudo install -m 0644 -- "${main_tmp}" "${CADDY_MAIN_FILE}"
    fi

    if ! sudo caddy validate --config "${CADDY_MAIN_FILE}" --adapter caddyfile; then
        if [[ "${had_previous}" == "1" ]]; then
            sudo install -m 0644 -- "${previous_tmp}" "${CADDY_FILE}"
        else
            sudo rm -f -- "${CADDY_FILE}"
        fi
        fail "Caddy rejected its configuration. The prior cash-27 site was preserved; inspect ${CADDY_MAIN_FILE} and rerun setup.sh."
    fi
    trap - RETURN
}

render_only() {
    mkdir -p -- "$(dirname -- "${CADDY_FILE}")"
    write_caddy_body "${CADDY_FILE}"
    say "Rendered deployment configuration without installing or starting services."
}

start_application() {
    local username
    username="$(id -un)"

    say "Building application image ${IMAGE}..."
    "${PROJECT_ROOT}/deploy/podman-build.sh"

    sudo loginctl enable-linger "${username}"
    systemctl --user daemon-reload
    if ! systemctl --user cat cash-27.service >/dev/null 2>&1; then
        fail "Podman did not generate cash-27.service from ${QUADLET_FILE}. Check that this Podman installation includes Quadlet."
    fi
    systemctl --user restart cash-27.service

    sudo systemctl enable --now caddy
    sudo systemctl reload caddy
}

start_backups() {
    systemctl --user enable --now cash-27-backup.timer
    if [[ "${BACKUP_CONFIGURED}" == "1" ]]; then
        say "Creating the first Google Drive backup..."
        if ! systemctl --user start cash-27-backup.service; then
            say "WARNING: The first Google Drive backup failed. Cash 27 will show the failure to administrators."
            say "Inspect it with: journalctl --user --unit cash-27-backup --no-pager"
        fi
    fi
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
    systemctl --user --no-pager status cash-27.service || true
    fail "the container did not become healthy on localhost:8000."
}

main() {
    cd -- "${PROJECT_ROOT}"

    if [[ "${SETUP_RENDER_ONLY}" != "1" ]]; then
        require_normal_user
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
        install_host_packages
        configure_google_drive_backup
    fi

    write_private_configuration
    install_reference_data
    write_quadlet
    write_backup_units
    write_backup_host_status

    if [[ "${SETUP_RENDER_ONLY}" == "1" ]]; then
        render_only
        return
    fi

    validate_host
    configure_host_firewall
    install_caddy_configuration
    start_application
    wait_for_health
    start_backups

    say ""
    say "cash-27 is running."
    say "Open: https://${DOMAIN}/login"
    say "Application image: ${IMAGE}"
    say "Persistent data: ${DATA_ROOT}"
    say "Account and class CSV copies: ${REFERENCE_DIR}"
    if [[ "${BACKUP_CONFIGURED}" == "1" ]]; then
        say "Google Drive backups: ${BACKUP_REMOTE}:${BACKUP_PATH}"
    else
        say "Google Drive backups: not configured (rerun setup.sh to configure them)"
    fi
    say ""
    say "Caddy will obtain HTTPS automatically once DNS points ${DOMAIN} to this VM"
    say "and the cloud firewall permits inbound TCP ports 80 and 443."
    say "For an upgrade: ./upgrade.sh"
}

main "$@"
