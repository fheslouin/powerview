#!/usr/bin/env python3
"""
TSV to InfluxDB 3 Core Parser
Recursively parses TSV files and loads data into InfluxDB buckets.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Tuple, Any

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

from core import TSVParserFactory, parse_tsv_header, parse_timestamp
from fs_utils import (
    extract_path_components as _extract_path_components,
    find_tsv_files,
    move_parsed_file,
    move_error_file,
)
from influx_utils import (
    setup_influxdb_client,
    create_bucket_if_not_exists,
    write_points,
    write_run_summary_to_influx,
    count_points_for_file,
)

# Load environment variables from .env file
load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("tsv_parser")


def setup_logging() -> None:
    """
    Configure le logging de base.

    - Niveau par défaut : INFO (surchageable via TSV_LOG_LEVEL)
    - Format simple avec timestamp / niveau / message

    On évite d'empiler plusieurs handlers si le logging est déjà configuré
    (ce qui provoquerait des logs en double).
    """
    root_logger = logging.getLogger()
    level_name = os.getenv("TSV_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    if root_logger.handlers:
        # Logging déjà configuré ailleurs : on ajuste juste le niveau
        root_logger.setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )


# ---------------------------------------------------------------------------
# Traitement d'un fichier
# ---------------------------------------------------------------------------

def _compute_time_range_from_tsv(tsv_file: str) -> Tuple[str, str]:
    """
    Relit le fichier TSV (colonne 0) pour calculer la plage temporelle
    min / max des timestamps valides, et retourne deux timestamps ISO 8601 (UTC).

    On réutilise la même logique de parsing de dates que dans core.py.
    """
    times: List[datetime] = []

    with open(tsv_file, "r", encoding="utf-8") as f:
        # sauter les 2 lignes de header
        next(f, None)
        next(f, None)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if not parts:
                continue
            timestamp_str = str(parts[0])

            ts = parse_timestamp(timestamp_str)
            if ts is None:
                continue

            # on considère que c'est du temps local, on le convertit en UTC naïf
            times.append(ts)

    if not times:
        raise ValueError("Aucun timestamp valide trouvé dans le fichier pour calculer la plage temporelle")

    start_dt = min(times)
    end_dt = max(times)

    # On les rend explicites en UTC
    start_iso = start_dt.replace(tzinfo=timezone.utc).isoformat()
    end_iso = end_dt.replace(tzinfo=timezone.utc).isoformat()
    return start_iso, end_iso


def process_tsv_file(
    tsv_file: str,
    base_folder: str,
    client: InfluxDBClient,
    org: str,
) -> Tuple[bool, Dict[str, Any]]:
    """
    Traite un fichier TSV et écrit les points dans InfluxDB.

    Retourne:
        (success: bool, file_report: dict)
    """
    file_report: Dict[str, Any] = {
        "file_path": tsv_file,
        "bucket": None,
        "campaign": None,
        "device_master_sn": None,
        "status": "error",
        "error": None,
        "nb_rows": 0,
        "nb_channels": 0,
        "nb_points": 0,
        "nb_invalid_timestamps": 0,
        "nb_invalid_values": 0,
        "channels": {},
        "nb_points_expected": None,
        "nb_points_in_influx": None,
        "time_start": None,
        "time_end": None,
    }

    try:
        logger.info("Processing: %s", tsv_file)

        # Extraction des composants de chemin
        bucket_name, campaign_name, device_master_sn = _extract_path_components(
            tsv_file, base_folder
        )

        file_report["bucket"] = bucket_name
        file_report["campaign"] = campaign_name
        file_report["device_master_sn"] = device_master_sn

        # S'assure que le bucket existe
        create_bucket_if_not_exists(client, bucket_name, org)

        # Lecture rapide du header pour récupérer le format
        with open(tsv_file, "r", encoding="utf-8") as f:
            _line1 = f.readline().strip().split("\t")
            line2 = f.readline().strip().split("\t")
        file_format = line2[0]

        logger.info("  Bucket: %s", bucket_name)
        logger.info("  Campaign: %s", campaign_name)
        logger.info("  Master device: %s", device_master_sn)
        logger.info("  File format: %s", file_format)

        # Parser adapté au format
        parser = TSVParserFactory.get_parser(file_format)

        # Parse complet (header + data) avec les bons tags
        # Schéma unifié : measurement = "electrical"
        points, stats = parser.parse(
            tsv_file,
            campaign=campaign_name,
            bucket_name=bucket_name,
            table_name="electrical",
        )

        logger.info("  Points created: %d", len(points))

        file_report["nb_rows"] = stats.get("nb_rows", 0)
        file_report["nb_channels"] = stats.get("nb_channels", 0)
        file_report["nb_points"] = stats.get("nb_points", 0)
        file_report["nb_invalid_timestamps"] = stats.get("nb_invalid_timestamps", 0)
        file_report["nb_invalid_values"] = stats.get("nb_invalid_values", 0)
        file_report["channels"] = stats.get("channels", {})

        # Écriture Influx
        write_points(client, bucket_name, org, points)

        # Vérification optionnelle : compter les points dans Influx pour ce fichier
        try:
            expected = len(points)

            # Calcule la plage temporelle à partir du TSV (colonne 0)
            start_time_iso, end_time_iso = _compute_time_range_from_tsv(tsv_file)

            actual = count_points_for_file(
                client=client,
                org=org,
                bucket=bucket_name,
                campaign=campaign_name,
                device_master_sn=device_master_sn,
                start_time=start_time_iso,
                end_time=end_time_iso,
            )

            file_report["nb_points_expected"] = expected
            file_report["nb_points_in_influx"] = actual
            file_report["time_start"] = start_time_iso
            file_report["time_end"] = end_time_iso

            if actual >= expected:
                logger.info(
                    "  ✓ Vérification Influx OK: %d points attendus, %d trouvés (>=) "
                    "pour le fichier %s sur [%s ; %s]",
                    expected,
                    actual,
                    Path(tsv_file).name,
                    start_time_iso,
                    end_time_iso,
                )
            else:
                logger.warning(
                    "  ⚠ Vérification Influx INCOMPLETE: %d points attendus, %d trouvés "
                    "pour le fichier %s sur [%s ; %s]",
                    expected,
                    actual,
                    Path(tsv_file).name,
                    start_time_iso,
                    end_time_iso,
                )

        except Exception as e:
            logger.warning("Impossible de vérifier les points dans InfluxDB pour %s: %s", tsv_file, e)

        file_report["status"] = "success"
        return True, file_report

    except Exception as e:
        msg = str(e)
        logger.error("  ✗ Error processing %s: %s", tsv_file, msg)
        file_report["status"] = "error"
        file_report["error"] = msg
        return False, file_report


def write_run_report_to_file(report: Dict[str, Any], base_folder: str) -> None:
    """
    Écrit un rapport JSON d'exécution sur disque.

    Le chemin de base peut être configuré via la variable d'env TSV_REPORT_DIR,
    sinon on utilise <base_folder>/../logs/reports.
    """
    report_dir_env = os.getenv("TSV_REPORT_DIR")
    if report_dir_env:
        reports_dir = Path(report_dir_env)
    else:
        base = Path(base_folder).resolve()
        reports_dir = base.parent / "logs" / "reports"

    reports_dir.mkdir(parents=True, exist_ok=True)

    run_id = report.get("run_id", datetime.now(timezone.utc).isoformat())
    safe_run_id = run_id.replace(":", "-")
    filename = f"tsv_parser_{safe_run_id}.json"
    path = reports_dir / filename

    with path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info("Rapport d'exécution écrit dans: %s", path)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    """
    Main function to process TSV files recursively.
    """
    setup_logging()

    logger.info("=" * 70)
    logger.info("TSV to InfluxDB2 Parser")
    logger.info("=" * 70)

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--dataFolder", help="Path to the data folder (ex: /srv/powerview/data)")
    parser.add_argument("-t", "--tsvFile", help="Path to the TSV file(s)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Ne pas écrire dans InfluxDB, ne pas renommer les fichiers, "
            "ne pas sauvegarder le rapport, mais afficher le rapport JSON sur stdout."
        ),
    )
    args = parser.parse_args()

    # Validation : --tsvFile nécessite obligatoirement --dataFolder
    if args.tsvFile and not args.dataFolder:
        parser.error("--dataFolder est obligatoire quand --tsvFile est utilisé")

    tsv_files: List[str] = []
    base_folder: str = ""

    if args.tsvFile and args.dataFolder:
        base_folder = args.dataFolder
        if not os.path.exists(base_folder):
            logger.error("Error: Folder '%s' does not exist.", base_folder)
            sys.exit(1)
        logger.info("Using data folder: %s", base_folder)

        tsv_files = [args.tsvFile]
        logger.info("Using specified TSV files: %s", tsv_files)

    elif args.dataFolder and not args.tsvFile:
        base_folder = args.dataFolder
        if not os.path.exists(base_folder):
            logger.error("Error: Folder '%s' does not exist.", base_folder)
            sys.exit(1)
        logger.info("Using all TSV files in folder: %s", base_folder)
        tsv_files = find_tsv_files(base_folder)

    if not tsv_files:
        logger.info("No TSV files found to process.")
        return

    logger.info("Found %d TSV file(s) to process.", len(tsv_files))

    client: Any = None
    org: str = ""

    if not args.dry_run:
        try:
            client, org = setup_influxdb_client()
            logger.info("Connected to InfluxDB at %s", os.getenv("INFLUXDB_HOST"))
        except Exception as e:
            logger.error("Error connecting to InfluxDB: %s", e)
            sys.exit(1)
    else:
        logger.info("Mode DRY-RUN : aucune connexion à InfluxDB ne sera effectuée.")

    run_id = datetime.now(timezone.utc).isoformat()
    start_time = time.time()

    run_report: Dict[str, Any] = {
        "run_id": run_id,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "end_time": None,
        "duration_s": None,
        "base_folder": base_folder,
        "nb_files_total": len(tsv_files),
        "nb_files_success": 0,
        "nb_files_failed": 0,
        "nb_points_total": 0,
        "status": "success",
        "files": [],
        "dry_run": args.dry_run,
    }

    successful = 0
    failed = 0

    for tsv_file in tsv_files:
        if args.dry_run:
            logger.info("Processing (dry-run): %s", tsv_file)
            file_report: Dict[str, Any] = {
                "file_path": tsv_file,
                "bucket": None,
                "campaign": None,
                "device_master_sn": None,
                "status": "success",
                "error": None,
                "nb_rows": 0,
                "nb_channels": 0,
                "nb_points": 0,
                "nb_invalid_timestamps": 0,
                "nb_invalid_values": 0,
                "channels": {},
                "nb_points_expected": None,
                "nb_points_in_influx": None,
                "time_start": None,
                "time_end": None,
            }

            try:
                bucket_name, campaign_name, device_master_sn = _extract_path_components(
                    tsv_file, base_folder
                )
                file_report["bucket"] = bucket_name
                file_report["campaign"] = campaign_name
                file_report["device_master_sn"] = device_master_sn

                # Lecture du header pour récupérer format + mappings
                channel_mappings, file_format = parse_tsv_header(tsv_file)
                logger.info("  Bucket: %s", bucket_name)
                logger.info("  Campaign: %s", campaign_name)
                logger.info("  Master device: %s", device_master_sn)
                logger.info("  Channels: %d", len(channel_mappings))
                logger.info("  File format: %s", file_format)

                parser_impl = TSVParserFactory.get_parser(file_format)

                _, stats = parser_impl.parse(
                    tsv_file,
                    campaign=campaign_name,
                    bucket_name=bucket_name,
                    table_name="electrical",
                )

                file_report["nb_rows"] = stats.get("nb_rows", 0)
                file_report["nb_channels"] = stats.get("nb_channels", 0)
                file_report["nb_points"] = stats.get("nb_points", 0)
                file_report["nb_invalid_timestamps"] = stats.get("nb_invalid_timestamps", 0)
                file_report["nb_invalid_values"] = stats.get("nb_invalid_values", 0)
                file_report["channels"] = stats.get("channels", {})

                logger.info("  Points that would be created: %d", file_report["nb_points"])
                successful += 1
                run_report["nb_points_total"] += file_report.get("nb_points", 0)
            except Exception as e:
                msg = str(e)
                logger.error("  ✗ Error processing (dry-run) %s: %s", tsv_file, msg)
                file_report["status"] = "error"
                file_report["error"] = msg
                failed += 1

            run_report["files"].append(file_report)
        else:
            ok, file_report = process_tsv_file(tsv_file, base_folder, client, org)
            run_report["files"].append(file_report)

            if ok:
                successful += 1
                run_report["nb_points_total"] += file_report.get("nb_points", 0)
                # Fichier traité avec succès -> on le déplace dans parsed/
                try:
                    move_parsed_file(tsv_file)
                except Exception as e:
                    logger.warning(
                        "Impossible de déplacer le fichier traité vers 'parsed/': %s", e
                    )
            else:
                failed += 1
                # Erreur de traitement -> on le déplace dans error/
                try:
                    move_error_file(tsv_file)
                except Exception as e:
                    logger.warning(
                        "Impossible de déplacer le fichier en erreur vers 'error/': %s", e
                    )

    logger.info("=" * 70)
    logger.info("Processing complete!")
    logger.info("  Successful: %d", successful)
    logger.info("  Failed: %d", failed)
    logger.info("=" * 70)

    end_time = time.time()
    run_report["end_time"] = datetime.now(timezone.utc).isoformat()
    run_report["duration_s"] = end_time - start_time
    run_report["nb_files_success"] = successful
    run_report["nb_files_failed"] = failed
    run_report["status"] = "success" if failed == 0 else "partial_failure"

    if args.dry_run:
        print(
            "\n=== DRY RUN REPORT (aucune écriture InfluxDB, aucun renommage de fichier, "
            "aucun rapport sur disque) ==="
        )
        print(json.dumps(run_report, ensure_ascii=False, indent=2))
    else:
        try:
            write_run_report_to_file(run_report, base_folder)
        except Exception as e:
            logger.warning("Impossible d'écrire le rapport JSON: %s", e)

        try:
            # S'assure que le bucket meta existe avant d'écrire le résumé
            meta_bucket = os.getenv("TSV_META_BUCKET", "powerview_meta")
            create_bucket_if_not_exists(client, meta_bucket, org)
            write_run_summary_to_influx(client, org, run_report)
        except Exception as e:
            logger.warning("Impossible d'écrire le résumé d'exécution dans InfluxDB: %s", e)

        client.close()


if __name__ == "__main__":
    main()
