#!/usr/bin/env python3
"""
Gestion des tokens InfluxDB par bucket (un token par client).

Ce script :
- lit la configuration InfluxDB depuis les variables d'environnement :
  INFLUXDB_HOST, INFLUXDB_ADMIN_TOKEN, INFLUXDB_ORG
- prend en argument : --bucket <nom_du_bucket>
- vérifie si un token existe déjà pour ce bucket (via la description)
- sinon crée un token avec droits read/write sur ce bucket
- affiche le token sur stdout (sans autre texte)

Usage (manuel) :
    source envs/powerview/bin/activate
    export $(cat .env | xargs)
    python3 manage_influx_tokens.py --bucket company1
"""

import argparse
import os
import sys
from typing import Optional

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient
from influxdb_client.domain.authorization import Authorization
from influxdb_client.domain.permission import Permission
from influxdb_client.rest import ApiException

# Charge les variables d'environnement depuis .env (si présent)
load_dotenv()


def _get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Variable d'environnement manquante : {name}")
    return value


def _print_debug_env(url: str, org: str) -> None:
    """
    Affiche sur stderr les infos de connexion utilisées, pour debug.
    """
    print(
        f"[manage_influx_tokens] INFLUXDB_HOST = {url}",
        file=sys.stderr,
    )
    print(
        f"[manage_influx_tokens] INFLUXDB_ORG  = {org}",
        file=sys.stderr,
    )


def _extract_error_body(e: ApiException) -> str:
    """
    Essaie d'extraire le corps JSON/texte de la réponse InfluxDB
    pour l'afficher dans les messages d'erreur.
    """
    try:
        data = e.body
        if not data:
            return ""
        return str(data).strip()
    except Exception:
        return ""


def get_or_create_token_for_bucket(client, org_id, bucket_id, bucket_name):
    """
    Retourne un token existant pour ce bucket (si trouvé via la description),
    sinon crée un nouveau token avec droits read/write sur ce bucket.
    """
    auth_api = client.authorizations_api()

    description = f"powerview_token_for_bucket_{bucket_name}"

    # Vérifier si un token existe déjà
    try:
        existing = auth_api.find_authorizations()
    except ApiException as e:
        body = _extract_error_body(e)
        if e.status == 403:
            print(
                "Erreur 403 InfluxDB: le token INFLUXDB_ADMIN_TOKEN n'a pas les "
                "permissions suffisantes pour lister les authorizations.\n"
                "Vérifie dans l'UI InfluxDB que ce token est bien un token 'All Access' "
                "ou qu'il a au moins les droits sur 'authorizations/*'.",
                file=sys.stderr,
            )
            if body:
                print(f"Détail de la réponse InfluxDB: {body}", file=sys.stderr)
            sys.exit(1)
        print(f"Erreur InfluxDB (find_authorizations): {e}", file=sys.stderr)
        if body:
            print(f"Détail de la réponse InfluxDB: {body}", file=sys.stderr)
        sys.exit(1)

    for auth in existing or []:
        if getattr(auth, "description", None) == description and getattr(auth, "token", None):
            return auth.token

    # Permissions RW sur ce bucket
    permissions = [
        Permission(
            action="read",
            resource={
                "type": "buckets",
                "id": bucket_id,
                "org_id": org_id,
            },
        ),
        Permission(
            action="write",
            resource={
                "type": "buckets",
                "id": bucket_id,
                "org_id": org_id,
            },
        ),
    ]

    # Authorization DOIT être un objet
    auth_body = Authorization(
        org_id=org_id,
        description=description,
        permissions=permissions,
    )

    # Appel API (pas de dict !)
    try:
        new_auth = auth_api.create_authorization(authorization=auth_body)
    except ApiException as e:
        body = _extract_error_body(e)
        if e.status == 403:
            print(
                "Erreur 403 InfluxDB lors de la création du token dédié au bucket.\n"
                "Le token INFLUXDB_ADMIN_TOKEN n'a pas les permissions suffisantes pour "
                "créer des authorizations.\n\n"
                "Vérifie que :\n"
                "  - le token est bien de type 'All Access',\n"
                "  - il appartient à la même organisation que INFLUXDB_ORG,\n"
                "  - INFLUXDB_HOST pointe bien vers la même instance InfluxDB.\n",
                file=sys.stderr,
            )
            if body:
                print(f"Détail de la réponse InfluxDB: {body}", file=sys.stderr)
            sys.exit(1)
        print("Erreur InfluxDB lors de la création de l'authorization:", file=sys.stderr)
        print(str(e), file=sys.stderr)
        if body:
            print(f"Détail de la réponse InfluxDB: {body}", file=sys.stderr)
        sys.exit(1)

    if not new_auth.token:
        raise RuntimeError("Token non retourné par InfluxDB")

    return new_auth.token


def find_bucket_id(client: InfluxDBClient, bucket_name: str, org: str) -> Optional[str]:
    """
    Retourne l'ID du bucket pour un nom donné, ou None si introuvable.

    Utilise buckets_api.find_buckets(name=...) pour limiter les permissions
    nécessaires (plutôt que de lister tous les buckets).
    """
    buckets_api = client.buckets_api()

    # On essaie d'abord avec le filtre par nom (plus propre côté permissions)
    result = buckets_api.find_buckets(name=bucket_name)
    buckets = getattr(result, "buckets", None) or []

    for b in buckets:
        name = getattr(b, "name", None)
        if name == bucket_name:
            return getattr(b, "id", None)

    # Fallback : on tente un find_buckets() global si rien trouvé
    # (peut échouer si le token n'a pas les droits nécessaires)
    result_all = buckets_api.find_buckets()
    buckets_all = getattr(result_all, "buckets", None) or []
    for b in buckets_all:
        name = getattr(b, "name", None)
        if name == bucket_name:
            return getattr(b, "id", None)

    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Créer ou récupérer un token InfluxDB pour un bucket donné."
    )
    parser.add_argument(
        "--bucket",
        required=True,
        help="Nom du bucket InfluxDB (par ex. company1)",
    )
    args = parser.parse_args()

    try:
        url = _get_env("INFLUXDB_HOST")
        token = _get_env("INFLUXDB_ADMIN_TOKEN")
        org = _get_env("INFLUXDB_ORG")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    # Affiche les infos de connexion utilisées (debug)
    _print_debug_env(url, org)

    client = InfluxDBClient(url=url, token=token, org=org)

    try:
        # Vérification basique : est-ce que le token peut au moins lister les buckets ?
        try:
            _ = client.buckets_api().find_buckets(limit=1)
        except ApiException as e:
            body = _extract_error_body(e)
            if e.status == 403:
                print(
                    "Erreur 403 InfluxDB: le token INFLUXDB_ADMIN_TOKEN n'a pas les "
                    "permissions suffisantes pour lire les buckets.\n"
                    "Vérifie dans l'UI InfluxDB que ce token est bien un token 'All Access' "
                    "ou qu'il a au moins les droits read/write sur les buckets.",
                    file=sys.stderr,
                )
                if body:
                    print(f"Détail de la réponse InfluxDB: {body}", file=sys.stderr)
                sys.exit(1)
            elif e.status == 401:
                print(
                    "Erreur 401 InfluxDB: le token INFLUXDB_ADMIN_TOKEN est invalide "
                    "(mauvais token ou org).",
                    file=sys.stderr,
                )
                if body:
                    print(f"Détail de la réponse InfluxDB: {body}", file=sys.stderr)
                sys.exit(1)
            else:
                print(
                    f"Erreur InfluxDB lors de la vérification des buckets: {e}",
                    file=sys.stderr,
                )
                if body:
                    print(f"Détail de la réponse InfluxDB: {body}", file=sys.stderr)
                sys.exit(1)

        orgs_api = client.organizations_api()
        orgs = orgs_api.find_organizations(org=org)
        if not orgs:
            print(f"Organisation InfluxDB introuvable : {org}", file=sys.stderr)
            sys.exit(1)

        # orgs[0] est un objet Organization
        org_obj = orgs[0]
        org_id = getattr(org_obj, "id", None)
        print(f"[manage_influx_tokens] Organisation trouvée: id={org_id}", file=sys.stderr)
        if not org_id:
            print(
                f"Impossible de récupérer l'ID de l'organisation : {org}",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            bucket_id = find_bucket_id(client, args.bucket, org)
        except ApiException as e:
            body = _extract_error_body(e)
            if e.status == 403:
                print(
                    "Erreur 403 InfluxDB: le token INFLUXDB_ADMIN_TOKEN n'a pas les "
                    f"droits nécessaires pour accéder au bucket '{args.bucket}'.\n"
                    "Vérifie les permissions de ce token dans InfluxDB.",
                    file=sys.stderr,
                )
                if body:
                    print(f"Détail de la réponse InfluxDB: {body}", file=sys.stderr)
                sys.exit(1)
            print(f"Erreur InfluxDB lors de la recherche du bucket: {e}", file=sys.stderr)
            if body:
                print(f"Détail de la réponse InfluxDB: {body}", file=sys.stderr)
            sys.exit(1)

        if bucket_id is None:
            print(
                f"Bucket InfluxDB introuvable ou inaccessible : {args.bucket}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"[manage_influx_tokens] Bucket '{args.bucket}' trouvé: id={bucket_id}", file=sys.stderr)

        # Création / récupération du token dédié au bucket
        bucket_token = get_or_create_token_for_bucket(
            client=client,
            org_id=org_id,
            bucket_id=bucket_id,
            bucket_name=args.bucket,
        )

        # IMPORTANT : on n'affiche QUE le token, sans texte autour
        print(bucket_token)
    finally:
        client.close()


if __name__ == "__main__":
    main()
