# Grafana Automation with Ansible

Ce dossier contient les playbooks Ansible utilisés pour automatiser la création
et la suppression des ressources Grafana associées aux campagnes de mesure
(PowerView).

Ces playbooks sont appelés automatiquement par le hook SFTPGo (`on-upload.sh`),
mais peuvent aussi être exécutés manuellement.

## Fonctionnalités actuelles

- ✅ Création d’une **team Grafana** par client (`company_name`)
- ✅ Création d’un **dossier Grafana** par client (`company_name`)
- ✅ Création d’une **datasource InfluxDB par client** :
  - nom : `influxdb_{{ company_name }}`
  - type de plugin : `influxdb-adecwatts-datasource`
  - bucket par défaut : `{{ company_name }}`
  - **token utilisé : un token dédié par bucket**, créé/récupéré via
    `manage_influx_tokens.py` (`powerview_token_for_bucket_<company_name>`)
- ✅ Création automatique d’un **dashboard par campagne** :
  - export d’un dashboard maître existant (UID fixe),
  - adaptation (titre = `campaign_name`, datasource remplacée par `influxdb_{{ company_name }}` avec le plugin `influxdb-adecwatts-datasource`),
  - import dans le dossier de la team.
- ✅ Application des **permissions** :
  - la team a accès en lecture (viewer) au dossier et au dashboard.
- ✅ Marquage des campagnes déjà initialisées via un fichier :
  - `data/<company_name>/<campaign_name>/.dashboard.created`
- ✅ Playbook de **nettoyage** pour supprimer :
  - dashboards, dossier, datasource du client, utilisateur Grafana associé.

> Remarque : la **création d’utilisateurs Grafana** n’est plus gérée par le
> playbook de création (`create_grafana_resources.yml`).  
> Seul le playbook de suppression (`delete_grafana_resources.yml`) manipule
> encore un utilisateur (pour le supprimer).

## Prérequis

- Ansible Core 2.19 / Ansible 12
- Python 3.9+
- Accès à une instance Grafana
- Accès à une instance InfluxDB (mode Flux) via Grafana
- Le plugin de datasource Grafana **`influxdb-adecwatts-datasource`** installé
  - chaque datasource créée par client utilise ce type de plugin,
  - le bucket et l’org sont configurés via les champs de la datasource.
- Fichier `.env` à la racine du projet PowerView (`/srv/powerview/.env`) contenant au minimum :

  - `GRAFANA_URL`
  - soit **un token API Grafana** (recommandé) :
    - `GRAFANA_API_TOKEN` = token d’un service account Grafana avec rôle **Admin**
  - soit (ancien mode) un login/mot de passe admin :
    - `GRAFANA_USERNAME`
    - `GRAFANA_PASSWORD`
  - `INFLUXDB_HOST`
  - `INFLUXDB_ORG`
  - `INFLUXDB_ADMIN_TOKEN`
  - `INFLUXDB_USERNAME`
  - `INFLUXDB_PASSWORD`

- CLI `influx` installée et configurée avec un **profil root actif** utilisant
  `INFLUXDB_ADMIN_TOKEN` (voir `manage_influx_tokens.py` pour l’exemple de
  configuration).

> Note : le playbook appelle `manage_influx_tokens.py` pour créer ou récupérer
> un token dédié par bucket (`powerview_token_for_bucket_<company>`), via la
> CLI `influx`.  
> Ce script suppose que la CLI est configurée avec un token root (All Access)
> correspondant à `INFLUXDB_ADMIN_TOKEN`.  
> Le token dédié retourné par `manage_influx_tokens.py` est ensuite injecté
> dans la datasource Grafana `influxdb_<company>`.

## Schéma de données attendu côté InfluxDB

[...]  (contenu inchangé en dehors de cette section de prérequis)
