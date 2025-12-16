# Déploiement complet de PowerView

Ce document décrit un **guide de déploiement complet** de PowerView sur un
serveur Debian/Ubuntu récent, en environnement de pré‑prod ou de prod.

Il reprend et détaille les grandes étapes résumées dans `README.md` et `HOWTOS.md`.

---

## 1. Vue d’ensemble de l’architecture

PowerView s’appuie sur les briques suivantes :

- **SFTPGo**  
  - Fournit un service SFTP multi‑utilisateurs.  
  - Déclenche des hooks (`on-upload.sh`) lors des événements `mkdir` et `upload`.

- **Script Python `tsv_parser.py`**  
  - Parse les fichiers TSV uploadés.  
  - Écrit les mesures dans **InfluxDB**.  
  - Génère des rapports JSON et un résumé d’exécution dans un bucket meta.

- **InfluxDB**  
  - Stocke les mesures électriques (un bucket par client).  
  - Stocke aussi un bucket meta (`TSV_META_BUCKET`, par défaut `powerview_meta`).

- **Grafana**  
  - Visualise les données InfluxDB.  
  - Une seule instance partagée entre tous les clients (multi‑tenant).

- **Ansible + scripts Python**  
  - Création automatique des ressources Grafana (team, folder, datasource, dashboard principal du client).  
  - Gestion des tokens InfluxDB dédiés par bucket (`manage_influx_tokens.py`).

- **Caddy**  
  - Reverse‑proxy HTTPS devant Grafana, InfluxDB et SFTPGo.

- **Podman**  
  - Héberge InfluxDB et Grafana via `podman compose`.

Arborescence principale sur le serveur :

```text
/srv/
  ├── powerview/          # code de l’application + env Python + docker-compose
  └── sftpgo/
      ├── data/           # données SFTP (TSV)
      └── logs/           # logs du hook / rapports JSON
```

---

## 2. Pré‑requis système

Sur un serveur Debian/Ubuntu récent, connecté à Internet :

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.13-venv rename podman podman-compose podman-docker acl curl git
```

Créer le répertoire de travail :

```bash
sudo mkdir -p /srv
sudo chown "$USER":"$USER" /srv
```

> Remarque : adapte `$USER` si tu déploies avec un autre compte (ex. `ubuntu`).

---

## 3. Récupération du code et environnement Python

### 3.1 Cloner le dépôt

```bash
cd /srv
git clone https://github.com/fheslouin/powerview.git
# ou ton propre remote si tu as un fork
cd powerview
```

Pour un déploiement en prod, il est recommandé de se placer sur un **tag** :

```bash
git fetch --tags
git checkout v0.3.2   # par exemple
```

### 3.2 Créer l’environnement virtuel

```bash
python3 -m venv /srv/powerview/envs/powerview
source /srv/powerview/envs/powerview/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

> Astuce : ajoute la commande `source /srv/powerview/envs/powerview/bin/activate`
> dans ton `.bashrc` si tu travailles souvent sur ce serveur.

---

## 4. Configuration de l’environnement (`.env`)

### 4.1 Création du fichier `.env`

Depuis `/srv/powerview` :

```bash
cp .env.sample .env
nano .env
```

Variables minimales à renseigner :

- `INFLUXDB_HOST`  
  URL InfluxDB accessible depuis le parseur et Ansible, ex. :
  ```env
  INFLUXDB_HOST=http://localhost:8086
  ```

- `INFLUXDB_ORG`  
  Nom de l’organisation InfluxDB, ex. :
  ```env
  INFLUXDB_ORG=powerview
  ```

- `INFLUXDB_ADMIN_TOKEN`  
  Token **root** InfluxDB (All Access). Il sera utilisé :
  - par `tsv_parser.py` pour créer les buckets et écrire les points ;
  - par la CLI `influx` (profil root) pour permettre à `manage_influx_tokens.py`
    de créer des tokens dédiés par bucket.

- `INFLUXDB_USERNAME` / `INFLUXDB_PASSWORD`  
  Compte admin InfluxDB (utilisé pour la configuration initiale, si besoin).

- `GRAFANA_URL`  
  URL Grafana **interne** (sans passer par Caddy), ex. :
  ```env
  GRAFANA_URL=http://localhost:8088
  ```

- `GRAFANA_USERNAME` / `GRAFANA_PASSWORD`  
  Compte admin Grafana (utilisé par Ansible pour créer les ressources).

Variables optionnelles :

- `GRAFANA_API_TOKEN`  
  Token API Grafana (rôle Admin) pour certains appels `/api/*`.  
  Si défini, il peut être utilisé à la place du couple user/password.

- `TSV_META_BUCKET`  
  Bucket meta pour les résumés d’exécution (par défaut `powerview_meta`).

- `TSV_LOG_LEVEL`  
  Niveau de logs du parseur (`INFO`, `DEBUG`, etc.).

- `TSV_REPORT_DIR`  
  Dossier où écrire les rapports JSON.  
  Par défaut : `<base_folder>/../logs/reports` (typiquement `/srv/sftpgo/logs/reports`).

> **Important** : ne jamais committer `.env` dans Git.  
> Le fichier doit rester uniquement sur le serveur (sauvegardes chiffrées si besoin).

---

## 5. Déploiement d’InfluxDB et Grafana (Podman)

### 5.1 Démarrer les services

Depuis `/srv/powerview` :

```bash
podman compose pull      # optionnel, pour mettre à jour les images
podman compose up -d
```

Cela démarre au minimum :

- InfluxDB sur le port `8086` (interne) ;
- Grafana sur le port `8088` (interne).

### 5.2 Vérifications initiales

- InfluxDB :  
  `http://<serveur>:8086`  
  Suivre l’assistant de configuration si c’est un premier démarrage
  (création de l’org, de l’utilisateur admin, du token admin).

- Grafana :  
  `http://<serveur>:8088`  
  Créer l’utilisateur admin si nécessaire, puis vérifier que tu peux te connecter
  avec `GRAFANA_USERNAME` / `GRAFANA_PASSWORD`.

Mettre à jour `.env` avec les valeurs définitives (notamment `INFLUXDB_ADMIN_TOKEN`).

---

## 6. Mise en place de Caddy (reverse‑proxy HTTPS)

### 6.1 Installation de Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

### 6.2 Configuration du Caddyfile

Adapter les domaines à ton environnement :

```bash
cat << EOF | sudo tee /etc/caddy/Caddyfile
{
    email contact@ton-domaine.tld
}

ftp.powerview.ton-domaine.tld {
    reverse_proxy localhost:8080    # interface web SFTPGo
}

powerview.ton-domaine.tld {
    reverse_proxy localhost:8088    # Grafana
}

db.powerview.ton-domaine.tld {
    reverse_proxy localhost:8086    # InfluxDB
}
EOF
```

Appliquer la configuration :

```bash
sudo systemctl restart caddy
```

Vérifier ensuite :

- `https://powerview.ton-domaine.tld` → Grafana  
- `https://ftp.powerview.ton-domaine.tld` → SFTPGo  
- `https://db.powerview.ton-domaine.tld` → InfluxDB

---

## 7. Installation et configuration de SFTPGo

### 7.1 Installation

```bash
sudo add-apt-repository ppa:sftpgo/sftpgo
sudo apt update
sudo apt install -y sftpgo
```

Vérifier :

```bash
systemctl status sftpgo
```

L’interface web est généralement exposée sur `http://localhost:8080`
(ou via Caddy en HTTPS).

### 7.2 Configuration du hook `on-upload.sh`

PowerView utilise un script `on-upload.sh` comme hook SFTPGo pour :

- déclencher le parseur TSV lors d’un `upload` ;
- déclencher Ansible lors d’un `mkdir` (création de campagne).

Créer le fichier d’environnement SFTPGo :

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

Créer le répertoire de logs :

```bash
sudo mkdir -p /srv/sftpgo/logs
sudo chown -R sftpgo:sftpgo /srv/sftpgo
```

### 7.3 Droits sur `/srv`

Les dossiers `/srv/sftpgo` et `/srv/powerview` doivent appartenir à l’utilisateur
`sftpgo`, tout en laissant un accès pratique à l’utilisateur d’admin (ex. `ubuntu`)
via les ACL :

```bash
sudo chown -R sftpgo:sftpgo /srv/
sudo apt install -y acl
sudo setfacl -d -R -m u:ubuntu:rwx /srv/   # adapter l’utilisateur si besoin
sudo chown -R sftpgo:sftpgo /srv/
```

Redémarrer SFTPGo pour prendre en compte la configuration :

```bash
sudo systemctl restart sftpgo
```

### 7.4 Création des utilisateurs SFTP

Via l’interface web SFTPGo (`https://ftp.powerview.ton-domaine.tld`) :

1. Créer un utilisateur admin SFTPGo (si ce n’est pas déjà fait).  
2. Créer un utilisateur SFTP par client, par exemple :
   - username : `company1`
   - home directory : `/srv/sftpgo/data/company1`

L’arborescence attendue pour les données est :

```text
/srv/sftpgo/data/<company_name>/<campaign_name>/<device_master_sn>/<fichier>.tsv
```

---

## 8. Sécurisation réseau (UFW)

Activer le firewall UFW avec les ports nécessaires :

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw limit ssh
sudo ufw allow https        # 443 pour Caddy
sudo ufw allow 2022/tcp     # port SFTPGo
sudo ufw enable
```

> Adapter les règles si tu exposes directement d’autres services.

---

## 9. Initialisation Grafana et InfluxDB

### 9.1 Grafana

1. Accéder à Grafana via Caddy :  
   `https://powerview.ton-domaine.tld`
2. Créer l’utilisateur admin (si ce n’est pas déjà fait).  
3. Vérifier que tu peux te connecter avec `GRAFANA_USERNAME` / `GRAFANA_PASSWORD`
   définis dans `.env`.

### 9.2 InfluxDB

1. Accéder à InfluxDB via Caddy :  
   `https://db.powerview.ton-domaine.tld`
2. Créer l’organisation `INFLUXDB_ORG` et l’utilisateur admin si nécessaire.  
3. Récupérer le token admin (All Access) et le renseigner dans `.env` :
   ```env
   INFLUXDB_ADMIN_TOKEN=...
   ```

Les buckets clients et le bucket meta seront créés automatiquement par le parseur
et/ou les scripts associés.

---

## 10. Tests de bout en bout

### 10.1 Test manuel du parseur

Activer l’environnement virtuel :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
```

Dry‑run sur un dossier de données :

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --dry-run
```

- Aucune écriture InfluxDB.  
- Aucun déplacement de fichiers.  
- Aucun rapport JSON sur disque (le rapport est seulement affiché sur stdout).

Traitement réel d’un fichier TSV :

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --tsvFile /srv/sftpgo/data/company1/campaign1/02001084/T302_251012_031720.tsv
```

Après succès :

- le fichier est déplacé dans `.../parsed/` (ou `.../error/` en cas d’échec) ;
- les points sont écrits dans InfluxDB (bucket = `company1`, measurement = `electrical`) ;
- un rapport JSON est généré dans le dossier de rapports (par défaut `/srv/sftpgo/logs/reports`) ;
- un résumé d’exécution est envoyé dans le bucket meta (`TSV_META_BUCKET`).

### 10.2 Test de l’intégration SFTPGo → Ansible → Grafana

1. Créer une campagne (mkdir) :

   ```bash
   mkdir -p /srv/sftpgo/data/company1/campaign_test
   chown -R sftpgo:sftpgo /srv/sftpgo/data/company1
   ```

   SFTPGo doit déclencher `on-upload.sh` avec `SFTPGO_ACTION=mkdir`, qui appelle
   le playbook Ansible `grafana-automation/playbooks/create_grafana_resources.yml`.

   Pour tester à la main :

   ```bash
   cd /srv/powerview
   source envs/powerview/bin/activate
   ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
     --extra-vars "company_name=company1 campaign_name=campaign_test"
   ```

   Vérifier dans Grafana :

   - team `company1` ;
   - folder `company1` ;
   - datasource `influxdb_company1` (plugin `influxdb-adecwatts-datasource`) ;
   - **un dashboard principal pour le client** (actuellement un seul dashboard par client, pas un par campagne).

2. Upload d’un fichier TSV (upload) :

   Uploader un fichier TSV dans :

   ```text
   /srv/sftpgo/data/company1/campaign_test/02001084/T302_251012_031720.tsv
   ```

   SFTPGo déclenche `on-upload.sh` avec `SFTPGO_ACTION=upload` (hook `post_disconnect`),
   qui appelle `tsv_parser.py`.

   Après succès :

   - le fichier est déplacé dans `.../parsed/` ;
   - les points sont écrits dans InfluxDB (bucket `company1`, measurement `electrical`) ;
   - un rapport JSON est écrit ;
   - un résumé d’exécution est envoyé dans le bucket meta.

---

## 11. Logs et supervision

### 11.1 Logs SFTPGo

```bash
journalctl -u sftpgo -f
```

### 11.2 Logs du hook / parseur

Le script `on-upload.sh` redirige généralement stdout/stderr vers un fichier,
par exemple :

```bash
tail -f /srv/sftpgo/logs/uploads.log
```

### 11.3 Rapports JSON

Par défaut (si `TSV_REPORT_DIR` n’est pas défini) :

```bash
ls -l /srv/sftpgo/logs/reports
```

Chaque rapport contient un résumé détaillé du traitement d’un fichier TSV
(nombre de lignes, erreurs, etc.).

---

## 12. Bonnes pratiques de déploiement en production

- **Toujours déployer depuis un tag git** (ex. `v0.3.2`) pour savoir exactement
  quelle version tourne.
- **Ne jamais committer `.env`** : le garder uniquement sur le serveur.
- **Surveiller la taille des logs** (`uploads.log`, rapports JSON) et mettre en
  place une rotation (logrotate).
- **Tester en dry‑run** (`--dry-run`) avant chaque grosse mise à jour.
- **Documenter les buckets InfluxDB** (un bucket par client) et les dashboards
  Grafana associés.
- **Sauvegardes** :
  - sauvegarder régulièrement les volumes InfluxDB (données) ;
  - sauvegarder la configuration Grafana (dashboards, datasources) ;
  - sauvegarder `/srv/powerview` (code + scripts) et `/srv/sftpgo` (données brutes).

---

## 13. Mise à jour d’une instance existante

1. Sauvegarder l’ancienne version (optionnel mais recommandé) :

   ```bash
   sudo systemctl stop sftpgo || true
   cd /srv
   mv /srv/powerview /srv/powerview_backup_$(date +%Y%m%d_%H%M%S)
   ```

2. Reprendre les étapes de clonage, création de venv, mise à jour de `.env`.  
3. Redémarrer Podman (`podman compose up -d`).  
4. Redémarrer SFTPGo et Caddy si nécessaire.  
5. Tester en dry‑run puis en réel sur un fichier de test.

---

Fin du document.
