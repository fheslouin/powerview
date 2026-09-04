# Changelog

Tous les changements notables de ce projet sont documentés dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

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
