# PowerView

PowerView est une chaîne complète de collecte et de visualisation de données
électriques à partir de fichiers TSV :

- Upload des fichiers via **SFTPGo**
- Parsing automatique par un script Python (`tsv_parser.py`)
- Stockage des mesures dans **InfluxDB**
- Création automatique des ressources **Grafana**
- Accès multi‑tenant : une seule instance Grafana partagée entre plusieurs clients

Toute la documentation détaillée (déploiement, intégration, architecture, etc.)
est désormais déplacée dans le dossier [`docs/`](docs/).

---

## Fonctionnalités principales

- Détection automatique des fichiers `.tsv` uploadés via SFTPGo.
- Parsing robuste (validation des timestamps / valeurs, rapports d’erreurs).
- Écriture dans InfluxDB avec un schéma unifié :
  - bucket = client ;
  - measurement unique : `electrical` ;
  - un field par canal, nommé `<channel_id>_<unit>` (ex. `M02001171_Ch1_M02001171_V`) ;
  - tags principaux : `campaign`, `channel_id`, `channel_unit`, `channel_label`,
    `channel_name`, `device`, `device_type`, `device_subtype`,
    `device_master_sn`, `device_sn`, `file_name`, etc.
- Création automatique dans Grafana :
  - team par client ;
  - folder par client ;
  - datasource InfluxDB dédiée par client (plugin custom `influxdb-adecwatts-datasource`,
    token InfluxDB dédié au bucket) ;
  - dashboard par campagne.
- Mode **dry‑run** pour tester sans rien écrire ni déplacer.
- Déploiement via **Podman** (InfluxDB + Grafana).
- Intégration avec **Caddy** pour l’accès HTTPS.
- Intégration avec **SFTPGo** (hooks `upload` / `mkdir`).

Les dashboards Grafana (template Jinja + dashboards importés par Ansible)
doivent être construits pour ce schéma (measurement `electrical`,
champs nommés par canal), et non plus sur l’ancien modèle
“measurement = nom de campagne, field = value”.

---

## Installation rapide (environnement de test)

Sur un serveur Debian/Ubuntu récent :

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.13-venv rename podman podman-compose podman-docker acl curl git
sudo mkdir -p /srv
sudo chown "$USER":"$USER" /srv
```

Cloner le dépôt et préparer l’environnement Python :

```bash
cd /srv/
git clone https://github.com/fheslouin/powerview.git
cd powerview

python3 -m venv /srv/powerview/envs/powerview
source /srv/powerview/envs/powerview/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

Préparer le fichier `.env` :

```bash
cp .env.sample .env
# puis éditer .env avec tes vraies valeurs
```

Variables minimales à définir :

- `INFLUXDB_HOST` (URL InfluxDB accessible depuis le parseur et Ansible, ex. `http://localhost:8086`)
- `INFLUXDB_ORG` (nom de l’organisation InfluxDB, ex. `powerview`)
- `INFLUXDB_ADMIN_TOKEN` (**token root** InfluxDB, All Access)
- `INFLUXDB_USERNAME` / `INFLUXDB_PASSWORD`
- `GRAFANA_URL` (URL Grafana accessible depuis le parseur et Ansible, ex. `http://localhost:8088`)
- `GRAFANA_USERNAME`
- `GRAFANA_PASSWORD`

Optionnel :

- `GRAFANA_API_TOKEN` (token API Grafana avec rôle Admin, utilisé pour certains appels REST `/api/*`)
- `TSV_META_BUCKET` (par défaut `powerview_meta`)
- `TSV_LOG_LEVEL` (par ex. `INFO` ou `DEBUG`)
- `TSV_REPORT_DIR` (pour changer l’emplacement des rapports JSON)

> Remarque importante :
>
> - Pour les **scripts internes** (parseur, Ansible), il est recommandé de
>   pointer `GRAFANA_URL` vers l’URL interne du service Grafana, par exemple :
>
>   ```env
>   GRAFANA_URL='http://localhost:8088'
>   ```
>
>   Cela évite de dépendre du reverse‑proxy (Caddy) et des éventuelles règles
>   d’authentification HTTP qui peuvent bloquer certaines routes API
>   (`/api/teams`, `/api/teams/search`, etc.).
>
> - Pour les **utilisateurs humains**, l’accès se fait via Caddy en HTTPS :
>   `https://powerview.adecwatts.fr`.

> Remarque sur les tokens InfluxDB :
>
> - `INFLUXDB_ADMIN_TOKEN` reste le **token root partagé** utilisé :
>   - par le parseur TSV (`tsv_parser.py`) pour créer les buckets et écrire les points,
>   - par la configuration de la CLI `influx` (profil root) pour permettre à
>     `manage_influx_tokens.py` de créer des authorizations.
> - le playbook Ansible `create_grafana_resources.yml` n’utilise plus directement
>   `INFLUXDB_ADMIN_TOKEN` dans les datasources Grafana : il appelle
>   `manage_influx_tokens.py` pour créer/récupérer un **token dédié par bucket**
>   (`powerview_token_for_bucket_<company>`), qui est ensuite injecté dans la
>   datasource `influxdb_<company>`.

> ⚠️ **Rappel important pour Ansible**
>
> Avant **toute** commande `ansible-playbook`, il faut :
>
> ```bash
> cd /srv/powerview
> source envs/powerview/bin/activate
> export $(grep -v '^#' .env | xargs)
> ```
>
> Sans cette séquence, les variables `GRAFANA_*` et `INFLUXDB_*` ne seront pas
> présentes dans l’environnement, et les playbooks échoueront (erreurs sur
> `GRAFANA_URL`, `GRAFANA_USERNAME`, `INFLUXDB_HOST`, etc.).

Démarrer InfluxDB et Grafana (via Podman) :

```bash
podman compose up -d
```

Cela démarre au minimum :

- InfluxDB (port 8086 dans le compose)
- Grafana (port 8088 dans le compose)

Pour un guide de déploiement complet (Caddy, SFTPGo, firewall, bonnes pratiques) :  
→ voir [`docs/deploiement.md`](docs/deploiement.md)

---

## Premier test du parseur

Une fois l’environnement virtuel activé :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
```

Dry‑run sur un dossier de données (aucune écriture InfluxDB, aucun déplacement de fichiers, aucun rapport JSON sur disque) :

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --dry-run
```

Traitement réel d’un fichier TSV :

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --tsvFile /srv/sftpgo/data/company3/campaign/02001084/T302_251012_031720.tsv
```

Après succès :

- le fichier est déplacé dans `.../parsed/` (ou `.../error/` en cas d’échec),
- les points sont écrits dans InfluxDB (bucket = `<company_name>`, measurement = `electrical`),
- un rapport JSON est généré dans le dossier de rapports (par défaut `<base_folder>/../logs/reports`,
  donc typiquement `/srv/sftpgo/logs/reports` si `TSV_REPORT_DIR` n’est pas défini),
- un résumé d’exécution est envoyé dans le bucket meta (par défaut `powerview_meta`).

Pour plus de détails sur les options et le comportement du parseur :  
→ [`docs/utilisation-parseur.md`](docs/utilisation-parseur.md)

---

## Tests

Ce projet est un projet **Python**, pas un projet Node/JS : il n’y a pas de `package.json`,
donc les commandes du type `yarn test` ou `npm test` ne sont pas pertinentes ici.

Pour lancer les tests automatisés (par exemple ceux de `tests/test_tsv_parser.py`) :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
pytest
```

Si `pytest` n’est pas installé globalement, il est déjà inclus dans l’environnement virtuel
via `requirements.txt` (ou à ajouter si besoin).

---

## Architecture et workflow

### Vue d’ensemble

Flux global :

```text
Client SFTP
    |
    v
SFTPGo  --(hooks)-->  on-upload.sh
                          |
                          +-- SFTPGO_ACTION=upload --> tsv_parser.py
                          |                               |
                          |                               +--> InfluxDB (données)
                          |                               +--> rapports JSON + bucket meta
                          |
                          +-- SFTPGO_ACTION=mkdir  --> Ansible (create_grafana_resources.yml)
                                                          |
                                                          +--> Team, folder, datasource, dashboards Grafana
```

Arborescence attendue pour les données :

```text
/srv/sftpgo/data/<company_name>/<campaign_name>/<device_master_sn>/<fichier>.tsv
```

Exemple :

```text
├── /srv/sftpgo/data/
│   ├── company1
│   │   └── campaign1
│   │       └── 02001084
│   │           ├── T302_251012_031720.tsv
│   │           ├── T302_251013_031720.tsv
│   │           ├── T302_251014_031719.tsv
│   │           ├── T302_251015_031739.tsv
│   │           ├── T302_251016_031719.tsv
│   │           ├── T302_251017_031740.tsv
│   │           ├── T302_251018_031739.tsv
│   │           ├── T302_251019_031739.tsv
│   │           ├── T302_251020_031500.tsv
│   │           └── T302_251021_031740.tsv
│   └── compagny2
│       └── capaign23
│           └── 02001084
│               └── T302_251021_031740.tsv
```

Schéma logique :

```text
data / client_name / campaign_name / device_master_sn / *.tsv
```

Rôle de `on-upload.sh` :

- sur `upload` :
  - déclenché par SFTPGo avec `SFTPGO_ACTION=upload` (hook `post_disconnect`) ;
  - active le venv Python, charge `.env` ;
  - appelle `tsv_parser.py` avec :
    - `--dataFolder /srv/sftpgo/data`
    - `--tsvFile "$SFTPGO_ACTION_PATH"` ;
  - le parseur :
    - parse le fichier TSV,
    - crée le bucket InfluxDB si besoin,
    - écrit les points dans InfluxDB,
    - déplace le fichier (`parsed/` ou `error`),
    - écrit un rapport JSON,
    - écrit un résumé d’exécution dans le bucket meta.

- sur `mkdir` :
  - déclenché par SFTPGo avec `SFTPGO_ACTION=mkdir` ;
  - extrait `company_name` et `campaign_name` à partir du chemin relatif à `/srv/sftpgo/data` ;
  - si le dossier est au niveau `company/campaign` (et non `device`), appelle le playbook Ansible
    `grafana-automation/playbooks/create_grafana_resources.yml` avec :
    - `--extra-vars "company_name=<company> campaign_name=<campaign>"` ;
  - ce playbook crée/maintient les ressources Grafana pour ce couple (voir ci‑dessous).

Pour une description détaillée de l’architecture et du schéma InfluxDB :  
→ [`docs/architecture.md`](docs/architecture.md)

---

## Grafana multi‑tenant et automatisation

PowerView utilise **une seule instance Grafana** partagée entre tous les clients.
L’isolation se fait via des ressources logiques créées automatiquement par Ansible.

Pour chaque client (`company_name`) :

- une **team Grafana** : `company_name` ;
- un **folder Grafana** : `company_name` ;
- une **datasource InfluxDB dédiée** : `influxdb_<company_name>`
  - type de plugin : `influxdb-adecwatts-datasource` ;
  - bucket par défaut : `<company_name>` ;
  - **token InfluxDB dédié** : token spécifique au bucket `<company_name>`
    (`powerview_token_for_bucket_<company_name>`) créé/récupéré par
    `manage_influx_tokens.py` ;
- un ou plusieurs **dashboards** (un par campagne) dans le folder `company_name` ;
- des **permissions** qui donnent à la team `company_name` un accès *viewer* au folder et aux dashboards.

Schéma simplifié :

```text
Instance Grafana unique
    |
    +-- Team company1
    |      |
    |      +-- Folder "company1"
    |      |       |
    |      |       +-- Datasource "influxdb_company1"
    |      |       |      - plugin: influxdb-adecwatts-datasource
    |      |       |      - bucket: company1
    |      |       |      - token: powerview_token_for_bucket_company1
    |      |       |
    |      |       +-- Dashboards: campaign1, campaign2, ...
    |
    +-- Team company2
           |
           +-- Folder "company2"
                   |
                   +-- Datasource "influxdb_company2"
                   |      - plugin: influxdb-adecwatts-datasource
                   |      - bucket: company2
                   |      - token: powerview_token_for_bucket_company2
                   |
                   +-- Dashboards: campaignA, campaignB, ...
```

Les **utilisateurs Grafana** peuvent être gérés de deux façons :

- soit manuellement via l’UI Grafana, puis rattachés à la team du client ;
- soit automatiquement via le playbook de création, qui crée un utilisateur
  `{{ company_name }} Default` (login par défaut `user_{{ company_name }}`) et
  l’ajoute à la team `{{ company_name }}`.

Le playbook de création (`grafana-automation/playbooks/create_grafana_resources.yml`) :

- crée/maintient : team, folder, datasource `influxdb_<company_name>` (plugin `influxdb-adecwatts-datasource`), dashboards, permissions de la team ;
- crée également un utilisateur “par défaut de la team” (sauf si tu surcharges les
  variables pour utiliser un compte existant) ;
- s’appuie sur `manage_influx_tokens.py` pour créer/récupérer un token InfluxDB
  dédié par bucket.

Le playbook de suppression (`grafana-automation/playbooks/delete_grafana_resources.yml`) permet, lui, de :

- supprimer les dashboards et le folder d’un client ;
- supprimer la datasource `influxdb_<company_name>` ;
- supprimer la team associée ;
- et, si tu le souhaites, supprimer aussi un utilisateur Grafana donné.  
  (Les buckets InfluxDB et les données ne sont pas supprimés.)

Pour plus de détails sur l’automatisation Grafana et l’intégration SFTPGo/Ansible :  
→ [`docs/integration-sftpgo-grafana.md`](docs/integration-sftpgo-grafana.md)

---

## Accès et sécurité

Une fois le déploiement effectué :

- Grafana : `https://powerview.adecwatts.fr/` (via Caddy)
- SFTPGo (interface web) : `https://ftp.powerview.adecwatts.fr/`
- InfluxDB : `https://db.powerview.adecwatts.fr/` (via Caddy)

Le service SFTP est accessible sur le port `2022` (configuré dans SFTPGo, ouvert dans UFW).

Le firewall UFW est typiquement configuré ainsi :

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw limit ssh
sudo ufw allow https        # 443 pour Caddy
sudo ufw allow 2022/tcp     # port SFTPGo
sudo ufw enable
```

Pour la configuration détaillée de Caddy, SFTPGo, les ACL sur `/srv`, etc. :  
→ [`docs/deploiement.md`](docs/deploiement.md)

---

## Bonnes pratiques et TODO techniques

Bonnes pratiques de déploiement en production (détaillées dans la doc) :

- Déployer depuis un **tag git** (ex. `v0.3.2`) pour savoir exactement quelle version tourne.
- Ne jamais committer `.env` : le garder uniquement sur le serveur.
- Surveiller la taille des logs (`/srv/sftpgo/logs/uploads.log`, rapports JSON) et mettre en place une rotation.
- Tester en **dry‑run** (`--dry-run`) avant chaque grosse mise à jour.
- Documenter les buckets InfluxDB (un bucket par client) et les dashboards Grafana associés.

TODO techniques (à détailler dans `docs/developpement.md`) :

- vérifier que l’upload du fichier est bien complet avant de le traiter ;
- copier le fichier à son arrivée dans le FTP (dans un répertoire technique) et traiter uniquement la copie ;
- API‑CONFIG :
  - logs légers (dernier parsing réussi / 10 derniers rapports d’erreur) ;
  - ajouter un nouvel utilisateur Grafana/config pour une team donnée
    (comment automatiser, injecter `grafana_user_name` dans la requête GET config) ;
- DATA : un device pourrait migrer à travers les campagnes ;
- dashboard : à enrichir / factoriser.

---

## Documentation détaillée

Toute la documentation détaillée est dans le dossier [`docs/`](docs/) :

- Déploiement complet (prod) : [`docs/deploiement.md`](docs/deploiement.md)
- Utilisation du parseur TSV : [`docs/utilisation-parseur.md`](docs/utilisation-parseur.md)
- Intégration SFTPGo / Ansible / Grafana : [`docs/integration-sftpgo-grafana.md`](docs/integration-sftpgo-grafana.md)
- Architecture technique et schéma InfluxDB : [`docs/architecture.md`](docs/architecture.md)
- Notes pour développeurs / TODO : [`docs/developpement.md`](docs/developpement.md)

---

## Licence et contact

- Auteur : Adecwatts  
- Contact : support@adecwatts.com  
- Licence : à préciser selon ton choix (MIT, Apache‑2.0, propriétaire, etc.).
