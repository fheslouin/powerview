# PowerView

## Setup your environnement

Install necessary packages

```shell
sudo apt install python3.13-venv rename podman podman-compose podman-docker
```

Enable podman service

```shell
systemctl --user enable --now podman.socket
```

Verify if service is running

```shell
curl -H "Content-Type: application/json" --unix-socket /var/run/user/$UID/podman/podman.sock http://localhost/_ping
```

Clone this repository

```shell
cd /srv/
git clone https://github.com/fheslouin/powerview.git
```

Create a virtual env

```shell
python3 -m venv /srv/powerview/envs/powerview
```

Enable the virtual env

```shell
source /srv/powerview/envs/powerview/bin/activate
```

Install python dependencies

```shell
pip install -r requirements.txt
```

## Deploy Grafana & InfluxDB

### Create .env file

Copy the file to create a new one and fill it up with your own information

```shell
cp .env.sample .env
```

* Set a `GRAFANA_PASSWORD`
* Set `INFLUXDB_ADMIN_TOKEN` you can retrieve it from the influxdb web interface

### Start Grafana and InfluxDB

```shell
podman compose up -d
```

### Install Caddy on host

#### Add repository and install Caddy package

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

#### Create Caddy config

```shell
cat << EOF | sudo tee /etc/caddy/Caddyfile
{
    # Set your ACME contact email for Let's Encrypt notifications
    email contact@adecwatts.fr
}

ftp.powerview.adecwatts.fr {
    # Reverse proxy to FTP service running on port 8080
    reverse_proxy localhost:8080
}

powerview.adecwatts.fr {
    # Reverse proxy to grafana service running on port 3000
    reverse_proxy localhost:8088
}

db.powerview.adecwatts.fr {
    # Reverse proxy to influxdb service running on port 8086
    reverse_proxy localhost:8086
}
EOF
```

#### Apply changes

Restart Caddy to apply changes

```shell
sudo systemctl restart caddy
```

### Install SFTPGo server

Install the server

```shell
sudo add-apt-repository ppa:sftpgo/sftpgo
sudo apt install sftpgo
 ```

Verify it runs correctly

```shell
systemctl status sftpgo
```

Create a config file to trigger a script each time a TSV file or folder are uploded

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

Restart sftpgo to apply changes

```shell
systemctl restart sftpgo
```

Create a log directory where logs from `on-upload.sh` will be written (nice and easy way to see what is happening each time a file is pushed on the ftp server)

```shell
mkdir /srv/sftpgo/logs
```

Folders and files inside this path `/srv/sftpgo` and `/srv/powerview` must be owned by sftpgo user to works correctly

Set owner on `/srv/`

```shell
chown -R sftpgo:sftpgo /srv/
```

We need also our ubuntu user to have ownership for convenience. To do so we will use `acl`

Install `acl` package

```shell
sudo apt install acl
```

and add ownership for ubuntu user on `/srv` sub-folders

```shell
sudo setfacl -d -R -m u:ubuntu:rwx /srv/
sudo chown -R sftpgo:sftpgo /srv/
```

## Security

Enable firewall and allow traffics over 443 and 2022 ports

```shell
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw limit ssh
sudo ufw allow https
sudo ufw allow 2022
sudo ufw enable
```

Grafana, InfluxDB and FTP Webservice are available behind Caddy reverse proxy on 443
sFTP service is available over 2022 port

## General workflow

We now have Grafana, Influxdb and a FTP server running.

Head to

* Grafana : http://powerview.adecwatts.fr:8088/ and create an admin user.
* Sftpgo : http://powerview.adecwatts.fr:8080/ and create an admin user. Then create a user (it can be "company1")

For each uploaded file, it will be pushed in the tree shown below

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

data / client_name / campaign_name / device_master_sn / \*.tsv files

SFTPGo creates event that are catch each time a file is uploaded or a directory is created.
`on-upload.sh` react on this event and does two things :

* on `upload` it runs the `tsv_parser.py` python script that mainly parse the `.tsv` file and inject the data in influxDB
* on `mkdir` (when the MV2 creates a new campaign directory) it runs an ansible playbook (stored in `grafana-automation`). This playbook will automatically create Grafana resources based on the tree above
  * Create a Team (get from `data / client_name`)
  * Create a folder associated to the Team (get from `data / client_name / campaign_name`)
  * Create an InfluxDB resource connected to the `client_name` database
  * Export the Master Grafana Dashboard used as a reference (can be seen in Grafana in : Dashboard -> admin -> Master)
  * Modify and import the newly created dashboard in the Team folder
  * Set permissions on the dashboard and the team folder for the created Team

Once done, you'll be able to see a new Dashboard created in Grafana based on data imported by the python parser.

## Workflow de traitement des données (détaillé)

Cette section résume le flux complet, de l’upload d’un fichier TSV jusqu’à l’affichage dans Grafana, en tenant compte de la nouvelle logique de déplacement des fichiers dans des sous-dossiers `parsed/` et `error/`.

1. **Upload SFTP**
   * Un client dépose un fichier TSV sur le serveur SFTP (SFTPGo).
   * Le fichier est initialement stocké sous la forme :
     `/srv/sftpgo/data/<company>/<campaign>/<device_master_sn>/<fichier>.tsv`.
   * SFTPGo déclenche un hook qui exécute `/srv/powerview/on-upload.sh` avec des variables d’environnement (notamment `SFTPGO_ACTION` et `SFTPGO_ACTION_PATH`).

2. **Script `on-upload.sh` – cas `upload`**
   * Si `SFTPGO_ACTION=upload` :
     * Le script active l’environnement virtuel Python et charge le fichier `.env`.
     * Il appelle le parseur Python sur le fichier uploadé :
       ```bash
       python3 /srv/powerview/tsv_parser.py \
         --dataFolder /srv/sftpgo/data \
         --tsvFile "$SFTPGO_ACTION_PATH"
       ```
     * Toute la sortie (logs) est écrite dans `/srv/sftpgo/logs/uploads.log`.

3. **Parsing TSV et écriture dans InfluxDB (`tsv_parser.py`)**
   * Le script :
     * déduit `company`, `campaign` et `device_master_sn` à partir du chemin du fichier,
     * crée le bucket InfluxDB `<company>` s’il n’existe pas encore,
     * lit le header du TSV pour détecter le format (actuellement `MV_T302_V002`),
     * construit les mappings de canaux (device, numéro de canal, unité, etc.),
     * parse toutes les lignes de données et crée des points InfluxDB :
       * bucket = `<company>`
       * measurement = `<campaign>`
       * tags : `campaign`, `device_sn`, `device_master_sn`, `channel_id`, `channel_name`, `channel_number`, `channel_type`, `unit`, etc.
     * écrit les points dans InfluxDB,
     * génère un rapport JSON d’exécution et un résumé dans un bucket meta (par défaut `powerview_meta`).

4. **Gestion des fichiers après traitement (`parsed/` et `error/`)**
   * La logique de post-traitement des fichiers est maintenant la suivante :
     * Si le fichier est **traité avec succès** (parsing + écriture InfluxDB OK) :
       * il est déplacé dans un sous-dossier `parsed` du device :
         ```text
         /srv/sftpgo/data/<company>/<campaign>/<device_master_sn>/parsed/<fichier>.tsv
         ```
     * Si une **erreur** survient pendant le traitement (exception dans le parseur ou l’écriture InfluxDB) :
       * le fichier est déplacé dans un sous-dossier `error` du device :
         ```text
         /srv/sftpgo/data/<company>/<campaign>/<device_master_sn>/error/<fichier>.tsv
         ```
   * La fonction `find_tsv_files` ignore explicitement les sous-dossiers `parsed/` et `error/`, ce qui garantit que :
     * seuls les fichiers “bruts” à la racine du device sont (re)traités,
     * les fichiers déjà traités ou en erreur ne sont pas rescannés.

5. **Script `on-upload.sh` – cas `mkdir`**
   * Si `SFTPGO_ACTION=mkdir` :
     * Le script extrait `company` et `campaign` à partir du chemin créé sous `/srv/sftpgo/data`.
     * Si le dossier correspond à un niveau `company/campaign` (et non à un dossier de device), il lance un playbook Ansible :
       ```bash
       ansible-playbook /srv/powerview/grafana-automation/playbooks/create_grafana_resources.yml \
         --extra-vars "company_name=<company> campaign_name=<campaign>"
       ```

6. **Automatisation Grafana (Ansible)**
   * Le playbook `create_grafana_resources.yml` :
     * crée (ou vérifie l’existence de) l’équipe Grafana `<company>`,
     * crée le dossier Grafana `<company>`,
     * crée une datasource InfluxDB `influxdb_<company>` pointant sur le bucket `<company>` (mode Flux),
     * exporte un dashboard maître, le duplique et l’adapte pour la campagne (`title = <campaign>`, datasource mise à jour),
     * importe ce nouveau dashboard dans le dossier `<company>`,
     * applique les permissions (l’équipe `<company>` a accès en lecture au dossier et au dashboard),
     * crée un fichier `.dashboard.created` dans `/srv/sftpgo/data/<company>/<campaign>/` pour éviter de recréer les ressources à chaque événement.

7. **Visualisation dans Grafana**
   * Les dashboards créés automatiquement utilisent des requêtes Flux basées sur :
     * le bucket = `<company>`,
     * le measurement = `<campaign>`,
     * les tags (`device_master_sn`, `channel_name`, `unit`, etc.).
   * Dès que les fichiers TSV sont parsés et injectés, les données deviennent visibles dans Grafana via ces dashboards.

## Divers

### Grafana Flux query

To get all channels as a dashboard variables

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

To get a time series from the selected channels

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
