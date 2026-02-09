from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

import pytest

from core import TSVParserFactory, parse_tsv_header, parse_timestamp


def _epoch_s(ts: str) -> int:
    """
    Convertit un timestamp TSV (DD/MM/YY HH:MM:SS) en epoch seconds UTC,
    en réutilisant la logique du parseur (core.parse_timestamp).
    """
    dt = parse_timestamp(ts)
    assert dt is not None, f"Timestamp invalide dans le test: {ts}"
    # dt est déjà timezone-aware UTC dans core.parse_timestamp
    return int(dt.timestamp())


def _index_points_by_time_and_field(points) -> Dict[Tuple[int, str], float]:
    """
    Indexe les points InfluxDB par (epoch_seconds, field_name) -> value.
    Hypothèse : chaque Point a exactement 1 field (c'est le cas du parseur).
    """
    out: Dict[Tuple[int, str], float] = {}
    for p in points:
        assert isinstance(p._time, int)
        fields = dict(p._fields)
        assert len(fields) == 1
        field_name, value = next(iter(fields.items()))
        out[(p._time, field_name)] = value
    return out


def test_v003_tri_with_slaves_real_file_header_and_points_and_values():
    """
    Test d'intégration basé sur le fichier réel :
    data/test_v003_utc_metadata_4/02000800/T302_260208_211459_UTC.tsv

    Objectifs :
    - vérifier le format détecté (MV_T302_V003)
    - vérifier le nombre de mappings (12)
    - vérifier le schéma de channel_id distinct par slave :
      - master : M02000800_U1/U2/U3 + M02000800_Ch1..Ch4
      - slave 04001002 : M02000800_S04001002_Ch1..Ch5
    - vérifier le nombre de points (6 lignes * 12 canaux = 72)
    - vérifier des valeurs exactes sur la première ligne (19:10:00)
    """
    tsv_path = Path("data/test_v003_utc_metadata_4/02000800/T302_260208_211459_UTC.tsv")
    if not tsv_path.exists():
        pytest.skip(f"Fichier de test absent: {tsv_path}")

    mappings, file_format = parse_tsv_header(str(tsv_path))
    assert file_format == "MV_T302_V003"
    assert len(mappings) == 12

    channel_ids = [m["channel_id"] for m in mappings]

    # Master (7 colonnes de mesure master : Ph1/2/3 + Voie1/2/3 + minipince)
    assert "M02000800_U1" in channel_ids
    assert "M02000800_U2" in channel_ids
    assert "M02000800_U3" in channel_ids
    assert "M02000800_Ch1" in channel_ids
    assert "M02000800_Ch2" in channel_ids
    assert "M02000800_Ch3" in channel_ids

    # Slave 04001002 (5 colonnes de mesure slave)
    assert "M02000800_S04001002_Ch1" in channel_ids
    assert "M02000800_S04001002_Ch2" in channel_ids
    assert "M02000800_S04001002_Ch3" in channel_ids
    assert "M02000800_S04001002_Ch4" in channel_ids
    assert "M02000800_S04001002_Ch5" in channel_ids

    parser = TSVParserFactory.get_parser(file_format)
    points, stats = parser.parse(
        str(tsv_path),
        campaign="campaign_test",
        bucket_name="company_test",
        table_name="electrical",
    )

    assert stats["nb_rows"] == 6
    assert stats["nb_channels"] == 12
    assert stats["nb_points"] == 72
    assert len(points) == 72

    # Indexation des points pour assertions de valeurs
    idx = _index_points_by_time_and_field(points)

    t0 = _epoch_s("08/02/26 19:10:00")

    # Valeurs attendues (ligne 19:10:00 du fichier)
    # Colonnes master :
    # U1=244.26, U2=244.26, U3=244.27
    # Ch1(Voie1)=11.7, Ch2(Voie2)=133.6, Ch3(Voie3)=2.9, Ch4(minipince)=121.7
    assert idx[(t0, "M02000800_U1_V")] == pytest.approx(244.26)
    assert idx[(t0, "M02000800_U2_V")] == pytest.approx(244.26)
    assert idx[(t0, "M02000800_U3_V")] == pytest.approx(244.27)

    assert idx[(t0, "M02000800_Ch1_W")] == pytest.approx(11.7)
    assert idx[(t0, "M02000800_Ch2_W")] == pytest.approx(133.6)
    assert idx[(t0, "M02000800_Ch3_W")] == pytest.approx(2.9)


    # Colonnes slave 04001002 :
    # Ch1(Voie2)=154.8, Ch2(Voie3)=-3.7, Ch3(minipinc2)=-5.8, Ch4(Voie5)=-3.0, Ch5(Voie6)=-6.6
    assert idx[(t0, "M02000800_S04001002_Ch1_W")] == pytest.approx(121.7)
    assert idx[(t0, "M02000800_S04001002_Ch2_W")] == pytest.approx(154.8)
    assert idx[(t0, "M02000800_S04001002_Ch3_W")] == pytest.approx(-3.7)
    assert idx[(t0, "M02000800_S04001002_Ch4_W")] == pytest.approx(-5.8)
    assert idx[(t0, "M02000800_S04001002_Ch5_W")] == pytest.approx(-3.0)
    assert idx[(t0, "M02000800_S04001002_Ch6_W")] == pytest.approx(-6.6)

    # Vérifie aussi que les tags essentiels sont présents et cohérents sur un point
    sample = points[0]
    tags = dict(sample._tags)
    assert tags["campaign"] == "campaign_test"
    assert tags["device_master_sn"] == "02000800"
    assert "device_sn" in tags
    assert "channel_id" in tags
    assert "channel_unit" in tags
