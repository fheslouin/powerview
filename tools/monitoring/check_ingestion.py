#!/usr/bin/env python3
"""
Vérification fonctionnelle du pipeline d'ingestion PowerView.

À lancer par cron (utilisateur sftpgo, qui lit /srv/powerview/.env), toutes
les heures :

    17 * * * * cd /srv/powerview && envs/powerview/bin/python \
        tools/monitoring/check_ingestion.py >> logs/monitoring.log 2>&1

Deux signaux, lus dans le bucket meta (résumés écrits par tsv_parser.py) :

- aucun run ``tsv_parser_run`` depuis plus de MONITORING_MAX_RUN_AGE_HOURS
  (défaut 26 h — le datalogger uploade quotidiennement vers 06:15 UTC) :
  hook SFTPGo cassé, SFTPGo down, .env corrompu... ;
- des fichiers ``failed`` ou ``deferred`` sur la dernière fenêtre d'une heure :
  fichier invalide, ou InfluxDB indisponible pendant un run.

En cas de problème, publie une notification ntfy (config .monitoring.env).
Sans NTFY_TOPIC, loggue seulement. Exit 0 si tout va bien, 1 sinon.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

# Racine du repo (tools/monitoring/ -> tools/ -> racine)
BASE_DIR = Path(__file__).resolve().parents[2]

logger = logging.getLogger("monitoring")

DEFAULT_MAX_RUN_AGE_HOURS = 26.0
FAILURE_WINDOW_HOURS = 1


class MonitoringConfigError(Exception):
    """Configuration incomplète pour la vérification d'ingestion."""


def query_last_run_time(
    client: InfluxDBClient, org: str, meta_bucket: str
) -> Optional[datetime]:
    """
    Retourne le timestamp du dernier run tsv_parser_run, ou None si aucun
    run sur les 30 derniers jours.
    """
    flux = f"""
from(bucket: "{meta_bucket}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "tsv_parser_run")
  |> filter(fn: (r) => r._field == "nb_files_total")
  |> last()
"""
    tables = client.query_api().query(org=org, query=flux)
    for table in tables:
        for record in table.records:
            return record.get_time()
    return None


def query_recent_failures(
    client: InfluxDBClient, org: str, meta_bucket: str, window_hours: int
) -> Tuple[int, int]:
    """
    Retourne (nb_files_failed, nb_files_deferred) cumulés sur la fenêtre.
    """
    flux = f"""
from(bucket: "{meta_bucket}")
  |> range(start: -{window_hours}h)
  |> filter(fn: (r) => r._measurement == "tsv_parser_run")
  |> filter(fn: (r) => r._field == "nb_files_failed" or r._field == "nb_files_deferred")
  |> sum()
"""
    failed = 0
    deferred = 0
    tables = client.query_api().query(org=org, query=flux)
    for table in tables:
        for record in table.records:
            if record.get_field() == "nb_files_failed":
                failed += int(record.get_value())
            elif record.get_field() == "nb_files_deferred":
                deferred += int(record.get_value())
    return failed, deferred


def notify(title: str, message: str, priority: str = "high") -> None:
    """
    Publie une notification ntfy. No-op (warning) si NTFY_TOPIC absent.
    """
    topic = os.getenv("NTFY_TOPIC", "")
    server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    if not topic:
        logger.warning("NTFY_TOPIC non défini, notification non envoyée: %s", title)
        return
    resp = requests.post(
        f"{server}/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": "warning"},
        timeout=10,
    )
    resp.raise_for_status()


def check_ingestion(client: InfluxDBClient, org: str, meta_bucket: str) -> list[str]:
    """
    Exécute les deux vérifications et retourne la liste des problèmes détectés.
    """
    problems: list[str] = []
    max_age_hours = float(
        os.getenv("MONITORING_MAX_RUN_AGE_HOURS", str(DEFAULT_MAX_RUN_AGE_HOURS))
    )

    last_run = query_last_run_time(client, org, meta_bucket)
    if last_run is None:
        problems.append(
            f"Aucun run tsv_parser_run dans '{meta_bucket}' sur les 30 derniers jours"
        )
    else:
        age_hours = (datetime.now(timezone.utc) - last_run).total_seconds() / 3600
        # âge = now - dernier run ; seuil configurable via MONITORING_MAX_RUN_AGE_HOURS
        if age_hours > max_age_hours:
            problems.append(
                f"Dernier run du parser il y a {age_hours:.1f} h "
                f"(seuil {max_age_hours:.0f} h) — hook SFTPGo ou uploads en panne ?"
            )

    failed, deferred = query_recent_failures(
        client, org, meta_bucket, FAILURE_WINDOW_HOURS
    )
    if failed > 0:
        problems.append(
            f"{failed} fichier(s) en échec (error/) sur la dernière heure"
        )
    if deferred > 0:
        problems.append(
            f"{deferred} fichier(s) différé(s) (InfluxDB indisponible) sur la dernière heure"
        )

    return problems


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    load_dotenv(BASE_DIR / ".env")
    load_dotenv(BASE_DIR / ".monitoring.env")

    url = os.getenv("INFLUXDB_HOST") or os.getenv("INFLUXDB_URL")
    token = os.getenv("INFLUXDB_ADMIN_TOKEN")
    org = os.getenv("INFLUXDB_ORG", "")
    meta_bucket = os.getenv("TSV_META_BUCKET", "powerview_meta")

    if not url or not token:
        raise MonitoringConfigError(
            "INFLUXDB_HOST/INFLUXDB_URL et INFLUXDB_ADMIN_TOKEN sont requis (.env)"
        )

    try:
        with InfluxDBClient(url=url, token=token, org=org) as client:
            problems = check_ingestion(client, org, meta_bucket)
    except Exception as e:
        # InfluxDB injoignable : le stack_watchdog et healthchecks couvrent déjà
        # ce cas toutes les 5 min ; on loggue sans doubler la notification.
        logger.error("Vérification impossible (InfluxDB injoignable ?): %s", e)
        return 1

    if not problems:
        logger.info("Ingestion OK")
        return 0

    message = "\n".join(problems)
    logger.error("Problèmes d'ingestion détectés:\n%s", message)
    try:
        notify("PowerView : problème d'ingestion", message)
    except Exception as e:
        logger.error("Échec de la notification ntfy: %s", e)
    return 1


if __name__ == "__main__":
    sys.exit(main())
