# Intégration SFTPGo / Ansible / Grafana

Ce document décrit en détail comment PowerView intègre :

- **SFTPGo** (service SFTP) ;
- **Ansible** (automatisation Grafana) ;
- **Grafana** (visualisation des données InfluxDB).

---

## 1. Vue d’ensemble du workflow

Flux global :

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

Arborescence attendue pour les données :

```text
/srv/sftpgo/data/<company_name>/<campaign_name>/<device_master_sn>/<fichier>.tsv
```

---

## 2. Rôle de SFTPGo

SFTPGo fournit :

- un service SFTP multi‑utilisateurs ;
- une interface web d’administration ;
- un système de **hooks** permettant d’exécuter des scripts lors de certains
  événements (upload, mkdir, etc.).

### 2.1 Utilisateurs SFTP

Pour chaque client (`company_name`), on crée un utilisateur SFTP avec :

- un répertoire racine : `/srv/sftpgo/data/<company_name>` ;
- des droits limités à ce sous‑dossier.

Le client peut alors :

- créer des dossiers de campagne : `/srv/sftpgo/data/company1/campaign1` ;
- uploader des fichiers TSV dans :
  `/srv/sftpgo/data/company1/campaign1/02001084/T302_251012_031720.tsv`.

### 2.2 Hooks SFTPGo

La configuration (via `/etc/sftpgo/sftpgo.env`) indique à SFTPGo :

- d’exécuter `/srv/powerview/on-upload.sh` sur certains événements ;
- de passer des variables d’environnement comme `SFTPGO_ACTION` et
  `SFTPGO_ACTION_PATH`.

Exemple de configuration :

```ini
SFTPGO_COMMON__ACTIONS__EXECUTE_ON=mkdir
SFTPGO_COMMON__ACTIONS__HOOK=/srv/powerview/on-upload.sh
SFTPGO_COMMON__POST_DISCONNECT_HOOK=/srv/powerview/on-upload.sh
SFTPGO_COMMAND__COMMANDS__0__PATH=/srv/powerview/on-upload.sh
SFTPGO_COMMAND__COMMANDS__0__ENV=SFTPGO_ACTION=upload
SFTPGO_COMMAND__COMMANDS__0__HOOK=post_disconnect
```

---

## 3. Script `on-upload.sh`

`on-upload.sh` est le point d’entrée unique appelé par SFTPGo.  
Il gère deux cas principaux :

- `SFTPGO_ACTION=upload` → appel du parseur TSV ;
- `SFTPGO_ACTION=mkdir` → appel d’Ansible pour Grafana.

### 3.1 Cas `upload` (post_disconnect)

Lorsqu’un client termine un upload de fichier TSV :

1. SFTPGo déclenche le hook `post_disconnect` avec :
   - `SFTPGO_ACTION=upload` ;
   - `SFTPGO_ACTION_PATH=/srv/sftpgo/data/company1/campaign1/02001084/T302_251012_031720.tsv`.

2. `on-upload.sh` :
   - active l’environnement virtuel Python (`/srv/powerview/envs/powerview`) ;
   - charge les variables de `.env` ;
   - appelle :
     ```bash
     python3 tsv_parser.py \
       --dataFolder /srv/sftpgo/data \
       --tsvFile "$SFTPGO_ACTION_PATH"
     ```

3. `tsv_parser.py` :
   - parse le fichier ;
   - écrit les points dans InfluxDB (bucket = `company1`, measurement = `electrical`) ;
   - déplace le fichier dans `parsed/` ou `error/` ;
   - écrit un rapport JSON ;
   - écrit un résumé d’exécution dans le bucket meta.

### 3.2 Cas `mkdir` (création de campagne)

Lorsqu’un client crée un dossier de campagne :

1. SFTPGo déclenche le hook d’action avec :
   - `SFTPGO_ACTION=mkdir` ;
   - `SFTPGO_ACTION_PATH=/srv/sftpgo/data/company1/campaign_test`.

2. `on-upload.sh` :
   - vérifie que le chemin correspond à un niveau `company/campaign`  
     (et non `device`) ;
   - extrait `company_name=company1` et `campaign_name=campaign_test` ;
   - appelle :
     ```bash
     ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
       --extra-vars "company_name=company1 campaign_name=campaign_test"
     ```

3. Le playbook Ansible crée/maintient les ressources Grafana pour ce couple
   (voir section suivante).

---

## 4. Automatisation Grafana avec Ansible

Les playbooks Ansible se trouvent dans `grafana-automation/playbooks/` :

- `create_grafana_resources.yml`  
  - création/mise à jour des ressources Grafana pour un client + campagne.

- `delete_grafana_resources.yml`  
  - suppression des ressources Grafana d’un client (dashboards, folder, datasource, team).

### 4.1 Ressources créées pour un client

Pour chaque `company_name`, le playbook de création :

1. Crée/maintient une **team Grafana** :  
   - nom : `company_name`.

2. Crée/maintient un **folder Grafana** :  
   - nom : `company_name`.

3. Crée/maintient une **datasource InfluxDB dédiée** :  
   - nom : `influxdb_<company_name>` ;
   - type de plugin : `influxdb-adecwatts-datasource` ;
   - bucket par défaut : `<company_name>` ;
   - token InfluxDB dédié : `powerview_token_for_bucket_<company_name>`.

4. Crée/maintient un ou plusieurs **dashboards** dans le folder `company_name` :
   - un dashboard par campagne (`campaign_name`) ;
   - basé sur un template Jinja (`templates/dashboard.json.j2`).

5. Configure les **permissions** :
   - la team `company_name` a un accès *viewer* au folder et aux dashboards.

### 4.2 Gestion des tokens InfluxDB

Le playbook de création n’utilise pas directement `INFLUXDB_ADMIN_TOKEN` dans les
datasources Grafana.  
À la place, il appelle le script Python `manage_influx_tokens.py` pour :

1. Créer le bucket `<company_name>` s’il n’existe pas.  
2. Chercher un token existant pour ce bucket (`powerview_token_for_bucket_<company_name>`).  
3. Créer un token si nécessaire, avec les droits limités au bucket du client.  
4. Retourner ce token au playbook, qui l’injecte dans la datasource Grafana.

Ainsi :

- `INFLUXDB_ADMIN_TOKEN` reste un **token root partagé** utilisé uniquement
  côté serveur (parseur, CLI `influx`, Ansible).  
- Chaque datasource Grafana utilise un **token dédié par bucket**, limitant
  l’accès aux données du client concerné.

### 4.3 Utilisateurs Grafana

Le playbook de création peut également :

- créer un utilisateur Grafana par défaut pour la team, par exemple :
  - nom : `{{ company_name }} Default`  
  - login : `user_{{ company_name }}`  
- ajouter cet utilisateur à la team `company_name`.

Alternatives :

- créer les utilisateurs manuellement via l’UI Grafana, puis les rattacher à la
  team du client ;
- surcharger les variables Ansible pour utiliser un compte existant.

---

## 5. Schéma multi‑tenant dans Grafana

PowerView utilise **une seule instance Grafana** partagée entre tous les clients.

Schéma simplifié :

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

Avantages :

- une seule instance Grafana à maintenir (mises à jour, plugins, sauvegardes) ;
- isolation logique par team/folder/datasource ;
- possibilité de gérer les permissions finement (viewer/editor/admin par team).

---

## 6. Suppression des ressources Grafana

Le playbook `delete_grafana_resources.yml` permet de nettoyer les ressources
Grafana d’un client.

Il peut :

- supprimer les dashboards et le folder d’un client ;
- supprimer la datasource `influxdb_<company_name>` ;
- supprimer la team associée ;
- éventuellement supprimer un utilisateur Grafana donné.

> Les **buckets InfluxDB** et les **données** ne sont pas supprimés par ce
> playbook. La suppression des données doit être gérée séparément (via InfluxDB).

Exemple d’appel :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
ansible-playbook grafana-automation/playbooks/delete_grafana_resources.yml \
  --extra-vars "company_name=company1"
```

---

## 7. Bonnes pratiques d’intégration

- Toujours tester les playbooks Ansible sur un environnement de test avant de
  les exécuter en prod.  
- Documenter pour chaque client :
  - la team Grafana ;
  - le folder Grafana ;
  - la datasource InfluxDB ;
  - les campagnes (dashboards) existantes.  
- Limiter l’accès à `INFLUXDB_ADMIN_TOKEN` aux seuls scripts internes
  (`tsv_parser.py`, `manage_influx_tokens.py`, Ansible).  
- Utiliser des **tokens dédiés par bucket** pour toutes les intégrations externes
  (Grafana, autres outils).

---

Fin du document.
