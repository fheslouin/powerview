# Changelog

Tous les changements notables de ce projet sont documentés dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

## [0.6.0] - 2026-09-09

### Ajouté

- `check_ingestion.py` : l'alerte détaille chaque fichier en échec ou différé
  (`bucket/campagne/device/nom` et cause), lus dans les points
  `tsv_parser_file` du bucket meta. `influx_utils.py` écrit désormais la cause
  (champ `error`) sur ces points.
- `check_ingestion.py` : livraison « au moins une fois » via un fichier d'état
  (`MONITORING_STATE_FILE`, défaut `logs/check_ingestion.state`) ; une alerte
  refusée par ntfy est renvoyée au passage suivant au lieu d'être perdue.

### Corrigé

- Monitoring : les notifications ntfy partaient en IPv6 et étaient refusées
  (`429 daily message quota reached`, quota anonyme compté par préfixe `/64`
  partagé chez l'hébergeur). Les deux échecs de parsing du 2026-09-08 ont été
  détectés mais jamais notifiés. IPv4 forcé dans `check_ingestion.py`
  (`NTFY_FORCE_IPV4`, défaut 1) et `stack_watchdog.sh` (`curl -4`).
- `check_ingestion.py` : le journal n'expose plus l'URL du topic ntfy en cas
  d'échec d'envoi.

## [0.5.2] - 2026-09-09

### Corrigé

- `core.py` : les fichiers portant un bloc `START_HEADER` mais un marqueur
  `MV_T302_V002` (device 02001315, campagne `AUE_corse/SARTENE`) partaient en
  `error/` avec « Expected 1 fields in line 5, saw 25 ». Le parseur est
  désormais choisi d'après la structure du fichier
  (`TSVParserFactory.get_parser_for_file`) : bloc d'en-tête présent = parseur
  V003, quel que soit le marqueur. La lecture des lignes de format est
  centralisée dans `read_format_lines` (supprime trois copies de la même
  boucle dans `core.py` et `tsv_parser.py`).

## [0.5.1] - 2026-09-04

### Corrigé

- `check_ingestion.py` : un fichier `.env` / `.monitoring.env` illisible
  (permissions) faisait crasher la vérification avant toute alerte ; dégradé
  en warning.

## [0.5.0] - 2026-09-04

### Ajouté

- Monitoring en trois couches (`docs/monitoring.md`) suite à l'incident de
  l'été 2026 (stack morte 7 semaines sans alerte) :
  - `tools/monitoring/stack_watchdog.sh` — dead-man switch cron 5 min :
    vérifie conteneurs et endpoints locaux, pingue healthchecks.io (l'absence
    de ping alerte), notifie ntfy en cas de problème détecté ;
  - `tools/monitoring/check_ingestion.py` — vérification horaire du pipeline
    dans le bucket meta : dernier run trop vieux (seuil 26 h configurable via
    `MONITORING_MAX_RUN_AGE_HOURS`), fichiers `failed`/`deferred` récents ;
  - `tools/monitoring/monitoring.env.sample` — modèle de configuration
    (`NTFY_TOPIC`, `HEALTHCHECKS_PING_URL`), à copier en `.monitoring.env`
    non committé ;
  - couche externe documentée : UptimeRobot sur les URLs publiques,
    notifications convergeant vers un topic ntfy unique (Mac + mobile).

## [0.4.3] - 2026-09-03

### Corrigé

- `docker-compose.yml` : le healthcheck InfluxDB (`influx ping -host localhost`)
  échouait systématiquement (`unsupported protocol scheme`), laissant le
  conteneur `unhealthy` en permanence — le CLI v2 attend une URL complète
  (`--host http://localhost:8086`).

## [0.4.2] - 2026-09-03

### Corrigé

- `tsv_parser.py` : une panne InfluxDB (connexion refusée, timeout, 5xx
  passerelle) envoyait les fichiers TSV en `error/`, dossier exclu des scans
  suivants — les imports étaient perdus silencieusement même après le retour
  d'InfluxDB (incident AUE_corse du 2026-09-02). Ces erreurs sont désormais
  requalifiées en `InfluxUnavailableError` (nouvelle exception typée dans
  `influx_utils.py`) et le fichier, marqué `deferred`, reste en place pour être
  rejoué au prochain déclenchement du hook. Un `ping()` InfluxDB en début de
  run arrête par ailleurs le traitement sans déplacer aucun fichier si le
  serveur ne répond pas.

### Ajouté

- Compteur `nb_files_deferred` dans le rapport JSON de run et le résumé écrit
  dans le bucket meta (`tsv_parser_run`).
