#!/bin/bash
#
# Watchdog de la stack podman PowerView (dead-man switch).
#
# À lancer par cron (utilisateur ubuntu, propriétaire de la stack rootless),
# toutes les 5 minutes :
#   */5 * * * * /srv/powerview/tools/monitoring/stack_watchdog.sh >/dev/null 2>&1
#
# Logique :
#   - vérifie que les 3 conteneurs (influxdb, grafana, powerview-config-api)
#     sont Up et pas unhealthy ;
#   - vérifie les endpoints HTTP locaux d'InfluxDB et Grafana ;
#   - tout va bien  -> ping HEALTHCHECKS_PING_URL (dead-man switch : c'est
#     l'ABSENCE de ping qui alerte, donc une machine morte alerte aussi) ;
#   - problème      -> ping HEALTHCHECKS_PING_URL/fail + notification ntfy.
#
# Configuration : /srv/powerview/.monitoring.env (voir monitoring.env.sample).

set -uo pipefail  # pas de -e : on veut collecter tous les problèmes avant de notifier

readonly BASE_DIR="/srv/powerview"
readonly ENV_FILE="${BASE_DIR}/.monitoring.env"
readonly LOG_FILE="${BASE_DIR}/logs/monitoring.log"
readonly EXPECTED_CONTAINERS=(influxdb grafana powerview-config-api)

mkdir -p "$(dirname "${LOG_FILE}")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "${LOG_FILE}"
}

if [[ -f "${ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    set -a; source "${ENV_FILE}"; set +a
fi

NTFY_SERVER="${NTFY_SERVER:-https://ntfy.sh}"
NTFY_TOPIC="${NTFY_TOPIC:-}"
HEALTHCHECKS_PING_URL="${HEALTHCHECKS_PING_URL:-}"

problems=()

# --- Conteneurs -------------------------------------------------------------
ps_output="$(podman ps -a --format '{{.Names}}|{{.Status}}' 2>&1)" || {
    problems+=("podman ps en échec: ${ps_output}")
    ps_output=""
}

for name in "${EXPECTED_CONTAINERS[@]}"; do
    status="$(echo "${ps_output}" | awk -F'|' -v n="${name}" '$1 == n {print $2}')"
    if [[ -z "${status}" ]]; then
        problems+=("conteneur ${name}: absent")
    elif [[ "${status}" != Up* ]]; then
        problems+=("conteneur ${name}: ${status}")
    elif [[ "${status}" == *unhealthy* ]]; then
        problems+=("conteneur ${name}: unhealthy")
    fi
done

# --- Endpoints HTTP locaux --------------------------------------------------
if ! curl -fsS -m 10 http://localhost:8086/health > /dev/null 2>&1; then
    problems+=("InfluxDB ne répond pas sur localhost:8086/health")
fi
if ! curl -fsS -m 10 http://localhost:8088/api/health > /dev/null 2>&1; then
    problems+=("Grafana ne répond pas sur localhost:8088/api/health")
fi

# --- Verdict ----------------------------------------------------------------
if [[ ${#problems[@]} -eq 0 ]]; then
    log "OK"
    if [[ -n "${HEALTHCHECKS_PING_URL}" ]]; then
        curl -fsS -m 10 --retry 3 "${HEALTHCHECKS_PING_URL}" > /dev/null 2>&1
    fi
    exit 0
fi

msg="$(printf '%s\n' "${problems[@]}")"
log "PROBLEMES DETECTES:"
log "${msg}"

if [[ -n "${HEALTHCHECKS_PING_URL}" ]]; then
    curl -fsS -m 10 --retry 3 --data-raw "${msg}" "${HEALTHCHECKS_PING_URL}/fail" > /dev/null 2>&1
fi

if [[ -n "${NTFY_TOPIC}" ]]; then
    curl -fsS -m 10 \
        -H "Title: PowerView stack en panne" \
        -H "Priority: urgent" \
        -H "Tags: rotating_light" \
        --data-raw "${msg}" \
        "${NTFY_SERVER}/${NTFY_TOPIC}" > /dev/null 2>&1
fi

exit 1
