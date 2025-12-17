# Setup et patch des composants PowerView (infra existante)

Ce document décrit les opérations courantes sur une instance PowerView
**déjà déployée** :

- mise à jour du code et des dépendances ;
- mise à jour d’InfluxDB / Grafana (containers) ;
- ajout d’un nouveau client ;
- ajout d’une nouvelle campagne ;
- (re)création / suppression des ressources Grafana via Ansible ;
- tests du parseur et debug.

Pour un **déploiement complet** sur un nouveau serveur, voir
[`deploiement-infra.md`](deploiement-infra.md).

---

## 1. Mettre à jour le code et l’environnement Python

Depuis `/srv/powerview` :

```bash
sudo systemctl stop sftpgo || true   # optionnel mais recommandé
cd /srv/powerview
git fetch
git checkout <nouvelle_version_ou_tag>
```

Mettre à jour le venv :

```bash
python3 -m venv /srv/powerview/envs/powerview
source /srv/powerview/envs/powerview/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Vérifier que `.env` est toujours correct (surtout les tokens).

Redémarrer SFTPGo :

```bash
sudo systemctl restart sftpgo
```

---

## 2. Mettre à jour InfluxDB et Grafana (containers)

Depuis `/srv/powerview` :

```bash
podman compose pull
podman compose up -d
```

Les services définis dans `docker-compose.yml` :

- `influxdb2` (image `influxdb:2`) ;
- `grafana` (image `grafana/grafana-enterprise`) ;
- `powerview-config-api` (image `powerview-config-api:0.3.2`).

Les volumes (`influxdb2-data`, `grafana-data`, `config-api-data`) conservent les données.

---

## 3. Ajouter un nouveau client

### 3.1 Créer l’utilisateur SFTPGo

Via l’UI SFTPGo ou en CLI, créer un utilisateur :

- username : `companyX`
- home : `/srv/sftpgo/data/companyX`

S’assurer que le dossier existe et appartient à `sftpgo` :

```bash
sudo mkdir -p /srv/sftpgo/data/companyX
sudo chown -R sftpgo:sftpgo /srv/sftpgo/data/companyX
```

### 3.2 Créer les ressources Grafana pour ce client

Depuis `/srv/powerview` :

```bash
source envs/powerview/bin/activate
ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
  --extra-vars "company_name=companyX"
```

Le playbook :

- crée/maintient la **team** `companyX` ;
- crée le **folder** `companyX` ;
- crée la **datasource** `influxdb_companyX` (plugin `influxdb-adecwatts-datasource`) ;
- crée un **dashboard principal** pour le client (UID `powerview_companyX`) ;
- crée un **token InfluxDB dédié** au bucket `companyX` via `manage_influx_tokens.py` ;
- crée un utilisateur Grafana par défaut (`user_companyX`) et l’ajoute à la team.

Un fichier `.company.created` est posé dans `/srv/sftpgo/data/companyX` pour marquer
que la création a déjà été faite (idempotence).

---

## 4. Ajouter une nouvelle campagne pour un client existant

### 4.1 Créer le dossier de campagne

```bash
sudo mkdir -p /srv/sftpgo/data/companyX/campaignY
sudo chown -R sftpgo:sftpgo /srv/sftpgo/data/companyX
```

SFTPGo déclenchera `on-upload.sh` avec `SFTPGO_ACTION=mkdir` si le hook est configuré.
Le script :

- détecte `companyX` / `campaignY` à partir du chemin relatif à `/srv/sftpgo/data` ;
- appelle le playbook `create_grafana_resources.yml` avec ces variables.

> Actuellement, le playbook crée **un seul dashboard par client** (overview),
> pas un dashboard distinct par campagne. La variable `campaign_name` est
> disponible pour des évolutions futures.

Pour forcer la création/MAJ à la main :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
  --extra-vars "company_name=companyX campaign_name=campaignY"
```

---

## 5. Supprimer les ressources Grafana d’un client

Le playbook `grafana-automation/playbooks/delete_grafana_resources.yml` permet de
nettoyer les ressources Grafana d’un client (sans toucher aux buckets InfluxDB).

Exemple :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
ansible-playbook grafana-automation/playbooks/delete_grafana_resources.yml \
  --extra-vars "company_name=companyX"
```

Ce playbook :

- supprime les dashboards du folder `companyX` ;
- supprime le folder `companyX` ;
- supprime la datasource `influxdb_companyX` ;
- supprime la team `companyX` ;
- peut aussi supprimer un utilisateur Grafana donné (via `user_name` si tu le fournis).

Les données InfluxDB (bucket `companyX`) ne sont pas supprimées.

---

## 6. Tester le parseur et déboguer

### 6.1 Dry‑run

```bash
cd /srv/powerview
source envs/powerview/bin/activate

python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --dry-run
```

### 6.2 Traitement d’un fichier précis

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --tsvFile /srv/sftpgo/data/companyX/campaignY/02001084/T302_251012_031720.tsv
```

Après succès :

- le fichier est déplacé dans `.../parsed/` (ou `error/` en cas d’échec) ;
- les points sont écrits dans InfluxDB (bucket `companyX`, measurement `electrical`) ;
- un rapport JSON est écrit (par défaut `/srv/sftpgo/logs/reports`) ;
- un résumé d’exécution est envoyé dans le bucket meta (`TSV_META_BUCKET`).

### 6.3 Logs utiles

- SFTPGo :

  ```bash
  journalctl -u sftpgo -f
  ```

- Hook / parseur :

  ```bash
  tail -f /srv/sftpgo/logs/uploads.log
  ```

- Rapports JSON :

  ```bash
  ls -l /srv/sftpgo/logs/reports
  ```

---

## 7. Procédure de mise à jour “safe”

1. Stopper SFTPGo (éviter les hooks pendant la MAJ) :

   ```bash
   sudo systemctl stop sftpgo
   ```

2. Sauvegarder l’ancienne version (optionnel mais recommandé) :

   ```bash
   cd /srv
   mv powerview powerview_backup_$(date +%Y%m%d_%H%M%S)
   ```

3. Cloner la nouvelle version ou mettre à jour le repo, recréer le venv,
   réinstaller les dépendances, remettre `.env`.

4. Redémarrer les containers :

   ```bash
   cd /srv/powerview
   podman compose up -d
   ```

5. Redémarrer SFTPGo :

   ```bash
   sudo systemctl restart sftpgo
   ```

6. Tester en dry‑run puis sur un fichier réel.

---

Fin du document.
