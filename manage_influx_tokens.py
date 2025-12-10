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
    export $(cat .env)
    python3 manage_influx_tokens.py --bucket company1
"""

import argparse
import os
import sys
from typing import Optional

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

# Charge les variables d'environnement depuis .env (si présent)
load_dotenv()


def _get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Variable d'environnement manquante : {name}")
    return value


def get_or_create_token_for_bucket(
    client: InfluxDBClient,
    org_id: str,
    bucket_id: str,
    bucket_name: str,
) -> str:
    """
    Retourne un token existant pour ce bucket (si trouvé via la description),
    sinon crée un nouveau token avec droits read/write sur ce bucket.

    Implémentation compatible avec influxdb-client 1.49.0 en utilisant
    un simple dict pour l'authorization (pas les classes Authorization/Permission/Resource).
    """
    auth_api = client.authorizations_api()

    description = f"powerview_token_for_bucket_{bucket_name}"

    # 1. Cherche un token existant avec cette description
    existing = auth_api.find_authorizations()
    for auth in existing or []:
        desc = getattr(auth, "description", None)
        token = getattr(auth, "token", None)
        if desc == description and token:
            return token

    # 2. Crée un nouveau token avec permissions RW sur ce bucket
    body = {
        "orgID": org_id,
        "description": description,
        "permissions": [
            {
                "action": "read",
                "resource": {
                    "type": "buckets",
                    "id": bucket_id,
                    "orgID": org_id,
                },
            },
            {
                "action": "write",
                "resource": {
                    "type": "buckets",
                    "id": bucket_id,
                    "orgID": org_id,
                },
            },
        ],
    }

    new_auth = auth_api.create_authorization(authorization=body)

    token = getattr(new_auth, "token", None)
    if not token:
        raise RuntimeError("Impossible de récupérer le token créé pour le bucket")

    return token


def find_bucket_id(client: InfluxDBClient, bucket_name: str, org: str) -> Optional[str]:
    """
    Retourne l'ID du bucket pour un nom donné, ou None si introuvable.
    Compatible avec influxdb-client 1.49.0 où find_buckets().buckets
    renvoie des objets Bucket.
    """
    buckets_api = client.buckets_api()
    result = buckets_api.find_buckets()
    buckets = getattr(result, "buckets", None) or []

    for b in buckets:
        # b est un objet Bucket
        name = getattr(b, "name", None)
        org_name = getattr(b, "org", None)
        org_id = getattr(b, "org_id", None)

        if name == bucket_name and (org_name == org or org_id):
            return getattr(b, "id", None)

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Créer ou récupérer un token InfluxDB pour un bucket donné.")
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

    client = InfluxDBClient(url=url, token=token, org=org)

    try:
        orgs_api = client.organizations_api()
        orgs = orgs_api.find_organizations(org=org)
        if not orgs:
            print(f"Organisation InfluxDB introuvable : {org}", file=sys.stderr)
            sys.exit(1)

        # orgs[0] est un objet Organization
        org_obj = orgs[0]
        org_id = getattr(org_obj, "id", None)
        if not org_id:
            print(f"Impossible de récupérer l'ID de l'organisation : {org}", file=sys.stderr)
            sys.exit(1)

        bucket_id = find_bucket_id(client, args.bucket, org)
        if bucket_id is None:
            print(f"Bucket InfluxDB introuvable : {args.bucket}", file=sys.stderr)
            sys.exit(1)

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
