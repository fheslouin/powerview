# Notes pour développeurs et TODO techniques

Ce document s’adresse aux développeurs qui souhaitent contribuer à PowerView
ou l’adapter à leurs besoins.

---

## 1. Organisation du code

Principaux fichiers Python :

- `tsv_parser.py`  
  - point d’entrée CLI du parseur TSV ;
  - orchestration du traitement d’un fichier ou d’un dossier ;
  - gestion des logs, rapports JSON, intégration InfluxDB.

- `core.py`  
  - définition de l’interface `BaseTSVParser` ;
  - implémentation du parser `MV_T302_V002_Parser` ;
  - factory `TSVParserFactory` ;
  - fonctions utilitaires `parse_tsv_header`, `parse_tsv_data`.

- `fs_utils.py`  
  - fonctions liées au système de fichiers :
    - `extract_path_components` ;
    - `find_tsv_files` ;
    - `move_parsed_file` ;
    - `move_error_file`.

- `influx_utils.py`  
  - fonctions liées à InfluxDB :
    - `setup_influxdb_client` ;
    - `create_bucket_if_not_exists` ;
    - `write_points` ;
    - `write_run_summary_to_influx` ;
    - `count_points_for_file` (utilitaire de vérification).

- `manage_influx_tokens.py`  
  - script CLI utilisé par Ansible pour créer/récupérer des tokens InfluxDB
    dédiés par bucket.

- `on-upload.sh`  
  - script shell appelé par SFTPGo (hooks `upload` / `mkdir`).

- `tests/test_tsv_parser.py`  
  - tests unitaires et d’intégration pour le parseur, `fs_utils`, `influx_utils`.

---

## 2. Ajouter un nouveau format de fichier TSV

Pour supporter un nouveau format de fichier TSV :

1. **Créer une nouvelle classe de parser** dans `core.py` (ou un module dédié) :

   ```python
   class MyNewFormatParser(BaseTSVParser):
       @classmethod
       def build_channel_mappings(cls, line1, line2):
           # TODO: implémenter
           ...

       def parse_header(self, tsv_file: str) -> Tuple[List[Dict], str]:
           # TODO: implémenter
           ...

       def parse_data(
           self,
           tsv_file: str,
           channel_mappings: List[Dict],
           campaign: str,
           bucket_name: str,
           table_name: str,
           # autres paramètres si besoin
       ):
           # TODO: implémenter
           ...
   ```

2. **Étendre l’`Enum FileFormat`** pour inclure le nouveau format.

3. **Enregistrer le parser** dans `TSVParserFactory` :

   ```python
   class TSVParserFactory:
       @classmethod
       def get_parser(cls, file_format: str) -> BaseTSVParser:
           if file_format == FileFormat.MV_T302_V002:
               return MV_T302_V002_Parser()
           elif file_format == FileFormat.MY_NEW_FORMAT:
               return MyNewFormatParser()
           else:
               raise ValueError(f"Unsupported file format: {file_format}")
   ```

4. **Ajouter des tests** dans `tests/test_tsv_parser.py` pour couvrir :
   - le parsing d’en‑tête ;
   - la création de points InfluxDB ;
   - la gestion des erreurs (timestamps/valeurs invalides).

---

## 3. Tests et qualité

### 3.1 Lancer les tests

Ce projet est un projet **Python** (pas Node/JS) : il n’y a pas de `package.json`
et les commandes `yarn test` / `npm test` ne sont pas pertinentes ici.

Depuis `/srv/powerview` :

```bash
source envs/powerview/bin/activate
pytest
```

Les tests couvrent notamment :

- `parse_tsv_header` / `parse_tsv_data` ;
- `extract_path_components` ;
- `find_tsv_files` ;
- `move_parsed_file` / `move_error_file` ;
- `create_bucket_if_not_exists` ;
- `process_tsv_file_writes_points` ;
- `setup_influxdb_client_missing_env`.

### 3.2 Ajout de nouveaux tests

- Ajouter de nouveaux fichiers de test dans `tests/` si nécessaire.  
- Utiliser des fixtures `tmp_path`, `monkeypatch`, `caplog`, `capsys` pour
  isoler les tests.  
- Vérifier que les tests restent **indépendants** de l’environnement réel
  (pas d’accès direct à une vraie instance InfluxDB ou à un vrai SFTP).

---

## 4. TODO techniques (détaillés)

Cette section reprend et détaille les TODO listés dans le README.

### 4.1 Fiabiliser le traitement des uploads

**Problème** : le parseur peut être déclenché alors que l’upload n’est pas
totalement terminé (fichier partiel).

**Pistes** :

- Vérifier la taille du fichier sur deux lectures successives (avec un délai)
  pour s’assurer qu’elle est stable avant de lancer le parsing.  
- Copier le fichier à son arrivée dans un répertoire technique (ex.
  `/srv/powerview/incoming/`) et traiter uniquement la copie.  
- Utiliser un suffixe temporaire côté client (`.part`) puis renommer une fois
  l’upload terminé, et ne traiter que les fichiers sans suffixe.

### 4.2 API de configuration / monitoring

**Objectif** : exposer une API légère (REST ou autre) pour :

- consulter le dernier parsing réussi par fichier / par campagne / par client ;
- lister les 10 derniers rapports d’erreur ;
- déclencher manuellement un retraitement d’un fichier ;
- ajouter un nouvel utilisateur Grafana pour une team donnée.

**Pistes** :

- Créer un petit service FastAPI ou Flask qui lit les rapports JSON et/ou le
  bucket meta InfluxDB.  
- Ajouter des endpoints pour :
  - `/runs/latest` ;
  - `/runs/errors` ;
  - `/grafana/users` (création d’un utilisateur et rattachement à une team).

### 4.3 Gestion avancée des devices

**Problème** : un même device (`device_master_sn`) peut migrer à travers
plusieurs campagnes.

**Pistes** :

- Ajouter des tags supplémentaires pour suivre l’historique d’un device
  (ex. `device_location`, `device_owner`).  
- Créer des dashboards Grafana trans‑campagnes pour un device donné.  
- Ajouter une logique de “device registry” (fichier YAML/JSON ou table InfluxDB)
  décrivant les devices et leurs métadonnées.

### 4.4 Dashboards Grafana

**Objectif** : enrichir et factoriser les dashboards.

**Pistes** :

- Factoriser les panels communs dans le template Jinja
  (`grafana-automation/templates/dashboard.json.j2`).  
- Ajouter des variables de dashboard (sélection de device, de canal, de période).  
- Créer des dashboards de monitoring du parseur (basés sur le bucket meta).  
- Documenter les bonnes pratiques de requêtes pour le plugin
  `influxdb-adecwatts-datasource`.

---

## 5. Style de code et conventions

- Utiliser **typing** (`List`, `Dict`, `Tuple`, `Optional`, etc.) pour toutes
  les nouvelles fonctions.  
- Préférer les **dataclasses** pour les structures de données complexes
  (mappings de canaux, rapports, etc.).  
- Garder les logs **structurés** et informatifs (niveau `INFO` pour le flux
  normal, `WARNING`/`ERROR` pour les anomalies).  
- Respecter la structure actuelle des modules (`core.py`, `fs_utils.py`,
  `influx_utils.py`) pour éviter les dépendances circulaires.

---

## 6. Processus de contribution

1. Créer une branche à partir de `main` ou du tag concerné.  
2. Implémenter les changements.  
3. Ajouter/mettre à jour les tests.  
4. Lancer `pytest` et vérifier que tout passe.  
5. Mettre à jour la documentation dans `docs/` si nécessaire.  
6. Ouvrir une PR avec une description claire (contexte, changements, impact).

---

Fin du document.
