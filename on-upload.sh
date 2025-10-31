#!/bin/bash

set -euo pipefail  # Exit on error, undefined variables, and pipe failures

set -x            # Enable command tracing for debugging

# ============================================
# Configuration Variables
# ============================================
readonly BASE_DIR="/srv"
readonly SFTPGO_BASE="${BASE_DIR}/sftpgo"
readonly POWERVIEW_BASE="${BASE_DIR}/powerview"

readonly LOG_FILE="${SFTPGO_BASE}/logs/uploads.log"
readonly DATA_DIR="${SFTPGO_BASE}/data/"
readonly VENV_PATH="${POWERVIEW_BASE}/envs/powerview/bin/activate"
readonly ENV_FILE="${POWERVIEW_BASE}/.env"
readonly TSV_PARSER="${POWERVIEW_BASE}/tsv_parser.py"
readonly ANSIBLE_PLAYBOOK="${POWERVIEW_BASE}/grafana-automation/playbooks/create_grafana_resources.yml"

# Path structure indices (adjust if your structure changes)
readonly COMPANY_INDEX=5
readonly CAMPAIGN_INDEX=6
readonly DEVICE_INDEX=7

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

# Extract path component by index
extract_path_component() {
    local path="$1"
    local index="$2"
    echo "${path}" | cut -d/ -f"${index}"
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

    # Safer way to export env variables
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
}

# ============================================
# Action Handlers
# ============================================

handle_upload() {
    local file_path="$1"

    log "Upload complete action for file: ${file_path}"

    setup_environment

    if [[ ! -f "${TSV_PARSER}" ]]; then
        log_error "TSV parser not found: ${TSV_PARSER}"
    fi

#     if ! python3 "${TSV_PARSER}" "${DATA_DIR}" 2>&1 | tee -a "${LOG_FILE}"; then
      if ! python3 "${TSV_PARSER}" --dataFolder "${DATA_DIR}" --tsvFile "${file_path}" 2>&1 | tee -a "${LOG_FILE}"; then
        log_error "TSV parser failed for: ${file_path}"
    fi

    log "Upload processing completed successfully for: ${file_path}"
}

handle_mkdir() {
    local file_path="$1"
    local company_name="$2"
    local campaign_name="$3"
    local device_sn="$4"

    # Only run if DEVICE_SN is empty (not a device-level directory)
    if [[ -z "${device_sn}" ]]; then
        log "Mkdir action for: ${file_path}"

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
        log "Skipping mkdir action for device-level directory: ${file_path}"
    fi
}

# ============================================
# Main Logic
# ============================================

main() {
    # Validate required environment variables
    if [[ -z "${SFTPGO_ACTION:-}" ]]; then
        log_error "SFTPGO_ACTION environment variable not set"
    fi

    if [[ -z "${SFTPGO_ACTION_PATH:-}" ]]; then
        log_error "SFTPGO_ACTION_PATH environment variable not set"
    fi

    local file_path="${SFTPGO_ACTION_PATH}"
    local company_name
    local campaign_name
    local device_sn

    # Extract path components
    company_name="$(extract_path_component "${file_path}" "${COMPANY_INDEX}")"
    campaign_name="$(extract_path_component "${file_path}" "${CAMPAIGN_INDEX}")"
    device_sn="$(extract_path_component "${file_path}" "${DEVICE_INDEX}")"

    # Handle different actions
    case "${SFTPGO_ACTION}" in
        "upload")
            handle_upload "${file_path}"
            ;;

        "mkdir")
            handle_mkdir "${file_path}" "${company_name}" "${campaign_name}" "${device_sn}"
            ;;

        *)
            log "Unknown action: ${SFTPGO_ACTION} for file: ${file_path}"
            exit 0
            ;;
    esac
}

# Run main function
main

exit 0
