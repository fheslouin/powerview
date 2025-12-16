# Plugin de panel Grafana `adecwatts-series-config-panel`

Ce répertoire contient le code du **panel Grafana custom** `adecwatts-series-config-panel`,
utilisé par PowerView pour configurer les séries (voies) à afficher dans les
dashboards de campagne.

Ce panel est un plugin Grafana de type **panel** (React/TypeScript) qui
s’intègre avec :

- la datasource custom `influxdb-adecwatts-datasource` ;
- l’API de configuration `powerview-config-api` (service `powerview-config-api`).

---

## 1. Rôle fonctionnel du panel

Le panel « configuration des voies » sert de **panneau de contrôle** pour les
autres panels du dashboard :

- lister les **voies disponibles** pour un client / une campagne ;
- activer / désactiver des voies (sélection utilisateur) ;
- gérer des **métadonnées de séries** :
  - labels lisibles ;
  - regroupements (triphasique, virtuelles, etc.) ;
  - éventuels alias / couleurs par défaut ;
- exposer la configuration choisie aux autres panels (via variables, tags,
  ou API interne du plugin datasource).

Typiquement, le dashboard de campagne contient :

1. Un panel `adecwatts-series-config-panel` en haut de page ;
2. Un ou plusieurs panels de visualisation (timeseries) qui consomment la
   configuration choisie (liste de séries visibles, filtres, etc.).

---

## 2. Intégration dans les dashboards Grafana

Le panel est instancié dans le template de dashboard Ansible :

- fichier : `grafana-automation/templates/dashboard.json.j2`
- type de panel : `"type": "adecwatts-series-config-panel"`

Extrait du template :

```json
{
  "type": "adecwatts-series-config-panel",
  "title": "Configuration des voies",
  "datasource": {
    "type": "influxdb-adecwatts-datasource",
    "uid": "{{ datasource_uid }}"
  },
  "options": {
    "forcedClientId": "{{ datasource_client_id }}",
    "forcedDatasourceId": {{ datasource_id }}
  }
}
```

Les options importantes sont :

- `forcedClientId`  
  Identifiant logique du client, construit côté Ansible à partir de la
  datasource InfluxDB (`company_name` + login par défaut).  
  Le panel peut l’utiliser pour appeler l’API de configuration ou filtrer
  les séries.

- `forcedDatasourceId`  
  ID numérique de la datasource Grafana `influxdb_<company_name>`.  
  Utile si le panel doit effectuer des appels proxifiés via Grafana
  (`/api/datasources/proxy/...`).

Le panel doit donc :

- accepter ces options dans sa configuration ;
- les utiliser comme **valeurs par défaut non modifiables** par l’utilisateur
  final (d’où le préfixe `forced*`).

---

## 3. Structure attendue du répertoire `panel/`

Une structure typique (à adapter selon l’outillage choisi) :

```text
panel/
  ├── src/                     # code source TypeScript/React du panel
  │   ├── module.ts            # point d’entrée du plugin Grafana
  │   ├── Panel.tsx            # composant principal du panel
  │   ├── types.ts             # types/DTO internes
  │   └── services/            # appels API (config séries, etc.)
  ├── dist/                    # build final du plugin (copié sur le serveur Grafana)
  ├── package.json             # config npm/yarn
  ├── tsconfig.json            # config TypeScript
  ├── webpack.config.js        # ou vite.config.ts / esbuild, etc.
  ├── plugin.json              # manifeste Grafana du panel
  └── README.md                # ce fichier
```

Seul le contenu de `dist/` est nécessaire côté serveur Grafana.

---

## 4. Installation et développement local

### 4.1 Prérequis

- Node.js (version recommandée par la doc Grafana pour les plugins, ex. 18 LTS)
- Yarn ou npm
- Une instance Grafana locale (ou distante) pour tester le plugin

### 4.2 Installation des dépendances

Depuis le répertoire `panel/` :

```bash
cd panel
yarn install
# ou
npm install
```

### 4.3 Lancer le mode développement

Selon la configuration du projet (webpack, vite, etc.) :

```bash
yarn dev
# ou
yarn watch
# ou
npm run dev
```

Ensuite, lancer Grafana en pointant vers le répertoire du plugin en
développement, par exemple :

- en ajoutant un volume dans `docker-compose.yml` vers le répertoire du plugin ;
- ou en créant un lien symbolique depuis le dossier de plugins Grafana
  vers `panel/dist` (ou le dossier de build utilisé en dev).

---

## 5. Build et déploiement en production

### 5.1 Build

Toujours depuis `panel/` :

```bash
yarn build
# ou
npm run build
```

Le résultat doit se trouver dans `panel/dist/` (ou un dossier équivalent
selon la configuration du bundler).

### 5.2 Déploiement sur le serveur Grafana

Sur ta machine de build :

```bash
cd panel
yarn build
scp -r dist/ ubuntu@serveur:/srv/grafana-plugins/adecwatts-series-config-panel
```

Sur le serveur, le `docker-compose.yml` de PowerView doit monter ce
répertoire dans Grafana, par exemple :

```yaml
grafana:
  volumes:
    - /srv/grafana-plugins/adecwatts-series-config-panel:/var/lib/grafana/plugins/adecwatts-series-config-panel:ro
```

Et Grafana doit être configuré pour autoriser les plugins non signés :

```yaml
environment:
  GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS: "influxdb-adecwatts-datasource,adecwatts-series-config-panel"
```

Après copie, redémarrer Grafana (ou le conteneur) si nécessaire.

---

## 6. Intégration avec la datasource et l’API de config

Le panel est conçu pour fonctionner avec :

- la datasource `influxdb-adecwatts-datasource` (plugin custom) ;
- l’API `powerview-config-api` (service `powerview-config-api` dans
  `docker-compose.yml`).

Scénario typique :

1. Le panel récupère la liste des séries / canaux disponibles pour un client
   et une campagne (via l’API ou la datasource).
2. L’utilisateur coche/décoche les séries à afficher.
3. Le panel enregistre cette configuration (via l’API de config) et/ou
   la propage aux autres panels (par exemple via des variables de dashboard
   ou des tags spéciaux dans les requêtes Flux).

Les détails exacts de l’API (endpoints, payloads) doivent être documentés
dans la doc développeur du panel (TODO).

---

## 7. Tests

Selon la configuration du projet, des tests peuvent être définis dans
`package.json` :

```json
{
  "scripts": {
    "test": "jest",
    "lint": "eslint src --ext .ts,.tsx"
  }
}
```

Exécution :

```bash
cd panel
yarn test
# ou
npm test
```

> Si tu lances `yarn test` à la racine du dépôt sans `package.json`, tu
> obtiendras une erreur « Couldn't find a package.json file ».  
> Il faut exécuter les commandes Node/Yarn **dans le dossier `panel/`**.

---

## 8. Pistes d’évolution du panel

- Ajouter une UI plus riche pour :
  - filtrer les séries par type (tension, courant, puissance, etc.) ;
  - gérer des groupes de séries (triphasique, virtuelles) ;
  - sauvegarder des “profils” de configuration.
- Intégrer un retour visuel sur l’état de la datasource (erreurs Flux,
  absence de données, etc.).
- Ajouter des tests unitaires et e2e (ex. Playwright) pour valider
  l’ergonomie et les interactions avec Grafana.

---

Fin du fichier.
