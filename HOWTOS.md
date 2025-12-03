


## How to deploy

### 1. Se connecter au serveur


```bash
ssh ubuntu@37.59.124.77
cd /srv
```

(adapte l’utilisateur / l’IP si besoin)

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

### 2. Sauvegarder l’ancienne version (optionnel mais conseillé)

Si tu as déjà un dossier /srv/powerview existant :

```bash
sudo systemctl stop sftpgo || true   # pour éviter des hooks pendant la mise à jour
mv /srv/powerview /srv/powerview_backup_$(date +%Y%m%d_%H%M%S)
```

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

### 3. Récupérer la nouvelle version du code

Si ton dépôt est sur GitHub / GitLab, par ex :

```bash
cd /srv
git clone https://github.com/fheslouin/powerview.git
# ou ton propre remote si tu as un fork
cd powerview
```

Si tu as modifié le code localement, tu peux aussi faire un git pull dans l’ancien dossier au lieu de re-cloner, mais comme on vient de renommer l’ancien, le plus simple
est de repartir propre.

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

### 4. Créer / recréer l’environnement virtuel Python

```bash
python3 -m venv /srv/powerview/envs/powerview
source /srv/powerview/envs/powerview/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

### 5. Mettre à jour le fichier .env

 1 Copier le modèle :

```bash
cd /srv/powerview
cp .env.sample .env
```

 2 Éditer .env et remettre tes vraies valeurs :

```
nano .env
```

À minima :

 • GRAFANA_URL
 • GRAFANA_USERNAME
 • GRAFANA_PASSWORD
 • INFLUXDB_HOST
 • INFLUXDB_ADMIN_TOKEN
 • INFLUXDB_ORG
 • éventuellement TSV_META_BUCKET, TSV_LOG_LEVEL…

Tu peux reprendre les valeurs de ton ancien .env dans /srv/powerview_backup_.../.env si tu l’avais.

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

### 6. (Si besoin) Redéployer Grafana / InfluxDB

Si tu utilises Podman comme dans le README :

```bash
cd /srv/powerview
podman compose pull      # optionnel, pour mettre à jour les images
podman compose up -d
```

Vérifie que Grafana et InfluxDB répondent (via Caddy ou directement sur les ports internes).

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

### 7. Vérifier / mettre à jour la config SFTPGo

Le hook on-upload.sh a changé (nouvelle logique mkdir / upload, déplacement dans parsed/ et error/).

Assure-toi que /etc/sftpgo/sftpgo.env contient bien quelque chose comme :

```
SFTPGO_COMMON__ACTIONS__EXECUTE_ON=mkdir
SFTPGO_COMMON__ACTIONS__HOOK=/srv/powerview/on-upload.sh
SFTPGO_COMMON__POST_DISCONNECT_HOOK=/srv/powerview/on-upload.sh
SFTPGO_COMMAND__COMMANDS__0__PATH=/srv/powerview/on-upload.sh
SFTPGO_COMMAND__COMMANDS__0__ENV=SFTPGO_ACTION=upload
SFTPGO_COMMAND__COMMANDS__0__HOOK=post_disconnect
```

Puis :

```bash
sudo systemctl restart sftpgo
```

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

### 8. Droits sur /srv

Comme dans le README :

```bash
sudo chown -R sftpgo:sftpgo /srv/
sudo apt install -y acl
sudo setfacl -d -R -m u:ubuntu:rwx /srv/
sudo chown -R sftpgo:sftpgo /srv/
```

(adapte ubuntu à ton utilisateur SSH si besoin)

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

### 9. Tester le parseur manuellement

Avant de laisser SFTPGo appeler le script, tu peux tester à la main.

 1 Activer l’environnement :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
```

 2 Lancer un dry-run sur un dossier de test (par ex. /srv/sftpgo/data si tu as déjà des fichiers) :

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --dry-run
```

Tu dois voir des logs dans la console, mais aucune écriture InfluxDB ni déplacement de fichiers.

 3 Lancer un vrai run sur un fichier :

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --tsvFile /srv/sftpgo/data/company1/campaign1/02001084/T302_251012_031720.tsv
```

Après succès :

 • le fichier doit être déplacé dans .../parsed/,
 • les points doivent être écrits dans InfluxDB (bucket company1),
 • un rapport JSON doit apparaître dans …/logs/reports/ (par défaut /srv/sftpgo/logs/reports si TSV_REPORT_DIR n’est pas défini),
 • un résumé doit être écrit dans le bucket meta (par défaut powerview_meta).

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

### 10. Tester l’intégration SFTPGo + Ansible + Grafana

 1 Créer (via SFTP ou à la main) un dossier de campagne :

```bash
mkdir -p /srv/sftpgo/data/company1/campaign_test
chown -R sftpgo:sftpgo /srv/sftpgo/data/company1
```

 2 SFTPGo devrait déclencher on-upload.sh avec SFTPGO_ACTION=mkdir → le playbook Ansible :

```bash
ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
  --extra-vars "company_name=company1 campaign_name=campaign_test"
```

Si tu veux tester à la main :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
  --extra-vars "company_name=company1 campaign_name=campaign_test"
```

Vérifie ensuite dans Grafana :

 • la team company1,
 • le dossier company1,
 • la datasource influxdb_company1,
 • un dashboard avec le titre campaign_test.

───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

### 11. Vérifier les logs

 • Logs SFTPGo : journalctl -u sftpgo -f
 • Logs du hook : /srv/sftpgo/logs/uploads.log
 • Logs du parseur (dans ce même fichier, car on-upload.sh y redirige stdout/stderr)