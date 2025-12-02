import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Tuple

import pandas as pd
from influxdb_client import Point, WritePrecision

logger = logging.getLogger("tsv_parser")


class FileFormat(str, Enum):
    """
    Enum représentant les formats de fichiers supportés.
    Pour l’instant, un seul format est implémenté : MV_T302_V002.
    """
    MV_T302_V002 = "MV_T302_V002"


class BaseTSVParser:
    """
    Interface de base pour les parseurs TSV.
    Chaque implémentation gère un format de fichier spécifique.
    """

    def parse_header(self, tsv_file: str) -> Tuple[List[Dict], str]:
        """
        Parse les deux premières lignes du fichier TSV pour extraire
        les informations de devices et de canaux.

        Retourne:
            (channel_mappings, file_format)
        """
        with open(tsv_file, "r", encoding="utf-8") as f:
            line1 = f.readline().strip().split("\t")  # Device serial numbers
            line2 = f.readline().strip().split("\t")  # Channel names with units

        file_format = line2[0]
        channel_mappings, _ = self.build_channel_mappings(line1, line2)
        return channel_mappings, file_format

    def parse_data(
        self,
        tsv_file: str,
        channel_mappings: List[Dict],
        campaign: str,
        bucket_name: str,
        table_name: str,
    ) -> Tuple[List[Point], Dict[str, Any]]:
        """
        Parse les lignes de données TSV et crée les Points InfluxDB.
        """
        df = pd.read_csv(tsv_file, sep="\t", skiprows=2, header=None)

        points: List[Point] = []

        nb_invalid_timestamps = 0
        nb_invalid_values = 0
        nb_rows = len(df)
        nb_channels = len(channel_mappings)

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
            timestamp_str = str(row[0])

            try:
                timestamp = datetime.strptime(timestamp_str, "%m/%d/%y %H:%M:%S")
            except ValueError:
                try:
                    timestamp = datetime.strptime(timestamp_str, "%d/%m/%y %H:%M:%S")
                except ValueError:
                    logger.warning("Could not parse timestamp: %s", timestamp_str)
                    nb_invalid_timestamps += 1
                    continue

            for mapping in channel_mappings:
                col_idx = mapping["column_idx"]

                try:
                    value = float(row[col_idx])
                except (ValueError, KeyError):
                    logger.warning("Invalid value at column %s", col_idx)
                    nb_invalid_values += 1
                    continue

                point = Point(table_name)
                point = point.tag("channel_id", mapping["channel_id"])
                point = point.tag("channel_type", mapping["channel_type"])
                point = point.tag("channel_number", str(mapping["channel_number"]))
                point = point.tag("channel_name", mapping["channel_name"])
                point = point.tag("device_master_sn", mapping["device_master_sn"])
                point = point.tag("device_sn", mapping["device_sn"])
                point = point.tag("unit", mapping["unit"])
                point = point.tag("campaign", campaign)
                point = point.field("value", value)
                point = point.time(int(timestamp.timestamp()), WritePrecision.S)

                points.append(point)

                cid = mapping["channel_id"]
                cstats = channel_stats[cid]
                cstats["nb_points"] += 1
                cstats["sum"] += value
                if cstats["min"] is None or value < cstats["min"]:
                    cstats["min"] = value
                if cstats["max"] is None or value > cstats["max"]:
                    cstats["max"] = value

        for cid, cstats in channel_stats.items():
            if cstats["nb_points"] > 0:
                cstats["mean"] = cstats["sum"] / cstats["nb_points"]
            else:
                cstats["mean"] = None
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



class MV_T302_V002_Parser(BaseTSVParser):
    """
    Implémentation actuelle du parsing pour le format MV_T302_V002.
    """

    @classmethod
    def build_channel_mappings(cls, line1, line2):
        """
        Construit les mappings de canaux à partir des deux lignes de header déjà lues.

        line1 : liste des SN devices (1ère ligne)
        line2 : liste "format / nom canal + unité" (2ème ligne)
        """
        device_master_sn = line1[0]

        channel_mappings: List[Dict[str, Any]] = []
        device_channel_counter: Dict[str, int] = {}

        for col_idx in range(1, len(line1)):
            device_sn = line1[col_idx]
            channel_info = line2[col_idx]

            # Compteur de canaux par device
            if device_sn not in device_channel_counter:
                device_channel_counter[device_sn] = 0
            device_channel_counter[device_sn] += 1
            channel_number = device_channel_counter[device_sn]

            # Découpage "nom canal" / "unité"
            parts = channel_info.rsplit(" ", 1)
            if len(parts) == 2:
                channel_name, unit = parts
            else:
                channel_name = channel_info
                unit = ""

            channel_type = "master" if device_sn == device_master_sn else "slave"
            channel_type_prefix = "M" if device_sn == device_master_sn else "S"
            channel_id = f"{channel_type_prefix}{device_sn}_Ch{channel_number}_M{device_master_sn}"

            channel_mappings.append(
                {
                    "column_idx": col_idx,
                    "channel_id": channel_id,
                    "channel_type": channel_type,
                    "channel_number": channel_number,
                    "channel_name": channel_name.strip(),
                    "device_master_sn": device_master_sn,
                    "device_sn": device_sn,
                    "unit": unit.strip(),
                }
            )

        return channel_mappings, device_master_sn





class TSVParserFactory:
    """
    Factory retournant le parser adapté à un FileFormat.
    Pour l'instant, un seul format est supporté.
    """
    _registry = {
        FileFormat.MV_T302_V002: MV_T302_V002_Parser,
    }

    @classmethod
    def get_parser(cls, file_format: str) -> BaseTSVParser:
        """
        file_format est une string lue dans le fichier (ex: 'MV_T302_V002').
        On la mappe vers l'enum FileFormat si possible.
        """
        try:
            ff = FileFormat(file_format)
        except ValueError:
            raise ValueError(f"Format de fichier non supporté : {file_format}")

        parser_cls = cls._registry.get(ff)
        if parser_cls is None:
            raise ValueError(f"Aucun parser enregistré pour le format : {file_format}")
        return parser_cls()
