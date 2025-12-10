# PowerView

## Préparer ton environnement

Installer les paquets nécessaires :

```shell
sudo apt install python3.13-venv rename podman podman-compose podman-docker
```

Activer le service Podman (mode utilisateur) :

```shell
systemctl --user enable --now podman.socket
```

Vérifier que le service tourne :

```shell
curl -H "Content-Type: application/json" --unix-socket /var/run/user/$UID/podman/podman.sock http://localhost/_ping
```

Cloner ce dépôt :

```shell
cd /srv/
git clone https://github.com/fheslouin/powerview.git
```

Créer un environnement virtuel Python :

```shell
python3 -m venv /srv/powerview/envs/powerview
```

Activer l’environnement virtuel :

```shell
source /srv/powerview/envs/powerview/bin/activate
```

Installer les dépendances Python :

```shell
pip install -r requirements.txt
```

## Déployer Grafana & InfluxDB

### Créer le fichier .env

Copier le modèle et le remplir avec tes propres valeurs :

```shell
cp .env.sample .env
```

À minima :

* Définir un `GRAFANA_PASSWORD`
* Définir `INFLUXDB_ADMIN_TOKEN` **avec un token All Access (token root) de ton instance InfluxDB**
  * ce token est utilisé :
    * par le parseur TSV (`tsv_parser.py`) pour créer les buckets et écrire les points ;
    * par la CLI `influx` (config root) utilisée par `manage_influx_tokens.py` pour créer les tokens dédiés par bucket.
* Vérifier / ajuster :
  * `INFLUXDB_HOST` (URL InfluxDB accessible depuis le parseur et Ansible, ex. `http://localhost:8086`)
  * `INFLUXDB_ORG` (nom de l’organisation InfluxDB, ex. `powerview`)
  * `GRAFANA_URL`, `GRAFANA_USERNAME`, `GRAFANA_PASSWORD`

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

> Remarque : le modèle actuel combine les deux approches :
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
>
> Les détails sont dans `grafana-automation/README.md`.

### Démarrer Grafana et InfluxDB

Depuis la racine du projet (`/srv/powerview`) :

```shell
podman compose up -d
```

Cela démarre au minimum :

- InfluxDB (port 8086 dans le compose)
- Grafana (port 8088 dans le compose)

## Installer Caddy sur l’hôte

### Ajouter le dépôt et installer le paquet Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

### Créer la configuration Caddy

```shell
cat << EOF | sudo tee /etc/caddy/Caddyfile
{
    # Adresse email de contact pour Let's Encrypt
    email contact@adecwatts.fr
}

ftp.powerview.adecwatts.fr {
    # Reverse proxy vers le service FTP (port 8080)
    reverse_proxy localhost:8080
}

powerview.adecwatts.fr {
    # Reverse proxy vers Grafana (port 8088 dans podman-compose)
    reverse_proxy localhost:8088
}

db.powerview.adecwatts.fr {
    # Reverse proxy vers InfluxDB (port 8086)
    reverse_proxy localhost:8086
}
EOF
```

### Appliquer la configuration

Redémarrer Caddy :

```shell
sudo systemctl restart caddy
```

## Installer le serveur SFTPGo

Installer le serveur :

```shell
sudo add-apt-repository ppa:sftpgo/sftpgo
sudo apt install sftpgo
```

Vérifier qu’il tourne correctement :

```shell
systemctl status sftpgo
```

Créer un fichier de configuration pour déclencher un script à chaque upload de fichier TSV ou création de dossier :

```shell
cat << EOF | sudo tee /etc/sftpgo/sftpgo.env
SFTPGO_COMMON__ACTIONS__EXECUTE_ON=mkdir
SFTPGO_COMMON__ACTIONS__HOOK=/srv/powerview/on-upload.sh
SFTPGO_COMMON__POST_DISCONNECT_HOOK=/srv/powerview/on-upload.sh
SFTPGO_COMMAND__COMMANDS__0__PATH=/srv/powerview/on-upload.sh
SFTPGO_COMMAND__COMMANDS__0__ENV=SFTPGO_ACTION=upload
SFTPGO_COMMAND__COMMANDS__0__HOOK=post_disconnect
EOF
```

Redémarrer SFTPGo pour appliquer les changements :

```shell
systemctl restart sftpgo
```

Créer un répertoire de logs où `on-upload.sh` écrira ses logs (pratique pour voir ce qui se passe à chaque upload) :

```shell
mkdir /srv/sftpgo/logs
```

Les dossiers et fichiers sous `/srv/sftpgo` et `/srv/powerview` doivent appartenir à l’utilisateur `sftpgo` pour que tout fonctionne correctement.

Définir le propriétaire de `/srv/` :

```shell
chown -R sftpgo:sftpgo /srv/
```

On veut aussi que l’utilisateur `ubuntu` (ou un autre admin) ait des droits pratiques. Pour cela on utilise `acl`.

Installer le paquet `acl` :

```shell
sudo apt install acl
```

Ajouter des ACL par défaut pour l’utilisateur `ubuntu` sur les sous-dossiers de `/srv` :

```shell
sudo setfacl -d -R -m u:ubuntu:rwx /srv/
sudo chown -R sftpgo:sftpgo /srv/
```

## Sécurité

Activer le firewall et autoriser le trafic sur les ports 443 et 2022 :

```shell
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw limit ssh
sudo ufw allow https
sudo ufw allow 2022
sudo ufw enable
```

Grafana, InfluxDB et l’interface web SFTPGo sont accessibles derrière Caddy sur le port 443.  
Le service SFTP est accessible sur le port 2022.

## Workflow général

À ce stade, Grafana, InfluxDB et le serveur FTP (SFTPGo) sont en place.

Accéder à :

* Grafana : https://powerview.adecwatts.fr/ et créer un utilisateur admin.
* SFTPGo : https://ftp.powerview.adecwatts.fr/ et créer un utilisateur admin, puis un utilisateur client (par exemple `company1`).

Pour chaque fichier uploadé, l’arborescence cible est la suivante :

```bash
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

`data / client_name / campaign_name / device_master_sn / *.tsv`

SFTPGo génère des événements à chaque upload de fichier ou création de dossier.  
`on-upload.sh` réagit à ces événements et fait deux choses :

* sur `upload` : il lance le script Python `tsv_parser.py` qui parse le fichier `.tsv` et injecte les données dans InfluxDB ;
* sur `mkdir` (quand le MV2 crée un nouveau dossier de campagne) : il lance un playbook Ansible (dans `grafana-automation`) qui crée automatiquement les ressources Grafana à partir de l’arborescence ci‑dessus :
  * crée une Team (à partir de `data / client_name`) ;
  * crée un dossier associé à la Team (à partir de `data / client_name / campaign_name`) ;
  * crée une **datasource InfluxDB par client** dans Grafana :
    * nom : `influxdb_<company_name>` ;
    * type de plugin : `influxdb-adecwatts-datasource` (plugin custom défini par `plugin.json`) ;
    * bucket par défaut : `<company_name>` dans InfluxDB ;
    * **token InfluxDB dédié au bucket** : généré/récupéré par `manage_influx_tokens.py`
      (`powerview_token_for_bucket_<company_name>`) et injecté dans la datasource ;
  * rend un **template de dashboard** (`grafana-automation/templates/dashboard.json.j2`)
    avec `company_name`, `campaign_name` et le `datasource_uid` de
    `influxdb_<company_name>`, puis importe ce JSON comme dashboard Grafana ;
  * applique les permissions sur le dashboard et le dossier pour la Team créée.

> Important : le parseur écrit actuellement les points dans un **measurement unique**
> nommé `electrical`, avec un **field par canal** :
>
> - measurement : `electrical`
> - field : `"<channel_id>_<unit>"` (par ex. `M02001171_Ch1_M02001171_V`)
> - tags principaux : `campaign`, `channel_id`, `channel_unit`, `channel_label`,
>   `channel_name`, `device`, `device_type`, `device_subtype`,
>   `device_master_sn`, `device_sn`, `file_name`, etc.
>
> Les dashboards Grafana (template Jinja + dashboards importés par Ansible)
> doivent donc être construits pour ce schéma (measurement `electrical`,
> champs nommés par canal), et non plus sur l’ancien modèle
> “measurement = nom de campagne, field = value”.

Une fois ces étapes terminées, un nouveau dashboard apparaît dans Grafana, basé sur les données importées par le parseur Python.

### Grafana multi‑tenant : comment les clients sont isolés

PowerView utilise **une seule instance Grafana** partagée entre tous les clients.  
L’isolation se fait via des ressources logiques créées automatiquement par Ansible :

Pour chaque client (`company_name`) :

* une **team Grafana** : `company_name` ;
* un **folder Grafana** : `company_name` ;
* une **datasource InfluxDB dédiée** : `influxdb_<company_name>`
  * type de plugin : `influxdb-adecwatts-datasource` ;
  * bucket par défaut : `<company_name>` ;
  * **token InfluxDB dédié** : token spécifique au bucket `<company_name>`
    (`powerview_token_for_bucket_<company_name>`) créé/récupéré par
    `manage_influx_tokens.py` ;
* un ou plusieurs **dashboards** (un par campagne) dans le folder `company_name` ;
* des **permissions** qui donnent à la team `company_name` un accès *viewer* au folder et aux dashboards.

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

* soit manuellement via l’UI Grafana, puis rattachés à la team du client ;
* soit automatiquement via le playbook de création, qui crée un utilisateur
  `{{ company_name }} Admin` (login `admin_{{ company_name }}` par défaut) et
  l’ajoute à la team `{{ company_name }}`.

Le playbook de création (`grafana-automation/playbooks/create_grafana_resources.yml`) :

* crée/maintient : team, folder, datasource `influxdb_<company_name>` (plugin `influxdb-adecwatts-datasource`), dashboards, permissions de la team ;
* crée également un utilisateur “admin de la team” (sauf si tu surcharges les
  variables pour utiliser un compte existant).

Le playbook de suppression (`grafana-automation/playbooks/delete_grafana_resources.yml`) permet, lui, de :

* supprimer les dashboards et le folder d’un client ;
* supprimer la datasource `influxdb_<company_name>` ;
* supprimer la team associée ;
* et, si tu le souhaites, supprimer aussi un utilisateur Grafana donné.  
  (Les buckets InfluxDB et les données ne sont pas supprimés.)

Pour plus de détails sur l’automatisation Grafana, voir :

```text
grafana-automation/README.md
```

## Workflow de traitement des données (détaillé)

[...] (inchangé)
