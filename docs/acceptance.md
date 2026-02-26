# Plan de test d’acceptance (cas d’usage principaux) — PowerView

Ce document définit un **plan de tests d’acceptance** orienté usages utilisateur pour valider :
- le **workflow initial de création** (création user/team/campaign, provisioning Grafana/Influx),
- l’**ingestion** de données TSV (upload → parsing → InfluxDB → déplacement → rapports),
- la **robustesse** face aux erreurs de données,
- un contrôle “visuel” minimal côté Grafana.

---

## 0) Pré-requis (environnement de recette)

- Services opérationnels :
  - InfluxDB (ex. via `docker-compose.yml`)
  - Grafana (ex. via `docker-compose.yml`)
  - SFTPGo (service externe au compose, ou géré séparément)
- Hook SFTPGo configuré pour appeler `/srv/powerview/on-upload.sh` :
  - action `mkdir`
  - hook `post_disconnect` pour `upload`
- Environnement Python prêt :
  - venv : `/srv/powerview/envs/powerview`
  - variables chargées depuis `/srv/powerview/.env`
- Arborescence de données attendue :
  - `/srv/sftpgo/data/<company>/<campaign>/<device_master_sn>/<fichier>.tsv`

### Données de test
Disposer au minimum de :
- 1 fichier TSV **V002** (format `MV_T302_V002`)
- 1 fichier TSV **V003** (format `MV_T302_V003`, avec `START_HEADER/END_HEADER` + `START_DATA/END_DATA`)

---

## 1) Workflow initial de création (utilisateur SFTP → provisioning Grafana → campagne)

### A1 — Création initiale d’un utilisateur SFTP (démarre le workflow)

Objectif : valider la création du **premier utilisateur SFTP** (côté SFTPGo) et le fait que cela permet d’enclencher le workflow de création (provisioning Grafana) dès la création de la première campagne.

Action (admin SFTPGo) :
- Créer un utilisateur SFTP pour un nouveau client `<company>` avec :
  - login = `<company>` (recommandé)
  - 

Résultats attendus :
- L’utilisateur peut se connecter en SFTP et **ne voit que** son répertoire :
  - `/srv/sftpgo/data/<company>`
- L’utilisateur peut créer un dossier de campagne au niveau racine de son home.
- Aucun provisioning Grafana n’est attendu à cette étape seule (la création des ressources Grafana est déclenchée par `mkdir`, cf. A2).

---

### A2 — Création d’une campagne (mkdir → Ansible → ressources Grafana)

Objectif : valider le flux “création d’une campagne côté SFTP” déclenchant le provisioning Grafana.

Action utilisateur :
- Créer un dossier campagne :
  - `/srv/sftpgo/data/<company>/<campaign>`

Résultats attendus :
- Le hook est déclenché avec :
  - `SFTPGO_ACTION=mkdir`
  - `SFTPGO_ACTION_PATH=/srv/sftpgo/data/<company>/<campaign>`
- Le playbook `grafana-automation/playbooks/create_grafana_resources.yml` est exécuté avec :
  - `company_name=<company>`
  - `campaign_name=<campaign>`

Dans InfluxDB, les ressources existent (créées ou maintenues) :
- API Token : `powerview_token_for_bucket_<company>`
- Bucket : `<company>`

Dans Grafana, les ressources existent (créées ou maintenues) :
- Team : `<company>`
- Folder : `<company>`
- Datasource : `influxdb_<company>`
- Dashboard : “Overview <company>” (UID déterministe `powerview_<company>`)

Côté filesystem :
- Un marqueur est présent :
  - `/srv/sftpgo/data/<company>/.company.created`

=> pas certain pour le .company.created

---

## 2) Ingestion via upload (upload → parseur → InfluxDB)

### A3 — Upload d’un TSV V002 (cas nominal)

Objectif : valider le flux complet “upload → parsing → InfluxDB → déplacement → rapports”.

Action utilisateur :
- Uploader un fichier V002 dans :
  - `/srv/sftpgo/data/<company>/campaign_acceptance/<device_master_sn>/<fichier>.tsv`

Résultats attendus :
- Le hook est déclenché avec `SFTPGO_ACTION=upload`.
- Des points sont écrits dans InfluxDB :
  - bucket = `<company>`
  - measurement = `electrical`
  - tags incluent au minimum :
    - `campaign=campaign_acceptance`
    - `device_master_sn=<device_master_sn>`
- Le fichier est déplacé dans :
  - `.../parsed/`
- Un rapport JSON est créé :
  - dans `TSV_REPORT_DIR` si défini, sinon dans `<base_folder>/../logs/reports`
  - typiquement : `/srv/sftpgo/logs/reports`
- Un résumé d’exécution est écrit dans le bucket meta :
  - `TSV_META_BUCKET` (défaut `powerview_meta`)

---

### A4 — Upload d’un TSV V003 (cas nominal)

Objectif : valider la prise en charge du format V003.

Action utilisateur :
- Uploader un fichier V003 dans :
  - `/srv/sftpgo/data/<company>/campaign_acceptance/<device_master_sn>/<fichier>.tsv`

Résultats attendus :
- Parsing OK malgré :
  - `START_HEADER/END_HEADER`
  - `START_DATA/END_DATA`
- Déplacement du fichier dans `.../parsed/`.
- Écriture de points InfluxDB (bucket client, measurement `electrical`).
- Le rapport contient les métadonnées V003 dans `file_header_meta` (si présentes).

---

## 3) Robustesse (erreurs contrôlées côté données)

### A5 — Timestamp invalide dans le TSV (dégradation contrôlée)

Objectif : s’assurer que des lignes invalides sont ignorées sans bloquer tout le fichier.

Action utilisateur :
- Uploader un TSV contenant au moins une ligne avec un timestamp non parsable (hors format `DD/MM/YY HH:MM:SS`).

Résultats attendus :
- Le traitement continue (les lignes invalides sont ignorées).
- Le rapport indique :
  - `nb_invalid_timestamps > 0`
- Le fichier est déplacé en `parsed/` si le reste est valide et que des points ont été créés.

---

### A6 — Valeur non numérique dans une colonne (dégradation contrôlée)

Objectif : s’assurer que des valeurs invalides sont ignorées sans bloquer tout le fichier.

Action utilisateur :
- Uploader un TSV contenant une valeur non convertible en float sur une ou plusieurs colonnes.

Résultats attendus :
- Le traitement continue (valeurs invalides ignorées).
- Le rapport indique :
  - `nb_invalid_values > 0`
- Le fichier est déplacé en `parsed/` si le reste est valide et que des points ont été créés.

---

### A7 — Fichier non conforme / format non supporté (échec)

Objectif : valider la gestion d’échec (déplacement `error/`, rapport exploitable).

Action utilisateur :
- Uploader un fichier TSV non conforme (format inconnu, header absent ou illisible).

Résultats attendus :
- Le rapport indique :
  - `status=error`
  - une cause dans `error`
- Le fichier est déplacé dans :
  - `.../error/`
- Pas (ou très peu) de points écrits en base pour ce fichier.

---

## 4) Contrôle côté Grafana (post-ingestion)

### A8 — Consultation des données dans Grafana par un utilisateur de la team

Objectif : vérifier l’accès et l’affichage minimal des données.

Action utilisateur :
- Se connecter à Grafana avec un utilisateur membre de la team `<company>`.
- Ouvrir le dashboard “Overview <company>”.
- Sélectionner la variable `campaign` = `campaign_acceptance`.

Résultats attendus :
- Le dashboard s’affiche sans erreur.
- Les panels affichent des séries sur la période sélectionnée (si les timestamps des TSV couvrent la fenêtre de temps).

---

## 5) Logs et traçabilité (sanity check)

### A9 — Vérification des logs d’exécution hook/parseur

Objectif : s’assurer que le diagnostic est possible via les logs.

Action :
- Consulter :
  - `/srv/sftpgo/logs/uploads.log`

Résultats attendus :
- Entrées correspondant aux actions `mkdir` et `upload`.
- En cas d’échec, une erreur explicite (fichier concerné, cause).
