#!/usr/bin/env python3
"""
TSV to InfluxDB 3 Core Parser
Recursively parses TSV files and loads data into InfluxDB buckets.
"""

import os
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Any
import json
import time
import logging

import pandas as pd
import influxdb_client
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from dotenv import load_dotenv

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
    """
    level_name = os.getenv("TSV_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )


# ---------------------------------------------------------------------------
# Parsing TSV
# ---------------------------------------------------------------------------

def parse_tsv_header(tsv_file: str) -> Tuple[List[Dict], str]:
    """
    Parse the first two lines of TSV file to extract device and channel information.

    Returns:
        Tuple of (channel_mappings, campaign_name)
        channel_mappings: List of dicts with device, channel info
        campaign_name: The first column value of line 2
    """
    with open(tsv_file, 'r', encoding='utf-8') as f:
        line1 = f.readline().strip().split('\t')  # Device serial numbers
        line2 = f.readline().strip().split('\t')  # Channel names with units

    device_master_sn = line1[0]
    file_format = line2[0]

    # Build channel mappings
    channel_mappings = []
    device_channel_counter: Dict[str, int] = {}

    for col_idx in range(1, len(line1)):
        device_sn = line1[col_idx]
        channel_info = line2[col_idx]

        # Track channel index per device
        if device_sn not in device_channel_counter:
            device_channel_counter[device_sn] = 0
        device_channel_counter[device_sn] += 1
        channel_number = device_channel_counter[device_sn]

        # Parse channel name and unit
        parts = channel_info.rsplit(' ', 1)
        if len(parts) == 2:
            channel_name, unit = parts
        else:
            channel_name = channel_info
            unit = ''

        channel_type = 'master' if device_sn == device_master_sn else 'slave'
        channel_type_prefix = 'M' if device_sn == device_master_sn else 'S'
        channel_id = f"{channel_type_prefix}{device_sn}_Ch{channel_number}_M{device_master_sn}"

        channel_mappings.append({
            'column_idx': col_idx,
            'channel_id': channel_id,
            'channel_type': channel_type,
            'channel_number': channel_number,
            'channel_name': channel_name.strip(),
            'device_master_sn': device_master_sn,
            'device_sn': device_sn,
            'unit': unit.strip()
        })

    return channel_mappings, file_format


def parse_tsv_data(
    tsv_file: str,
    channel_mappings: List[Dict],
    campaign: str,
    bucket_name: str,
    table_name: str
) -> Tuple[List[Point], Dict[str, Any]]:
    """
    Parse TSV data rows and create InfluxDB Points.

    Args:
        tsv_file: Path to TSV file
        channel_mappings: Channel mapping information from header
        campaign: Campaign name from folder structure
        bucket_name: Bucket name (top folder)
        table_name: Table name (campaign folder)

    Returns:
        Tuple:
          - List of InfluxDB Point objects
          - Dict de statistiques pour le rapport, structure:
            {
              "nb_rows": int,
              "nb_channels": int,
              "nb_points": int,
              "nb_invalid_timestamps": int,
              "nb_invalid_values": int,
              "channels": {
                  channel_id: {
                      "channel_name": str,
                      "unit": str,
                      "nb_points": int,
                      "mean": float,
                      "min": float,
                      "max": float,
                  },
                  ...
              }
            }
    """
    # Read data starting from line 3 (skip header lines)
    df = pd.read_csv(tsv_file, sep='\t', skiprows=2, header=None)

    points: List[Point] = []

    # Statistiques globales pour ce fichier
    nb_invalid_timestamps = 0
    nb_invalid_values = 0
    nb_rows = len(df)
    nb_channels = len(channel_mappings)

    # Stats par channel_id
    channel_stats: Dict[str, Dict[str, Any]] = {}
    for mapping in channel_mappings:
        cid = mapping["channel_id"]
        channel_stats[cid] = {
            "channel_name": mapping["channel_name"],
            "unit": mapping["unit"],
            "nb_points": 0,
            "sum": 0.0,
            "min": None,
            "max": None,
        }

    for _, row in df.iterrows():
        # First column is timestamp
        timestamp_str = str(row[0])

        # Parse timestamp (format: MM/DD/YY HH:MM:SS ou DD/MM/YY HH:MM:SS)
        try:
            timestamp = datetime.strptime(timestamp_str, '%m/%d/%y %H:%M:%S')
        except ValueError:
            try:
                timestamp = datetime.strptime(timestamp_str, '%d/%m/%y %H:%M:%S')
            except ValueError:
                logger.warning("Could not parse timestamp: %s", timestamp_str)
                nb_invalid_timestamps += 1
                continue

        # Create a point for each channel
        for mapping in channel_mappings:
            col_idx = mapping['column_idx']

            # Get value from dataframe
            try:
                value = float(row[col_idx])
            except (ValueError, KeyError):
                logger.warning("Invalid value at column %s", col_idx)
                nb_invalid_values += 1
                continue

            # Create point with table name
            point = Point(table_name)

            # Add tags
            point = point.tag('channel_id', mapping['channel_id'])
            point = point.tag('channel_type', mapping['channel_type'])
            point = point.tag('channel_number', str(mapping['channel_number']))
            point = point.tag('channel_name', mapping['channel_name'])
            point = point.tag('device_master_sn', mapping['device_master_sn'])
            point = point.tag('device_sn', mapping['device_sn'])
            point = point.tag('unit', mapping['unit'])
            point = point.tag('campaign', campaign)

            # Add field (the actual measurement value)
            point = point.field('value', value)

            # Set timestamp (Unix timestamp in seconds)
            point = point.time(int(timestamp.timestamp()), WritePrecision.S)

            points.append(point)

            # Mise à jour des stats par channel
            cid = mapping["channel_id"]
            cstats = channel_stats[cid]
            cstats["nb_points"] += 1
            cstats["sum"] += value
            if cstats["min"] is None or value < cstats["min"]:
                cstats["min"] = value
            if cstats["max"] is None or value > cstats["max"]:
                cstats["max"] = value

    # Calcul des moyennes
    for cid, cstats in channel_stats.items():
        if cstats["nb_points"] > 0:
            cstats["mean"] = cstats["sum"] / cstats["nb_points"]
        else:
            cstats["mean"] = None
        # On ne garde pas "sum" dans le rapport final
        del cstats["sum"]

    stats = {
        "nb_rows": nb_rows,
        "nb_channels": nb_channels,
        "nb_points": len(points),
        "nb_invalid_timestamps": nb_invalid_timestamps,
        "nb_invalid_values": nb_invalid_values,
        "channels": channel_stats,
    }

    return points, stats


def extract_path_components(tsv_path: str, base_folder: str) -> Tuple[str, str, str]:
    """
    Extract bucket name, campaign name, and device serial from file path.

    Structure: base_folder/my_client/campaign/device_master_sn/file.tsv

    Returns:
        Tuple of (bucket_name, campaign_name, device_master_sn)
    """
    path = Path(tsv_path)
    relative_path = path.relative_to(base_folder)
    parts = relative_path.parts

    if len(parts) < 4:
        raise ValueError(f"Invalid path structure: {tsv_path}")

    bucket_name = parts[0]  # my_client (top folder)
    campaign_name = parts[1]  # campaign folder
    device_master_sn = parts[2]  # device serial number folder

    return bucket_name, campaign_name, device_master_sn


def process_tsv_file(
    tsv_file: str,
    base_folder: str,
    client: InfluxDBClient,
    org: str
) -> Tuple[bool, Dict[str, Any]]:
    """
    Process a single TSV file and write to InfluxDB.

    Returns:
        (success: bool, file_report: dict)
        file_report structure:
        {
          "file_path": str,
          "bucket": str,
          "campaign": str,
          "device_master_sn": str,
          "status": "success" | "error",
          "error": str | None,
          "nb_rows": int,
          "nb_channels": int,
          "nb_points": int,
          "nb_invalid_timestamps": int,
          "nb_invalid_values": int,
          "channels": { ... }  # stats par channel_id
        }
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
    }

    try:
        logger.info("Processing: %s", tsv_file)

        # Extract path components
        bucket_name, campaign_name, device_master_sn = extract_path_components(
            tsv_file, base_folder
        )

        file_report["bucket"] = bucket_name
        file_report["campaign"] = campaign_name
        file_report["device_master_sn"] = device_master_sn

        # Ensure bucket exists
        create_bucket_if_not_exists(client, bucket_name, org)

        # Parse TSV header
        channel_mappings, _ = parse_tsv_header(tsv_file)

        logger.info("  Bucket: %s", bucket_name)
        logger.info("  Campaign: %s", campaign_name)
        logger.info("  Master device: %s", device_master_sn)
        logger.info("  Channels: %d", len(channel_mappings))

        # Parse data and create points + stats
        points, stats = parse_tsv_data(
            tsv_file,
            channel_mappings,
            campaign_name,
            bucket_name,
            campaign_name  # table name is campaign name
        )

        logger.info("  Points created: %d", len(points))

        # Mise à jour du rapport fichier avec les stats retournées
        file_report["nb_rows"] = stats.get("nb_rows", 0)
        file_report["nb_channels"] = stats.get("nb_channels", 0)
        file_report["nb_points"] = stats.get("nb_points", 0)
        file_report["nb_invalid_timestamps"] = stats.get("nb_invalid_timestamps", 0)
        file_report["nb_invalid_values"] = stats.get("nb_invalid_values", 0)
        file_report["channels"] = stats.get("channels", {})

        # Write to InfluxDB
        if points:
            write_api = client.write_api(write_options=SYNCHRONOUS)
            write_api.write(bucket=bucket_name, org=org, record=points)
            logger.info("  ✓ Successfully written to InfluxDB")

        file_report["status"] = "success"
        return True, file_report

    except Exception as e:
        msg = str(e)
        logger.error("  ✗ Error processing %s: %s", tsv_file, msg)
        file_report["status"] = "error"
        file_report["error"] = msg
        return False, file_report


def rename_parsed_file(tsv_file: str) -> None:
    """
    Rename processed file by adding PARSED_ prefix.
    """
    path = Path(tsv_file)
    new_name = f"PARSED_{path.name}"
    new_path = path.parent / new_name
    path.rename(new_path)
    logger.info("  Renamed to: %s", new_name)


def find_tsv_files(base_folder: str) -> List[str]:
    """
    Recursively find all .tsv files that haven't been parsed yet.
    """
    tsv_files: List[str] = []
    for root, dirs, files in os.walk(base_folder):
        for file in files:
            if file.endswith('.tsv') and not file.startswith('PARSED_'):
                tsv_files.append(os.path.join(root, file))
    return tsv_files


def setup_influxdb_client() -> Tuple[InfluxDBClient, str]:
    """
    Setup InfluxDB client with configuration from environment variables.

    Returns:
        (client, org)
    """
    url = os.getenv('INFLUXDB_HOST')
    token = os.getenv('INFLUXDB_ADMIN_TOKEN')
    org = os.getenv('INFLUXDB_ORG')

    if not url or not token:
        raise ValueError(
            "Missing required environment variables: INFLUXDB_HOST and INFLUXDB_ADMIN_TOKEN"
        )

    # Create client
    client = influxdb_client.InfluxDBClient(
        url=url,
        token=token,
        org=org,
    )

    return client, org


def create_bucket_if_not_exists(client: InfluxDBClient, bucket_name: str, org: str) -> None:
    """
    Create InfluxDB bucket if it does not exist.
    """
    buckets_api = client.buckets_api()
    existing_buckets = buckets_api.find_buckets().buckets

    if not any(bucket.name == bucket_name for bucket in existing_buckets):
        logger.info("Creating bucket: %s", bucket_name)
        buckets_api.create_bucket(bucket_name=bucket_name, org=org)


def write_run_report_to_file(report: Dict[str, Any], base_folder: str) -> None:
    """
    Écrit un rapport JSON d'exécution sur disque.

    Le chemin de base peut être configuré via la variable d'env TSV_REPORT_DIR,
    sinon on utilise <base_folder>/../logs/reports.
    """
    # Répertoire de base configurable
    report_dir_env = os.getenv("TSV_REPORT_DIR")
    if report_dir_env:
        reports_dir = Path(report_dir_env)
    else:
        # Par défaut : un dossier logs/reports à côté du dossier data
        base = Path(base_folder).resolve()
        reports_dir = base.parent / "logs" / "reports"

    reports_dir.mkdir(parents=True, exist_ok=True)

    # Nom de fichier basé sur run_id ou start_time
    run_id = report.get("run_id", datetime.utcnow().isoformat())
    safe_run_id = run_id.replace(":", "-")
    filename = f"tsv_parser_{safe_run_id}.json"
    path = reports_dir / filename

    with path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info("Rapport d'exécution écrit dans: %s", path)


def write_run_summary_to_influx(client: InfluxDBClient, org: str, report: Dict[str, Any]) -> None:
    """
    Optionnel : écrit un résumé de l'exécution dans un bucket InfluxDB dédié
    pour pouvoir monitorer le parseur dans Grafana.

    Bucket configurable via TSV_META_BUCKET (par défaut: 'powerview_meta').
    Si INFLUXDB n'est pas accessible ou si l'écriture échoue, on loggue juste l'erreur.
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


def main():
    """
    Main function to process TSV files recursively.
    """
    setup_logging()

    logger.info("=" * 70)
    logger.info("TSV to InfluxDB2 Parser")
    logger.info("=" * 70)

    # Get TSV file from command line arguments or find all TSV files
    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--dataFolder", help="Path to the data folder (ex: /srv/powerview/data)")
    parser.add_argument("-t", "--tsvFile", help="Path to the TSV file(s)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ne pas écrire dans InfluxDB, ne pas renommer les fichiers, "
             "ne pas sauvegarder le rapport, mais afficher le rapport JSON sur stdout.",
    )
    args = parser.parse_args()

    tsv_files: List[str] = []
    base_folder: str = ""

    if args.tsvFile and args.dataFolder:
        base_folder = args.dataFolder
        if not os.path.exists(base_folder):
            logger.error("Error: Folder '%s' does not exist.", base_folder)
            sys.exit(1)
        else:
            logger.info("Using data folder: %s", base_folder)

        tsv_files = [args.tsvFile]
        logger.info("Using specified TSV files: %s", tsv_files)

    elif args.dataFolder and not args.tsvFile:
        base_folder = args.dataFolder
        if not os.path.exists(base_folder):
            logger.error("Error: Folder '%s' does not exist.", base_folder)
            sys.exit(1)
        else:
            logger.info("Using all TSV files in folder: %s", base_folder)
            tsv_files = find_tsv_files(base_folder)

    if not tsv_files:
        logger.info("No TSV files found to process.")
        return

    logger.info("Found %d TSV file(s) to process.", len(tsv_files))

    # En dry-run, on ne doit pas se connecter à InfluxDB
    client: Any = None
    org: str = ""

    if not args.dry_run:
        # Setup InfluxDB client
        try:
            client, org = setup_influxdb_client()
            logger.info("Connected to InfluxDB at %s", os.getenv('INFLUXDB_HOST'))
        except Exception as e:
            logger.error("Error connecting to InfluxDB: %s", e)
            sys.exit(1)
    else:
        logger.info("Mode DRY-RUN : aucune connexion à InfluxDB ne sera effectuée.")

    # Préparation du rapport global d'exécution
    run_id = datetime.utcnow().isoformat()
    start_time = time.time()

    run_report: Dict[str, Any] = {
        "run_id": run_id,
        "start_time": datetime.utcnow().isoformat(),
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

    # Process each file
    successful = 0
    failed = 0

    for tsv_file in tsv_files:
        if args.dry_run:
            # En dry-run, on ne passe pas de client InfluxDB et on ne doit pas écrire
            # On réutilise parse_tsv_header + parse_tsv_data pour remplir le rapport
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
            }

            try:
                bucket_name, campaign_name, device_master_sn = extract_path_components(
                    tsv_file, base_folder
                )
                file_report["bucket"] = bucket_name
                file_report["campaign"] = campaign_name
                file_report["device_master_sn"] = device_master_sn

                channel_mappings, _ = parse_tsv_header(tsv_file)
                logger.info("  Bucket: %s", bucket_name)
                logger.info("  Campaign: %s", campaign_name)
                logger.info("  Master device: %s", device_master_sn)
                logger.info("  Channels: %d", len(channel_mappings))

                _, stats = parse_tsv_data(
                    tsv_file,
                    channel_mappings,
                    campaign_name,
                    bucket_name,
                    campaign_name,
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
                rename_parsed_file(tsv_file)
            else:
                failed += 1

    # Summary
    logger.info("=" * 70)
    logger.info("Processing complete!")
    logger.info("  Successful: %d", successful)
    logger.info("  Failed: %d", failed)
    logger.info("=" * 70)

    # Finalisation du rapport global
    end_time = time.time()
    run_report["end_time"] = datetime.utcnow().isoformat()
    run_report["duration_s"] = end_time - start_time
    run_report["nb_files_success"] = successful
    run_report["nb_files_failed"] = failed
    run_report["status"] = "success" if failed == 0 else "partial_failure"

    if args.dry_run:
        # En dry-run : on affiche le rapport JSON sur stdout, et on ne touche pas au disque ni au bucket meta
        print("\n=== DRY RUN REPORT (aucune écriture InfluxDB, aucun renommage de fichier, aucun rapport sur disque) ===")
        print(json.dumps(run_report, ensure_ascii=False, indent=2))
    else:
        # Écriture du rapport JSON sur disque
        try:
            write_run_report_to_file(run_report, base_folder)
        except Exception as e:
            logger.warning("Impossible d'écrire le rapport JSON: %s", e)

        # Écriture optionnelle d'un résumé dans InfluxDB (bucket meta)
        try:
            write_run_summary_to_influx(client, org, run_report)
        except Exception as e:
            logger.warning("Impossible d'écrire le résumé d'exécution dans InfluxDB: %s", e)

        # Close client uniquement si on l'a créé
        client.close()


if __name__ == "__main__":
    main()
