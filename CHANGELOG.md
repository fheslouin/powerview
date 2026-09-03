# Changelog

Tous les changements notables de ce projet sont documentés dans ce fichier.

Le format est basé sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet adhère au [Semantic Versioning](https://semver.org/lang/fr/).

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
