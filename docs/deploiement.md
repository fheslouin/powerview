# Déploiement PowerView

Ce dépôt propose maintenant deux documents séparés pour couvrir les deux
grands scénarios de déploiement :

- **Nouvelle installation complète de l’infrastructure**  
  (serveur vierge, installation d’InfluxDB, Grafana, SFTPGo, Caddy, etc.) :  
  → voir [`deploiement-infra.md`](deploiement-infra.md)

- **Setup / patch des composants sur une infra existante**  
  (mise à jour du code, ajout de clients, ajout de campagnes, nettoyage Grafana, tests) :  
  → voir [`setup-composants.md`](setup-composants.md)

Pour les autres aspects (architecture, intégration SFTPGo/Grafana, utilisation
du parseur, notes de développement), se référer aux documents suivants :

- Architecture technique et schéma InfluxDB : [`architecture.md`](architecture.md)
- Intégration SFTPGo / Ansible / Grafana : [`integration-sftpgo-grafana.md`](integration-sftpgo-grafana.md)
- Utilisation du parseur TSV : [`utilisation-parseur.md`](utilisation-parseur.md)
- How‑tos et pas‑à‑pas : [`howtos.md`](howtos.md)
- Notes pour développeurs / TODO : [`developpement.md`](developpement.md)
