#!/usr/bin/env python3
"""
Gestion des tokens InfluxDB par bucket (un token par client), via la CLI `influx`.

Ce script :
- lit la configuration InfluxDB depuis les variables d'environnement :
  INFLUXDB_ORG, INFLUXDB_HOST, INFLUXDB_ADMIN_TOKEN
- suppose que la CLI `influx` est installée.
- prend en argument : --bucket <nom_du_bucket>
- vérifie si un token existe déjà pour ce bucket (via la description)
- sinon crée un token avec droits read/write sur ce bucket
- affiche le token sur stdout (sans autre texte)

Usage (manuel) :
    source envs/powerview/bin/activate
    export $(cat .env | xargs)
    python3 manage_influx_tokens.py --bucket company1

Prérequis côté CLI Influx :
    Soit:
      - un profil CLI déjà configuré avec un token root (via `influx config create --active ...`)
    Soit:
      - les variables d'env INFLUXDB_HOST et INFLUXDB_ADMIN_TOKEN définies,
        le script se charge alors de positionner INFLUX_HOST et INFLUX_TOKEN
        pour la CLI `influx`.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional, Union, List, Dict, Any

from dotenv import load_dotenv

# Charge les variables d'environnement depuis .env (si présent)
load_dotenv()

logger = logging.getLogger(__name__)

# Niveaux de downsampling créés pour chaque bucket client.
# Ordre croissant d'agrégation : 1h → 1d → 1w.
DOWNSAMPLE_LEVELS = [
    {"suffix": "1h", "every": "1h", "offset": "5m"},
    {"suffix": "1d", "every": "1d", "offset": "1h"},
    {"suffix": "1w", "every": "1w", "offset": "2h"},
]


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


def _prepare_influx_env() -> None:
    """
    Prépare l'environnement pour la CLI `influx` en positionnant
    explicitement INFLUX_HOST et INFLUX_TOKEN si possible.

    - INFLUX_HOST est dérivé de INFLUXDB_HOST (si présent)
    - INFLUX_TOKEN est dérivé de INFLUXDB_ADMIN_TOKEN (si présent)

    Cela permet à la CLI de fonctionner même si aucun profil actif
    n'est configuré, ou si le profil actif n'est pas celui attendu
    dans le contexte Ansible.
    """
    influxdb_host = os.getenv("INFLUXDB_HOST")
    influxdb_admin_token = os.getenv("INFLUXDB_ADMIN_TOKEN")

    # On ne force que si les variables sont présentes, pour ne pas casser
    # un éventuel profil CLI déjà correctement configuré.
    if influxdb_host and not os.getenv("INFLUX_HOST"):
        os.environ["INFLUX_HOST"] = influxdb_host

    if influxdb_admin_token and not os.getenv("INFLUX_TOKEN"):
        os.environ["INFLUX_TOKEN"] = influxdb_admin_token


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
    _prepare_influx_env()

    cmd = ["influx"] + args + ["--json"]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        # On remonte l'erreur brute pour que l'appelant puisse décider quoi faire
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

def create_bucket_cli(bucket_name: str, org: str) -> str:
    """
    Crée un bucket via la CLI `influx bucket create` et retourne son ID.

    Gère les deux formats possibles :
      - {"id": "...", "name": "...", ...}
      - [{"id": "...", "name": "...", ...}]
    """
    data = _run_influx_cmd([
        "bucket", "create",
        "--name", bucket_name,
        "--org", org,
    ])

    # Certaines versions renvoient un objet, d'autres une liste avec un seul élément
    if isinstance(data, list):
        if not data:
            raise RuntimeError(
                f"Réponse vide de la CLI influx pour la création du bucket {bucket_name}"
            )
        data = data[0]

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Format de réponse inattendu pour 'influx bucket create': {type(data)}"
        )

    bid = data.get("id")
    if not bid:
        raise RuntimeError(
            f"ID de bucket non retourné par la CLI influx pour le bucket {bucket_name} "
            f"(réponse: {data})"
        )
    return bid


def find_bucket_id_cli(bucket_name: str, org: str) -> str:
    """
    Retourne l'ID du bucket pour un nom donné, via la CLI `influx bucket find`.

    Si le bucket n'existe pas, il est créé automatiquement via `influx bucket create`.

    Gère les deux formats possibles :
      - {"buckets": [ {...}, ... ]}
      - [ {...}, ... ]
    """
    try:
        data = _run_influx_cmd(["bucket", "find", "--name", bucket_name, "--org", org])
    except RuntimeError as e:
        # Si la CLI renvoie explicitement un 404 "bucket not found", on crée le bucket
        msg = str(e)
        if "failed to find bucket by name" in msg or f"bucket \"{bucket_name}\" not found" in msg:
            # Création du bucket puis nouvelle tentative de find
            bucket_id = create_bucket_cli(bucket_name, org)
            return bucket_id
        # Autre erreur -> on remonte
        raise

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

    # Si on arrive ici, le bucket n'a pas été trouvé dans la réponse JSON.
    # On tente de le créer (cas où la CLI n'a pas levé d'erreur mais n'a rien retourné).
    bucket_id = create_bucket_cli(bucket_name, org)
    return bucket_id


def delete_auth_cli(auth_id: str, org: str) -> None:
    """Supprime une authorization InfluxDB par son ID."""
    _run_influx_cmd(["auth", "delete", "--id", auth_id, "--org", org])


def find_existing_token_for_bucket_cli(
    bucket_name: str,
    org: str,
    required_bucket_ids: List[str],
) -> Optional[str]:
    """
    Cherche une authorization existante pour ce bucket, identifiée par sa description.

    Description utilisée :
        powerview_token_for_bucket_<bucket_name>

    Vérifie que le token couvre bien tous les required_bucket_ids en lecture.
    Si le token existe mais est périmé (ne couvre pas tous les buckets), il est supprimé.

    Gère les deux formats possibles :
      - {"authorizations": [ {...}, ... ]}
      - [ {...}, ... ]

    Retourne le token si trouvé et valide, sinon None.
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
        if a.get("description") != description or not a.get("token"):
            continue

        # Vérifie que le token couvre tous les buckets requis en lecture
        permissions = a.get("permissions") or []
        read_ids = {
            p.get("resource", {}).get("id")
            for p in permissions
            if isinstance(p, dict)
            and p.get("action") == "read"
            and p.get("resource", {}).get("type") == "buckets"
        }
        if all(bid in read_ids for bid in required_bucket_ids):
            return a["token"]

        # Token périmé (ne couvre pas les nouveaux buckets) → supprimer
        auth_id = a.get("id", "")
        print(
            f"[manage_influx_tokens] Token périmé pour '{bucket_name}' (manque des buckets DS). Suppression...",
            file=sys.stderr,
        )
        if auth_id:
            try:
                delete_auth_cli(auth_id, org)
            except RuntimeError as e:
                print(f"[manage_influx_tokens] Avertissement: impossible de supprimer le token: {e}", file=sys.stderr)
        return None

    return None


def create_token_for_bucket_cli(
    raw_bucket_id: str,
    bucket_name: str,
    org: str,
    extra_read_bucket_ids: Optional[List[str]] = None,
) -> str:
    """
    Crée un token InfluxDB via `influx auth create` :
      - READ+WRITE sur raw_bucket_id (bucket principal)
      - READ sur chaque ID dans extra_read_bucket_ids (buckets downsamplings)

    Description :
        powerview_token_for_bucket_<bucket_name>

    Retourne la valeur du token.
    """
    description = f"powerview_token_for_bucket_{bucket_name}"
    args = [
        "auth", "create",
        "--org", org,
        "--description", description,
        "--read-bucket", raw_bucket_id,
        "--write-bucket", raw_bucket_id,
    ]
    for bid in (extra_read_bucket_ids or []):
        args += ["--read-bucket", bid]

    data = _run_influx_cmd(args)

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
# Downsampling : buckets + tasks
# ---------------------------------------------------------------------------

def ensure_downsampled_buckets_cli(bucket_name: str, org: str) -> Dict[str, str]:
    """
    Crée les buckets de downsampling s'ils n'existent pas.

    Retourne un dict { suffix → bucket_id } pour les 3 niveaux.
    """
    ids: Dict[str, str] = {}
    for level in DOWNSAMPLE_LEVELS:
        ds_bucket = f"{bucket_name}_{level['suffix']}"
        bucket_id = find_bucket_id_cli(ds_bucket, org)
        ids[level["suffix"]] = bucket_id
        print(
            f"[manage_influx_tokens] Bucket DS '{ds_bucket}' → id={bucket_id}",
            file=sys.stderr,
        )
    return ids


def ensure_downsample_tasks_cli(bucket_name: str, org: str) -> None:
    """
    Crée les InfluxDB Tasks de downsampling si elles n'existent pas encore.
    Utilise le token admin courant (INFLUX_TOKEN) — les tasks sont exécutées
    avec les permissions admin.
    """
    # Récupère la liste des tasks existantes pour filtrer par nom
    try:
        data = _run_influx_cmd(["task", "list", "--org", org])
    except RuntimeError as e:
        print(
            f"[manage_influx_tokens] Impossible de lister les tasks InfluxDB: {e}",
            file=sys.stderr,
        )
        return

    existing_tasks: List[Dict[str, Any]] = data if isinstance(data, list) else (data.get("tasks") or [])
    existing_names = {t.get("name") for t in existing_tasks if isinstance(t, dict)}

    for level in DOWNSAMPLE_LEVELS:
        task_name = f"downsample_{bucket_name}_{level['suffix']}"
        if task_name in existing_names:
            print(
                f"[manage_influx_tokens] Task '{task_name}' existe déjà, skip.",
                file=sys.stderr,
            )
            continue

        ds_bucket = f"{bucket_name}_{level['suffix']}"
        flux = (
            f'option task = {{name: "{task_name}", every: {level["every"]}, offset: {level["offset"]}}}\n\n'
            f'from(bucket: "{bucket_name}")\n'
            f'  |> range(start: -task.every)\n'
            f'  |> filter(fn: (r) => r._measurement == "electrical")\n'
            f'  |> aggregateWindow(every: {level["every"]}, fn: mean, createEmpty: false)\n'
            f'  |> to(bucket: "{ds_bucket}", org: "{org}")\n'
        )

        tmp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".flux", delete=False, prefix=f"pv_task_{task_name}_"
            ) as f:
                f.write(flux)
                tmp_path = f.name

            _run_influx_cmd(["task", "create", "--org", org, "--file", tmp_path])
            print(
                f"[manage_influx_tokens] Task '{task_name}' créée.",
                file=sys.stderr,
            )
        except RuntimeError as e:
            print(
                f"[manage_influx_tokens] Erreur création task '{task_name}': {e}",
                file=sys.stderr,
            )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


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

        # 2) Prépare l'environnement pour la CLI (INFLUX_HOST / INFLUX_TOKEN)
        _prepare_influx_env()

        # 3) Trouver (ou créer) le bucket principal
        raw_bucket_id = find_bucket_id_cli(bucket_name, org)

        # 4) Créer les buckets de downsampling si absents (idempotent)
        ds_ids = ensure_downsampled_buckets_cli(bucket_name, org)
        all_read_ids = [raw_bucket_id] + list(ds_ids.values())

        # 5) Voir si un token valide existe déjà (couvrant les 4 buckets)
        existing = find_existing_token_for_bucket_cli(bucket_name, org, all_read_ids)
        if existing:
            # IMPORTANT : on n'affiche QUE le token, sans texte autour
            print(existing)
            # Tâches de downsampling (idempotent même si le token était déjà OK)
            ensure_downsample_tasks_cli(bucket_name, org)
            return

        # 6) Sinon, créer un nouveau token couvrant raw + downsampling
        extra_read_ids = list(ds_ids.values())
        token = create_token_for_bucket_cli(raw_bucket_id, bucket_name, org, extra_read_ids)

        # 7) Créer les tasks InfluxDB de downsampling (idempotent)
        ensure_downsample_tasks_cli(bucket_name, org)

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
