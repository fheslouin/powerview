# Utilisation du parseur TSV (`tsv_parser.py`)

Ce document décrit en détail le fonctionnement et l’utilisation du parseur TSV
de PowerView, implémenté principalement dans :

- `tsv_parser.py` (orchestration, CLI, logs, rapports) ;
- `core.py` (logique de parsing TSV, factory de parseurs) ;
- `fs_utils.py` (gestion des chemins, déplacement des fichiers) ;
- `influx_utils.py` (écriture dans InfluxDB, bucket meta).

---

## 1. Objectifs et périmètre

Le parseur a pour rôle de :

1. **Découvrir** les fichiers `.tsv` à traiter dans un dossier racine.  
2. **Valider** la structure des fichiers (en-têtes, timestamps, valeurs).  
3. **Construire** des points InfluxDB selon un schéma unifié.  
4. **Écrire** ces points dans le bucket InfluxDB du client.  
5. **Déplacer** les fichiers traités dans des sous‑dossiers (`parsed/` ou `error/`).  
6. **Générer** un rapport JSON détaillé et un résumé d’exécution dans un bucket meta.

Le parseur est conçu pour être appelé :

- soit **manuellement** en ligne de commande ;
- soit **automatiquement** par le script `on-upload.sh` (hook SFTPGo).

---

## 2. Schéma de données InfluxDB

### 2.1 Buckets

- Un **bucket par client** (`<company_name>`).  
- Un **bucket meta** (par défaut `powerview_meta`) pour les résumés d’exécution.

Les buckets sont créés automatiquement si besoin (via `influx_utils.create_bucket_if_not_exists`).

### 2.2 Measurement et fields

Le schéma est **unifié** :

- `measurement` unique : `electrical`  
- un **field par canal**, nommé :

```text
<channel_id>_<unit>
```

Exemples :

- `M02001171_Ch1_M02001171_V`
- `M02001171_Ch2_M02001171_A`

Chaque point InfluxDB contient :

- un **timestamp** (issu du TSV, validé) ;
- un ou plusieurs **fields** (valeurs numériques) ;
- des **tags** décrivant le contexte.

### 2.3 Tags principaux

Les tags typiques sont :

- `campaign`  
- `channel_id`  
- `channel_unit`  
- `channel_label`  
- `channel_name`  
- `device`  
- `device_type`  
- `device_subtype`  
- `device_master_sn`  
- `device_sn`  
- `file_name`  
- etc.

Ces tags sont construits à partir :

- des métadonnées présentes dans le fichier TSV (en‑têtes, lignes de description) ;
- de la structure du chemin du fichier (voir `fs_utils.extract_path_components`).

---

## 3. Structure attendue des fichiers TSV

Le parseur est basé sur une interface générique `BaseTSVParser` (dans `core.py`)
et une implémentation actuelle `MV_T302_V002_Parser` pour le format `MV_T302_V002`.

### 3.1 Interface `BaseTSVParser`

Principales méthodes :

- `parse_header(tsv_file: str) -> Tuple[List[Dict], str]`  
  - Lit les premières lignes du fichier.  
  - Construit une liste de **mappings de canaux** (channel_id, unit, label, etc.).  
  - Retourne également une **ligne d’en‑tête brute** (pour référence).

- `parse_data(tsv_file: str, channel_mappings: List[Dict], campaign: str, bucket_name: str, table_name: str, ...)`  
  - Lit les lignes de données.  
  - Valide les timestamps et les valeurs.  
  - Construit des objets `Point` (InfluxDB) pour chaque ligne.  
  - Gère les erreurs (lignes invalides, valeurs non numériques, etc.).

- `build_channel_mappings(line1, line2)` (méthode de classe)  
  - Interprète les deux premières lignes (ou plus) pour déterminer la structure
    des colonnes (id de canal, unité, nom, etc.).

### 3.2 Format `MV_T302_V002`

L’implémentation `MV_T302_V002_Parser` gère un format spécifique de fichiers TSV
produits par les appareils T302.

Caractéristiques typiques :

- premières lignes contenant des métadonnées (nom de campagne, appareil, etc.) ;
- lignes d’en‑tête décrivant les canaux (id, unité, label) ;
- colonnes de données avec un timestamp + une valeur par canal.

Pour ajouter un **nouveau format**, il suffit de :

1. Créer une nouvelle classe héritant de `BaseTSVParser`.  
2. Implémenter `build_channel_mappings`, `parse_header`, `parse_data`.  
3. L’enregistrer dans `TSVParserFactory` (dans `core.py`) avec un nouvel enum `FileFormat`.

---

## 4. CLI et options de `tsv_parser.py`

### 4.1 Options principales

Les options exactes peuvent être consultées via :

```bash
python3 tsv_parser.py --help
```

Les plus importantes sont :

- `--dataFolder`  
  Dossier racine contenant les données SFTP, ex. :
  ```bash
  --dataFolder /srv/sftpgo/data
  ```

- `--tsvFile`  
  Chemin complet vers un fichier TSV à traiter.  
  Si non fourni, le parseur peut parcourir `dataFolder` pour trouver les fichiers.

- `--dry-run`  
  Mode simulation :  
  - aucune écriture InfluxDB ;  
  - aucun déplacement de fichiers ;  
  - aucun rapport JSON sur disque (le rapport est seulement affiché sur stdout).

- `--log-level` (si exposé)  
  Permet de surcharger `TSV_LOG_LEVEL` pour un run donné.

### 4.2 Exemples d’utilisation

Dry‑run sur tout un dossier :

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --dry-run
```

Traitement réel d’un fichier précis :

```bash
python3 tsv_parser.py \
  --dataFolder /srv/sftpgo/data \
  --tsvFile /srv/sftpgo/data/company1/campaign1/02001084/T302_251012_031720.tsv
```

---

## 5. Comportement détaillé du parseur

### 5.1 Initialisation et logs

Dans `tsv_parser.py` :

- `setup_logging()` configure le logger `tsv_parser` en fonction de :
  - la variable d’environnement `TSV_LOG_LEVEL` ;
  - éventuellement des options CLI.

Les logs sont envoyés sur stdout/stderr et peuvent être redirigés par `on-upload.sh`
vers un fichier (ex. `/srv/sftpgo/logs/uploads.log`).

### 5.2 Découverte des fichiers

La fonction `fs_utils.find_tsv_files(base_folder: str) -> List[str]` :

- parcourt récursivement `base_folder` ;
- retourne la liste des fichiers `.tsv` à traiter ;
- **exclut** les fichiers déjà déplacés dans `parsed/` ou `error/`.

Cette fonction est utilisée lorsque `--tsvFile` n’est pas fourni.

### 5.3 Extraction des métadonnées de chemin

`fs_utils.extract_path_components(tsv_path: str, base_folder: str) -> Tuple[str, str, str]` :

- vérifie que `tsv_path` est bien sous `base_folder` ;
- extrait :
  - `company_name` ;
  - `campaign_name` ;
  - `device_master_sn`.

Exemple :

```text
/srv/sftpgo/data/company1/campaign1/02001084/T302_251012_031720.tsv
```

→ `company1`, `campaign1`, `02001084`.

Ces informations sont utilisées :

- pour déterminer le **bucket** InfluxDB (`company1`) ;
- pour renseigner les **tags** (`campaign`, `device_master_sn`, etc.) ;
- pour nommer certains champs du rapport.

### 5.4 Parsing de l’en‑tête

`parse_tsv_header(tsv_file: str) -> Tuple[List[Dict], str]` (dans `core.py`) :

1. Détermine le format de fichier (via `TSVParserFactory.get_parser`).  
2. Appelle `parser.parse_header(tsv_file)`.  
3. Retourne :
   - une liste de `channel_mappings` (un dict par canal) ;
   - une représentation brute de l’en‑tête (pour debug / rapport).

Chaque `channel_mapping` contient typiquement :

- `channel_id`  
- `unit`  
- `label`  
- `name`  
- éventuellement d’autres métadonnées.

### 5.5 Parsing des données

`parse_tsv_data(...)` (dans `core.py`) :

1. Ouvre le fichier TSV.  
2. Pour chaque ligne de données :
   - parse le timestamp ;
   - parse les valeurs de chaque canal ;
   - valide les types (timestamp valide, valeur numérique) ;
   - construit un `Point` InfluxDB avec :
     - `measurement = "electrical"` ;
     - `time = <timestamp>` ;
     - `fields = { "<channel_id>_<unit>": <valeur> }` ;
     - `tags` (voir section 2.3).

3. En cas d’erreur sur une ligne :
   - logge un warning (ex. timestamp invalide, valeur non numérique) ;
   - **ignore** la ligne fautive (les autres lignes sont traitées).

4. Retourne la liste des points construits + un résumé (nombre de lignes, erreurs, etc.).

Les tests `tests/test_tsv_parser.py` couvrent notamment :

- `test_parse_tsv_data_invalid_timestamp_is_skipped`  
- `test_parse_tsv_data_invalid_value_is_skipped`

### 5.6 Écriture dans InfluxDB

`influx_utils.write_points(client, bucket_name, org, points, ...)` :

- utilise le client Python InfluxDB (`InfluxDBClient`) ;
- écrit les points dans le bucket `bucket_name` (créé si besoin) ;
- gère les erreurs d’écriture (exceptions, timeouts, etc.).

`influx_utils.write_run_summary_to_influx(client, org, report, ...)` :

- écrit un **résumé d’exécution** dans le bucket meta (`TSV_META_BUCKET`) ;
- ce résumé contient des informations comme :
  - nom du fichier ;
  - bucket cible ;
  - nombre de points écrits ;
  - nombre de lignes ignorées ;
  - durée du parsing, etc.

### 5.7 Déplacement des fichiers

`fs_utils.move_parsed_file(tsv_file: str) -> None` :

- crée (si besoin) un sous‑dossier `parsed/` à côté du fichier ;
- déplace le fichier TSV dans ce sous‑dossier ;
- logge l’opération.

`fs_utils.move_error_file(tsv_file: str) -> None` :

- même logique, mais vers un sous‑dossier `error/`.

Ces fonctions sont appelées par `tsv_parser.process_tsv_file` en fonction du
succès ou de l’échec du parsing/écriture.

### 5.8 Rapport JSON sur disque

`tsv_parser.write_run_report_to_file(report: Dict[str, Any], base_folder: str) -> None` :

- détermine le dossier de rapports :
  - si `TSV_REPORT_DIR` est défini → utilise cette valeur ;
  - sinon → `<base_folder>/../logs/reports`.
- crée le dossier si nécessaire.  
- écrit un fichier JSON contenant le rapport complet (un par fichier TSV traité).

Le rapport inclut typiquement :

- chemin du fichier ;
- bucket cible ;
- nombre de points écrits ;
- nombre de lignes ignorées ;
- erreurs rencontrées ;
- timestamps min/max ;
- etc.

---

## 6. Intégration avec SFTPGo (`on-upload.sh`)

Le script `on-upload.sh` est appelé par SFTPGo avec différentes valeurs de
`SFTPGO_ACTION` :

- `upload` (hook `post_disconnect`) ;
- `mkdir` (hook d’action).

### 6.1 Cas `SFTPGO_ACTION=upload`

1. SFTPGo appelle `on-upload.sh` avec :
   - `SFTPGO_ACTION=upload`  
   - `SFTPGO_ACTION_PATH=<chemin_absolu_du_fichier>`.

2. `on-upload.sh` :
   - active l’environnement virtuel Python ;
   - charge les variables de `.env` ;
   - appelle `tsv_parser.py` avec :
     ```bash
     python3 tsv_parser.py \
       --dataFolder /srv/sftpgo/data \
       --tsvFile "$SFTPGO_ACTION_PATH"
     ```

3. Le parseur :
   - parse le fichier ;
   - écrit les points dans InfluxDB ;
   - déplace le fichier (`parsed/` ou `error/`) ;
   - écrit un rapport JSON ;
   - écrit un résumé d’exécution dans le bucket meta.

### 6.2 Cas `SFTPGO_ACTION=mkdir`

1. SFTPGo appelle `on-upload.sh` avec :
   - `SFTPGO_ACTION=mkdir`  
   - `SFTPGO_ACTION_PATH=<chemin_absolu_du_dossier>`.

2. `on-upload.sh` :
   - vérifie si le dossier correspond à un niveau `company/campaign`  
     (et non `device`) ;
   - si oui, extrait `company_name` et `campaign_name` à partir du chemin relatif
     à `/srv/sftpgo/data` ;
   - appelle le playbook Ansible :
     ```bash
     ansible-playbook grafana-automation/playbooks/create_grafana_resources.yml \
       --extra-vars "company_name=<company> campaign_name=<campaign>"
     ```

3. Le playbook crée/maintient :
   - la team Grafana du client ;
   - le folder Grafana du client ;
   - la datasource InfluxDB dédiée (`influxdb_<company>`) ;
   - **un dashboard principal pour le client** (actuellement un seul dashboard par client, pas un par campagne).

---

## 7. Tests automatisés

Les tests se trouvent dans `tests/test_tsv_parser.py`.  
Ils couvrent notamment :

- parsing d’en‑tête (`test_parse_tsv_header_basic`) ;
- création de points InfluxDB (`test_parse_tsv_data_creates_points`) ;
- gestion des timestamps/valeurs invalides ;
- fonctions de `fs_utils` (`extract_path_components`, `find_tsv_files`, `move_parsed_file`, `move_error_file`) ;
- fonctions de `influx_utils` (`create_bucket_if_not_exists`, `write_points`, etc.) ;
- intégration globale (`test_process_tsv_file_writes_points`).

Pour lancer les tests :

```bash
cd /srv/powerview
source envs/powerview/bin/activate
pytest
```

---

## 8. Bonnes pratiques d’utilisation

- Toujours tester un **dry‑run** sur un nouveau jeu de données avant de lancer
  un traitement réel.  
- Vérifier régulièrement les **rapports JSON** pour détecter des anomalies
  (timestamps invalides, valeurs manquantes, etc.).  
- Surveiller la **taille des buckets** InfluxDB et mettre en place des politiques
  de rétention adaptées.  
- Documenter pour chaque client :
  - le bucket InfluxDB ;
  - les campagnes (dossiers SFTP) associées ;
  - les appareils (`device_master_sn`) utilisés.

---

Fin du document.
