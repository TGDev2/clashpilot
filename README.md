# ClashPilot

Planificateur local d'améliorations pour Clash of Clans : un backend FastAPI (Python 3.13,
OR-Tools) qui sert aussi l'application React construite, le tout dans un seul processus. Le plan
tient compte de vos constructeurs, de vos horaires de jeu et de vos ressources, et chaque
résultat dit d'où vient la donnée (confirmée, estimée ou inconnue).

Projet non officiel, sans lien avec Supercell.

## Prérequis

- Node.js 22.14 ou plus récent
- Python 3.13 (sous Windows : le lanceur `py -3.13`)

## Installation

```
npm run setup
```

Crée `backend/.venv`, installe les dépendances épinglées du backend et du frontend.
La commande peut être relancée sans risque : elle ne réinstalle rien si rien n'a changé.

## Démarrage

```
npm start      # construit le frontend puis sert l'API et l'application sur http://127.0.0.1:8000
npm run dev    # backend avec rechargement + serveur Vite (proxy /api vers le backend)
```

Ouvrez ensuite `http://127.0.0.1:8000`. Les données (base SQLite) sont rangées dans le dossier
`data/` ; le dossier peut être changé avec `CLASHPILOT_DATA_DIR`.

## Construire le pack de données

Aucune donnée de jeu réelle n'est livrée avec le dépôt. Le pack (noms, durées, coûts des
améliorations) se construit hors ligne, à partir d'une copie locale du jeu de données de la
communauté `clash-of-clans-data` (dossier `data/` du jeu de données) :

```
backend/.venv/Scripts/python -m clashpilot.gamedata.builder \
  --input chemin/vers/clash-of-clans-data/data \
  --output pack.json \
  --pack-id mon-pack --game-version 2026-11 \
  --as-of 2026-11-01 --retrieved-at 2026-11-11
```

(sous Linux ou macOS : `backend/.venv/bin/python`). L'outil ne touche pas au réseau. Un niveau
illisible n'est jamais deviné : il est écarté du pack et signalé. Importez ensuite `pack.json` dans
la page **Importer**, section « Données de jeu », puis activez-le. Un pack ne doit pas dépasser 5 Mo.

## Importer un village

1. Dans le jeu, copiez l'export de votre village (JSON).
2. Page **Importer** : collez l'export et validez. Un export plus récent remplace le précédent et
   la page « Changements » montre ce qui a bougé.
3. Complétez la **Saisie manuelle** (ressources en stock, revenu par heure, nombre de
   constructeurs) : ce que l'export ne contient pas reste « inconnu » tant que vous ne le saisissez
   pas, jamais compté comme zéro.
4. Renseignez vos horaires dans **Réglages**, puis calculez votre plan dans **Que faire
   maintenant ?**.

## Accès depuis le réseau local (`CLASHPILOT_HOST`)

Par défaut le serveur n'écoute que `127.0.0.1` : seul cet ordinateur y accède. Pour l'ouvrir depuis
un téléphone du même réseau, lancez-le avec `CLASHPILOT_HOST=0.0.0.0` (le port se règle avec
`CLASHPILOT_PORT`).

Le serveur ne répond qu'aux noms `127.0.0.1` et `localhost` (et à `CLASHPILOT_HOST` s'il est
explicite). Pour l'atteindre sous un autre nom, par exemple via Tailscale Serve ou l'adresse IP de
l'ordinateur, listez ces noms dans `CLASHPILOT_ALLOWED_HOSTS`, séparés par des virgules (la liste
remplace alors les valeurs par défaut) : `CLASHPILOT_ALLOWED_HOSTS=192.168.1.20,localhost`. Avec
`CLASHPILOT_HOST=0.0.0.0` et sans liste, tous les noms sont acceptés. Dans tous les cas, une
écriture envoyée par la page d'un autre site est refusée.

**Attention, avertissement de sécurité : ClashPilot n'a aucune authentification.** Avec
`CLASHPILOT_HOST=0.0.0.0`, toute personne qui atteint le port lit et modifie vos données. Ne
l'utilisez que sur un réseau de confiance, jamais derrière une redirection de port vers Internet.

L'installation de l'application sur un téléphone (PWA) et le service worker exigent un contexte
sécurisé : `localhost` sur l'ordinateur, HTTPS sur le téléphone (par exemple Tailscale Serve ou un
certificat local). Sur `http://192.168.x.x`, l'application reste une simple page web.

## Rappels et agenda

Les rappels de fin d'amélioration (page **Réglages**, section Notifications) ne fonctionnent que
lorsque l'application est ouverte : il n'y a pas de notification en arrière-plan. Pour être prévenu
application fermée, exportez le plan vers l'agenda du téléphone (`Exporter vers mon agenda (.ics)`) :
chaque fin d'amélioration devient un événement avec une alarme.

## Limites de l'API officielle

La synchronisation avec l'API officielle de Clash of Clans est facultative et limitée :

- elle ne lit que les niveaux des troupes, des héros, des sorts et de l'équipement ; les bâtiments,
  les ressources et les améliorations en cours viennent de l'export et de la saisie manuelle ;
- elle exige un jeton, lu uniquement dans la variable d'environnement `CLASHPILOT_COC_TOKEN` (il
  n'est jamais saisi dans l'interface ni enregistré) ; l'API officielle limite aussi le jeton à
  certaines adresses IP ;
- une valeur lue par l'API s'affiche avec sa source, sans toucher à l'export ; en cas de désaccord
  avec l'export, la page signale un conflit.

## Vérification

```
npm run verify              # lint, types, tests unitaires et build (quelques minutes)
node scripts/test-one.mjs <fichier>   # un seul fichier de test (.mjs, .py, .ts, .tsx)
npm run e2e                 # parcours de bout en bout dans Chromium (Playwright, plus lent)
npm run verify:full         # verify + tests lents + parcours de bout en bout
```

## Mentions

> This material is unofficial and is not endorsed by Supercell. For more information see Supercell's Fan Content Policy: www.supercell.com/fan-content-policy.

## Licence

MIT, voir `LICENSE`.
