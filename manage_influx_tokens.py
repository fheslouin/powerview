#!/usr/bin/env python3
"""
Gestion des tokens InfluxDB par bucket (un token par client), via la CLI `influx`.

Ce script :
- lit la configuration InfluxDB depuis les variables d'environnement :
  INFLUXDB_ORG
- suppose que la CLI `influx` est installée et déjà configurée avec un token root
  (via `influx config create --active ...`).
- prend en argument : --bucket <nom_du_bucket>
- vérifie si un token existe déjà pour ce bucket (via la description)
- sinon crée un token avec droits read/write sur ce bucket
- affiche le token sur stdout (sans autre texte)

Usage (manuel) :
    source envs/powerview/bin/activate
    export $(cat .env | xargs)
    python3 manage_influx_tokens.py --bucket company1

Prérequis côté CLI Influx :
    influx config create \
      --config-name powerview-root \
      --host http://localhost:8086 \
      --org powerview \
      --token 'TON_TOKEN_ROOT' \
      --active
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Optional, Union, List, Dict, Any

from dotenv import load_dotenv

# Charge les variables d'environnement depuis .env (si présent)
load_dotenv()


# ---------------------------------------------------------------------------
# Helpers génériques
# ---------------------------------------------------------------------------

def _get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Variable d'environnement manquante : {name}")
    return value


def _ensure_influx_cli_available() -> None:
    """
    Vérifie que la CLI `influx` est disponible dans le PATH.
    """
    if shutil.which("influx") is None:
        raise RuntimeError(
            "La CLI 'influx' n'est pas disponible dans le PATH.\n"
            "Installe-la et/ou configure ton PATH, puis vérifie avec 'which influx'."
        )


def _run_influx_cmd(args: List[str]) -> Union[Dict[str, Any], List[Any]]:
    """
    Exécute `influx ... --json` et retourne le JSON parsé.

    Selon la version de la CLI Influx, la sortie JSON peut être :
      - un objet dict, ex: {"buckets": [...]}
      - une liste, ex: [{"id": "...", "name": "..."}]

    On retourne donc soit un dict, soit une list, et les fonctions appelantes
    doivent gérer les deux cas.
    """
    _ensure_influx_cli_available()

    cmd = ["influx"] + args + ["--json"]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Commande influx échouée: {' '.join(cmd)}\n"
            f"stdout: {e.stdout}\n"
            f"stderr: {e.stderr}"
        ) from e

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Réponse non JSON de la CLI influx pour la commande: {' '.join(cmd)}\n"
            f"stdout: {result.stdout}"
        ) from e

    return data


# ---------------------------------------------------------------------------
# Logique métier via CLI
# ---------------------------------------------------------------------------

def find_bucket_id_cli(bucket_name: str, org: str) -> str:
    """
    Retourne l'ID du bucket pour un nom donné, via la CLI `influx bucket find`.

    Gère les deux formats possibles :
      - {"buckets": [ {...}, ... ]}
      - [ {...}, ... ]
    """
    data = _run_influx_cmd(["bucket", "find", "--name", bucket_name, "--org", org])

    buckets: List[Dict[str, Any]] = []

    if isinstance(data, dict):
        buckets = data.get("buckets") or []
    elif isinstance(data, list):
        buckets = data
    else:
        raise RuntimeError(
            f"Format de réponse inattendu pour 'influx bucket find': {type(data)}"
        )

    for b in buckets:
        if not isinstance(b, dict):
            continue
        if b.get("name") == bucket_name:
            bid = b.get("id")
            if bid:
                return bid

    raise RuntimeError(f"Bucket InfluxDB introuvable (via CLI) : {bucket_name}")


def find_existing_token_for_bucket_cli(bucket_name: str, org: str) -> Optional[str]:
    """
    Cherche une authorization existante pour ce bucket, identifiée par sa description.

    Description utilisée :
        powerview_token_for_bucket_<bucket_name>

    Gère les deux formats possibles :
      - {"authorizations": [ {...}, ... ]}
      - [ {...}, ... ]

    Retourne le token si trouvé, sinon None.
    """
    description = f"powerview_token_for_bucket_{bucket_name}"
    data = _run_influx_cmd(["auth", "list", "--org", org])

    auths: List[Dict[str, Any]] = []

    if isinstance(data, dict):
        auths = data.get("authorizations") or []
    elif isinstance(data, list):
        auths = data
    else:
        raise RuntimeError(
            f"Format de réponse inattendu pour 'influx auth list': {type(data)}"
        )

    for a in auths:
        if not isinstance(a, dict):
            continue
        if a.get("description") == description and a.get("token"):
            return a["token"]
    return None


def create_token_for_bucket_cli(bucket_id: str, bucket_name: str, org: str) -> str:
    """
    Crée un token avec read/write sur le bucket donné, via la CLI `influx auth create`.

    Description :
        powerview_token_for_bucket_<bucket_name>

    Retourne la valeur du token.
    """
    description = f"powerview_token_for_bucket_{bucket_name}"
    data = _run_influx_cmd([
        "auth", "create",
        "--org", org,
        "--description", description,
        "--read-bucket", bucket_id,
        "--write-bucket", bucket_id,
    ])

    # Selon la version, la sortie peut être un dict ou une liste avec un seul élément
    if isinstance(data, list):
        if not data:
            raise RuntimeError(
                f"Réponse vide de la CLI influx pour la création du token du bucket {bucket_name}"
            )
        data = data[0]

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Format de réponse inattendu pour 'influx auth create': {type(data)}"
        )

    token = data.get("token")
    if not token:
        raise RuntimeError(
            f"Token non retourné par la CLI influx pour le bucket {bucket_name} "
            f"(réponse: {data})"
        )
    return token


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Créer ou récupérer un token InfluxDB pour un bucket donné (via CLI influx)."
    )
    parser.add_argument(
        "--bucket",
        required=True,
        help="Nom du bucket InfluxDB (par ex. company1)",
    )
    args = parser.parse_args()

    try:
        org = _get_env("INFLUXDB_ORG")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    bucket_name = args.bucket

    try:
        # 1) Vérifie que la CLI influx est dispo
        _ensure_influx_cli_available()

        # 2) Trouver l'ID du bucket via la CLI
        bucket_id = find_bucket_id_cli(bucket_name, org)

        # 3) Voir si un token existe déjà pour ce bucket
        existing = find_existing_token_for_bucket_cli(bucket_name, org)
        if existing:
            # IMPORTANT : on n'affiche QUE le token, sans texte autour
            print(existing)
            return

        # 4) Sinon, créer un nouveau token
        token = create_token_for_bucket_cli(bucket_id, bucket_name, org)
        # IMPORTANT : on n'affiche QUE le token, sans texte autour
        print(token)

    except Exception as e:
        print(
            f"Erreur lors de la gestion du token pour le bucket '{bucket_name}': {e}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
