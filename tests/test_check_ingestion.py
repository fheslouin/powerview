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
    def __init__(self, field: str, value: Any, time: Optional[datetime] = None):
        self._field = field
        self._value = value
        self._time = time

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

    def __init__(self, last_run: Optional[datetime], failed: int, deferred: int):
        self._last_run = last_run
        self._failed = failed
        self._deferred = deferred

    def query(self, org: str, query: str) -> List[FakeTable]:
        if "last()" in query:
            if self._last_run is None:
                return []
            return [FakeTable([FakeRecord("nb_files_total", 1, self._last_run)])]
        # requête des échecs
        return [
            FakeTable([FakeRecord("nb_files_failed", self._failed)]),
            FakeTable([FakeRecord("nb_files_deferred", self._deferred)]),
        ]


class FakeClient:
    def __init__(self, last_run: Optional[datetime], failed: int = 0, deferred: int = 0):
        self._query_api = FakeQueryAPI(last_run, failed, deferred)

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
        def raise_for_status(self):
            pass

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
