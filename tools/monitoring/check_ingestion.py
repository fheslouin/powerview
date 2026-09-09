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
- des fichiers ``failed`` ou ``deferred`` depuis le dernier passage réussi :
  fichier invalide, ou InfluxDB indisponible pendant un run. L'alerte liste
  chaque fichier (bucket/campagne/device/nom) avec la cause, lue dans les
  points ``tsv_parser_file``.

Livraison « au moins une fois » : la date du dernier passage dont la
notification a abouti (ou qui n'avait rien à signaler) est mémorisée dans
MONITORING_STATE_FILE (défaut logs/check_ingestion.state). La fenêtre de
recherche des échecs part de cette date (bornée entre 1 h et 7 j) : si ntfy
refuse le message, l'échec est re-détecté et renvoyé au passage suivant au
lieu d'être perdu.

ntfy.sh compte le quota anonyme par visiteur, et regroupe les visiteurs IPv6
par préfixe /64, partagé entre de nombreux VPS chez le même hébergeur : en
IPv6 le serveur reçoit « 429 daily message quota reached » alors qu'il
n'envoie qu'un message par jour. On force donc IPv4 (NTFY_FORCE_IPV4=1 par
défaut).

En cas de problème, publie une notification ntfy (config .monitoring.env).
Sans NTFY_TOPIC, loggue seulement. Exit 0 si tout va bien, 1 sinon.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
import urllib3.util.connection as urllib3_connection
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

# Racine du repo (tools/monitoring/ -> tools/ -> racine)
BASE_DIR = Path(__file__).resolve().parents[2]

logger = logging.getLogger("monitoring")

DEFAULT_MAX_RUN_AGE_HOURS = 26.0
DEFAULT_STATE_FILE = BASE_DIR / "logs" / "check_ingestion.state"
# Fenêtre de recherche des échecs : au moins 1 h (un passage cron), au plus
# 7 jours (si le monitoring n'a pas tourné pendant longtemps, on ne remonte
# pas des semaines d'historique).
MIN_WINDOW_MINUTES = 60
MAX_WINDOW_MINUTES = 7 * 24 * 60
# Nombre maximal de fichiers détaillés dans une notification
MAX_FILES_IN_MESSAGE = 10


class MonitoringConfigError(Exception):
    """Configuration incomplète pour la vérification d'ingestion."""


class NtfyError(Exception):
    """Échec de publication ntfy (message sans l'URL du topic, qui est un secret)."""


@dataclass(frozen=True)
class FailedFile:
    """Un fichier en échec (error/) ou différé, tel que lu dans le bucket meta."""

    time: Optional[datetime]
    status: str
    bucket: str
    campaign: str
    device_master_sn: str
    file_name: str
    error: str = ""

    def describe(self) -> str:
        where = "/".join(
            part for part in (self.bucket, self.campaign, self.device_master_sn) if part
        )
        line = f"{where}/{self.file_name}"
        if self.error:
            line += f" : {self.error}"
        return line


# ---------------------------------------------------------------------------
# Requêtes bucket meta
# ---------------------------------------------------------------------------


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
    client: InfluxDBClient, org: str, meta_bucket: str, window_minutes: int
) -> Tuple[int, int]:
    """
    Retourne (nb_files_failed, nb_files_deferred) cumulés sur la fenêtre.
    """
    flux = f"""
from(bucket: "{meta_bucket}")
  |> range(start: -{int(window_minutes)}m)
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


def query_failed_files(
    client: InfluxDBClient, org: str, meta_bucket: str, window_minutes: int
) -> List[FailedFile]:
    """
    Retourne le détail des fichiers en statut error/deferred sur la fenêtre,
    lu dans les points tsv_parser_file (un point par fichier et par run).

    Le champ ``error`` (cause) n'existe que sur les points écrits depuis la
    version 0.6.0 ; on l'agrège avec ``nb_rows`` (toujours présent) pour
    identifier chaque fichier.
    """
    flux = f"""
from(bucket: "{meta_bucket}")
  |> range(start: -{int(window_minutes)}m)
  |> filter(fn: (r) => r._measurement == "tsv_parser_file")
  |> filter(fn: (r) => r.status == "error" or r.status == "deferred")
  |> filter(fn: (r) => r._field == "nb_rows" or r._field == "error")
"""
    by_key: Dict[Tuple, Dict[str, object]] = {}
    tables = client.query_api().query(org=org, query=flux)
    for table in tables:
        for record in table.records:
            values = record.values
            key = (
                record.get_time(),
                str(values.get("status", "")),
                str(values.get("bucket", "")),
                str(values.get("campaign", "")),
                str(values.get("device_master_sn", "")),
                str(values.get("file_name", "")),
            )
            entry = by_key.setdefault(key, {"error": ""})
            if record.get_field() == "error":
                entry["error"] = str(record.get_value() or "")

    files = [
        FailedFile(
            time=key[0],
            status=key[1],
            bucket=key[2],
            campaign=key[3],
            device_master_sn=key[4],
            file_name=key[5],
            error=str(entry["error"]),
        )
        for key, entry in by_key.items()
    ]
    files.sort(key=lambda f: (f.time or datetime.min.replace(tzinfo=timezone.utc)))
    return files


# ---------------------------------------------------------------------------
# Fichier d'état (livraison au moins une fois)
# ---------------------------------------------------------------------------


def state_file_path() -> Path:
    return Path(os.getenv("MONITORING_STATE_FILE", str(DEFAULT_STATE_FILE)))


def read_last_success(path: Path) -> Optional[datetime]:
    """Date ISO du dernier passage réussi, ou None si absente/illisible."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("Fichier d'état illisible (%s), fenêtre par défaut", path)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def write_last_success(path: Path, when: datetime) -> None:
    """Mémorise la date du passage ; un échec d'écriture ne doit pas tuer le check."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(when.isoformat(), encoding="utf-8")
    except OSError as e:
        logger.warning("Impossible d'écrire le fichier d'état %s: %s", path, e)


def compute_window_minutes(
    last_success: Optional[datetime], now: Optional[datetime] = None
) -> int:
    """
    Fenêtre de recherche des échecs, en minutes : depuis le dernier passage
    réussi, bornée à [MIN_WINDOW_MINUTES ; MAX_WINDOW_MINUTES].
    """
    if last_success is None:
        return MIN_WINDOW_MINUTES
    now = now or datetime.now(timezone.utc)
    elapsed = int((now - last_success).total_seconds() // 60)
    return max(MIN_WINDOW_MINUTES, min(MAX_WINDOW_MINUTES, elapsed))


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


def _force_ipv4() -> None:
    """
    Force urllib3 (utilisé par requests) à ne résoudre qu'en IPv4.
    Voir le module docstring : quota ntfy.sh partagé par préfixe IPv6 /64.
    """
    urllib3_connection.allowed_gai_family = lambda: socket.AF_INET


def notify(title: str, message: str, priority: str = "high") -> None:
    """
    Publie une notification ntfy. No-op (warning) si NTFY_TOPIC absent.
    Lève NtfyError en cas de refus HTTP, sans exposer l'URL du topic.
    """
    topic = os.getenv("NTFY_TOPIC", "")
    server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    if not topic:
        logger.warning("NTFY_TOPIC non défini, notification non envoyée: %s", title)
        return
    if os.getenv("NTFY_FORCE_IPV4", "1") not in ("0", "false", "no"):
        _force_ipv4()
    resp = requests.post(
        f"{server}/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": "warning"},
        timeout=10,
    )
    if resp.status_code >= 400:
        body = (getattr(resp, "text", "") or "")[:200].replace(topic, "<topic>")
        raise NtfyError(f"ntfy HTTP {resp.status_code}: {body}")


# ---------------------------------------------------------------------------
# Vérification
# ---------------------------------------------------------------------------


def _format_window(window_minutes: int) -> str:
    if window_minutes % 60 == 0:
        hours = window_minutes // 60
        return "la dernière heure" if hours == 1 else f"les {hours} dernières heures"
    return f"les {window_minutes} dernières minutes"


def _describe_files(files: List[FailedFile]) -> List[str]:
    lines = [f"  - {f.describe()}" for f in files[:MAX_FILES_IN_MESSAGE]]
    if len(files) > MAX_FILES_IN_MESSAGE:
        lines.append(f"  ... et {len(files) - MAX_FILES_IN_MESSAGE} autre(s)")
    return lines


def check_ingestion(
    client: InfluxDBClient,
    org: str,
    meta_bucket: str,
    window_minutes: int = MIN_WINDOW_MINUTES,
) -> list[str]:
    """
    Exécute les vérifications et retourne la liste des problèmes détectés.
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

    failed, deferred = query_recent_failures(client, org, meta_bucket, window_minutes)
    if failed == 0 and deferred == 0:
        return problems

    files = query_failed_files(client, org, meta_bucket, window_minutes)
    window_txt = _format_window(window_minutes)
    if failed > 0:
        lines = [f"{failed} fichier(s) en échec (error/) sur {window_txt}"]
        lines += _describe_files([f for f in files if f.status == "error"])
        problems.append("\n".join(lines))
    if deferred > 0:
        lines = [f"{deferred} fichier(s) différé(s) (InfluxDB indisponible) sur {window_txt}"]
        lines += _describe_files([f for f in files if f.status == "deferred"])
        problems.append("\n".join(lines))

    return problems


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    # Un fichier illisible (permissions) ne doit pas tuer le monitoring :
    # on dégrade en warning, notify() se rabattra sur le log si NTFY_TOPIC manque.
    for env_file in (BASE_DIR / ".env", BASE_DIR / ".monitoring.env"):
        try:
            load_dotenv(env_file)
        except OSError as e:
            logger.warning("Impossible de lire %s: %s", env_file, e)

    url = os.getenv("INFLUXDB_HOST") or os.getenv("INFLUXDB_URL")
    token = os.getenv("INFLUXDB_ADMIN_TOKEN")
    org = os.getenv("INFLUXDB_ORG", "")
    meta_bucket = os.getenv("TSV_META_BUCKET", "powerview_meta")

    if not url or not token:
        raise MonitoringConfigError(
            "INFLUXDB_HOST/INFLUXDB_URL et INFLUXDB_ADMIN_TOKEN sont requis (.env)"
        )

    state_path = state_file_path()
    started_at = datetime.now(timezone.utc)
    window_minutes = compute_window_minutes(read_last_success(state_path), started_at)

    try:
        with InfluxDBClient(url=url, token=token, org=org) as client:
            problems = check_ingestion(client, org, meta_bucket, window_minutes)
    except Exception as e:
        # InfluxDB injoignable : le stack_watchdog et healthchecks couvrent déjà
        # ce cas toutes les 5 min ; on loggue sans doubler la notification.
        logger.error("Vérification impossible (InfluxDB injoignable ?): %s", e)
        return 1

    if not problems:
        logger.info("Ingestion OK (fenêtre %d min)", window_minutes)
        write_last_success(state_path, started_at)
        return 0

    message = "\n".join(problems)
    logger.error("Problèmes d'ingestion détectés:\n%s", message)
    try:
        notify("PowerView : problème d'ingestion", message)
    except Exception as e:
        # Fichier d'état non mis à jour : les échecs seront re-détectés et
        # renvoyés au prochain passage.
        logger.error("Échec de la notification ntfy (sera retentée): %s", e)
        return 1
    write_last_success(state_path, started_at)
    return 1


if __name__ == "__main__":
    sys.exit(main())
