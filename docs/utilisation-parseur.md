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
2. **Valider** la structure des fichiers (en‑têtes, timestamps, valeurs).  
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

- `M02001171_M02001171_U1_V`
- `M02001171_Ch1_W`

Chaque point InfluxDB contient :

- un **timestamp** (issu du TSV, validé) ;
- un **field** (valeur numérique) pour un canal donné ;
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

Le tag `file_name` n’est **plus** ajouté aux points de mesure (pour limiter la
cardinalité). Le nom de fichier reste disponible dans les rapports JSON et dans
le bucket meta.

Ces tags sont construits à partir :

- des métadonnées présentes dans le fichier TSV (en‑têtes, lignes de description) ;
- de la structure du chemin du fichier (voir `fs_utils.extract_path_components`).

---

## 3. Structure attendue des fichiers TSV

Le parseur est basé sur une interface générique `BaseTSVParser` (dans `core.py`)
et plusieurs implémentations concrètes :

- `MV_T302_V002_Parser` pour le format `MV_T302_V002` (T302 historique) ;
- `MV_T302_V003_Parser` pour le format `MV_T302_V003` (avec header JSON).

### 3.1 Format de timestamp (T302 / MV_T302_V00x)

Pour les fichiers actuels de type **T302** (formats `MV_T302_V002` et
`MV_T302_V003`), la première colonne de données (colonne 0) doit être au format :

```text
DD/MM/YY HH:MM:SS
```

Exemples :

```text
05/01/26 10:00:00
05/01/26 10:10:00
```

Ce format est interprété comme :

- `05/01/26` → **5 janvier 2026** (jour/mois/année sur 2 chiffres) ;
- l’heure est considérée comme étant **déjà en UTC** dans le fichier.

Dans le code (`core.parse_timestamp`) :

- on parse la chaîne avec `datetime.strptime(..., "%d/%m/%y %H:%M:%S")` ;
- on attache explicitement `tzinfo=timezone.utc` au `datetime` retourné ;
- l’appel ultérieur à `timestamp()` produit donc un epoch en **secondes UTC**.

Les informations de fuseau éventuellement présentes dans le header JSON V003
(`DataTimeSettings`, `TimeZone`, `TimeZoneName`) ne sont **pas encore exploitées**.

Si un timestamp ne respecte pas ce format, la ligne est ignorée et un warning
est loggé (`Could not parse timestamp: ...`).  
Le compteur `nb_invalid_timestamps` dans le rapport reflète ce nombre de lignes
ignorées pour cause de timestamp invalide.

### 3.2 Interface `BaseTSVParser`

Principales méthodes :

- `parse_header(tsv_file: str) -> Tuple[List[Dict], str]`  
  - Lit les premières lignes utiles du fichier.  
  - Construit une liste de **mappings de canaux** (channel_id, unit, label, etc.).  
  - Retourne également le **file_format** (ex. `MV_T302_V002`).

- `parse_data(tsv_file: str, channel_mappings: List[Dict], campaign: str, bucket_name: str, table_name: str, ...)`  
  - Lit les lignes de données.  
  - Valide les timestamps et les valeurs.  
  - Construit des objets `Point` (InfluxDB) pour chaque ligne/canal.  
  - Gère les erreurs (lignes invalides, valeurs non numériques, etc.).

- `build_channel_mappings(line1, line2)` (méthode de classe)  
  - Interprète les lignes d’en‑tête pour déterminer la structure
    des colonnes (id de canal, unité, nom, etc.).

### 3.3 Format `MV_T302_V002`

L’implémentation `MV_T302_V002_Parser` gère les fichiers TSV produits par les
appareils T302 au format V002.

Structure typique :

- **ligne 1** : numéros de série des devices (master + éventuels slaves) ;
- **ligne 2** : format + libellés de colonnes (ex. `MV_T302_V002`, `Ph 1 V`, `Voie1 W`, …) ;
- **lignes suivantes** : données (`timestamp`, puis une valeur par canal).

La méthode `build_channel_mappings` :

- détecte si le device est **mono** ou **tri** en cherchant `Ph 1`, `Ph 2`, `Ph 3`
  dans les libellés ;
- construit pour chaque colonne un mapping contenant notamment :
  - `column_idx` (index de colonne dans le TSV) ;
  - `channel_id` (ex. `M02001171_M02001171_U1` ou `M02001171_Ch1`) ;
  - `device_type` (`master` ou `slave`) ;
  - `device_subtype` (`mono` ou `tri` pour le master) ;
  - `channel_label` (`U1`, `Ch1`, …) ;
  - `channel_name` (libellé brut sans unité, ex. `Ph 1`, `Voie1`) ;
  - `unit` (`V`, `W`, …) ;
  - `device_master_sn`, `device_sn`, `device`.

### 3.4 Format `MV_T302_V003` (avec header JSON)

L’implémentation `MV_T302_V003_Parser` gère les fichiers TSV de type V003, qui
ont une structure différente :

```text
START_HEADER
{"DataZoneCode":3,"DataTypeCode":2,"DataTimeSettings":"UTC","TimeZone":"CET-1CEST,M3.5.0,M10.5.0/3","TimeZoneName":"Europe/Paris","FileVersion":3,"MasterType":"Mono"}
END_HEADER
START_DATA
02001311	02001311	02001311	...
MV_T302_V003	Ph 1 V	Voie1 W	Voie2 W	...
21/01/26 08:15:24	236.14	0.0	0.0	...
...
END_DATA
```

Caractéristiques :

- bloc `START_HEADER` / `END_HEADER` contenant un **JSON** avec des métadonnées
  globales (ex. `FileVersion`, `MasterType`, `TimeZoneName`, …) ;
- bloc `START_DATA` / `END_DATA` délimitant les données ;
- 1ère ligne après `START_DATA` : SN des devices (souvent tous identiques en mono) ;
- 2ème ligne : format + libellés de colonnes (`MV_T302_V003`, `Ph 1 V`, `Voie1 W`, …) ;
- lignes suivantes : données (`timestamp`, puis une valeur par canal).

Le parser V003 :

- lit et parse le JSON du header (stocké dans `stats["file_header_meta"]`) ;
- construit les mappings de canaux avec la même logique de `channel_id` /
  `channel_label` que V002 (mono) ;
- lit les lignes de données entre `START_DATA` et `END_DATA` sans utiliser
  `skiprows=2` (car le header JSON change la structure).

Les métadonnées du header JSON ne sont **pas encore** utilisées pour enrichir
les tags InfluxDB, mais elles sont disponibles dans les rapports pour une
future base de configuration des devices.

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

1. Lit les premières lignes utiles du fichier :
   - soit directement les 2 premières lignes (V002) ;
   - soit le bloc `START_HEADER` / `END_HEADER` puis `START_DATA` (V003).  
2. Détermine le format de fichier (`MV_T302_V002` ou `MV_T302_V003`).  
3. Appelle `TSVParserFactory.get_parser(file_format)` pour obtenir le parser adapté.  
4. Appelle `parser.build_channel_mappings(line1, line2)`.  
5. Retourne :
   - une liste de `channel_mappings` (un dict par canal) ;
   - le `file_format`.

Chaque `channel_mapping` contient typiquement :

- `column_idx`  
- `channel_id`  
- `unit`  
- `channel_label`  
- `channel_name`  
- `device`  
- `device_type`  
- `device_subtype`  
- `device_master_sn`  
- `device_sn`.

### 5.5 Parsing des données

`parse_tsv_data(...)` (dans `core.py`) :

1. Relit le début du fichier pour déterminer le `file_format`.  
2. Récupère le parser adapté via `TSVParserFactory`.  
3. Délègue à `parser.parse_data(...)` :
   - pour V002, implémentation générique basée sur `pandas.read_csv(..., skiprows=2)` ;
   - pour V003, implémentation spécifique qui lit entre `START_DATA` et `END_DATA`.

Pour chaque ligne de données :

- parse le timestamp (au format `DD/MM/YY HH:MM:SS`, interprété comme UTC) ;
- parse les valeurs de chaque canal ;
- valide les types (timestamp valide, valeur numérique) ;
- construit un `Point` InfluxDB avec :
  - `measurement = "electrical"` ;
  - `time = <timestamp UTC>` ;
  - `field = { "<channel_id>_<unit>": <valeur> }` ;
  - `tags` (voir section 2.3).

En cas d’erreur sur une ligne :

- logge un warning (ex. timestamp invalide, valeur non numérique) ;
- **ignore** la ligne fautive (les autres lignes sont traitées).

La méthode retourne la liste des points construits + un résumé (nombre de lignes,
erreurs, etc.). Pour V003, ce résumé contient en plus `file_header_meta` avec
le JSON du header parsé.

### 5.6 Écriture dans InfluxDB

`influx_utils.write_points(client, bucket_name, org, points, ...)` :

- utilise le client Python InfluxDB (`InfluxDBClient`) ;
- écrit les points dans le bucket `bucket_name` (créé si besoin) ;
- logge un message de succès et imprime également
  `Successfully written to InfluxDB` sur stdout (utilisé par les tests).

`influx_utils.write_run_summary_to_influx(client, org, report, ...)` :

- écrit un **résumé d’exécution** dans le bucket meta (`TSV_META_BUCKET`) ;
- ce résumé contient des informations comme :
  - nombre de fichiers traités ;
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
- écrit un fichier JSON contenant le rapport complet (un par run).

Le rapport inclut typiquement, pour chaque fichier :

- chemin du fichier ;
- bucket cible ;
- campagne ;
- `device_master_sn` ;
- nombre de points écrits ;
- nombre de lignes ignorées ;
- erreurs rencontrées ;
- timestamps min/max (en ISO 8601, UTC) ;
- pour V003, le contenu du header JSON dans `file_header_meta`.

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
   - appelle :
     ```bash
     python3 tsv_parser.py \
       --dataFolder /srv/sftpgo/data \
       --tsvFile "$SFTPGO_ACTION_PATH"
     ```

3. `tsv_parser.py` :
   - parse le fichier (V002 ou V003) ;
   - écrit les points dans InfluxDB (bucket = client, measurement = `electrical`) ;
   - déplace le fichier dans `parsed/` ou `error/` ;
   - écrit un rapport JSON ;
   - écrit un résumé d’exécution dans le bucket meta.

### 6.2 Cas `SFTPGO_ACTION=mkdir`

1. SFTPGo appelle `on-upload.sh` avec :
   - `SFTPGO_ACTION=mkdir`  
   - `SFTPGO_ACTION_PATH=<chemin_absolu_du_dossier>`.

2. `on-upload.sh` :
   - vérifie si le dossier correspond à un niveau `company/campaign`  
     (et non `device`) ;
   - extrait `company_name` et `campaign_name` à partir du chemin relatif
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
   - un dashboard principal pour le client (un par client, pas encore un par campagne).

---

## 7. Tests automatisés

Les tests se trouvent dans `tests/test_tsv_parser.py`.  
Ils couvrent notamment :

- parsing d’en‑tête V002 et V003 (`test_parse_tsv_header_*`) ;
- création de points InfluxDB (`test_parse_tsv_data_creates_points`,
  `test_parse_tsv_data_v003_creates_points`) ;
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

Ce projet est un projet **Python**, pas un projet Node/JS : il n’y a pas de
`package.json` et les commandes `yarn test` / `npm test` ne sont pas pertinentes
ici (elles renverront une erreur “Couldn’t find a package.json file …”).

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
