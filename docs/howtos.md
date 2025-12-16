## How to deploy

### Table des matières

- [1. Pré-requis système](#1-pré-requis-système)
- [2. Activer Podman (mode utilisateur)](#2-activer-podman-mode-utilisateur)
- [3. Sauvegarder l’ancienne version (optionnel)](#3-sauvegarder-lancienne-version-optionnel)
- [4. Récupérer la nouvelle version du code](#4-récupérer-la-nouvelle-version-du-code)
- [5. Créer / recréer l’environnement virtuel Python](#5-créer--recréer-lenvironnement-virtuel-python)
- [6. Mettre à jour le fichier env](#6-mettre-à-jour-le-fichier-env)
- [7. Déployer  mettre à jour InfluxDB  Grafana Podman](#7-déployer--mettre-à-jour-influxdb--grafana-podman)
- [8. Installer  mettre à jour Caddy reverse-proxy-https](#8-installer--mettre-à-jour-caddy-reverse-proxy-https)
  - [8.1 Installer Caddy](#81-installer-caddy)
  - [8.2 Configurer Caddy](#82-configurer-caddy)
- [9. Installer  configurer SFTPGo](#9-installer--configurer-sftpgo)
  - [9.1 Installation](#91-installation)
  - [9.2 Configurer le hook on-uploadsh](#92-configurer-le-hook-on-uploadsh)
  - [9.3 Droits sur srv](#93-droits-sur-srv)
- [10. Sécuriser le serveur UFW](#10-sécuriser-le-serveur-ufw)
- [11. Initialiser Grafana  SFTPGo](#11-initialiser-grafana--sftpgo)
- [12. Tester le parseur manuellement](#12-tester-le-parseur-manuellement)
- [13. Tester lintégration SFTPGo  Ansible  Grafana](#13-tester-lintégration-sftpgo--ansible--grafana)
  - [13.1 Création dune campagne mkdir--ansible](#131-création-dune-campagne-mkdir--ansible)
  - [13.2 Upload dun fichier TSV upload--parseur](#132-upload-dun-fichier-tsv-upload--parseur)
- [14. Vérifier les logs](#14-vérifier-les-logs)
- [15. Bonnes pratiques de déploiement en prod](#15-bonnes-pratiques-de-déploiement-en-prod)

---

### 1. Pré-requis système

Sur un serveur Debian/Ubuntu récent :

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.13-venv rename podman podman-compose podman-docker acl curl git
```

Créer (si besoin) le répertoire de travail :

```bash
sudo mkdir -p /srv
sudo chown "$USER":"$USER" /srv
```


### 2. Activer Podman (mode utilisateur)

Activer le socket utilisateur Podman :

```bash
systemctl --user enable --now podman.socket
```

Vérifier :

```bash
curl -H "Content-Type: application/json" \
     --unix-socket /var/run/user/$UID/podman/podman.sock \
     http://localhost/_ping
```


### 3. Sauvegarder l’ancienne version (optionnel)

Si un dossier `/srv/powerview` existe déjà :

```bash
ssh ubuntu@<ip_serveur>
cd /srv
sudo systemctl stop sftpgo || true   # pour éviter des hooks pendant la mise à jour
mv /srv/powerview /srv/powerview_backup_$(date +%Y%m%d_%H%M%S)
```


### 4. Récupérer la nouvelle version du code

```bash
cd /srv
git clone https://github.com/fheslouin/powerview.git
# ou ton propre remote si tu as un fork
cd powerview
```


### 5. Créer / recréer l’environnement virtuel Python

```bash
python3 -m venv /srv/powerview/envs/powerview
source /srv/powerview/envs/powerview/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```


### 6. Mettre à jour le fichier .env

1. Copier le modèle :

```bash
cd /srv/powerview
cp .env.sample .env
```

2. Éditer `.env` et remettre tes vraies valeurs :

```bash
nano .env
export $(grep -v '^#' .env | xargs)
```

À minima :

- `INFLUXDB_HOST` (ex. `http://localhost:8086`)
- `INFLUXDB_ORG`
- `INFLUXDB_ADMIN_TOKEN`
- `INFLUXDB_USERNAME` / `INFLUXDB_PASSWORD`
- `GRAFANA_URL` (ex. `http://localhost:8088`)
- `GRAFANA_USERNAME`
- `GRAFANA_PASSWORD`

Optionnel :

- `TSV_META_BUCKET` (par défaut `powerview_meta`)
- `TSV_LOG_LEVEL` (par ex. `INFO` ou `DEBUG`)
- `TSV_REPORT_DIR` (si tu veux changer l’emplacement des rapports JSON)


### 7. Déployer / mettre à jour InfluxDB + Grafana (Podman)

Depuis `/srv/powerview` :

```bash
podman compose pull      # optionnel, pour mettre à jour les images
podman compose up -d
```

Vérifier :

- InfluxDB : `http://<serveur>:8086`
- Grafana : `http://<serveur>:8088`

Dans InfluxDB, créer l’org et le token admin si ce n’est pas déjà fait, puis mettre à jour `.env` avec le bon `INFLUXDB_ADMIN_TOKEN`.


### 8. Installer / mettre à jour Caddy (reverse proxy HTTPS)

#### 8.1 Installer Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

#### 8.2 Configurer Caddy

Adapter les domaines à ton environnement :

```bash
cat << EOF | sudo tee /etc/caddy/Caddyfile
{
    # Email de contact pour Let's Encrypt
    email contact@ton-domaine.tld
}

ftp.powerview.ton-domaine.tld {
    # Reverse proxy vers l’interface web SFTPGo (port 8080)
    reverse_proxy localhost:8080
}

powerview.ton-domaine.tld {
    # Reverse proxy vers Grafana (port 8088 dans docker-compose)
    reverse_proxy localhost:8088
}

db.powerview.ton-domaine.tld {
    # Reverse proxy vers InfluxDB (port 8086)
    reverse_proxy localhost:8086
}
EOF
```

Appliquer la configuration :

```bash
sudo systemctl restart caddy
```


### 9. Installer / configurer SFTPGo

#### 9.1 Installation

```bash
sudo add-apt-repository ppa:sftpgo/sftpgo
sudo apt update
sudo apt install -y sftpgo
```

Vérifier :

```bash
systemctl status sftpgo
```


#### 9.2 Configurer le hook on-upload.sh

Créer le fichier d’environnement SFTPGo :

```bash
cat << EOF | sudo tee /etc/sftpgo/sftpgo.env
SFTPGO_COMMON__ACTIONS__EXECUTE_ON=mkdir
SFTPGO_COMMON__ACTIONS__HOOK=/srv/powerview/on-upload.sh
SFTPGO_COMMON__POST_DISCONNECT_HOOK=/srv/powerview/on-upload.sh
SFTPGO_COMMAND__COMMANDS__0__PATH=/srv/powerview/on-upload.sh
SFTPGO_COMMAND__COMMANDS__0__ENV=SFTPGO_ACTION=upload
SFTPGO_COMMAND__COMMANDS__0__HOOK=post_disconnect
EOF
```

Créer le répertoire de logs :

```bash
sudo mkdir -p /srv/sftpgo/logs
```


#### 9.3 Droits sur /srv

Les dossiers `/srv/sftpgo` et `/srv/powerview` doivent appartenir à l’utilisateur `sftpgo`, tout en laissant un accès pratique à l’utilisateur d’admin (ex. `ubuntu`) via ACL :

```bash
sudo chown -R sftpgo:sftpgo /srv/
sudo apt install -y acl
sudo setfacl -d -R -m u:ubuntu:rwx /srv/   # adapter l’utilisateur si besoin
sudo chown -R sftpgo:sftpgo /srv/
```

Redémarrer SFTPGo pour prendre en compte la config :

```bash
sudo systemctl restart sftpgo
```


### 10. Sécuriser le serveur (UFW)

Activer le firewall avec les ports nécessaires :

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw limit ssh
sudo ufw allow https        # 443 pour Caddy
sudo ufw allow 2022/tcp     # port SFTPGo
sudo ufw enable
```


### 11. Initialiser Grafana & SFTPGo

1. Accéder à Grafana via Caddy :

   - `https://powerview.ton-domaine.tld`
   - Créer l’utilisateur admin Grafana (si ce n’est pas déjà fait).
   - Vérifier que tu peux te connecter avec `GRAFANA_USERNAME` / `GRAFANA_PASSWORD` définis dans `.env`.

2. Accéder à SFTPGo :

   - `https://ftp.powerview.ton-domaine.tld`
   - Créer l’admin SFTPGo.
   - Créer un utilisateur SFTP pour un client (ex. `company1`), avec un répertoire racine du type :
     ```
     /srv/sftpgo/data/company1
     ```


### 12. Tester le parseur manuellement

Avant de laisser SFTPGo appeler le script, tu peux tester à la main.

1. Activer l’environnement :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
```

2. Lancer un dry-run sur un dossier de test (par ex. `/srv/sftpgo/data` si tu as déjà des fichiers) :

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --dry-run
```

Tu dois voir des logs dans la console, mais :

- aucune écriture InfluxDB,
- aucun déplacement de fichiers,
- aucun rapport JSON écrit sur disque (le rapport est seulement affiché sur stdout).

3. Lancer un vrai run sur un fichier :

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --tsvFile /srv/sftpgo/data/company1/campaign1/02001084/T302_251012_031720.tsv
```

Après succès :

- le fichier doit être déplacé dans `.../parsed/`,
- les points doivent être écrits dans InfluxDB (bucket `company1`),
- un rapport JSON doit apparaître dans le dossier de rapports (par défaut : `<base_folder>/../logs/reports`, donc typiquement `/srv/sftpgo/logs/reports` si `TSV_REPORT_DIR` n’est pas défini),
- un résumé doit être écrit dans le bucket meta (par défaut `powerview_meta`).


### 13. Tester l’intégration SFTPGo + Ansible + Grafana

#### 13.1 Création d’une campagne (mkdir → Ansible)

Créer (via SFTP ou à la main) un dossier de campagne :

```bash
mkdir -p /srv/sftpgo/data/company1/campaign_test
chown -R sftpgo:sftpgo /srv/sftpgo/data/company1
```

SFTPGo doit déclencher `on-upload.sh` avec `SFTPGO_ACTION=mkdir` → ce script appelle le playbook Ansible :

```bash
ansible-playbook /srv/powerview/grafana-automation/playbooks/create_grafana_resources.yml \
  --extra-vars "company_name=company1 campaign_name=campaign_test"
```

Pour tester à la main :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
  --extra-vars "company_name=company1 campaign_name=campaign_test"
```

Vérifier ensuite dans Grafana :

- la team `company1`,
- le dossier `company1`,
- la datasource `influxdb_company1`,
- un dashboard avec le titre `campaign_test` dans le dossier `company1`.


#### 13.2 Upload d’un fichier TSV (upload → parseur)

Depuis un client SFTP, uploader un fichier TSV dans :

```text
/srv/sftpgo/data/<company>/<campaign>/<device_master_sn>/<fichier>.tsv
```

Par exemple :

```text
/srv/sftpgo/data/company1/campaign_test/02001084/T302_251012_031720.tsv
```

SFTPGo déclenche `on-upload.sh` avec `SFTPGO_ACTION=upload` (via le hook `post_disconnect`), qui :

- active l’environnement virtuel Python,
- charge `.env`,
- appelle `tsv_parser.py` avec :
  - `--dataFolder /srv/sftpgo/data`
  - `--tsvFile "$SFTPGO_ACTION_PATH"`

Après succès :

- le fichier est déplacé dans `.../parsed/`,
- les points sont écrits dans InfluxDB (bucket = `<company>`, measurement = `<campaign>`),
- un rapport JSON est écrit,
- un résumé d’exécution est envoyé dans le bucket meta (`TSV_META_BUCKET`).


### 14. Vérifier les logs

- Logs SFTPGo :
  ```bash
  journalctl -u sftpgo -f
  ```

- Logs du hook / parseur (stdout + stderr redirigés) :
  ```bash
  tail -f /srv/sftpgo/logs/uploads.log
  ```

- Rapports JSON du parseur (si `TSV_REPORT_DIR` non défini) :
  ```bash
  ls -l /srv/sftpgo/logs/reports
  ```


### 15. Bonnes pratiques de déploiement en prod

- **Toujours déployer depuis un tag git** (ex. `v0.2.5`) pour savoir exactement quelle version tourne.
- **Ne jamais commiter `.env`** : le garder uniquement sur le serveur, avec des sauvegardes chiffrées si besoin.
- **Surveiller la taille des logs** (`/srv/sftpgo/logs/uploads.log`, rapports JSON) et mettre en place une rotation (logrotate) si nécessaire.
- **Tester en dry-run** (`--dry-run`) avant chaque grosse mise à jour pour vérifier que la structure des dossiers et des fichiers TSV est toujours conforme.
- **Documenter les buckets InfluxDB** (un bucket par client) et les dashboards Grafana associés pour faciliter le support et le debug.
