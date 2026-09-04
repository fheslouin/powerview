# Monitoring PowerView

Architecture en trois couches, conçue pour survivre à la mort du serveur qui
surveille (leçon de l'incident de juillet-septembre 2026 : stack morte 7
semaines sans alerte). Toutes les notifications convergent vers un topic
[ntfy](https://ntfy.sh) unique, reçu sur Mac (et mobile en option).

| Couche | Détecte | Outil | Où |
|---|---|---|---|
| 1. Sonde externe | site/serveur injoignable | UptimeRobot | cloud |
| 2. Dead-man switch | stack ou machine malade | `stack_watchdog.sh` + healthchecks.io | serveur (cron ubuntu, 5 min) |
| 3. Pipeline métier | données qui n'arrivent plus | `check_ingestion.py` | serveur (cron sftpgo, 1 h) |

## Configuration commune

Copier le modèle et le remplir (le topic ntfy fait office de secret — nom
long et aléatoire, jamais committé) :

```bash
cp /srv/powerview/tools/monitoring/monitoring.env.sample /srv/powerview/.monitoring.env
```

## Couche 1 — sonde externe (UptimeRobot)

Sur https://uptimerobot.com (gratuit), créer 3 moniteurs HTTP(S), intervalle
5 min :

- `https://powerview.adecwatts.fr/api/health` (Grafana)
- `https://db.powerview.adecwatts.fr/health` (InfluxDB)
- `https://ftp.powerview.adecwatts.fr/` (SFTPGo)

Notification : webhook vers `https://ntfy.sh/<NTFY_TOPIC>` (POST, body =
`*monitorFriendlyName* *alertTypeFriendlyName*`), ou e-mail à défaut.

## Couche 2 — dead-man switch (`stack_watchdog.sh`)

Le script vérifie les 3 conteneurs (`Up`, pas `unhealthy`) et les endpoints
HTTP locaux d'InfluxDB/Grafana. Tout va bien → il pingue healthchecks.io ;
c'est **l'absence de ping** qui alerte : machine morte, cron mort, stack
morte après reboot — tout alerte. En cas de problème détecté, il pingue
`/fail` et notifie ntfy directement (alerte immédiate, sans attendre la
période de grâce).

1. Sur https://healthchecks.io (gratuit), créer un check « powerview-stack »,
   période **5 min**, grâce **10 min** ; intégration ntfy (native) ou webhook
   vers le topic. Coller l'URL de ping dans `HEALTHCHECKS_PING_URL` de
   `.monitoring.env`.
2. Installer le cron (utilisateur **ubuntu**, propriétaire de la stack
   rootless) :

   ```bash
   (crontab -l 2>/dev/null; echo '*/5 * * * * /srv/powerview/tools/monitoring/stack_watchdog.sh >/dev/null 2>&1') | crontab -
   ```

Log : `/srv/powerview/logs/monitoring.log`.

## Couche 3 — pipeline métier (`check_ingestion.py`)

Lit le bucket meta (`tsv_parser_run`) et alerte via ntfy si :

- aucun run depuis plus de `MONITORING_MAX_RUN_AGE_HOURS` (défaut **26 h** —
  le datalogger uploade quotidiennement vers 06:15 UTC) : hook SFTPGo cassé,
  SFTPGo down, `.env` corrompu… ;
- des fichiers `failed` ou `deferred` sur la dernière heure.

Installer le cron (utilisateur **sftpgo**, qui lit `/srv/powerview/.env`) :

```bash
sudo -u sftpgo bash -c "(crontab -l 2>/dev/null; echo '17 * * * * cd /srv/powerview && envs/powerview/bin/python tools/monitoring/check_ingestion.py >> logs/monitoring.log 2>&1') | crontab -"
```

Si InfluxDB est injoignable, le script loggue sans notifier (la couche 2
couvre déjà ce cas toutes les 5 min — pas de double alerte).

Tant qu'un problème persiste, l'alerte se répète à chaque passage horaire
(pas de déduplication — assumé, max 24 notifications/jour).

## Réception sur le Mac (ntfy)

```bash
brew install ntfy
```

Config `~/.config/ntfy/client.yml` :

```yaml
default-host: https://ntfy.sh
subscribe:
  - topic: <NTFY_TOPIC>
    command: 'osascript -e "display notification \"$m\" with title \"$t\" sound name \"Sosumi\""'
```

LaunchAgent pour l'abonnement permanent (relancé au login et en cas de
crash) : `~/Library/LaunchAgents/sh.ntfy.subscribe.plist` avec
`ntfy subscribe --from-config` et `KeepAlive=true`, puis :

```bash
launchctl load ~/Library/LaunchAgents/sh.ntfy.subscribe.plist
```

En complément : app ntfy iOS/Android abonnée au même topic pour les alertes
en déplacement.

## Test de bout en bout

```bash
# Notification directe (doit apparaître sur le Mac)
curl -d "test monitoring" -H "Title: PowerView test" https://ntfy.sh/<NTFY_TOPIC>

# Watchdog en conditions réelles (sur le serveur)
/srv/powerview/tools/monitoring/stack_watchdog.sh; echo "exit=$?"; tail -3 /srv/powerview/logs/monitoring.log

# Vérification ingestion (sur le serveur)
cd /srv/powerview && sudo -u sftpgo envs/powerview/bin/python tools/monitoring/check_ingestion.py
```
