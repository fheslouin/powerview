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

* Grafana : http://powerview.adecwatts.fr:8088/ et créer un utilisateur admin.
* SFTPGo : http://powerview.adecwatts.fr:8080/ et créer un utilisateur admin, puis un utilisateur client (par exemple `company1`).

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
  * exporte le dashboard maître Grafana utilisé comme référence (visible dans Grafana : Dashboard -> admin -> Master, UID `adq2j6z`) ;
  * modifie et importe le nouveau dashboard dans le dossier de la Team (en remplaçant type + uid de la datasource par ceux de `influxdb_<company_name>`) ;
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
> Les dashboards Grafana (dashboard maître + dashboards clonés par Ansible)
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

Les **utilisateurs Grafana** sont créés séparément (via l’UI Grafana ou un autre mécanisme), puis rattachés à la team du client :

* tu crées par exemple `user_company1_x` dans Grafana ;
* tu l’ajoutes comme membre de la team `company1` ;
* il hérite alors des droits de la team sur le folder `company1` et ses dashboards.

Le playbook de création (`grafana-automation/playbooks/create_grafana_resources.yml`) :

* ne crée **pas** d’utilisateurs ;
* crée/maintient uniquement : team, folder, datasource `influxdb_<company_name>` (plugin `influxdb-adecwatts-datasource`), dashboards, permissions de la team.

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

Cette section résume le flux complet, de l’upload d’un fichier TSV jusqu’à l’affichage dans Grafana, en tenant compte de la logique de déplacement des fichiers dans les sous‑dossiers `parsed/` et `error/`.

1. **Upload SFTP**
   * Un client dépose un fichier TSV sur le serveur SFTP (SFTPGo).
   * Le fichier est initialement stocké sous la forme :
     `/srv/sftpgo/data/<company>/<campaign>/<device_master_sn>/<fichier>.tsv`.
   * SFTPGo déclenche un hook qui exécute `/srv/powerview/on-upload.sh` avec des variables d’environnement (notamment `SFTPGO_ACTION` et `SFTPGO_ACTION_PATH`).

2. **Script `on-upload.sh` – cas `upload`**
   * Si `SFTPGO_ACTION=upload` :
     * le script active l’environnement virtuel Python et charge le fichier `.env` ;
     * il appelle le parseur Python sur le fichier uploadé :
       ```bash
       python3 /srv/powerview/tsv_parser.py \
         --dataFolder /srv/sftpgo/data \
         --tsvFile "$SFTPGO_ACTION_PATH"
       ```
     * toute la sortie (logs) est écrite dans `/srv/sftpgo/logs/uploads.log`.

3. **Parsing TSV et écriture dans InfluxDB (`tsv_parser.py`)**
   * Le script :
     * déduit `company`, `campaign` et `device_master_sn` à partir du chemin du fichier ;
     * crée le bucket InfluxDB `<company>` s’il n’existe pas encore (`create_bucket_if_not_exists`) ;
     * lit le header du TSV pour détecter le format (actuellement `MV_T302_V002`) ;
     * construit les mappings de canaux (device, numéro de canal, unité, etc.) ;
     * parse toutes les lignes de données et crée des points InfluxDB via `core.BaseTSVParser.parse_data` :
       * measurement = `electrical` ;
       * field = `"<channel_id>_<unit>"` (par ex. `M02001171_Ch1_M02001171_V`) ;
       * tags : `campaign`, `device_sn`, `device_master_sn`, `channel_id`, `channel_name`,
         `channel_label`, `channel_unit`, `device_type`, `device_subtype`, `device`,
         `file_name`, etc. ;
     * écrit les points dans InfluxDB (`write_points`) ;
     * calcule une plage temporelle [start, end] à partir de la colonne timestamp du TSV ;
     * compte les points réellement présents dans InfluxDB pour ce fichier (`count_points_for_file`) et loggue un message de vérification ;
     * génère un rapport JSON d’exécution et un résumé dans un bucket meta (par défaut `powerview_meta`).

4. **Gestion des fichiers après traitement (`parsed/` et `error/`)**
   * La logique de post‑traitement des fichiers est la suivante :
     * si le fichier est **traité avec succès** (parsing + écriture InfluxDB OK) :
       * il est déplacé dans un sous‑dossier `parsed` du device :
         ```text
         /srv/sftpgo/data/<company>/<campaign>/<device_master_sn>/parsed/<fichier>.tsv
         ```
     * si une **erreur** survient pendant le traitement (exception dans le parseur ou l’écriture InfluxDB) :
       * le fichier est déplacé dans un sous‑dossier `error` du device :
         ```text
         /srv/sftpgo/data/<company>/<campaign>/<device_master_sn>/error/<fichier>.tsv
         ```
   * La fonction `find_tsv_files` ignore explicitement les sous‑dossiers `parsed/` et `error/`, ce qui garantit que :
     * seuls les fichiers “bruts” à la racine du device sont (re)traités ;
     * les fichiers déjà traités ou en erreur ne sont pas rescannés.

5. **Script `on-upload.sh` – cas `mkdir`**
   * Si `SFTPGO_ACTION=mkdir` :
     * le script extrait `company` et `campaign` à partir du chemin créé sous `/srv/sftpgo/data` ;
     * si le dossier correspond à un niveau `company/campaign` (et non à un dossier de device), il lance un playbook Ansible :
       ```bash
       ansible-playbook /srv/powerview/grafana-automation/playbooks/create_grafana_resources.yml \
         --extra-vars "company_name=<company> campaign_name=<campaign>"
       ```

6. **Automatisation Grafana (Ansible)**
   * Le playbook `create_grafana_resources.yml` :
     * crée (ou vérifie l’existence de) la team Grafana `<company>` ;
     * crée le folder Grafana `<company>` ;
     * crée une datasource `influxdb_<company>` de type plugin `influxdb-adecwatts-datasource` pointant sur :
       * `INFLUXDB_HOST` (URL),
       * l’organisation `INFLUXDB_ORG`,
       * le bucket par défaut `<company>`,
       * un **token dédié au bucket `<company>`** généré/récupéré par `manage_influx_tokens.py` ;
     * exporte un dashboard maître, le duplique et l’adapte pour la campagne (`title = <campaign>`, datasource mise à jour vers `influxdb_<company>` avec type `influxdb-adecwatts-datasource`) ;
     * importe ce nouveau dashboard dans le folder `<company>` ;
     * applique les permissions (la team `<company>` a accès en lecture au folder et au dashboard) ;
     * crée un fichier `.dashboard.created` dans `/srv/sftpgo/data/<company>/<campaign>/` pour éviter de recréer les ressources à chaque événement.

7. **Visualisation dans Grafana**
   * Les dashboards créés automatiquement utilisent des requêtes (SQL ou Flux selon le plugin) basées sur :
     * le bucket = `<company>` (configuré dans la datasource),
     * le measurement = `electrical`,
     * les tags (`campaign`, `device_master_sn`, `channel_name`, `channel_label`, `channel_unit`, etc.),
     * les fields `"<channel_id>_<unit>"`.
   * Dès que les fichiers TSV sont parsés et injectés, les données deviennent visibles dans Grafana via ces dashboards.

## Divers

### Exemple de requêtes Flux Grafana

Ces exemples supposent un schéma de données basé sur :

- bucket = `<company>`
- measurement = `${__dashboard.name}` (ancien modèle)
- field = `value`

Ils sont conservés ici à titre d’exemple, mais **ne correspondent plus exactement**
au schéma actuel (measurement `electrical`, fields `"<channel_id>_<unit>"`).
Adapte‑les si tu utilises Flux avec le plugin `influxdb-adecwatts-datasource`.

Pour obtenir toutes les voies en variable de dashboard :

```flux
from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "${__dashboard.name}")
  |> filter(fn: (r) => r.unit == "W")
  |> map(fn: (r) => ({
      _field: "SN: " + string(v: r.device) + " - Ch: " + string(v: r.channel_name)
  }))
  |> distinct(column: "_field")
  |> sort(columns: ["_field"])
```

Pour obtenir une série temporelle sur les voies sélectionnées :

```flux
from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "${__dashboard.name}")
  |> map(fn: (r) => ({
      _time: r._time,
      _value: r._value,
      _field: "SN: " + string(v: r.device) + " - Ch: " + string(v: r.channel_name)
  }))
  |> filter(fn: (r) => contains(value: r._field, set: ${channels:json}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> yield(name: "multi_sn_channel")
```
