# PowerView — Plateforme de collecte et visualisation électrique

Pipeline complet de traitement de données électriques TSV :
**SFTPGo → tsv_parser.py → InfluxDB → Grafana** (multi-tenant, automatisé par Ansible).

---

## Contenu du dépôt

```
powerview/
├── tsv_parser.py              # Parseur principal (point d'entrée)
├── core.py                    # Utilitaires de parsing (26 KB)
├── influx_utils.py            # Client InfluxDB
├── manage_influx_tokens.py    # Création de tokens par bucket
├── on-upload.sh               # Hook SFTPGo (upload + mkdir)
├── grafana-automation/
│   ├── playbooks/             # Ansible : create / add-user / delete
│   └── templates/             # Template Jinja dashboard.json.j2
├── powerview_config_api/      # API FastAPI (config séries, partagée avec le plugin)
├── tests/                     # Tests pytest
├── tools/                     # Scripts utilitaires
├── ci/                        # Scripts de déploiement CI
├── data/                      # Données de test TSV
└── docs/                      # Documentation détaillée (voir ci-dessous)
```

---

## Quick start

### Prérequis

```bash
sudo apt install -y python3.13-venv podman podman-compose git
```

### Installation

```bash
git clone https://github.com/fheslouin/powerview.git /srv/powerview
cd /srv/powerview

python3 -m venv envs/powerview
source envs/powerview/bin/activate
pip install -r requirements.txt

cp .env.sample .env
# Éditer .env avec les vraies valeurs (voir section Variables ci-dessous)
```

### Démarrage

```bash
podman compose up -d          # InfluxDB (8086) + Grafana (8088)
```

### Test du parseur

```bash
source envs/powerview/bin/activate

# Dry-run (aucune écriture)
python3 tsv_parser.py --dataFolder /srv/sftpgo/data --dry-run

# Traitement réel
python3 tsv_parser.py --dataFolder /srv/sftpgo/data --tsvFile <chemin>.tsv
```

### Tests automatisés

```bash
source envs/powerview/bin/activate
pytest
```

### Ansible (avant tout playbook)

```bash
cd /srv/powerview
source envs/powerview/bin/activate
export $(grep -v '^#' .env | xargs)

ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
  --extra-vars "company_name=company1 campaign_name=campaign1"
```

---

## Variables d'environnement (`.env`)

| Variable | Obligatoire | Description |
|---|---|---|
| `INFLUXDB_HOST` | oui | URL interne InfluxDB (ex. `http://localhost:8086`) |
| `INFLUXDB_ORG` | oui | Organisation InfluxDB (ex. `powerview`) |
| `INFLUXDB_ADMIN_TOKEN` | oui | Token root InfluxDB (All Access) |
| `GRAFANA_URL` | oui | URL interne Grafana (ex. `http://localhost:8088`) |
| `GRAFANA_USERNAME` | oui | Compte Admin Grafana |
| `GRAFANA_PASSWORD` | oui | Mot de passe Admin Grafana |
| `GRAFANA_API_TOKEN` | oui | Token API Admin Grafana |
| `TSV_META_BUCKET` | non | Bucket de métadonnées (défaut : `powerview_meta`) |
| `TSV_LOG_LEVEL` | non | Niveau de log (défaut : `INFO`) |
| `TSV_REPORT_DIR` | non | Dossier des rapports JSON |

> Pointer `GRAFANA_URL` vers l'URL **interne** du service pour éviter les règles
> du reverse-proxy Caddy sur les routes API (`/api/teams/search`, etc.).

---

## Architecture

```
Client SFTP
    │
    ▼
SFTPGo ──(hooks)──► on-upload.sh
                        │
                        ├── upload  ──► tsv_parser.py
                        │                  ├── InfluxDB (bucket = client, measurement = electrical)
                        │                  ├── rapport JSON
                        │                  └── bucket meta (powerview_meta)
                        │
                        └── mkdir   ──► Ansible: create_grafana_resources.yml
                                            ├── Team + Folder + Datasource
                                            └── Dashboard par campagne
```

**Structure des données sur disque :**

```
/srv/sftpgo/data/<company_name>/<campaign_name>/<device_master_sn>/*.tsv
```

**Schéma InfluxDB :**

- Bucket : un par client (ex. `company1`)
- Measurement : `electrical`
- Fields : `<channel_id>_<unit>` (ex. `M02001171_Ch1_M02001171_V`)
- Tags principaux : `campaign`, `channel_id`, `channel_unit`, `device`, `device_master_sn`, `file_name`

**Modèle Grafana multi-tenant :**

Une seule instance Grafana partagée. Pour chaque client :
- Team + Folder `company_name`
- Datasource `influxdb_<company_name>` (plugin `influxdb-adecwatts-datasource`, token dédié au bucket)
- Dashboards par campagne (Viewer pour la team, Editor pour l'utilisateur propriétaire)

---

## Playbooks Ansible

| Playbook | Rôle |
|---|---|
| `create_grafana_resources.yml` | Crée team, folder, datasource, dashboard, user par défaut |
| `add_grafana_user_to_team.yml` | Ajoute un utilisateur à une team existante avec sa propre datasource |
| `delete_grafana_resources.yml` | Supprime dashboards, folder, datasource, team (pas les buckets InfluxDB) |

---

## Documentation

| Document | Contenu |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Architecture technique et schéma InfluxDB détaillé |
| [`docs/deploiement.md`](docs/deploiement.md) | Déploiement production (Caddy, SFTPGo, UFW, bonnes pratiques) |
| [`docs/deploiement-infra.md`](docs/deploiement-infra.md) | Guide d'infrastructure complète (serveur, DNS, certificats) |
| [`docs/integration-sftpgo-grafana.md`](docs/integration-sftpgo-grafana.md) | Intégration SFTPGo / Ansible / Grafana |
| [`docs/utilisation-parseur.md`](docs/utilisation-parseur.md) | Options et comportement de `tsv_parser.py` |
| [`docs/developpement.md`](docs/developpement.md) | Notes dev, TODOs, contribution |
| [`docs/howtos.md`](docs/howtos.md) | Recettes opérationnelles courantes |
| [`docs/setup-composants.md`](docs/setup-composants.md) | Configuration initiale des composants (InfluxDB, Grafana, SFTPGo) |
| [`docs/acceptance.md`](docs/acceptance.md) | Critères d'acceptance fonctionnels |

---

## Accès en production

| Service | URL |
|---|---|
| Grafana | `https://powerview.adecwatts.fr/` |
| SFTPGo (web) | `https://ftp.powerview.adecwatts.fr/` |
| InfluxDB | `https://db.powerview.adecwatts.fr/` |
| SFTP | port `2022` |
