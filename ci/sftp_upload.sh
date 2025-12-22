#!/usr/bin/env bash
set -euo pipefail

# -------- USAGE -----------------
# SFTP :
#   ci/sftp_upload.sh company3 ./data/company1/campaign/02001084/T302_251012_031720.tsv campaign1/02001084
#
# FTP (via curl, nécessite FTP activé sur SFTPGo) :
#   FTP_PASSWORD='motdepasse' \
#     ci/sftp_upload.sh company3 ./data/company1/campaign/02001084/T302_251012_031720.tsv campaign1/02001084 "" ftp
# --------------------------------

HOST="ftp.powerview.adecwatts.fr"
SFTP_PORT="2022"
FTP_PORT="21"   # adapter si besoin à ta conf SFTPGo

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <user> <local_file> <remote_dir> [remote_filename] [protocol:sftp|ftp]" >&2
  exit 1
fi

USER="$1"
LOCAL_FILE="$2"
REMOTE_DIR="$3"
REMOTE_FILE_NAME="${4:-$(basename "$LOCAL_FILE")}"
PROTOCOL="${5:-sftp}"   # sftp par défaut

if [[ ! -f "$LOCAL_FILE" ]]; then
  echo "Fichier local introuvable: $LOCAL_FILE" >&2
  exit 1
fi

# Normalise: pas de double slash, pas de slash final
REMOTE_DIR="${REMOTE_DIR#/}"
REMOTE_DIR="${REMOTE_DIR%/}"

echo "Protocole : $PROTOCOL" >&2
echo "Upload en tant que : $USER" >&2
echo "Fichier local : $LOCAL_FILE" >&2
echo "Chemin distant : $REMOTE_DIR/$REMOTE_FILE_NAME" >&2

case "$PROTOCOL" in
  sftp)
    # Construit une séquence de commandes pour créer les dossiers et uploader
    CMDS_FILE="$(mktemp)"
    trap 'rm -f "$CMDS_FILE"' EXIT

    # Crée les dossiers un par un (ignore si existe)
    current=""
    IFS='/' read -r -a parts <<< "$REMOTE_DIR"
    for p in "${parts[@]}"; do
      [[ -z "$p" ]] && continue
      current="${current:+$current/}$p"
      echo "mkdir $current" >> "$CMDS_FILE"
    done

    {
      echo "cd $REMOTE_DIR"
      echo "put $LOCAL_FILE $REMOTE_FILE_NAME"
    } >> "$CMDS_FILE"

    sftp -P "$SFTP_PORT" "${USER}@${HOST}" < "$CMDS_FILE"
    ;;

  ftp)
    # Vérifie que curl est disponible
    if ! command -v curl >/dev/null 2>&1; then
      echo "Erreur : curl n'est pas installé, requis pour le mode ftp." >&2
      exit 1
    fi

    # Mot de passe via variable d'environnement pour éviter de le taper en clair dans la ligne de commande
    FTP_PASSWORD="${FTP_PASSWORD:-}"
    if [[ -z "$FTP_PASSWORD" ]]; then
      echo "Erreur : FTP_PASSWORD n'est pas défini dans l'environnement pour le mode ftp." >&2
      echo "Exemple :" >&2
      echo "  FTP_PASSWORD='motdepasse' $0 $USER $LOCAL_FILE $REMOTE_DIR \"$REMOTE_FILE_NAME\" ftp" >&2
      exit 1
    fi

    # Upload du fichier via curl en FTP
    # --ftp-create-dirs : crée les dossiers distants si besoin
    # --verbose : affiche le détail de la session FTP (utile pour debug)
    echo "Commande curl :" >&2
    echo "  curl --ftp-create-dirs --verbose --user '${USER}:***' --upload-file '${LOCAL_FILE}' 'ftp://${HOST}:${FTP_PORT}/${REMOTE_DIR}/${REMOTE_FILE_NAME}'" >&2

    curl --ftp-create-dirs --verbose \
      --user "${USER}:${FTP_PASSWORD}" \
      --upload-file "${LOCAL_FILE}" \
      "ftp://${HOST}:${FTP_PORT}/${REMOTE_DIR}/${REMOTE_FILE_NAME}"

    ;;

  *)
    echo "Protocole inconnu: $PROTOCOL (attendu: sftp ou ftp)" >&2
    exit 1
    ;;
esac

echo "Upload terminé via $PROTOCOL" >&2
