import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional

import pytest

# tools/monitoring n'est pas un package : on l'ajoute au path comme module
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "monitoring"))

import check_ingestion  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes InfluxDB (aucun service réel requis)
# ---------------------------------------------------------------------------

class FakeRecord:
    def __init__(
        self,
        field: str,
        value: Any,
        time: Optional[datetime] = None,
        tags: Optional[dict] = None,
    ):
        self._field = field
        self._value = value
        self._time = time
        # Comme FluxRecord.values : tags + colonnes système
        self.values = {"_field": field, "_value": value, "_time": time, **(tags or {})}

    def get_field(self) -> str:
        return self._field

    def get_value(self) -> Any:
        return self._value

    def get_time(self) -> Optional[datetime]:
        return self._time


class FakeTable:
    def __init__(self, records: List[FakeRecord]):
        self.records = records


class FakeQueryAPI:
    """Retourne des résultats différents selon le contenu de la requête Flux."""

    def __init__(
        self,
        last_run: Optional[datetime],
        failed: int,
        deferred: int,
        file_records: Optional[List[FakeRecord]] = None,
    ):
        self._last_run = last_run
        self._failed = failed
        self._deferred = deferred
        self._file_records = file_records or []
        self.queries: List[str] = []

    def query(self, org: str, query: str) -> List[FakeTable]:
        self.queries.append(query)
        if "last()" in query:
            if self._last_run is None:
                return []
            return [FakeTable([FakeRecord("nb_files_total", 1, self._last_run)])]
        if "tsv_parser_file" in query:
            return [FakeTable(self._file_records)] if self._file_records else []
        # requête des échecs
        return [
            FakeTable([FakeRecord("nb_files_failed", self._failed)]),
            FakeTable([FakeRecord("nb_files_deferred", self._deferred)]),
        ]


class FakeClient:
    def __init__(
        self,
        last_run: Optional[datetime],
        failed: int = 0,
        deferred: int = 0,
        file_records: Optional[List[FakeRecord]] = None,
    ):
        self._query_api = FakeQueryAPI(last_run, failed, deferred, file_records)

    def query_api(self) -> FakeQueryAPI:
        return self._query_api


# ---------------------------------------------------------------------------
# Tests check_ingestion
# ---------------------------------------------------------------------------

def test_check_ingestion_ok(monkeypatch):
    """Run récent et aucun échec : aucun problème remonté."""
    monkeypatch.delenv("MONITORING_MAX_RUN_AGE_HOURS", raising=False)
    client = FakeClient(last_run=datetime.now(timezone.utc) - timedelta(hours=2))

    problems = check_ingestion.check_ingestion(client, "org", "powerview_meta")

    assert problems == []


def test_check_ingestion_stale_run(monkeypatch):
    """Dernier run plus vieux que le seuil : problème remonté."""
    monkeypatch.delenv("MONITORING_MAX_RUN_AGE_HOURS", raising=False)
    client = FakeClient(last_run=datetime.now(timezone.utc) - timedelta(hours=30))

    problems = check_ingestion.check_ingestion(client, "org", "powerview_meta")

    assert len(problems) == 1
    assert "Dernier run du parser" in problems[0]


def test_check_ingestion_no_run_at_all():
    """Aucun run sur 30 jours : problème remonté."""
    client = FakeClient(last_run=None)

    problems = check_ingestion.check_ingestion(client, "org", "powerview_meta")

    assert len(problems) == 1
    assert "Aucun run" in problems[0]


def test_check_ingestion_failed_and_deferred(monkeypatch):
    """Des fichiers failed/deferred sur la fenêtre : un problème par signal."""
    monkeypatch.delenv("MONITORING_MAX_RUN_AGE_HOURS", raising=False)
    client = FakeClient(
        last_run=datetime.now(timezone.utc) - timedelta(hours=1),
        failed=2,
        deferred=3,
    )

    problems = check_ingestion.check_ingestion(client, "org", "powerview_meta")

    assert len(problems) == 2
    assert any("2 fichier(s) en échec" in p for p in problems)
    assert any("3 fichier(s) différé(s)" in p for p in problems)


def test_check_ingestion_threshold_override(monkeypatch):
    """Le seuil est configurable via MONITORING_MAX_RUN_AGE_HOURS."""
    monkeypatch.setenv("MONITORING_MAX_RUN_AGE_HOURS", "48")
    client = FakeClient(last_run=datetime.now(timezone.utc) - timedelta(hours=30))

    problems = check_ingestion.check_ingestion(client, "org", "powerview_meta")

    assert problems == []


def test_notify_without_topic_is_noop(monkeypatch, caplog):
    """Sans NTFY_TOPIC, notify loggue un warning et n'appelle pas requests."""
    monkeypatch.delenv("NTFY_TOPIC", raising=False)

    def fail_post(*args, **kwargs):  # pragma: no cover - ne doit pas être appelé
        raise AssertionError("requests.post ne doit pas être appelé sans topic")

    monkeypatch.setattr(check_ingestion.requests, "post", fail_post)
    caplog.set_level("WARNING", logger="monitoring")

    check_ingestion.notify("titre", "message")

    assert any("NTFY_TOPIC" in rec.getMessage() for rec in caplog.records)


def test_notify_posts_to_ntfy(monkeypatch):
    """Avec NTFY_TOPIC, notify poste sur <server>/<topic> avec titre/priorité."""
    monkeypatch.setenv("NTFY_TOPIC", "mon-topic")
    monkeypatch.setenv("NTFY_SERVER", "https://ntfy.example")

    calls = {}

    class FakeResponse:
        status_code = 200
        text = ""

    def fake_post(url, data=None, headers=None, timeout=None):
        calls["url"] = url
        calls["data"] = data
        calls["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr(check_ingestion.requests, "post", fake_post)

    check_ingestion.notify("titre", "message", priority="urgent")

    assert calls["url"] == "https://ntfy.example/mon-topic"
    assert calls["data"] == "message".encode("utf-8")
    assert calls["headers"]["Title"] == "titre"
    assert calls["headers"]["Priority"] == "urgent"


# ---------------------------------------------------------------------------
# Détail des fichiers en échec dans l'alerte
# ---------------------------------------------------------------------------

def _file_record(field, value, status, file_name, when):
    return FakeRecord(
        field,
        value,
        when,
        tags={
            "status": status,
            "bucket": "AUE_corse",
            "campaign": "SARTENE",
            "device_master_sn": "02001315",
            "file_name": file_name,
        },
    )


def test_check_ingestion_lists_failed_files_with_cause(monkeypatch):
    """L'alerte détaille chaque fichier (chemin logique + cause) par statut."""
    monkeypatch.delenv("MONITORING_MAX_RUN_AGE_HOURS", raising=False)
    when = datetime(2026, 9, 9, 1, 15, tzinfo=timezone.utc)
    records = [
        _file_record("nb_rows", 0, "error", "T302_260909_031459_UTC.tsv", when),
        _file_record("error", "Expected 1 fields in line 5, saw 25", "error",
                     "T302_260909_031459_UTC.tsv", when),
        # point ancien sans champ error (avant 0.6.0) : listé sans cause
        _file_record("nb_rows", 0, "deferred", "T302_260908_165103_UTC.tsv", when),
    ]
    client = FakeClient(
        last_run=datetime.now(timezone.utc) - timedelta(hours=1),
        failed=1,
        deferred=1,
        file_records=records,
    )

    problems = check_ingestion.check_ingestion(client, "org", "powerview_meta")

    assert len(problems) == 2
    failed_msg = next(p for p in problems if "en échec" in p)
    deferred_msg = next(p for p in problems if "différé" in p)
    assert (
        "  - AUE_corse/SARTENE/02001315/T302_260909_031459_UTC.tsv : "
        "Expected 1 fields in line 5, saw 25"
    ) in failed_msg
    assert "T302_260908_165103_UTC.tsv" in deferred_msg
    assert " : " not in deferred_msg.splitlines()[1]


def test_check_ingestion_window_is_used_in_flux(monkeypatch):
    """La fenêtre passée en minutes est celle envoyée à InfluxDB."""
    monkeypatch.delenv("MONITORING_MAX_RUN_AGE_HOURS", raising=False)
    client = FakeClient(last_run=datetime.now(timezone.utc), failed=1, deferred=0)

    problems = check_ingestion.check_ingestion(client, "org", "meta", window_minutes=180)

    assert any("range(start: -180m)" in q for q in client._query_api.queries)
    assert "sur les 3 dernières heures" in problems[0]


def test_check_ingestion_truncates_long_file_list(monkeypatch):
    """Au-delà de MAX_FILES_IN_MESSAGE fichiers, la liste est tronquée."""
    monkeypatch.delenv("MONITORING_MAX_RUN_AGE_HOURS", raising=False)
    when = datetime.now(timezone.utc)
    n = check_ingestion.MAX_FILES_IN_MESSAGE + 3
    records = [_file_record("nb_rows", 0, "error", f"f{i}.tsv", when) for i in range(n)]
    client = FakeClient(last_run=when, failed=n, deferred=0, file_records=records)

    problems = check_ingestion.check_ingestion(client, "org", "meta")

    assert problems[0].count("  - ") == check_ingestion.MAX_FILES_IN_MESSAGE
    assert "... et 3 autre(s)" in problems[0]


# ---------------------------------------------------------------------------
# Fichier d'état et fenêtre (livraison au moins une fois)
# ---------------------------------------------------------------------------

def test_compute_window_defaults_and_bounds():
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    assert check_ingestion.compute_window_minutes(None, now) == 60
    # 2 h 30 depuis le dernier succès -> 150 min
    assert check_ingestion.compute_window_minutes(now - timedelta(minutes=150), now) == 150
    # borne basse : 10 min -> 60 min
    assert check_ingestion.compute_window_minutes(now - timedelta(minutes=10), now) == 60
    # borne haute : 30 jours -> 7 jours
    assert (
        check_ingestion.compute_window_minutes(now - timedelta(days=30), now)
        == check_ingestion.MAX_WINDOW_MINUTES
    )


def test_state_file_roundtrip_and_unreadable(tmp_path):
    path = tmp_path / "sub" / "check_ingestion.state"
    assert check_ingestion.read_last_success(path) is None

    when = datetime(2026, 9, 9, 11, 17, tzinfo=timezone.utc)
    check_ingestion.write_last_success(path, when)
    assert check_ingestion.read_last_success(path) == when

    path.write_text("pas une date", encoding="utf-8")
    assert check_ingestion.read_last_success(path) is None


def test_main_keeps_state_when_ntfy_refuses(monkeypatch, tmp_path):
    """
    Notification refusée -> fichier d'état non mis à jour, pour que l'échec
    soit re-détecté au passage suivant. Notification acceptée -> état écrit.
    """
    state = tmp_path / "state"
    monkeypatch.setenv("MONITORING_STATE_FILE", str(state))
    monkeypatch.setenv("INFLUXDB_HOST", "http://influx.example")
    monkeypatch.setenv("INFLUXDB_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("NTFY_TOPIC", "mon-topic")
    monkeypatch.delenv("MONITORING_MAX_RUN_AGE_HOURS", raising=False)

    class FakeInfluxClient:
        def __init__(self, *args, **kwargs):
            self._client = FakeClient(last_run=datetime.now(timezone.utc), failed=1)

        def __enter__(self):
            return self._client

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(check_ingestion, "InfluxDBClient", FakeInfluxClient)
    monkeypatch.setattr(check_ingestion, "load_dotenv", lambda *a, **k: None)

    def refuse(*args, **kwargs):
        raise check_ingestion.NtfyError("ntfy HTTP 429: quota")

    monkeypatch.setattr(check_ingestion, "notify", refuse)
    assert check_ingestion.main() == 1
    assert not state.exists()

    monkeypatch.setattr(check_ingestion, "notify", lambda *a, **k: None)
    assert check_ingestion.main() == 1
    assert state.exists()
    assert check_ingestion.read_last_success(state) is not None


# ---------------------------------------------------------------------------
# ntfy : IPv4 forcé, topic jamais exposé
# ---------------------------------------------------------------------------

def test_notify_forces_ipv4_by_default(monkeypatch):
    import socket

    monkeypatch.setenv("NTFY_TOPIC", "mon-topic")
    monkeypatch.delenv("NTFY_FORCE_IPV4", raising=False)
    conn = check_ingestion.urllib3_connection
    monkeypatch.setattr(conn, "allowed_gai_family", lambda: socket.AF_UNSPEC)

    class FakeResponse:
        status_code = 200
        text = ""

    monkeypatch.setattr(check_ingestion.requests, "post", lambda *a, **k: FakeResponse())
    check_ingestion.notify("titre", "message")

    assert conn.allowed_gai_family() == socket.AF_INET


def test_notify_http_error_hides_topic(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "topic-secret-xyz")
    monkeypatch.setenv("NTFY_FORCE_IPV4", "0")

    class FakeResponse:
        status_code = 429
        text = '{"error":"daily message quota reached","topic":"topic-secret-xyz"}'

    monkeypatch.setattr(check_ingestion.requests, "post", lambda *a, **k: FakeResponse())

    with pytest.raises(check_ingestion.NtfyError) as exc:
        check_ingestion.notify("titre", "message")

    assert "429" in str(exc.value)
    assert "quota" in str(exc.value)
    assert "topic-secret-xyz" not in str(exc.value)
