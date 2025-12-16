# Architecture technique et schéma InfluxDB

Ce document décrit l’architecture technique globale de PowerView et le schéma
de données utilisé dans InfluxDB.

---

## 1. Composants principaux

PowerView est composé des éléments suivants :

1. **SFTPGo**  
   - Service SFTP multi‑utilisateurs.  
   - Gère les comptes clients et leurs répertoires.  
   - Déclenche des hooks (`on-upload.sh`) sur `upload` et `mkdir`.

2. **Script `on-upload.sh`**  
   - Point d’entrée des hooks SFTPGo.  
   - Active l’environnement Python, charge `.env`.  
   - Appelle soit `tsv_parser.py`, soit Ansible.

3. **Parseur TSV (`tsv_parser.py` + `core.py`)**  
   - Découvre et parse les fichiers `.tsv`.  
   - Valide les données.  
   - Construit des points InfluxDB.  
   - Gère les rapports JSON et le bucket meta.

4. **Utilitaires fichiers (`fs_utils.py`)**  
   - Gestion des chemins et de l’arborescence.  
   - Déplacement des fichiers (`parsed/`, `error/`).  
   - Recherche des fichiers `.tsv`.

5. **Utilitaires InfluxDB (`influx_utils.py`)**  
   - Création des buckets si besoin.  
   - Écriture des points.  
   - Écriture des résumés d’exécution dans le bucket meta.

6. **Automatisation Grafana (Ansible + `manage_influx_tokens.py`)**  
   - Création des teams, folders, datasources, dashboards.  
   - Gestion des tokens InfluxDB dédiés par bucket.

7. **InfluxDB**  
   - Stockage des mesures électriques (un bucket par client).  
   - Stockage des résumés d’exécution (bucket meta).

8. **Grafana**  
   - Visualisation des données InfluxDB.  
   - Multi‑tenant via teams/folders/datasources.

9. **Caddy**  
   - Reverse‑proxy HTTPS devant Grafana, InfluxDB et SFTPGo.

---

## 2. Flux de données

### 2.1 De l’upload SFTP à InfluxDB

1. Le client SFTP se connecte à SFTPGo et uploade un fichier TSV dans :

   ```text
   /srv/sftpgo/data/<company>/<campaign>/<device_master_sn>/<fichier>.tsv
   ```

2. À la fin de la session, SFTPGo déclenche le hook `post_disconnect` avec
   `SFTPGO_ACTION=upload`.

3. `on-upload.sh` est exécuté :
   - active le venv Python ;
   - charge `.env` ;
   - appelle `tsv_parser.py` avec `--dataFolder` et `--tsvFile`.

4. `tsv_parser.py` :
   - utilise `fs_utils.extract_path_components` pour extraire
     `company_name`, `campaign_name`, `device_master_sn` ;
   - appelle `parse_tsv_header` et `parse_tsv_data` (dans `core.py`) ;
   - construit des points InfluxDB (measurement `electrical`, fields par canal) ;
   - appelle `influx_utils.write_points` pour écrire dans le bucket du client ;
   - appelle `fs_utils.move_parsed_file` ou `move_error_file` ;
   - appelle `write_run_report_to_file` pour le rapport JSON ;
   - appelle `influx_utils.write_run_summary_to_influx` pour le bucket meta.

### 2.2 De la création de campagne à Grafana

1. Le client crée un dossier de campagne (via SFTP ou à la main) :

   ```text
   /srv/sftpgo/data/company1/campaign_test
   ```

2. SFTPGo déclenche le hook d’action avec `SFTPGO_ACTION=mkdir`.

3. `on-upload.sh` :
   - détecte qu’il s’agit d’un niveau `company/campaign` ;
   - extrait `company_name` et `campaign_name` ;
   - appelle le playbook Ansible `create_grafana_resources.yml`.

4. Le playbook :
   - crée/maintient la team, le folder, la datasource InfluxDB, les dashboards ;
   - utilise `manage_influx_tokens.py` pour obtenir un token InfluxDB dédié
     au bucket du client.

---

## 3. Schéma InfluxDB

### 3.1 Buckets

- **Bucket par client** :  
  - nom : `<company_name>` (ex. `company1`) ;
  - contient toutes les mesures électriques de ce client, toutes campagnes confondues.

- **Bucket meta** (`TSV_META_BUCKET`) :  
  - par défaut `powerview_meta` ;
  - contient les résumés d’exécution du parseur (un point par run).

### 3.2 Measurement et fields (bucket client)

Pour les mesures électriques :

- `measurement` unique : `electrical`  
- un **field par canal**, nommé :

```text
<channel_id>_<unit>
```

Exemples :

- `M02001171_Ch1_M02001171_V`  
- `M02001171_Ch2_M02001171_A`

Chaque point représente une **ligne de données** du TSV (un timestamp) et peut
contenir plusieurs fields (un par canal).

### 3.3 Tags (bucket client)

Tags principaux :

- `campaign` : nom de la campagne (dérivé du chemin) ;
- `channel_id` : identifiant du canal ;
- `channel_unit` : unité (V, A, etc.) ;
- `channel_label` : label lisible ;
- `channel_name` : nom technique ;
- `device` : type d’appareil (ex. T302) ;
- `device_type`, `device_subtype` : typologie plus fine si disponible ;
- `device_master_sn` : numéro de série maître (dérivé du chemin) ;
- `device_sn` : numéro de série du module, si différent ;
- `file_name` : nom du fichier TSV source ;
- éventuellement d’autres tags selon le format.

Ces tags permettent de filtrer/agréger les données dans Grafana.

### 3.4 Schéma du bucket meta

Le bucket meta contient des points décrivant chaque exécution du parseur :

- `measurement` (par exemple `tsv_run_summary`) ;
- tags possibles :
  - `company` ;
  - `campaign` ;
  - `device_master_sn` ;
  - `file_name` ;
  - `status` (`success`, `error`, etc.) ;
- fields possibles :
  - `points_written` ;
  - `lines_total` ;
  - `lines_ignored` ;
  - `duration_ms` ;
  - etc.

Ce schéma permet de construire des dashboards de monitoring du parseur
(derniers runs, taux d’erreur, etc.).

---

## 4. Architecture logicielle du parseur

### 4.1 `BaseTSVParser` et implémentations

`BaseTSVParser` (dans `core.py`) définit l’interface commune :

- `parse_header` ;
- `parse_data` ;
- `build_channel_mappings`.

`MV_T302_V002_Parser` est l’implémentation actuelle pour le format `MV_T302_V002`.

Pour ajouter un nouveau format :

1. Créer une classe `MyNewFormatParser(BaseTSVParser)` ;  
2. Implémenter les méthodes nécessaires ;  
3. Ajouter une valeur dans l’`Enum FileFormat` ;  
4. Enregistrer le parser dans `TSVParserFactory`.

### 4.2 `TSVParserFactory`

`TSVParserFactory.get_parser(file_format: str) -> BaseTSVParser` :

- prend en entrée une chaîne (ou un enum) représentant le format de fichier ;
- retourne une instance du parser adapté (`MV_T302_V002_Parser`, etc.) ;
- permet d’étendre facilement le support de nouveaux formats.

### 4.3 `tsv_parser.py`

Rôles principaux :

- parser les arguments CLI ;
- configurer les logs (`setup_logging`) ;
- orchestrer l’appel à `parse_tsv_header`, `parse_tsv_data` ;
- appeler `influx_utils` pour l’écriture InfluxDB ;
- appeler `fs_utils` pour le déplacement des fichiers ;
- générer les rapports JSON et les résumés meta.

Fonctions clés :

- `_compute_time_range_from_tsv(tsv_file: str) -> Tuple[str, str]`  
  - calcule la plage temporelle couverte par le fichier (min/max timestamp).  

- `process_tsv_file(tsv_file: str, base_folder: str, client: InfluxDBClient, org: str, ...)`  
  - pipeline complet pour un fichier donné.  

- `write_run_report_to_file(report: Dict[str, Any], base_folder: str) -> None`  
  - écrit le rapport JSON sur disque.  

- `main()`  
  - point d’entrée CLI.

---

## 5. Sécurité et isolation

### 5.1 Isolation des données

- Un bucket InfluxDB par client (`company1`, `company2`, …).  
- Une datasource Grafana par client, pointant vers le bucket correspondant.  
- Un token InfluxDB dédié par bucket, utilisé uniquement par la datasource Grafana.

Ainsi, un utilisateur Grafana rattaché à la team `company1` ne voit que :

- le folder `company1` ;
- les dashboards `company1` ;
- la datasource `influxdb_company1` (bucket `company1`).

### 5.2 Gestion des secrets

- `INFLUXDB_ADMIN_TOKEN` est un **secret serveur** :
  - utilisé par `tsv_parser.py` et `manage_influx_tokens.py` ;
  - jamais exposé dans Grafana ou côté client.  

- Les tokens dédiés par bucket (`powerview_token_for_bucket_<company>`) sont
  utilisés dans les datasources Grafana, avec des droits limités.

- Le fichier `.env` ne doit jamais être committé dans Git.

### 5.3 Accès réseau

- Tous les accès externes passent par Caddy en HTTPS :
  - `https://powerview.ton-domaine.tld` → Grafana ;
  - `https://ftp.powerview.ton-domaine.tld` → SFTPGo ;
  - `https://db.powerview.ton-domaine.tld` → InfluxDB.  

- Le firewall UFW limite les ports ouverts (SSH, HTTPS, SFTP).

---

## 6. Évolutions possibles

Quelques pistes d’évolution (également listées dans les TODO) :

- **Fiabilisation de l’upload** :
  - vérifier que l’upload du fichier est complet avant de le traiter ;
  - copier le fichier à son arrivée dans un répertoire technique et traiter
    uniquement la copie.

- **API de configuration / monitoring** :
  - exposer une API légère pour consulter les derniers rapports d’erreur,
    le dernier parsing réussi, etc.  
  - automatiser la création d’utilisateurs Grafana pour une team donnée.

- **Gestion avancée des devices** :
  - permettre à un même device de migrer à travers plusieurs campagnes
    tout en gardant une vue consolidée.

- **Dashboards Grafana** :
  - enrichir et factoriser les dashboards (variables, panels réutilisables,
    templates plus génériques).

---

Fin du document.
