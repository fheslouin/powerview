#!/bin/bash

set -euo pipefail  # Exit on error, undefined variables, and pipe failures

# Active le tracing uniquement si DEBUG=1 dans l'environnement
if [[ "${DEBUG:-0}" == "1" ]]; then
    set -x
fi

# ============================================
# Configuration Variables
# ============================================
readonly BASE_DIR="/srv"
readonly SFTPGO_BASE="${BASE_DIR}/sftpgo"
readonly POWERVIEW_BASE="${BASE_DIR}/powerview"

readonly LOG_FILE="${SFTPGO_BASE}/logs/uploads.log"
readonly DATA_DIR="${SFTPGO_BASE}/data"
readonly VENV_PATH="${POWERVIEW_BASE}/envs/powerview/bin/activate"
readonly ENV_FILE="${POWERVIEW_BASE}/.env"
readonly TSV_PARSER="${POWERVIEW_BASE}/tsv_parser.py"
readonly ANSIBLE_PLAYBOOK="${POWERVIEW_BASE}/grafana-automation/playbooks/create_grafana_resources.yml"

# Indices de structure conservés si besoin ailleurs, mais on ne les utilise plus
# directement pour extraire company/campaign/device.
readonly COMPANY_INDEX=5
readonly CAMPAIGN_INDEX=6
readonly DEVICE_INDEX=7

# S'assure que le dossier de logs existe
mkdir -p "$(dirname "${LOG_FILE}")"

# Redirige stdout/stderr vers le fichier de log
exec 3>&1 1>>"${LOG_FILE}" 2>&1

# ============================================
# Helper Functions
# ============================================

# Log messages with timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "${LOG_FILE}"
}

# Log error and exit
log_error() {
    log "ERROR: $*"
    exit 1
}

# Extract path component by index (non utilisé dans la nouvelle logique,
# mais conservé pour compatibilité potentielle)
extract_path_component() {
    local path="$1"
    local index="$2"
    echo "${path}" | cut -d/ -f"${index}"
}

# Extrait company / campaign / device à partir d'un chemin absolu,
# en le rendant relatif à DATA_DIR.
extract_relative_components() {
    local file_path="$1"

    # S'assure que le chemin commence bien par DATA_DIR
    if [[ "${file_path}" != "${DATA_DIR}"* ]]; then
        log_error "Le chemin '${file_path}' ne se trouve pas sous DATA_DIR='${DATA_DIR}'"
    fi

    # Supprime le préfixe DATA_DIR (éventuel slash de plus)
    local relative="${file_path#${DATA_DIR}}"
    relative="${relative#/}"  # enlève un / de tête éventuel

    # relative = company/campaign/device/... ou company/campaign/...
    local company_name
    local campaign_name
    local device_sn

    company_name="$(echo "${relative}" | cut -d/ -f1)"
    campaign_name="$(echo "${relative}" | cut -d/ -f2)"
    device_sn="$(echo "${relative}" | cut -d/ -f3)"

    echo "${company_name}" "${campaign_name}" "${device_sn}"
}

# Activate Python virtual environment and load env variables
setup_environment() {
    if [[ ! -f "${VENV_PATH}" ]]; then
        log_error "Virtual environment not found: ${VENV_PATH}"
    fi

    if [[ ! -f "${ENV_FILE}" ]]; then
        log_error "Environment file not found: ${ENV_FILE}"
    fi

    # shellcheck disable=SC1090
    source "${VENV_PATH}"

    # Exporte les variables du .env
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
}

# ============================================
# Action Handlers
# ============================================

handle_upload() {
    local file_path="${SFTPGO_ACTION_PATH:-}"

    # On loggue quand même pour voir ce que SFTPGo nous passe
    log "Upload complete action triggered. SFTPGO_ACTION_PATH='${file_path:-<unset>}'"

    setup_environment

    if [[ ! -f "${TSV_PARSER}" ]]; then
        log_error "TSV parser not found: ${TSV_PARSER}"
    fi

    # Si SFTPGO_ACTION_PATH est vide (cas actuel), on traite TOUTES les TSV du DATA_DIR,
    # mais en ignorant les fichiers dont le nom commence par 'PARSED_'.
    if [[ -z "${file_path}" ]]; then
        log "No SFTPGO_ACTION_PATH provided, running parser on all TSV files in ${DATA_DIR} (en ignorant les fichiers PARSED_*)"
        if ! python3 "${TSV_PARSER}" \
            --dataFolder "${DATA_DIR}" \
            2>&1 | tee -a "${LOG_FILE}"; then
            log_error "TSV parser failed for dataFolder: ${DATA_DIR}"
        fi
    else
        # Si un chemin est fourni, on ignore explicitement les fichiers déjà marqués PARSED_
        local basename
        basename="$(basename "${file_path}")"
        if [[ "${basename}" == PARSED_* ]]; then
            log "Skipping upload processing for already parsed file: ${file_path}"
        else
            log "Running parser for single file: ${file_path}"
            if ! python3 "${TSV_PARSER}" \
                --dataFolder "${DATA_DIR}" \
                --tsvFile "${file_path}" \
                2>&1 | tee -a "${LOG_FILE}"; then
                log_error "TSV parser failed for file: ${file_path}"
            fi
        fi
    fi

    log "Upload processing completed (dataFolder=${DATA_DIR}, file='${file_path:-ALL}')"
}

handle_mkdir() {
    local file_path="$1"

    # Extrait company / campaign / device à partir du chemin relatif à DATA_DIR
    read -r company_name campaign_name device_sn < <(extract_relative_components "${file_path}")

    # Only run if DEVICE_SN is empty (not a device-level directory)
    if [[ -z "${device_sn}" ]]; then
        log "Mkdir action for: ${file_path} (company='${company_name}', campaign='${campaign_name}')"

        setup_environment

        if [[ ! -f "${ANSIBLE_PLAYBOOK}" ]]; then
            log_error "Ansible playbook not found: ${ANSIBLE_PLAYBOOK}"
        fi

        if ! ansible-playbook "${ANSIBLE_PLAYBOOK}" \
            --extra-vars "company_name=${company_name} campaign_name=${campaign_name}" \
            2>&1 | tee -a "${LOG_FILE}"; then
            log_error "Ansible playbook failed for: ${file_path}"
        fi

        log "Grafana resources created successfully for: ${company_name}/${campaign_name}"
    else
        log "Skipping mkdir action for device-level directory: ${file_path} (device_sn='${device_sn}')"
    fi
}

# ============================================
# Main Logic
# ============================================

main() {
    # Log de debug au tout début pour voir ce que SFTPGo nous passe
    log "=== on-upload.sh invoked ==="
    log "  PID=$$ USER=$(whoami)"
    log "  SFTPGO_ACTION='${SFTPGO_ACTION:-<unset>}'"
    log "  SFTPGO_ACTION_PATH='${SFTPGO_ACTION_PATH:-<unset>}'"

    # Validate required environment variables
    if [[ -z "${SFTPGO_ACTION:-}" ]]; then
        log "Environment dump (partial) because SFTPGO_ACTION is unset:"
        # On loggue quelques variables utiles, pas tout l'env pour éviter le bruit
        log "  PATH='${PATH}'"
        log "  PWD='${PWD}'"
        log_error "SFTPGO_ACTION environment variable not set"
    fi

    case "${SFTPGO_ACTION}" in
        "upload")
            log "Handling action 'upload'"
            handle_upload
            ;;

        "mkdir")
            if [[ -z "${SFTPGO_ACTION_PATH:-}" ]]; then
                log_error "SFTPGO_ACTION_PATH environment variable not set for mkdir"
            fi

            local file_path="${SFTPGO_ACTION_PATH}"
            log "Handling action 'mkdir' for path '${file_path}'"
            handle_mkdir "${file_path}"
            ;;

        *)
            # Action inconnue : on loggue mais on ne casse pas SFTPGo
            log "Unknown action: ${SFTPGO_ACTION}"
            exit 0
            ;;
    esac
}

# Run main function
main

exit 0
