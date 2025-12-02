import os
import logging
from datetime import datetime
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
    url = os.getenv('INFLUXDB_HOST')
    token = os.getenv('INFLUXDB_ADMIN_TOKEN')
    org = os.getenv('INFLUXDB_ORG')

    if not url or not token:
        raise ValueError(
            "Missing required environment variables: INFLUXDB_HOST and INFLUXDB_ADMIN_TOKEN"
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
            .time(datetime.utcnow(), WritePrecision.S)
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
                .time(datetime.utcnow(), WritePrecision.S)
            )
            file_points.append(p_file)

        records: List[Point] = [p_run] + file_points
        write_api.write(bucket=meta_bucket, org=org, record=records)
        logger.info("Résumé d'exécution écrit dans InfluxDB (bucket=%s).", meta_bucket)
    except Exception as e:
        logger.warning("Impossible d'écrire le résumé d'exécution dans InfluxDB: %s", e)
