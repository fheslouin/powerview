#!/usr/bin/env python3
"""
Backfill des buckets de downsampling à partir des données raw existantes.

Pour chaque niveau (1h, 1d, 1w), exécute des requêtes Flux par tranches
(pour éviter les timeouts InfluxDB) et écrit le résultat agrégé dans le
bucket cible via `to()` (opération 100 % server-side).

Usage :
    source envs/powerview/bin/activate
    export $(grep -v '^#' .env | xargs)

    # Backfill complet depuis 2017-01-01
    python3 backfill_downsample.py --bucket company1

    # Plage personnalisée
    python3 backfill_downsample.py --bucket company1 --start 2020-01-01 --end 2023-12-31

    # Voir les requêtes sans exécuter
    python3 backfill_downsample.py --bucket company1 --dry-run
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import List, Tuple

import influxdb_client
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")

# Taille des tranches par niveau.
# Plus la tranche est grande, plus chaque requête est lourde côté serveur.
CHUNK_DAYS = {
    "1h": 30,    # 30j × 6pts/h × N champs : raisonnable
    "1d": 365,   # 1 an × 24pts/j × N champs : très léger
    "1w": 3650,  # pratiquement tout d'un coup
}

AGGREGATION_FN = "mean"  # mean pour puissance/tension/courant


def _setup_client(timeout_ms: int = 300_000) -> Tuple[influxdb_client.InfluxDBClient, str]:
    url = os.getenv("INFLUXDB_HOST") or os.getenv("INFLUXDB_URL")
    token = os.getenv("INFLUXDB_ADMIN_TOKEN")
    org = os.getenv("INFLUXDB_ORG")
    if not url or not token:
        raise ValueError("INFLUXDB_HOST/INFLUXDB_URL et INFLUXDB_ADMIN_TOKEN sont requis.")
    client = influxdb_client.InfluxDBClient(url=url, token=token, org=org, timeout=timeout_ms)
    return client, org


def _iter_chunks(start: datetime, end: datetime, chunk_days: int):
    """Génère des tranches (chunk_start, chunk_end) entre start et end."""
    cursor = start
    delta = timedelta(days=chunk_days)
    while cursor < end:
        chunk_end = min(cursor + delta, end)
        yield cursor, chunk_end
        cursor = chunk_end


def _flux_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def backfill_level(
    client: influxdb_client.InfluxDBClient,
    org: str,
    raw_bucket: str,
    suffix: str,
    every: str,
    start: datetime,
    end: datetime,
    dry_run: bool,
) -> None:
    ds_bucket = f"{raw_bucket}_{suffix}"
    chunk_days = CHUNK_DAYS[suffix]
    chunks = list(_iter_chunks(start, end, chunk_days))

    logger.info(
        "=== Niveau %-3s → bucket '%s'  (%d tranche(s) de %d j) ===",
        suffix, ds_bucket, len(chunks), chunk_days,
    )

    query_api = client.query_api()

    for i, (cs, ce) in enumerate(chunks, 1):
        flux = (
            f'from(bucket: "{raw_bucket}")\n'
            f'  |> range(start: {_flux_ts(cs)}, stop: {_flux_ts(ce)})\n'
            f'  |> filter(fn: (r) => r._measurement == "electrical")\n'
            f'  |> aggregateWindow(every: {every}, fn: {AGGREGATION_FN}, createEmpty: false)\n'
            f'  |> to(bucket: "{ds_bucket}", org: "{org}")\n'
        )

        label = f"[{i}/{len(chunks)}] {_flux_ts(cs)} → {_flux_ts(ce)}"

        if dry_run:
            logger.info("DRY-RUN %s\n%s", label, flux)
            if i == 1:
                # Affiche uniquement la première tranche pour ne pas noyer la sortie
                logger.info("(dry-run : seule la 1ère tranche est affichée par niveau)")
            continue

        t0 = time.monotonic()
        try:
            query_api.query(org=org, query=flux)
            elapsed = time.monotonic() - t0
            logger.info("OK  %s  (%.1f s)", label, elapsed)
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error("ERREUR %s (%.1f s) : %s", label, elapsed, e)
            # On continue sur les autres tranches


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill des buckets de downsampling InfluxDB."
    )
    parser.add_argument(
        "--bucket", required=True,
        help="Nom du bucket raw (ex: company1)",
    )
    parser.add_argument(
        "--start", default="2017-01-01",
        help="Date de début ISO (défaut: 2017-01-01)",
    )
    parser.add_argument(
        "--end", default=None,
        help="Date de fin ISO (défaut: maintenant)",
    )
    parser.add_argument(
        "--levels", nargs="+", choices=["1h", "1d", "1w"], default=["1h", "1d", "1w"],
        help="Niveaux à backfiller (défaut: les 3)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Affiche les requêtes sans les exécuter",
    )
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = (
        datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
        if args.end
        else datetime.now(timezone.utc)
    )

    if start >= end:
        logger.error("--start doit être antérieur à --end")
        sys.exit(1)

    logger.info("Backfill bucket='%s'  %s → %s  dry_run=%s",
                args.bucket, _flux_ts(start), _flux_ts(end), args.dry_run)

    try:
        client, org = _setup_client()
    except ValueError as e:
        logger.error("%s", e)
        sys.exit(1)

    level_params = {
        "1h": "1h",
        "1d": "1d",
        "1w": "1w",
    }

    total_t0 = time.monotonic()
    for suffix in args.levels:
        backfill_level(
            client=client,
            org=org,
            raw_bucket=args.bucket,
            suffix=suffix,
            every=level_params[suffix],
            start=start,
            end=end,
            dry_run=args.dry_run,
        )

    logger.info("Terminé en %.1f s.", time.monotonic() - total_t0)


if __name__ == "__main__":
    main()
