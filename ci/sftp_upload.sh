#!/usr/bin/env bash
set -euo pipefail

# -------- USAGE -----------------
# ci/sftp_upload.sh company3 ./data/company1/campaign/02001084/T302_251012_031720.tsv campaign1/02001084
# --------------------------------



HOST="ftp.powerview.adecwatts.fr"
PORT="2022"

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <user> <local_file> <remote_dir> [remote_filename]"
  exit 1
fi

USER="$1"
LOCAL_FILE="$2"
REMOTE_DIR="$3"
REMOTE_FILE_NAME="${4:-$(basename "$LOCAL_FILE")}"

if [[ ! -f "$LOCAL_FILE" ]]; then
  echo "❌ Local file not found: $LOCAL_FILE"
  exit 1
fi

# Normalise: pas de double slash, pas de slash final
REMOTE_DIR="${REMOTE_DIR#/}"
REMOTE_DIR="${REMOTE_DIR%/}"

# Construit une séquence de commandes sftp qui crée chaque segment
SFTP_CMDS="$(mktemp)"
trap 'rm -f "$SFTP_CMDS"' EXIT

echo "➡️  Uploading as user: $USER" | tee /dev/stderr
echo "➡️  Local file: $LOCAL_FILE"  | tee /dev/stderr
echo "➡️  Remote path: $REMOTE_DIR/$REMOTE_FILE_NAME" | tee /dev/stderr

# Crée les dossiers un par un (ignore si existe)
# On part de la racine SFTP (pas forcément /)
current=""
IFS='/' read -r -a parts <<< "$REMOTE_DIR"
for p in "${parts[@]}"; do
  [[ -z "$p" ]] && continue
  current="${current:+$current/}$p"
  echo "mkdir $current" >> "$SFTP_CMDS"
done

# Puis on se place et on upload
{
  echo "cd $REMOTE_DIR"
  echo "put $LOCAL_FILE $REMOTE_FILE_NAME"
} >> "$SFTP_CMDS"

sftp -P "$PORT" "${USER}@${HOST}" < "$SFTP_CMDS"

echo "✅ Upload completed"
