import os
import textwrap
from pathlib import Path
from datetime import datetime
from typing import List

import pandas as pd
import pytest

# On importe le module à tester
import tsv_parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_tmp_tsv(tmp_path: Path, content: str) -> Path:
    """
    Écrit un contenu TSV dans un fichier temporaire et retourne son chemin.
    """
    file_path = tmp_path / "test.tsv"
    file_path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return file_path


# ---------------------------------------------------------------------------
# Tests pour parse_tsv_header
# ---------------------------------------------------------------------------

def test_parse_tsv_header_basic(tmp_path):
    """
    Vérifie que parse_tsv_header extrait correctement les mappings de canaux.
    """
    # 1ère ligne : SN des devices
    # 2ème ligne : nom de canal + unité
    content = """
    02001171\t02001171\t04000466
    MV_T302_V002\tPh 1 V\tVoie1 W
    03/08/25 03:20:00\t242.25\t31.5
    """

    tmp_dir = tmp_path
    tsv_file = write_tmp_tsv(tmp_dir, content)

    mappings, file_format = tsv_parser.parse_tsv_header(str(tsv_file))

    # file_format = première colonne de la 2ème ligne
    assert file_format == "MV_T302_V002"

    # On doit avoir 2 colonnes de données (hors timestamp)
    assert len(mappings) == 2

    # Premier canal : device master
    m0 = mappings[0]
    assert m0["column_idx"] == 1
    assert m0["device_sn"] == "02001171"
    assert m0["device_master_sn"] == "02001171"
    assert m0["channel_number"] == 1
    assert m0["channel_name"] == "Ph 1"
    assert m0["unit"] == "V"
    assert m0["channel_type"] == "master"
    assert m0["channel_id"].startswith("M02001171_Ch1_M02001171")

    # Deuxième canal : device esclave
    m1 = mappings[1]
    assert m1["column_idx"] == 2
    assert m1["device_sn"] == "04000466"
    assert m1["device_master_sn"] == "02001171"
    assert m1["channel_number"] == 1
    assert m1["channel_name"] == "Voie1"
    assert m1["unit"] == "W"
    assert m1["channel_type"] == "slave"
    assert m1["channel_id"].startswith("S04000466_Ch1_M02001171")


# ---------------------------------------------------------------------------
# Tests pour parse_tsv_data
# ---------------------------------------------------------------------------

def test_parse_tsv_data_creates_points(monkeypatch, tmp_path):
    """
    Vérifie que parse_tsv_data crée bien des Points InfluxDB avec les bons tags/champs.
    """
    content = """
    02001171\t02001171
    MV_T302_V002\tPh 1 V
    03/08/25 03:20:00\t242.25
    03/08/25 03:30:00\t243.00
    """
    tsv_file = write_tmp_tsv(tmp_path, content)

    mappings, _ = tsv_parser.parse_tsv_header(str(tsv_file))

    # On appelle parse_tsv_data
    points, stats = tsv_parser.parse_tsv_data(
        str(tsv_file),
        mappings,
        campaign="campaign1",
        bucket_name="company1",
        table_name="campaign1",
    )

    # 2 lignes de données * 1 canal = 2 points
    assert len(points) == 2
    assert stats["nb_rows"] == 2
    assert stats["nb_channels"] == 1
    assert stats["nb_points"] == 2

    p0 = points[0]
    # measurement = table_name
    assert p0._name == "campaign1"

    # tags
    tags = dict(p0._tags)
    assert tags["campaign"] == "campaign1"
    assert tags["channel_name"] == "Ph 1"
    assert tags["unit"] == "V"
    assert tags["device_master_sn"] == "02001171"
    assert tags["device_sn"] == "02001171"
    assert tags["channel_type"] == "master"
    assert tags["channel_number"] == "1"

    # field
    fields = dict(p0._fields)
    assert fields["value"] == pytest.approx(242.25)

    # timestamp : on vérifie juste que c'est un entier (epoch seconds)
    assert isinstance(p0._time, int)


def test_parse_tsv_data_invalid_timestamp_is_skipped(tmp_path, capsys):
    """
    Vérifie qu'une ligne avec timestamp invalide est ignorée.
    """
    content = """
    02001171\t02001171
    MV_T302_V002\tPh 1 V
    INVALID_TS\t242.25
    03/08/25 03:30:00\t243.00
    """
    tsv_file = write_tmp_tsv(tmp_path, content)
    mappings, _ = tsv_parser.parse_tsv_header(str(tsv_file))

    points, stats = tsv_parser.parse_tsv_data(
        str(tsv_file),
        mappings,
        campaign="campaign1",
        bucket_name="company1",
        table_name="campaign1",
    )

    # Une seule ligne valide → 1 point
    assert len(points) == 1
    assert stats["nb_invalid_timestamps"] == 1

    captured = capsys.readouterr()
    assert "Warning: Could not parse timestamp" in captured.out


def test_parse_tsv_data_invalid_value_is_skipped(tmp_path, capsys):
    """
    Vérifie qu'une valeur non numérique est ignorée pour un canal.
    """
    content = """
    02001171\t02001171
    MV_T302_V002\tPh 1 V
    03/08/25 03:20:00\tNOT_A_NUMBER
    """
    tsv_file = write_tmp_tsv(tmp_path, content)
    mappings, _ = tsv_parser.parse_tsv_header(str(tsv_file))

    points, stats = tsv_parser.parse_tsv_data(
        str(tsv_file),
        mappings,
        campaign="campaign1",
        bucket_name="company1",
        table_name="campaign1",
    )

    # Aucune valeur valide → 0 point
    assert len(points) == 0
    assert stats["nb_invalid_values"] == 1

    captured = capsys.readouterr()
    assert "Warning: Invalid value at column" in captured.out


# ---------------------------------------------------------------------------
# Test d'intégration : parsing de toutes les colonnes du fichier réel
# ---------------------------------------------------------------------------

def test_full_file_parsing_all_columns():
    """
    Test d'intégration sur le fichier réel
    data/company1/campaign1/02001084/T302_251012_031720.tsv

    - Vérifie que toutes les colonnes de mesure sont mappées par parse_tsv_header
    - Vérifie que parse_tsv_data produit un point par (ligne, colonne)
      sans warnings de timestamp ni de valeur invalide.
    """
    # Localisation du fichier réel par rapport à la racine du projet
    project_root = Path(__file__).resolve().parents[1]
    tsv_path = project_root / "data" / "company1" / "campaign1" / "02001084" / "T302_251012_031720.tsv"

    assert tsv_path.exists(), f"Fichier de test manquant : {tsv_path}"

    # 1) Header : on doit avoir autant de mappings que de colonnes - 1 (timestamp)
    mappings, file_format = tsv_parser.parse_tsv_header(str(tsv_path))

    # On relit les deux premières lignes pour compter les colonnes
    with tsv_path.open("r", encoding="utf-8") as f:
        line1 = f.readline().strip().split("\t")
        line2 = f.readline().strip().split("\t")

    # Sanity checks sur le header
    assert len(line1) == len(line2)
    # 1ère colonne = master SN, 2ème ligne 1ère colonne = format de fichier
    assert file_format == line2[0]
    # Nombre de colonnes de données (hors timestamp)
    expected_channels = len(line1) - 1
    assert len(mappings) == expected_channels

    # Vérifie que chaque mapping pointe vers une colonne existante
    for m in mappings:
        assert 1 <= m["column_idx"] < len(line1)

    # 2) Data : on parse toutes les lignes
    points, stats = tsv_parser.parse_tsv_data(
        str(tsv_path),
        mappings,
        campaign="campaign1",
        bucket_name="company1",
        table_name="campaign1",
    )

    # On lit le fichier complet avec pandas pour connaître le nombre de lignes de données
    df = pd.read_csv(tsv_path, sep="\t", skiprows=2, header=None)
    nb_rows = len(df)

    # On s'attend à nb_rows * expected_channels points
    assert len(points) == nb_rows * expected_channels
    assert stats["nb_rows"] == nb_rows
    assert stats["nb_channels"] == expected_channels
    assert stats["nb_points"] == len(points)

    # Vérifie quelques propriétés sur les points
    # - measurement correct
    # - tags cohérents
    # - aucune valeur None
    for p in points[:10]:  # on échantillonne quelques points pour ne pas tout parcourir
        assert p._name == "campaign1"
        tags = dict(p._tags)
        assert tags["campaign"] == "campaign1"
        assert "channel_id" in tags
        assert "channel_name" in tags
        assert "device_sn" in tags
        assert "unit" in tags
        fields = dict(p._fields)
        assert "value" in fields
        assert fields["value"] is not None


# ---------------------------------------------------------------------------
# Tests pour extract_path_components
# ---------------------------------------------------------------------------

def test_extract_path_components_ok():
    """
    Vérifie l'extraction bucket / campagne / device depuis le chemin.
    """
    base_folder = "/srv/powerview/data"
    tsv_path = "/srv/powerview/data/company1/campaign1/02001084/T302_251012_031720.tsv"

    bucket, campaign, device = tsv_parser.extract_path_components(tsv_path, base_folder)

    assert bucket == "company1"
    assert campaign == "campaign1"
    assert device == "02001084"


def test_extract_path_components_invalid_path():
    """
    Vérifie qu'un chemin trop court lève une ValueError.
    """
    base_folder = "/srv/powerview/data"
    tsv_path = "/srv/powerview/data/company1/file.tsv"

    with pytest.raises(ValueError):
        tsv_parser.extract_path_components(tsv_path, base_folder)


# ---------------------------------------------------------------------------
# Tests pour find_tsv_files
# ---------------------------------------------------------------------------

def test_find_tsv_files_excludes_parsed(tmp_path):
    """
    Vérifie que find_tsv_files ignore les fichiers préfixés par PARSED_.
    """
    base = tmp_path / "data"
    base.mkdir()

    (base / "a.tsv").write_text("x", encoding="utf-8")
    (base / "PARSED_b.tsv").write_text("y", encoding="utf-8")
    sub = base / "sub"
    sub.mkdir()
    (sub / "c.tsv").write_text("z", encoding="utf-8")

    files = tsv_parser.find_tsv_files(str(base))

    # On doit trouver a.tsv et sub/c.tsv, mais pas PARSED_b.tsv
    basenames = {Path(f).name for f in files}
    assert "a.tsv" in basenames
    assert "c.tsv" in basenames
    assert "PARSED_b.tsv" not in basenames


# ---------------------------------------------------------------------------
# Tests pour rename_parsed_file
# ---------------------------------------------------------------------------

def test_rename_parsed_file(tmp_path, capsys):
    """
    Vérifie que rename_parsed_file renomme correctement le fichier.
    """
    file_path = tmp_path / "T302_251012_031720.tsv"
    file_path.write_text("dummy", encoding="utf-8")

    tsv_parser.rename_parsed_file(str(file_path))

    new_path = tmp_path / "PARSED_T302_251012_031720.tsv"
    assert new_path.exists()
    assert not file_path.exists()

    captured = capsys.readouterr()
    assert "Renamed to: PARSED_T302_251012_031720.tsv" in captured.out


# ---------------------------------------------------------------------------
# Tests pour setup_influxdb_client (mock)
# ---------------------------------------------------------------------------

class DummyBucketsAPI:
    def __init__(self):
        self._buckets = []

    def find_buckets(self):
        class Result:
            def __init__(self, buckets):
                self.buckets = buckets

        return Result(self._buckets)

    def create_bucket(self, bucket_name, org):
        class Bucket:
            def __init__(self, name):
                self.name = name

        bucket = Bucket(bucket_name)
        self._buckets.append(bucket)
        return bucket


class DummyClient:
    def __init__(self):
        self._buckets_api = DummyBucketsAPI()
        self.written = []

    def buckets_api(self):
        return self._buckets_api

    class DummyWriteAPI:
        def __init__(self, parent):
            self.parent = parent

        def write(self, bucket, org, record):
            self.parent.written.append((bucket, org, record))

    def write_api(self, write_options=None):
        return DummyClient.DummyWriteAPI(self)

    def close(self):
        pass


def test_create_bucket_if_not_exists_creates(monkeypatch):
    """
    Vérifie que create_bucket_if_not_exists crée un bucket manquant.
    """
    client = DummyClient()
    org = "my-org"

    # Aucun bucket au départ
    assert client.buckets_api().find_buckets().buckets == []

    tsv_parser.create_bucket_if_not_exists(client, "company1", org)

    buckets = client.buckets_api().find_buckets().buckets
    assert len(buckets) == 1
    assert buckets[0].name == "company1"

    # Deuxième appel ne doit pas recréer un bucket
    tsv_parser.create_bucket_if_not_exists(client, "company1", org)
    buckets2 = client.buckets_api().find_buckets().buckets
    assert len(buckets2) == 1


def test_process_tsv_file_writes_points(monkeypatch, tmp_path, capsys):
    """
    Vérifie que process_tsv_file appelle bien l'API d'écriture Influx.
    """
    # Préparation de l'arborescence : base_folder/client/campaign/device/file.tsv
    base_folder = tmp_path / "data"
    tsv_dir = base_folder / "company1" / "campaign1" / "02001084"
    tsv_dir.mkdir(parents=True)

    content = """
    02001084\t02001084
    MV_T302_V002\tPh 1 V
    03/08/25 03:20:00\t242.25
    """
    tsv_file = write_tmp_tsv(tsv_dir, content)

    client = DummyClient()
    org = "my-org"

    ok, file_report = tsv_parser.process_tsv_file(str(tsv_file), str(base_folder), client, org)

    assert ok is True
    assert file_report["status"] == "success"
    assert file_report["nb_points"] == 1

    # Un seul appel à write, avec des points non vides
    assert len(client.written) == 1
    bucket, written_org, record = client.written[0]
    assert bucket == "company1"
    assert written_org == "my-org"
    assert isinstance(record, list)
    assert len(record) == 1

    captured = capsys.readouterr()
    assert "Successfully written to InfluxDB" in captured.out


def test_setup_influxdb_client_missing_env(monkeypatch):
    """
    Vérifie que setup_influxdb_client lève une erreur si les variables d'env sont manquantes.
    """
    monkeypatch.delenv("INFLUXDB_HOST", raising=False)
    monkeypatch.delenv("INFLUXDB_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("INFLUXDB_ORG", raising=False)

    with pytest.raises(ValueError):
        tsv_parser.setup_influxdb_client()
