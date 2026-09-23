"""
Tests de manage_influx_tokens : script Flux des tâches de downsampling et
création / mise à jour idempotente via la CLI influx (mockée).
"""
from typing import Any, Dict, List

import pytest

import manage_influx_tokens as mit


def test_build_downsample_task_flux_uses_lookback_and_aligned_start():
    level = {"suffix": "1h", "every": "1h", "offset": "5m", "lookback": "3d"}
    flux = mit.build_downsample_task_flux("AUE_corse", "powerview", level)

    assert flux.startswith('import "date"\n')
    assert 'option task = {name: "downsample_AUE_corse_1h", every: 1h, offset: 5m}' in flux
    # La fenêtre relue est le lookback tronqué à une fenêtre entière, pas -task.every
    assert "range(start: date.truncate(t: -3d, unit: 1h))" in flux
    assert "range(start: -task.every)" not in flux
    assert "aggregateWindow(every: 1h, fn: mean, createEmpty: false)" in flux
    assert 'to(bucket: "AUE_corse_1h", org: "powerview")' in flux


def test_downsample_levels_all_have_lookback_larger_than_every():
    """Chaque niveau doit relire bien plus que sa propre période (upload quotidien)."""
    for level in mit.DOWNSAMPLE_LEVELS:
        assert set(level) >= {"suffix", "every", "offset", "lookback"}
        assert level["lookback"] != level["every"]
    assert [lvl["lookback"] for lvl in mit.DOWNSAMPLE_LEVELS] == ["3d", "14d", "5w"]


class FakeInfluxCli:
    """Enregistre les commandes influx et simule `task list`."""

    def __init__(self, tasks: List[Dict[str, Any]]):
        self.tasks = tasks
        self.calls: List[List[str]] = []
        self.written_flux: Dict[str, str] = {}

    def __call__(self, args: List[str]):
        self.calls.append(list(args))
        if args[:2] == ["task", "list"]:
            return self.tasks
        if args[:2] in (["task", "create"], ["task", "update"]):
            path = args[args.index("--file") + 1]
            with open(path, encoding="utf-8") as f:
                self.written_flux[" ".join(args[:2] + args[2:4])] = f.read()
            return {}
        raise AssertionError(f"commande inattendue: {args}")


def _all_current(bucket: str, org: str) -> List[Dict[str, Any]]:
    return [
        {
            "id": f"id_{lvl['suffix']}",
            "name": f"downsample_{bucket}_{lvl['suffix']}",
            "flux": mit.build_downsample_task_flux(bucket, org, lvl),
        }
        for lvl in mit.DOWNSAMPLE_LEVELS
    ]


def test_ensure_tasks_creates_missing(monkeypatch):
    cli = FakeInfluxCli(tasks=[])
    monkeypatch.setattr(mit, "_run_influx_cmd", cli)

    mit.ensure_downsample_tasks_cli("AUE_corse", "powerview")

    creates = [c for c in cli.calls if c[:2] == ["task", "create"]]
    assert len(creates) == 3
    assert all(c[2:4] == ["--org", "powerview"] for c in creates)
    assert not any(c[:2] == ["task", "update"] for c in cli.calls)


def test_ensure_tasks_updates_outdated_script(monkeypatch):
    """Une tâche existante avec l'ancien Flux (-task.every) est mise à jour en place."""
    tasks = _all_current("AUE_corse", "powerview")
    tasks[0]["flux"] = (
        'option task = {name: "downsample_AUE_corse_1h", every: 1h, offset: 5m}\n\n'
        'from(bucket: "AUE_corse")\n  |> range(start: -task.every)\n'
    )
    cli = FakeInfluxCli(tasks=tasks)
    monkeypatch.setattr(mit, "_run_influx_cmd", cli)

    mit.ensure_downsample_tasks_cli("AUE_corse", "powerview")

    updates = [c for c in cli.calls if c[:2] == ["task", "update"]]
    assert updates == [["task", "update", "--id", "id_1h", "--file", updates[0][5]]]
    assert not any(c[:2] == ["task", "create"] for c in cli.calls)
    assert "date.truncate(t: -3d, unit: 1h)" in cli.written_flux["task update --id id_1h"]


def test_ensure_tasks_skips_up_to_date(monkeypatch):
    cli = FakeInfluxCli(tasks=_all_current("AUE_corse", "powerview"))
    monkeypatch.setattr(mit, "_run_influx_cmd", cli)

    mit.ensure_downsample_tasks_cli("AUE_corse", "powerview")

    assert cli.calls == [["task", "list", "--org", "powerview"]]


def test_main_tasks_only_never_touches_tokens(monkeypatch, capsys):
    import sys as _sys

    cli = FakeInfluxCli(tasks=[])
    monkeypatch.setattr(mit, "_run_influx_cmd", cli)
    monkeypatch.setattr(mit, "_ensure_influx_cli_available", lambda: None)
    monkeypatch.setattr(mit, "_prepare_influx_env", lambda: None)
    monkeypatch.setattr(mit, "find_bucket_id_cli", lambda name, org: f"bid_{name}")
    monkeypatch.setattr(
        mit, "ensure_downsampled_buckets_cli", lambda name, org: {"1h": "a", "1d": "b", "1w": "c"}
    )
    monkeypatch.setattr(
        mit, "find_existing_token_for_bucket_cli",
        lambda *a, **k: pytest.fail("les tokens ne doivent pas être consultés"),
    )
    monkeypatch.setattr(
        mit, "create_token_for_bucket_cli",
        lambda *a, **k: pytest.fail("aucun token ne doit être créé"),
    )
    monkeypatch.setenv("INFLUXDB_ORG", "powerview")
    monkeypatch.setattr(_sys, "argv", ["manage_influx_tokens.py", "--bucket", "AUE_corse", "--tasks-only"])

    mit.main()

    assert len([c for c in cli.calls if c[:2] == ["task", "create"]]) == 3
    # Rien sur stdout : le mode tasks-only n'imprime pas de token
    assert capsys.readouterr().out == ""
