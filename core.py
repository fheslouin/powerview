import logging
import re
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import pandas as pd
from influxdb_client import Point, WritePrecision

logger = logging.getLogger("tsv_parser")


class FileFormat(str, Enum):
    """
    Enum représentant les formats de fichiers supportés.
    """
    MV_T302_V002 = "MV_T302_V002"
    MV_T302_V003 = "MV_T302_V003"


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """
    Essaie de parser un timestamp issu du TSV en datetime.

    Pour le format actuel MV_T302_V002 / MV_T302_V003 (T302), le format attendu est :

        DD/MM/YY HH:MM:SS

    Exemple : "05/01/26 10:00:00"  (5 janvier 2026 à 10h00)

    IMPORTANT :
    - On retourne désormais un datetime "aware" en UTC (tzinfo=timezone.utc).
    - On considère que l'heure fournie dans le fichier est déjà en UTC.
      (Les informations de fuseau éventuelles dans le header JSON V003 ne sont
       pas encore exploitées.)

    Retourne:
        - un datetime (UTC) si le parsing réussit
        - None sinon
    """
    ts = str(timestamp_str).strip()
    if not ts:
        return None

    fmt = "%d/%m/%y %H:%M:%S"

    try:
        dt = datetime.strptime(ts, fmt)
        # On attache explicitement le fuseau UTC
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class BaseTSVParser:
    """
    Interface de base pour les parseurs TSV.
    Chaque implémentation gère un format de fichier spécifique.
    """

    @classmethod
    def build_channel_mappings(cls, line1, line2):
        """
        Doit être implémentée par les sous-classes :
        construit les mappings de canaux à partir des deux lignes de header.
        """
        raise NotImplementedError

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
        Implémentation par défaut, réutilisée par les sous-classes.
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
                "column_idx": mapping["column_idx"],
                "device_master_sn": mapping["device_master_sn"],
                "device_sn": mapping["device_sn"],
                "device_type": mapping["device_type"],
                "channel_label": mapping["channel_label"],
                "channel_name": mapping["channel_name"],
                "channel_unit": mapping["unit"],
                "nb_points": 0,
                "sum": 0.0,
                "min": None,
                "max": None,
                "mean": None,
            }

        # On garde file_name uniquement pour les stats/rapports éventuels,
        # mais on ne l'utilise plus comme tag dans InfluxDB.
        file_name = Path(tsv_file).name

        for _, row in df.iterrows():
            timestamp_str = str(row[0])

            timestamp = parse_timestamp(timestamp_str)
            if timestamp is None:
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

                # Measurement unifié "electrical"
                point = Point("electrical")
                # Field = "<channel_id>_<unit>"
                point = point.field(f"{mapping['channel_id']}_{mapping['unit']}", value)
                # timestamp est déjà en UTC (tzinfo=timezone.utc), timestamp()
                # renvoie donc un epoch en secondes UTC.
                point = point.time(int(timestamp.timestamp()), WritePrecision.S)
                point = point.tag("campaign", campaign)                            # campagne de mesure
                point = point.tag("channel_id", mapping["channel_id"])             # M02001171_Ch1_M020011201
                point = point.tag("channel_unit", mapping["unit"])                 # V, W, Wa
                point = point.tag("channel_label", mapping["channel_label"])       # "frigo"
                point = point.tag("channel_name", mapping["channel_name"])         # Ui ou Chi
                point = point.tag("device", mapping["device"])                     # MV2
                point = point.tag("device_type", mapping["device_type"])           # master/slave
                point = point.tag("device_subtype", mapping["device_subtype"])     # null/tri/mono
                point = point.tag("device_master_sn", mapping["device_master_sn"]) # 02001171
                point = point.tag("device_sn", mapping["device_sn"])               # 020011201
                # NE PLUS ajouter file_name comme tag pour éviter la forte cardinalité
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

    def parse(
        self,
        tsv_file: str,
        campaign: str,
        bucket_name: str,
        table_name: str,
    ) -> Tuple[List[Point], Dict[str, Any]]:
        """
        Parse complet : header + data.

        - lit les 2 premières lignes
        - construit les mappings via build_channel_mappings
        - appelle parse_data avec les paramètres fournis.
        """
        with open(tsv_file, "r", encoding="utf-8") as f:
            line1 = f.readline().strip().split("\t")
            line2 = f.readline().strip().split("\t")

        # file_format = line2[0]  # non utilisé ici, mais cohérent avec l'API
        channel_mappings, _ = self.build_channel_mappings(line1, line2)

        return self.parse_data(tsv_file, channel_mappings, campaign, bucket_name, table_name)


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

        # Détection robuste tri/mono :
        # tri si on trouve Ph 1, Ph 2 et Ph 3 dans les libellés (peu importe l'ordre)
        header_labels = [str(x).strip() for x in line2[1:]]  # on ignore line2[0] = file_format
        has_ph1 = any(re.match(r"^Ph\s*1\b", s) for s in header_labels)
        has_ph2 = any(re.match(r"^Ph\s*2\b", s) for s in header_labels)
        has_ph3 = any(re.match(r"^Ph\s*3\b", s) for s in header_labels)
        device_subtype = "tri" if (has_ph1 and has_ph2 and has_ph3) else "mono"

        channel_mappings: List[Dict[str, Any]] = []
        device_channel_counter: Dict[str, int] = {}

        for col_idx in range(1, len(line1)):
            device_sn = line1[col_idx]
            channel_info = line2[col_idx]

            device_type = "master" if device_sn == device_master_sn else "slave"
            device_type_prefix = "M" if device_sn == device_master_sn else "S"

            # Compteur de canaux par device
            if device_sn not in device_channel_counter:
                device_channel_counter[device_sn] = 0
            device_channel_counter[device_sn] += 1
            channel_number = device_channel_counter[device_sn]
            if device_type == "master":
                if device_subtype == "tri":
                    if channel_number <= 3:
                        channel_label = f"U{channel_number}"
                    else:
                        channel_label = f"Ch{channel_number-3}"
                else:
                    if channel_number <= 1:
                        channel_label = f"U{channel_number}"
                    else:
                        channel_label = f"Ch{channel_number-1}"
            else:
                channel_label = f"Ch{channel_number}"

            # Découpage "nom canal" / "unité"
            parts = channel_info.rsplit(" ", 1)
            if len(parts) == 2:
                channel_name, unit = parts
            else:
                channel_name = channel_info
                unit = ""

            # channel_id = f"{device_type_prefix}{device_sn}_{channel_label}_M{device_master_sn}"
            channel_id = f"M{device_master_sn}_{device_type_prefix}{device_sn}_{channel_label}" \
                if device_type == "master" \
                else f"M{device_master_sn}_{channel_label}"

            channel_mappings.append(
                {
                    "column_idx": col_idx,
                    "channel_id": channel_id,
                    "device_type": device_type,
                    "device_subtype": device_subtype if device_type == "master" else None,
                    "channel_label": channel_label,
                    "channel_name": channel_name.strip(),
                    "device_master_sn": device_master_sn,
                    "device": "MV2",
                    "device_sn": device_sn,
                    "unit": unit.strip(),
                }
            )

        return channel_mappings, device_master_sn


class MV_T302_V003_Parser(BaseTSVParser):
    """
    Implémentation du parsing pour le format MV_T302_V003.
    Fichiers avec blocs START_HEADER/END_HEADER et START_DATA/END_DATA.
    """

    @classmethod
    def build_channel_mappings(cls, line1, line2):
        """
        line1 : SN devices (1ère ligne après START_DATA)
        line2 : format + nom canal + unité (2ème ligne après START_DATA)
        """
        device_master_sn = line1[0]

        # D'après l'exemple, MasterType = Mono
        device_subtype = "mono"

        channel_mappings: List[Dict[str, Any]] = []
        device_channel_counter: Dict[str, int] = {}

        for col_idx in range(1, len(line1)):
            device_sn = line1[col_idx]
            channel_info = line2[col_idx]

            device_type = "master" if device_sn == device_master_sn else "slave"
            device_type_prefix = "M" if device_sn == device_master_sn else "S"

            # Compteur de canaux par device
            if device_sn not in device_channel_counter:
                device_channel_counter[device_sn] = 0
            device_channel_counter[device_sn] += 1
            channel_number = device_channel_counter[device_sn]

            # Même logique que V002 mono pour les labels
            if device_type == "master":
                if channel_number <= 1:
                    channel_label = f"U{channel_number}"
                else:
                    channel_label = f"Ch{channel_number-1}"
            else:
                channel_label = f"Ch{channel_number}"

            # Découpage "nom canal" / "unité"
            parts = channel_info.rsplit(" ", 1)
            if len(parts) == 2:
                channel_name, unit = parts
            else:
                channel_name = channel_info
                unit = ""

            # Même schéma de channel_id que V002
            channel_id = (
                f"M{device_master_sn}_{device_type_prefix}{device_sn}_{channel_label}"
                if device_type == "master"
                else f"M{device_master_sn}_{channel_label}"
            )

            channel_mappings.append(
                {
                    "column_idx": col_idx,
                    "channel_id": channel_id,
                    "device_type": device_type,
                    "device_subtype": device_subtype if device_type == "master" else None,
                    "channel_label": channel_label,
                    "channel_name": channel_name.strip(),
                    "device_master_sn": device_master_sn,
                    "device": "MV2",
                    "device_sn": device_sn,
                    "unit": unit.strip(),
                }
            )

        return channel_mappings, device_master_sn

    def _read_header_and_data(
        self, tsv_file: str
    ) -> Tuple[Dict[str, Any], List[str], List[str], List[str]]:
        """
        Retourne :
        - header_meta : dict issu du JSON entre START_HEADER / END_HEADER
        - line1 : SN devices (1ère ligne après START_DATA)
        - line2 : format + noms de canaux
        - data_lines : lignes de données jusqu'à END_DATA exclu
        """
        header_meta: Dict[str, Any] = {}
        line1: List[str] = []
        line2: List[str] = []
        data_lines: List[str] = []

        with open(tsv_file, "r", encoding="utf-8") as f:
            in_header = False
            in_data = False
            header_json_line: Optional[str] = None

            for raw in f:
                line = raw.rstrip("\n")
                if line == "START_HEADER":
                    in_header = True
                    continue
                if line == "END_HEADER":
                    in_header = False
                    if header_json_line:
                        try:
                            header_meta = json.loads(header_json_line)
                        except Exception as e:
                            logger.warning("Impossible de parser le header JSON V003: %s", e)
                    continue
                if in_header:
                    # Dans l'exemple, le JSON tient sur une seule ligne
                    header_json_line = line
                    continue

                if line == "START_DATA":
                    in_data = True
                    # les deux prochaines lignes sont line1 et line2
                    line1 = f.readline().rstrip("\n").split("\t")
                    line2 = f.readline().rstrip("\n").split("\t")
                    continue

                if in_data:
                    if line == "END_DATA":
                        break
                    data_lines.append(line)

        return header_meta, line1, line2, data_lines

    def parse_header(self, tsv_file: str) -> Tuple[List[Dict], str]:
        """
        Pour V003, on lit jusqu'à START_DATA, puis on utilise build_channel_mappings.
        """
        _header_meta, line1, line2, _ = self._read_header_and_data(tsv_file)
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
        Implémentation spécifique V003 (on ne peut pas utiliser le skiprows=2 générique).
        """
        header_meta, _line1, _line2, data_lines = self._read_header_and_data(tsv_file)

        points: List[Point] = []
        nb_invalid_timestamps = 0
        nb_invalid_values = 0
        nb_rows = len(data_lines)
        nb_channels = len(channel_mappings)

        channel_stats: Dict[str, Dict[str, Any]] = {}
        for mapping in channel_mappings:
            cid = mapping["channel_id"]
            channel_stats[cid] = {
                "column_idx": mapping["column_idx"],
                "device_master_sn": mapping["device_master_sn"],
                "device_sn": mapping["device_sn"],
                "device_type": mapping["device_type"],
                "channel_label": mapping["channel_label"],
                "channel_name": mapping["channel_name"],
                "channel_unit": mapping["unit"],
                "nb_points": 0,
                "sum": 0.0,
                "min": None,
                "max": None,
                "mean": None,
            }

        for raw in data_lines:
            parts = raw.split("\t")
            if not parts:
                continue
            timestamp_str = str(parts[0])
            timestamp = parse_timestamp(timestamp_str)
            if timestamp is None:
                logger.warning("Could not parse timestamp: %s", timestamp_str)
                nb_invalid_timestamps += 1
                continue

            for mapping in channel_mappings:
                col_idx = mapping["column_idx"]
                if col_idx >= len(parts):
                    continue
                value_str = parts[col_idx]
                try:
                    value = float(value_str)
                except ValueError:
                    logger.warning("Invalid value at column %s", col_idx)
                    nb_invalid_values += 1
                    continue

                point = Point("electrical")
                point = point.field(f"{mapping['channel_id']}_{mapping['unit']}", value)
                point = point.time(int(timestamp.timestamp()), WritePrecision.S)
                point = point.tag("campaign", campaign)
                point = point.tag("channel_id", mapping["channel_id"])
                point = point.tag("channel_unit", mapping["unit"])
                point = point.tag("channel_label", mapping["channel_label"])
                point = point.tag("channel_name", mapping["channel_name"])
                point = point.tag("device", mapping["device"])
                point = point.tag("device_type", mapping["device_type"])
                point = point.tag("device_subtype", mapping["device_subtype"])
                point = point.tag("device_master_sn", mapping["device_master_sn"])
                point = point.tag("device_sn", mapping["device_sn"])
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
            "file_header_meta": header_meta,
        }

        return points, stats

    def parse(
        self,
        tsv_file: str,
        campaign: str,
        bucket_name: str,
        table_name: str,
    ) -> Tuple[List[Point], Dict[str, Any]]:
        """
        Parse complet V003 : header + data.
        """
        _header_meta, line1, line2, _data_lines = self._read_header_and_data(tsv_file)
        channel_mappings, _ = self.build_channel_mappings(line1, line2)
        return self.parse_data(tsv_file, channel_mappings, campaign, bucket_name, table_name)


class TSVParserFactory:
    """
    Factory retournant le parser adapté à un FileFormat.
    Pour l'instant, deux formats sont supportés.
    """
    _registry = {
        FileFormat.MV_T302_V002: MV_T302_V002_Parser,
        FileFormat.MV_T302_V003: MV_T302_V003_Parser,
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


# ---------------------------------------------------------------------------
# Parsing du header (utilisé par les tests)
# ---------------------------------------------------------------------------

def parse_tsv_header(tsv_file: str) -> Tuple[List[Dict], str]:
    """
    Lit les deux premières lignes utiles du fichier, détecte le format et
    délègue la construction des mappings au parser adapté.

    Gère à la fois les fichiers "classiques" (V002) et ceux avec
    START_HEADER/END_HEADER + START_DATA (V003).
    """
    with open(tsv_file, "r", encoding="utf-8") as f:
        first = f.readline().strip()
        if first == "START_HEADER":
            # On saute le header JSON jusqu'à START_DATA
            for line in f:
                line = line.strip()
                if line == "START_DATA":
                    # Les deux prochaines lignes sont line1 et line2
                    line1 = f.readline().strip().split("\t")
                    line2 = f.readline().strip().split("\t")
                    break
        else:
            # Cas V002 : on a déjà lu la première ligne
            line1 = first.split("\t")
            line2 = f.readline().strip().split("\t")

    file_format = line2[0]
    parser = TSVParserFactory.get_parser(file_format)

    if hasattr(parser, "build_channel_mappings"):
        channel_mappings, _ = parser.build_channel_mappings(line1, line2)
    else:
        # Fallback générique : on laisse le parser relire le fichier
        channel_mappings, _ = parser.parse_header(tsv_file)

    return channel_mappings, file_format


def parse_tsv_data(
    tsv_file: str,
    channel_mappings: List[Dict],
    campaign: str,
    bucket_name: str,
    table_name: str,
) -> Tuple[List[Any], Dict[str, Any]]:
    """
    Parse les données en utilisant le parser adapté au format détecté
    dans le header du fichier.

    Signature conservée pour compatibilité avec les tests.
    """
    with open(tsv_file, "r", encoding="utf-8") as f:
        first = f.readline().strip()
        if first == "START_HEADER":
            # Aller jusqu'à START_DATA puis lire line2
            for line in f:
                line = line.strip()
                if line == "START_DATA":
                    _line1 = f.readline().strip().split("\t")
                    line2 = f.readline().strip().split("\t")
                    break
        else:
            _line1 = first.split("\t")
            line2 = f.readline().strip().split("\t")

    file_format = line2[0]
    parser = TSVParserFactory.get_parser(file_format)
    return parser.parse_data(tsv_file, channel_mappings, campaign, bucket_name, table_name)
