#!/usr/bin/env python3
"""
Backfill one-shot du catalogue des voies (known_channels) dans le config API.

Pour chaque bucket (client_id) donné :
  1. Liste les campagnes distinctes via schema.tagValues (rapide, utilise l'index)
  2. Pour chaque campagne, scanne les field keys + tags channel via schema.tagValues
  3. POST au config API (/clients/{id}/channels) pour peupler la table SQLite

Ce script est nécessaire UNE FOIS pour les campagnes ingérées AVANT l'ajout
du hook publish_channels dans tsv_parser.py. Après, la table est maintenue
automatiquement à chaque ingestion.

Usage :
    source envs/powerview/bin/activate
    export $(grep -v '^#' .env | xargs)
    export CONFIG_API_URL=http://localhost:8000

    # Un seul bucket
    python3 backfill_known_channels.py --bucket big_mama

    # Tous les buckets clients (skip les _1h/_1d/_1w/_meta)
    python3 backfill_known_channels.py --all

    # Dry run (affiche ce qui serait fait, sans POST)
    python3 backfill_known_channels.py --bucket big_mama --dry-run
"""

import argparse
import logging
import os
import sys
from typing import Dict, List, Set

import requests
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

load_dotenv()

logger = logging.getLogger("backfill_known_channels")

DOWNSAMPLE_SUFFIXES = ("_1h", "_1d", "_1w")
SYSTEM_BUCKETS = {"_monitoring", "_tasks", "powerview_meta"}


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )


def _get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Variable d'environnement manquante : {name}")
    return value


def _list_client_buckets(client: InfluxDBClient) -> List[str]:
    """
    Liste les buckets clients (raw), en filtrant les buckets système et les downsamplés.
    """
    api = client.buckets_api()
    all_buckets = api.find_buckets(limit=1000).buckets or []
    names = [b.name for b in all_buckets]

    return sorted(
        n for n in names
        if n not in SYSTEM_BUCKETS
        and not any(n.endswith(suf) for suf in DOWNSAMPLE_SUFFIXES)
    )


def _scan_campaigns(client: InfluxDBClient, org: str, bucket: str) -> List[str]:
    """
    Liste les valeurs distinctes du tag `campaign` pour un bucket (schema.tagValues,
    utilise l'index TSI).
    """
    flux = f'''
import "influxdata/influxdb/schema"
schema.tagValues(
  bucket: "{bucket}",
  tag: "campaign",
  predicate: (r) => r._measurement == "electrical",
  start: 0
)
'''
    tables = client.query_api().query(query=flux, org=org)
    values: Set[str] = set()
    for t in tables:
        for rec in t.records:
            v = rec.get_value()
            if isinstance(v, str) and v:
                values.add(v)
    return sorted(values)


def _parse_field_id(field_id: str) -> Dict[str, str]:
    """
    Field name convention : `<channel_id>_<unit>` (ex. "M02000835_Ch1_W").
    L'unité est le dernier segment après `_`. Le reste est le channel_id.
    """
    if "_" not in field_id:
        return {"channelId": None, "channelUnit": None}
    channel_id, _, unit = field_id.rpartition("_")
    return {"channelId": channel_id or None, "channelUnit": unit or None}


def _scan_fields_for_campaign(
    client: InfluxDBClient, org: str, bucket: str, campaign: str
) -> List[Dict[str, str]]:
    """
    Liste les field keys pour (bucket, campaign) via schema.tagValues sur `_field`
    (utilise l'index TSI d'Influx). Lent sur gros buckets (~1min) mais seule voie
    fiable quand la cardinalité explose côté points.

    Essaie d'abord `<bucket>_1w` (index plus léger), fallback sur raw.
    Les tags channel_id/unit sont inférés depuis le nom du field
    (convention : `<channel_id>_<unit>`). Les métadonnées optionnelles
    (channel_label, device_master_sn) ne sont pas récupérées ici.
    """
    def _run(bucket_name: str) -> List[str]:
        flux = f'''
import "influxdata/influxdb/schema"
schema.tagValues(
  bucket: "{bucket_name}",
  tag: "_field",
  predicate: (r) => r._measurement == "electrical" and r.campaign == "{campaign}",
  start: 0
)
'''
        tables = client.query_api().query(query=flux, org=org)
        values: Set[str] = set()
        for t in tables:
            for rec in t.records:
                v = rec.get_value()
                if isinstance(v, str) and v:
                    values.add(v)
        return sorted(values)

    field_ids: List[str] = []
    for candidate in (f"{bucket}_1w", bucket):
        try:
            field_ids = _run(candidate)
        except Exception as e:
            logger.warning("Scan %s échoué (%s)", candidate, e)
            continue
        if field_ids:
            logger.info("  fields depuis %s : %d", candidate, len(field_ids))
            break

    return [
        {
            "fieldId": fid,
            **_parse_field_id(fid),
            "channelLabel": None,
            "deviceMasterSn": None,
        }
        for fid in field_ids
    ]


def _publish_channels(
    api_url: str, client_id: str, campaign: str, channels: List[Dict[str, str]]
) -> None:
    url = f"{api_url.rstrip('/')}/clients/{client_id}/channels"
    resp = requests.post(
        url,
        json={"campaign": campaign, "channels": channels},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json() if resp.content else {}
    logger.info(
        "  POST %s → inserted=%s updated=%s",
        url,
        body.get("inserted", "?"),
        body.get("updated", "?"),
    )


def backfill_bucket(
    client: InfluxDBClient, org: str, api_url: str, bucket: str, dry_run: bool
) -> None:
    logger.info("=== Bucket %s ===", bucket)
    try:
        campaigns = _scan_campaigns(client, org, bucket)
    except Exception as e:
        logger.error("Échec list campagnes pour %s : %s", bucket, e)
        return

    if not campaigns:
        logger.info("  (aucune campagne détectée)")
        return

    logger.info("  Campagnes détectées : %s", ", ".join(campaigns))
    for camp in campaigns:
        try:
            rows = _scan_fields_for_campaign(client, org, bucket, camp)
        except Exception as e:
            logger.error("  Scan fields pour %s/%s : %s", bucket, camp, e)
            continue

        logger.info("  %s/%s : %d voies", bucket, camp, len(rows))
        if dry_run:
            for r in rows[:5]:
                logger.info("    [dry] %s", r)
            if len(rows) > 5:
                logger.info("    [dry] ... (+%d)", len(rows) - 5)
            continue

        if not rows:
            continue

        try:
            _publish_channels(api_url, bucket, camp, rows)
        except Exception as e:
            logger.error("  Échec publish %s/%s : %s", bucket, camp, e)


def main() -> int:
    _setup_logging()

    parser = argparse.ArgumentParser(description="Backfill du catalogue known_channels côté config API.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bucket", help="Bucket client à traiter (ex. big_mama)")
    group.add_argument("--all", action="store_true", help="Traiter tous les buckets clients")
    parser.add_argument("--dry-run", action="store_true", help="Affiche sans POST")
    args = parser.parse_args()

    host = _get_env("INFLUXDB_HOST")
    org = _get_env("INFLUXDB_ORG")
    token = _get_env("INFLUXDB_ADMIN_TOKEN")
    api_url = os.getenv("CONFIG_API_URL", "http://localhost:8000")

    logger.info("Influx : %s (org=%s)", host, org)
    logger.info("Config API : %s  (dry_run=%s)", api_url, args.dry_run)

    # Timeout généreux : schema.tagValues peut prendre >1min sur gros buckets.
    with InfluxDBClient(url=host, token=token, org=org, timeout=300_000) as client:
        buckets = [args.bucket] if args.bucket else _list_client_buckets(client)
        logger.info("Buckets à traiter : %s", ", ".join(buckets) if buckets else "(aucun)")

        for b in buckets:
            backfill_bucket(client, org, api_url, b, args.dry_run)

    logger.info("Terminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
