# Grafana Automation with Ansible

Ce dossier contient les playbooks Ansible utilisés pour automatiser la création
et la suppression des ressources Grafana associées aux campagnes de mesure
(PowerView).

Ces playbooks sont appelés automatiquement par le hook SFTPGo (`on-upload.sh`),
mais peuvent aussi être exécutés manuellement.

## Fonctionnalités actuelles

- ✅ Création automatique d’une **team Grafana par client** (`company_name`)
- ✅ Création d’un **dossier Grafana** par client (`company_name`)
- ✅ Création d’une **datasource InfluxDB par client** :
  - nom : `influxdb_{{ company_name }}`
  - type de plugin : `influxdb-adecwatts-datasource`
  - bucket par défaut : `{{ company_name }}`
  - **token utilisé : un token dédié par bucket**, créé/récupéré via
    `manage_influx_tokens.py` (`powerview_token_for_bucket_<company_name>`)
- ✅ Création automatique d’un **dashboard par campagne** :
  - rendu d’un template Jinja (`templates/dashboard.json.j2`) avec :
    - `company_name`
    - `campaign_name`
    - `datasource_uid` de la datasource `influxdb_{{ company_name }}`
  - import dans le dossier de la team, avec un UID déterministe
    (`powerview_{{ company_name }}_{{ campaign_name }}`) pour l’idempotence.
- ✅ Application des **permissions** :
  - la team a accès en lecture (viewer) au dossier et au dashboard.
- ✅ Marquage des campagnes déjà initialisées via un fichier :
  - `data/<company_name>/<campaign_name>/.dashboard.created`
- ✅ Playbook de **nettoyage** pour supprimer :
  - dashboards, dossier, datasource du client, team Grafana associée,
  - et éventuellement un utilisateur Grafana donné (si tu le demandes).
- ✅ Création d’un **utilisateur Grafana dédié par client** (playbook de création) :
  - nom : `{{ company_name }} Admin`
  - login par défaut : `admin_{{ company_name }}`
  - email par défaut : `admin_{{ company_name }}@example.com`
  - mot de passe par défaut : `ChangeMe123!` (surchageable via `--extra-vars`)
  - l’utilisateur est ajouté comme membre de la team `{{ company_name }}`.

> Remarque : la création d’utilisateurs Grafana **est gérée** par le
> playbook de création (`create_grafana_resources.yml`) via le module
> `community.grafana.grafana_user`.  
> Le playbook de suppression (`delete_grafana_resources.yml`) peut, lui,
> supprimer un utilisateur donné (en plus de la team, du folder, des dashboards
> et de la datasource).

## Prérequis

- Ansible Core 2.19 / Ansible 12
- Python 3.9+
- Accès à une instance Grafana
- Accès à une instance InfluxDB (mode Flux) via Grafana
- Le plugin de datasource Grafana **`influxdb-adecwatts-datasource`** installé
  - chaque datasource créée par client utilise ce type de plugin,
  - le bucket et l’org sont configurés via les champs de la datasource.
- Fichier `.env` à la racine du projet PowerView (`/srv/powerview/.env`) contenant au minimum :

  - `GRAFANA_URL` (souvent `http://localhost:8088` pour les scripts internes)
  - **Login/mot de passe admin Grafana** (pour les modules `community.grafana.*`) :
    - `GRAFANA_USERNAME`
    - `GRAFANA_PASSWORD`
  - **Token API Grafana Admin** (API key ou service account) pour les appels REST :
    - `GRAFANA_API_TOKEN` = token avec rôle **Admin**
    - utilisé pour :
      - création de team (`POST /api/teams`),
      - recherche de team (`GET /api/teams/search`),
      - permissions dashboard/folder (`/api/dashboards/.../permissions`, `/api/folders/.../permissions`).
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

## Custom HTTP Headers et plugin `influxdb-adecwatts-datasource`

Les playbooks configurent les **Custom HTTP Headers** de la datasource comme suit :

- `jsonData.httpHeaderName1 = "Authorization"`
- `secureJsonData.httpHeaderValue1 = "Token <token_dédié_bucket>"`

On le voit dans l’API Grafana :

```json
"jsonData": {
  "httpHeaderName1": "Authorization",
  ...
},
"secureJsonFields": {
  "httpHeaderValue1": true,
  "token": true
}
```

Quelques points importants :

- Grafana **ne renvoie jamais** la valeur réelle des champs `secureJsonData`
  dans l’API, seulement des booléens dans `secureJsonFields`.
- L’UI de la datasource ne montre la section “Custom HTTP Headers” que si le
  plugin déclare ces champs dans son `plugin.json` et/ou réutilise le composant
  standard de configuration de la datasource InfluxDB.
- Avec le plugin custom `influxdb-adecwatts-datasource`, il est donc possible
  que :
  - les headers soient bien configurés et utilisés côté backend (ce que montre
    l’API),
  - mais qu’ils **n’apparaissent pas visuellement** dans l’onglet “Custom HTTP
    Headers” de l’UI Grafana.

En résumé :

- **Les headers sont bien envoyés** par Grafana vers InfluxDB (via `jsonData` /
  `secureJsonData`).
- L’absence d’affichage dans l’UI est un détail de rendu du plugin, pas un
  problème de configuration Ansible.

## Schéma de données attendu côté InfluxDB

[...]  (contenu inchangé en dehors de cette section de prérequis)
