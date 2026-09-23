# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Activate venv (required before all Python commands)
source envs/powerview/bin/activate

# Tests
pytest                                          # all tests
pytest tests/test_tsv_parser.py                # single file
pytest tests/test_tsv_parser.py::test_name     # single test

# TSV parser
python3 tsv_parser.py --dataFolder /srv/sftpgo/data --dry-run
python3 tsv_parser.py --dataFolder /srv/sftpgo/data --tsvFile <path>

# One-shot : peupler le catalogue des voies du config API pour campagnes existantes
export CONFIG_API_URL=http://localhost:8000
python3 backfill_known_channels.py --bucket <company>             # un seul
python3 backfill_known_channels.py --all                          # tous les buckets clients
python3 backfill_known_channels.py --bucket <company> --dry-run   # aperçu sans POST

# Docker services (InfluxDB :8086 + Grafana :8088)
podman compose up -d
podman compose down -v

# Ansible (load .env first)
export $(grep -v '^#' .env | xargs)
ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
  --extra-vars "company_name=<company> campaign_name=<campaign>"
ansible-playbook grafana-automation/playbooks/add_grafana_user_to_team.yml \
  --extra-vars "company_name=<company> campaign_name=<campaign>"
ansible-playbook grafana-automation/playbooks/delete_grafana_resources.yml \
  --extra-vars "company_name=<company>"
```

## Architecture

**Pipeline:** `SFTPGo → on-upload.sh → tsv_parser.py → InfluxDB + Config API → Grafana`

`on-upload.sh` is a SFTPGo hook dispatcher. On `upload` action it calls `tsv_parser.py`; on `mkdir` (campaign-level only, not device-level) it triggers the Ansible playbook to provision Grafana resources. En prod, l'action `upload` est en réalité branchée sur le `post_disconnect_hook` de SFTPGo (`/etc/sftpgo/sftpgo.env`), **sans** `SFTPGO_ACTION_PATH` : chaque déconnexion de session lance un balayage complet de `/srv/sftpgo/data`. Les runs sont sérialisés par `flock` sur `logs/on-upload.lock`.

Après écriture Influx, `tsv_parser.py` publie le catalogue des voies (field keys + métadonnées) au config API (`POST /buckets/{bucket}/channels`). Le panel Grafana lookup ce catalogue SQLite au lieu de scanner Influx (évite les timeouts 502 sur les buckets à forte cardinalité).

### TSV Parsing (`tsv_parser.py` + `core.py`)

- `tsv_parser.py` — CLI entry point; orchestrates file discovery, parsing, InfluxDB writes, config API channel publish, and file moves (to `parsed/` or `error/`). Si InfluxDB est injoignable (`InfluxUnavailableError`), le fichier est marqué `deferred` et **reste en place** pour rejeu au prochain hook ; un `ping()` initial arrête le run sans rien déplacer si InfluxDB est down. Avant traitement, `select_ready_files()` écarte (laisse en place) les fichiers encore en cours d'upload : ouverts par un autre processus (`/proc`) ou sans `END_DATA` final et modifiés depuis moins de `TSV_INCOMPLETE_GRACE_S` s (défaut 600).
- `core.py` — Two concrete parsers (`MV_T302_V002_Parser`, `MV_T302_V003_Parser`) both extending `BaseTSVParser`, selected by `TSVParserFactory`. V003 adds a JSON header block before the data rows.
- `fs_utils.py` — Path component extraction (`bucket/campaign/device_master_sn` from disk path), file discovery, and file moves
- `influx_utils.py` — InfluxDB client setup, bucket creation, point writes, run summary writes
- `backfill_known_channels.py` — script one-shot : peuple la table `known_channels` du config API pour les campagnes ingérées **avant** l'ajout du hook publish dans `tsv_parser.py`. Utilise `schema.tagValues(_field, predicate=campaign=X)` (lent, ~1min) avec timeout de 5min, fallback raw `-7d` si `_1w` vide.

### InfluxDB Schema

- **Bucket raw:** `<company>` — données brutes 1 pt/10 min, une par client
- **Buckets downsamplés:** `<company>_1h`, `<company>_1d`, `<company>_1w` — agrégats `mean` créés automatiquement par `manage_influx_tokens.py`
- **Measurement:** `electrical`
- **Fields:** `<channel_id>_<unit>` (e.g., `M02001171_Ch1_M02001171_V`)
- **Tags:** `campaign`, `channel_id`, `channel_unit`, `channel_label`, `device`, `device_master_sn`, `file_name`
- **Meta bucket:** `powerview_meta` — stores per-run execution summaries

### Downsampling

`manage_influx_tokens.py` (appelé par Ansible `create_grafana_resources.yml`) :
1. Crée les 3 buckets DS `<company>_1h/1d/1w` (idempotent)
2. Crée un **token Grafana unique** couvrant les 4 buckets en lecture (raw + 3 DS) — supprime et recrée le token si périmé (les tokens InfluxDB v2 sont immuables)
3. Crée les **InfluxDB Tasks** de downsampling continu (agrégat `mean`, `aggregateWindow`) si elles n'existent pas
4. Affiche le token sur stdout (capturé par Ansible pour provisionner la datasource Grafana)

`backfill_downsample.py` — rempli les buckets DS à partir de l'historique existant :
```bash
source envs/powerview/bin/activate
export $(grep -v '^#' .env | xargs)

# Backfill complet depuis 2017
python3 backfill_downsample.py --bucket company1

# Plage personnalisée
python3 backfill_downsample.py --bucket company1 --start 2020-01-01 --end 2023-12-31

# Voir les requêtes sans exécuter
python3 backfill_downsample.py --bucket company1 --dry-run
```
Fonctionne par tranches (`30j/1h`, `365j/1d`, `3650j/1w`) pour éviter les timeouts. Idempotent (les points existants sont écrasés).

### Monitoring

Trois couches (voir `docs/monitoring.md`) : UptimeRobot sur les URLs publiques,
`tools/monitoring/stack_watchdog.sh` (cron ubuntu 5 min, dead-man switch
healthchecks.io), `tools/monitoring/check_ingestion.py` (cron sftpgo horaire,
lit le bucket meta). Notifications via ntfy (topic secret dans
`.monitoring.env`, jamais committé — modèle `tools/monitoring/monitoring.env.sample`).

### Disk Layout

```
/srv/sftpgo/data/<company_name>/<campaign_name>/<device_master_sn>/*.tsv
```

### Multi-tenant Grafana

One shared Grafana instance. Per client: **Team** + **Folder** + **Datasource** (`influxdb_<company>`) using a bucket-scoped InfluxDB token + **Dashboard** per campaign. Ansible playbooks are idempotent.

### Environment Variables (`.env`)

| Variable | Required | Default |
|---|---|---|
| `INFLUXDB_HOST` | yes | — |
| `INFLUXDB_ORG` | yes | — |
| `INFLUXDB_ADMIN_TOKEN` | yes | — |
| `GRAFANA_URL` | yes | — (use internal URL, not public reverse-proxy URL) |
| `GRAFANA_USERNAME` | yes | — |
| `GRAFANA_PASSWORD` | yes | — |
| `GRAFANA_API_TOKEN` | yes | — |
| `TSV_META_BUCKET` | no | `powerview_meta` |
| `TSV_LOG_LEVEL` | no | `INFO` |
| `TSV_REPORT_DIR` | no | — |
| `TSV_INCOMPLETE_GRACE_S` | no | `600` — délai pendant lequel un fichier sans `END_DATA` est présumé en cours d'upload et laissé en place |
| `CONFIG_API_URL` | no | — (ex. `http://localhost:8000`) — si défini, `tsv_parser.py` publie les voies au config API après ingestion ; sinon no-op silencieux |

> Point `GRAFANA_URL` to the **internal** service URL to bypass Caddy reverse-proxy rules that block `/api/teams/search` and similar routes.
