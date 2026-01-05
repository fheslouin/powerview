import os
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import influxdb_client
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from dotenv import load_dotenv

# Charge les variables d'environnement (.env)
load_dotenv()

logger = logging.getLogger("tsv_parser")


def setup_influxdb_client() -> Tuple[InfluxDBClient, str]:
    """
    Initialise le client InfluxDB à partir des variables d'environnement.

    Retourne:
        (client, org)
    """
    # On privilégie INFLUXDB_HOST, avec fallback sur INFLUXDB_URL
    url = os.getenv('INFLUXDB_HOST') or os.getenv('INFLUXDB_URL')
    token = os.getenv('INFLUXDB_ADMIN_TOKEN')
    org = os.getenv('INFLUXDB_ORG')

    if not url or not token:
        raise ValueError(
            "Missing required environment variables: INFLUXDB_HOST/INFLUXDB_URL and INFLUXDB_ADMIN_TOKEN"
        )

    client = influxdb_client.InfluxDBClient(
        url=url,
        token=token,
        org=org,
    )
    return client, org


def create_bucket_if_not_exists(client: InfluxDBClient, bucket_name: str, org: str) -> None:
    """
    Crée le bucket InfluxDB s'il n'existe pas.
    """
    buckets_api = client.buckets_api()
    existing_buckets = buckets_api.find_buckets().buckets

    if not any(bucket.name == bucket_name for bucket in existing_buckets):
        logger.info("Creating bucket: %s", bucket_name)
        buckets_api.create_bucket(bucket_name=bucket_name, org=org)


def write_points(
    client: InfluxDBClient,
    bucket_name: str,
    org: str,
    points: List[Point],
) -> None:
    """
    Écrit une liste de points dans InfluxDB.
    """
    if not points:
        return
    write_api = client.write_api(write_options=SYNCHRONOUS)
    write_api.write(bucket=bucket_name, org=org, record=points)
    # Message conservé dans les logs
    logger.info("  ✓ Successfully written to InfluxDB")
    # Et également sur stdout pour compatibilité avec les tests existants
    print("Successfully written to InfluxDB")


def write_run_summary_to_influx(
    client: InfluxDBClient,
    org: str,
    report: Dict[str, Any],
) -> None:
    """
    Écrit un résumé de l'exécution dans un bucket InfluxDB dédié
    (pour monitorer le parseur dans Grafana).

    Bucket configurable via TSV_META_BUCKET (par défaut: 'powerview_meta').
    Si l'écriture échoue, on loggue juste un warning.
    """
    meta_bucket = os.getenv("TSV_META_BUCKET", "powerview_meta")

    try:
        write_api = client.write_api(write_options=SYNCHRONOUS)

        # Point global pour le run
        p_run = (
            Point("tsv_parser_run")
            .tag("status", report.get("status", "unknown"))
            .field("nb_files_total", report.get("nb_files_total", 0))
            .field("nb_files_success", report.get("nb_files_success", 0))
            .field("nb_files_failed", report.get("nb_files_failed", 0))
            .field("nb_points_total", report.get("nb_points_total", 0))
            .field("duration_s", report.get("duration_s", 0.0))
            .field("base_folder", str(report.get("base_folder", "")))
            .time(datetime.now(timezone.utc), WritePrecision.S)
        )

        # Points par fichier (on reste léger : pas de stats détaillées par channel ici)
        file_points: List[Point] = []
        for f in report.get("files", []):
            p_file = (
                Point("tsv_parser_file")
                .tag("status", f.get("status", "unknown"))
                .tag("bucket", f.get("bucket", ""))
                .tag("campaign", f.get("campaign", ""))
                .tag("device_master_sn", f.get("device_master_sn", ""))
                .tag("file_name", Path(f.get("file_path", "")).name)
                .field("nb_rows", f.get("nb_rows", 0))
                .field("nb_channels", f.get("nb_channels", 0))
                .field("nb_points", f.get("nb_points", 0))
                .field("nb_invalid_timestamps", f.get("nb_invalid_timestamps", 0))
                .field("nb_invalid_values", f.get("nb_invalid_values", 0))
                .time(datetime.now(timezone.utc), WritePrecision.S)
            )
            file_points.append(p_file)

        records: List[Point] = [p_run] + file_points
        write_api.write(bucket=meta_bucket, org=org, record=records)
        logger.info("Résumé d'exécution écrit dans InfluxDB (bucket=%s).", meta_bucket)
    except Exception as e:
        logger.warning("Impossible d'écrire le résumé d'exécution dans InfluxDB: %s", e)


def _parse_iso(ts: str) -> datetime:
    """
    Parse un timestamp ISO 8601 en datetime (timezone-aware si suffixe Z ou offset).
    """
    # datetime.fromisoformat gère les offsets (+00:00, etc.) mais pas le 'Z' nu avant 3.11
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def count_points_for_file(
    client: InfluxDBClient,
    org: str,
    bucket: str,
    campaign: str,
    device_master_sn: str,
    file_name: str,
    start_time: str,
    end_time: str,
) -> int:
    """
    Compte le nombre de points pour une campagne / device_master_sn / file_name
    sur une plage temporelle explicite [start_time, end_time].

    start_time / end_time doivent être des timestamps ISO 8601 (UTC de préférence).
    On suppose que les points ont un tag 'file_name' avec le nom du fichier TSV.

    Schéma unifié : measurement unique 'electrical'.

    Note : dans Flux, 'start' est inclusif et 'stop' est exclusif.
    Pour inclure le dernier timestamp (end_time), on ajoute 1 seconde à end_time.
    """
    query_api = client.query_api()

    try:
        start_dt = _parse_iso(start_time)
        end_dt = _parse_iso(end_time)
        # On ajoute 1 seconde pour que la borne supérieure soit effectivement incluse
        end_dt_inclusive = end_dt + timedelta(seconds=1)
        start_flux = start_dt.isoformat()
        end_flux = end_dt_inclusive.isoformat()
    except Exception as e:
        logger.warning(
            "Impossible de parser start_time/end_time ('%s' / '%s'), utilisation brute dans Flux: %s",
            start_time,
            end_time,
            e,
        )
        start_flux = start_time
        end_flux = end_time

    flux = f"""
from(bucket: "{bucket}")
  |> range(start: {start_flux}, stop: {end_flux})
  |> filter(fn: (r) => r._measurement == "electrical")
  |> filter(fn: (r) => r.campaign == "{campaign}")
  |> filter(fn: (r) => r.device_master_sn == "{device_master_sn}")
  |> filter(fn: (r) => r.file_name == "{file_name}")
  |> count()
"""

    logger.debug("Flux query for count_points_for_file:\n%s", flux)

    tables = query_api.query(org=org, query=flux)
    total = 0
    for table in tables:
        for record in table.records:
            total += int(record.get_value())
    return total
