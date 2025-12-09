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
  - `GRAFANA_USERNAME`
  - `GRAFANA_PASSWORD`
  - `INFLUXDB_HOST`
  - `INFLUXDB_ORG`
  - `INFLUXDB_ADMIN_TOKEN`
  - `INFLUXDB_USERNAME`
  - `INFLUXDB_PASSWORD`

## Installation des collections Ansible

Depuis le dossier `grafana-automation/` :

```bash
cd grafana-automation/
ansible-galaxy collection install -r requirements.yml
```

Cela installe notamment la collection `community.grafana` utilisée par les
playbooks.

## Intégration avec SFTPGo (`on-upload.sh`)

Le script `/srv/powerview/on-upload.sh` est appelé par SFTPGo sur certains
événements :

- `SFTPGO_ACTION=mkdir` : création de dossier
- `SFTPGO_ACTION=upload` : upload de fichier TSV

Dans le cas `mkdir`, lorsque le chemin créé correspond à un niveau
`/srv/sftpgo/data/<company_name>/<campaign_name>` (et **pas** à un dossier de
device), le script appelle automatiquement :

```bash
ansible-playbook /srv/powerview/grafana-automation/playbooks/create_grafana_resources.yml \
  --extra-vars "company_name=<company_name> campaign_name=<campaign_name>"
```

Le playbook :

- crée la team, le dossier, la datasource `influxdb_<company_name>` (plugin `influxdb-adecwatts-datasource`) et le dashboard,
- applique les permissions,
- crée le fichier `.dashboard.created` dans :
  `/srv/sftpgo/data/<company_name>/<campaign_name>/.dashboard.created`

Si ce fichier existe déjà, le playbook ne recrée pas les ressources Grafana.

## Playbook : création des ressources Grafana

Fichier : `playbooks/create_grafana_resources.yml`

### Variables lues dans l’environnement

Le playbook lit les variables suivantes via `lookup('ansible.builtin.env', ...)` :

- `GRAFANA_URL`
- `GRAFANA_USERNAME`
- `GRAFANA_PASSWORD`
- `INFLUXDB_ADMIN_TOKEN`
- `INFLUXDB_USERNAME`
- `INFLUXDB_PASSWORD`

Ces variables sont typiquement chargées par `on-upload.sh` à partir de
`/srv/powerview/.env`.

### Variables à fournir

Le playbook attend **deux variables obligatoires** passées en `--extra-vars` :

- `company_name` : nom du client (team Grafana, dossier Grafana, datasource)
- `campaign_name` : nom de la campagne (titre du dashboard)

Exemple d’appel manuel :

```bash
cd /srv/powerview
export $(cat .env)
source envs/powerview/bin/activate

ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
  --extra-vars "company_name=company1 campaign_name=campaign_test"
```

### Comportement détaillé

1. **Vérification du marqueur `.dashboard.created`**

   - Chemin : `/srv/sftpgo/data/<company_name>/<campaign_name>/.dashboard.created`
   - Si le fichier existe :
     - le playbook **ne fait rien** (idempotence),
     - aucune ressource Grafana n’est recréée.

2. **Création de la team Grafana**

   - Appel direct à l’API Grafana (`/api/teams`).
   - Si la team existe déjà (`HTTP 409`), l’erreur est ignorée.

3. **Création du dossier Grafana**

   - Utilise `community.grafana.grafana_folder`.
   - Dossier nommé `company_name`.
   - Créé uniquement si la team vient d’être créée (ou n’existait pas).

4. **Création de la datasource InfluxDB (plugin)**

   - Utilise `community.grafana.grafana_datasource`.
   - Nom : `influxdb_<company_name>`.
   - `ds_type: influxdb-adecwatts-datasource`.
   - URL : `http://influxdb:8086`.
   - Authentification basique : `INFLUXDB_USERNAME` / `INFLUXDB_PASSWORD`.
   - `additional_json_data` :
     - `version: "Flux"`
     - `organization: "powerview"`
     - `defaultBucket: "<company_name>"`

5. **Export du dashboard maître**

   - Utilise `community.grafana.grafana_dashboard` avec `state: export`.
   - UID du dashboard maître : `adq2j6z`.
   - Fichier exporté : `/srv/powerview/dashboard_exported.json`.

6. **Modification du JSON avec `jq`**

   - Suppression des champs `version`, `id`, `uid`, `meta`.
   - Changement du titre du dashboard : `campaign_name`.
   - Remplacement de tous les blocs datasource de type `"influxdb"` par :
     - `type: "influxdb-adecwatts-datasource"`
     - `uid: "<uid de la datasource influxdb_<company_name>>"`
   - Résultat écrit dans `/srv/powerview/dashboard_master.json`.

7. **Import du dashboard adapté**

   - Utilise `community.grafana.grafana_dashboard` avec `state: present`.
   - Dashboard importé dans le dossier `company_name`.
   - Un nouvel UID est généré via `uuidgen`.

8. **Permissions**

   - Appels directs à l’API Grafana pour :
     - donner à la team `company_name` un accès viewer au dashboard,
     - donner à la team `company_name` un accès viewer au dossier.

9. **Nettoyage et marqueur**

   - Suppression des fichiers temporaires JSON.
   - Création du fichier `.dashboard.created` dans :
     `/srv/sftpgo/data/<company_name>/<campaign_name>/`.

10. **Résumé**

   - Affiche un message récapitulatif (team, datasource, dashboard, folder).

## Playbook : suppression des ressources Grafana

Fichier : `playbooks/delete_grafana_resources.yml`

Ce playbook est destiné à un usage **manuel** pour nettoyer toutes les
ressources Grafana associées à un client.

### Variables lues dans l’environnement

- `GRAFANA_URL`
- `GRAFANA_USERNAME`
- `GRAFANA_PASSWORD`

### Prompts interactifs

Le playbook demande :

- `user_name` : login Grafana de l’utilisateur à supprimer
- `company_name` : nom de la team / dossier à supprimer
- confirmation (`yes/no`) avant suppression

### Ressources supprimées

Dans l’ordre :

1. **Dashboards** du dossier `company_name`
2. **Dossier Grafana** `company_name`
3. **Datasource** `influxdb_<company_name>` (plugin `influxdb-adecwatts-datasource`)
4. **Team Grafana** `company_name`
5. **Utilisateur Grafana** `user_name`

> Ce playbook ne touche **pas** aux buckets InfluxDB ni aux données stockées
> dans InfluxDB. Il ne supprime que les ressources Grafana.

Un résumé est affiché à la fin.

## Résumé

- Le README racine et `HOWTOS.md` décrivent le workflow global
  (SFTPGo → `on-upload.sh` → parseur TSV + Ansible).
- Ce README se concentre sur la partie **Grafana Automation** :
  - `create_grafana_resources.yml` : création team + dossier + datasource (plugin `influxdb-adecwatts-datasource`) + dashboard.
  - `delete_grafana_resources.yml` : suppression des ressources Grafana d’un client
    (dashboards, folder, datasource, team, utilisateur).
- La création d’utilisateurs Grafana n’est plus gérée automatiquement lors de la
  création des ressources ; elle reste manuelle (ou gérée ailleurs), seul le
  playbook de suppression supprime encore un utilisateur existant.
