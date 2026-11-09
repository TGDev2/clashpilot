# ClashPilot

Planificateur local d'améliorations pour Clash of Clans : un backend FastAPI (Python 3.13,
OR-Tools) qui sert aussi l'application React construite, le tout dans un seul processus.

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

## Utilisation

```
npm start      # construit le frontend puis sert l'API et l'application sur http://127.0.0.1:8000
npm run dev    # backend avec rechargement + serveur Vite (proxy /api vers le backend)
```

Variables d'environnement : `CLASHPILOT_HOST`, `CLASHPILOT_PORT`, `CLASHPILOT_DATA_DIR`,
`CLASHPILOT_COC_TOKEN`. Ouvrir l'accès au réseau local (`CLASHPILOT_HOST=0.0.0.0`) n'ajoute
aucune authentification.

## Vérification

```
npm run verify              # lint, types, tests unitaires et build (quelques minutes)
node scripts/test-one.mjs <fichier>   # un seul fichier de test (.mjs, .py, .ts, .tsx)
npm run verify:full         # verify + tests lents + parcours de bout en bout
```

## Licence

MIT, voir `LICENSE`.
