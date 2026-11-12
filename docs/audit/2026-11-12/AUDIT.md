# Audit par sondes — ClashPilot — 2026-11-12

## 1. Synthèse

**Verdict : Prêt sous réserve.** Aucune sonde rouge critique, mais trois rouges hautes.

- **Sondes** : 7 rouges (0 critique, 3 hautes, 2 moyennes, 2 basses), 8 vertes, 0 non concluante.
- **Mutations** : 80 % détectées (16 sur 20), vérification réparée (voir S01).

**Rouges majeures**

1. **S01 — La vérification unique échoue sur un clone propre** (haute). `npm run verify` sort en
   erreur 4 fois sur 4 : la voie `node` écrit des `e01-*.test.ts` temporaires dans
   `frontend/src` pendant que la voie `frontend` lance Vitest, qui les ramasse puis ne les trouve
   plus. Backlog Autopilot ne peut rien livrer tant que c'est rouge. Correction validée : la voie
   `frontend` attend la fin de la voie `node`, sans allonger la vérification (89 s contre 92 s).
2. **S05a et S05b — Les événements du jeu ne peuvent pas entrer dans le plan** (hautes). Le moteur
   sait appliquer un événement (S05c, verte), mais aucun écran ne permet d'en saisir un, et
   « Enregistrer la saisie » efface ceux déjà enregistrés (ainsi que `storage_override` et
   `gold_pass`), car `PUT /manual` remplace tout l'état. C'est une exigence explicite du projet.
   Correction validée : une section « Événements du jeu » dans la saisie manuelle, qui relit et
   renvoie les événements, et conserve les champs que le formulaire n'affiche pas.
3. **S02 et S03 — Une page d'un autre site peut écrire dans ClashPilot ; un rebond DNS peut la
   lire** (moyennes). Le serveur accepte un `POST` en `text/plain` portant `Origin:
   https://evil.example` (import d'un faux village : 201) et répond à `Host: evil.example`.
   Correction validée : `TrustedHostMiddleware` (127.0.0.1 et localhost par défaut, liste
   réglable par `CLASHPILOT_ALLOWED_HOSTS`) et refus en 403 des écritures dont l'`Origin` n'est ni
   locale ni celle de l'hôte.

**Protections prouvées (sondes vertes)**

- Le moteur respecte les exigences du planning : aucune fin pendant le sommeil en mode strict,
  pour les 4 moteurs et 3 graines (S06) ; aucun ouvrier inactif quand une amélioration est
  possible (S07) ; un événement réduit bien durée et coût (S05c) ; la nuit suit l'heure locale
  aux changements d'heure (S11).
- La route qui sert l'application ne laisse sortir aucun fichier hors de `frontend/dist`, même
  avec des `..` encodés (S04) ; l'export `.ics` résiste à l'injection de lignes (S12).
- Aucun secret dans l'arbre ni dans les 80 commits (S09) ; aucune vulnérabilité connue ni licence
  copyleft dans le code livré (S08).

**Gains rapides** (effort S, gravité moyenne ou plus) : S01 (une fonction de `scripts/verify.mjs`),
S02 + S03 (un middleware et un réglage).

## 2. Périmètre et méthode

- **Dépôt** : `TGDev2/clashpilot`, branche `main`, SHA `2d560b82f8efbacd37d29ed5cf63405fb6df3144`
  (fusion de la PR #3), audité le 2026-11-12.
- **Fiche d'identité** : application web locale mono-utilisateur. Backend FastAPI 0.162 (Python
  3.13, OR-Tools 9.15 pour le solveur CP-SAT, SQLite avec 9 migrations), qui sert aussi le
  frontend React 19 + Vite 8 + Tailwind 4 construit (PWA). Points d'entrée : `npm start`
  (uvicorn sur 127.0.0.1:8000), `npm run dev`, l'outil hors ligne `clashpilot.gamedata.builder`.
  Parcours critiques : import d'un export de village, saisie manuelle, horaires, calcul et
  replanification du plan, export `.ics`, synchronisation facultative avec l'API officielle.
  Données : état de jeu d'un joueur, aucune donnée de paiement ; jeton facultatif de l'API
  officielle lu dans l'environnement. Taille : 233 fichiers suivis, ≈ 54 600 lignes
  (11 300 de code backend), 1 contributeur, 80 commits du 2026-11-08 au 2026-11-11.
- **Profil de risque** : faible exposition (127.0.0.1 par défaut, mode `0.0.0.0` documenté sans
  authentification), données peu sensibles. Le risque dominant est la confiance dans le plan et
  la fiabilité de la vérification utilisée par Backlog Autopilot.
- **Base de mesure** (sorties dans `raw/`) :
  - `npm run setup` : 0, 37 s (`raw/01_setup.log`) ;
  - `npm run verify` : **1**, 92 s, voie `frontend` en échec, 3 fois sur 3 (`raw/02_verify*.log`) ;
    voies `node`, `backend-lint` (ruff, mypy strict) et `backend-test` vertes ;
  - tests : 898 tests backend verts, couverture 95 % (`raw/06_backend_coverage.log`, module le
    moins couvert : `api/app.py` 72 %) ; 190 tests frontend verts quand la voie tourne seule
    (`raw/03_frontend_lane_seul.log`) ;
  - dépendances : `npm audit` 6 alertes, toutes dans les outils de build (`raw/04_*.json`) ;
    `pip-audit` 0 (`raw/05_pip_audit.json`).
- **Commande unique de vérification** : `npm run verify` (README), complétée par
  `npm run verify:full` (tests lents et Playwright, non lancés ici).
- **Outils** : Node 22.23.4, npm 10.9.9, Python 3.13.18, pytest 9.2.0 + pytest-cov, Vitest 5.1.0,
  ruff 0.17.4, mypy 2.4.1, pip-audit (dernière version au 2026-11-12), npm audit.
- **Limites de l'audit**
  - Linux, 2 processeurs : la course de S01 dépend de la vitesse de la machine. Sur le PC de DT
    elle peut ne se produire qu'une fois sur N ; la cause (deux voies qui touchent
    `frontend/src` en même temps) reste prouvée.
  - Non lancés : `npm run test:slow` (bancs d'essai de 300 à 400 tâches) et `npm run e2e`
    (Playwright + Chromium), jugés trop longs pour cette passe.
  - Pas d'essai sous Windows (`py -3.13`, chemins `Scripts\`) ni sur téléphone (PWA, HTTPS).
  - Les sondes ont été écrites et exécutées dans cette session, sans sous-agents parallèles.
  - Pas d'audit d'accessibilité outillé (axe, Lighthouse) : voir « À vérifier ».

## 3. Hypothèses et issues

| Hypothèse | Sonde | Statut | Gravité |
|-----------|-------|--------|---------|
| H01 Vérification rouge sur un clone propre | S01 | 🔴 rouge | haute |
| H02 Écriture depuis un autre site (CSRF) | S02 | 🔴 rouge | moyenne |
| H03 Lecture par rebond DNS | S03 | 🔴 rouge | moyenne |
| H04 Fichier servi hors de `dist` | S04 | 🟢 verte | haute |
| H05 Événements du jeu ignorés | S05a | 🔴 rouge | haute |
| H05 | S05b | 🔴 rouge | haute |
| H05 | S05c | 🟢 verte | haute |
| H06 Plan invalide ou fin pendant le sommeil (mode strict) | S06 | 🟢 verte | haute |
| H07 Ouvrier inactif | S07 | 🟢 verte | haute |
| H08 Dépendances vulnérables ou en fin de support | S08 | 🟢 verte | moyenne |
| H08 | S08b | 🔴 rouge | basse |
| H09 Secret commité | S09 | 🟢 verte | haute |
| H10 Pack de données sans limite de taille | S10 | 🔴 rouge | basse |
| H11 Changement d'heure | S11 | 🟢 verte | haute |
| H12 Injection dans l'export `.ics` | S12 | 🟢 verte | moyenne |

Aucune hypothèse émergente.

## 4. Sondes détaillées

Commandes données pour Windows `cmd`, à lancer à la racine du dépôt une fois la sonde copiée à
l'emplacement indiqué (les sondes `depot` se lancent telles quelles).

### S01 — La vérification unique passe sur un clone propre — 🔴 haute

- **Hypothèse** : H01. **Fichier** : `sondes/S01_verify_clean_clone.mjs`.
- **Commande** : `node docs\audit\2026-11-12\sondes\S01_verify_clean_clone.mjs .`
- **Preuve** : `verify exit 1; stray e01 files picked up by Vitest: 2` (`FAIL src/e01-pass-…test.ts`,
  `Cannot find module`). 4 exécutions sur 4. Cause : le test d'acceptation figé
  `scripts/foundation.acceptance.test.mjs:308-327` écrit deux fichiers dans `frontend/src` ;
  `scripts/verify.mjs` lance les quatre voies en parallèle.
- **Impact** : la vérification ne peut plus servir de garde-fou ; Backlog Autopilot échoue sur
  chaque tâche ; on s'habitue à un rouge.
- **Correction validée** : `reparations/S01.diff` (la voie `frontend` démarre après la voie
  `node`, les voies backend restent en parallèle). Après réparation : `S01 ok`, 89 s.
- **Effort** : S.

### S02 — Une page d'un autre site ne peut pas écrire — 🔴 moyenne

- **Hypothèse** : H02. **Fichier** : `sondes/S02_csrf_import_test.py`.
- **Commande** : `node scripts\test-one.mjs backend\tests\unit\S02_csrf_import_test.py`
- **Preuve** : `S02 cross-site import accepted: 201`. `villages.py:206` lit le corps quel que soit
  son type : un `POST` « simple » d'une page tierce passe sans preflight.
- **Impact** : un site visité pendant que ClashPilot tourne peut remplacer le village, supprimer des
  scénarios, déclencher la synchronisation avec le jeton. Probabilité faible (attaque ciblée).
- **Correction validée** : `reparations/S02_S03.diff` — middleware qui refuse en 403
  (`forbidden_origin`) les méthodes d'écriture dont l'`Origin` n'est ni 127.0.0.1/localhost (le
  serveur Vite) ni l'hôte lui-même. 931 tests backend verts avec la correction.
- **Effort** : S.

### S03 — Un nom d'hôte étranger est refusé — 🔴 moyenne

- **Hypothèse** : H03. **Fichier** : `sondes/S03_dns_rebinding_test.py`.
- **Commande** : `node scripts\test-one.mjs backend\tests\unit\S03_dns_rebinding_test.py`
- **Preuve** : `S03 foreign Host accepted: 200`. La sonde construit les réglages comme au lancement
  réel (`Settings.from_env`) ; elle a été corrigée une fois en ce sens avant la réparation et
  restait rouge sur le SHA audité.
- **Impact** : lecture de toutes les données depuis un site tiers par rebond DNS.
- **Correction validée** : même diff que S02 — `TrustedHostMiddleware` avec
  `Settings.allowed_hosts` : 127.0.0.1 et localhost par défaut, plus l'hôte d'écoute s'il est
  explicite, `*` en `0.0.0.0` (mode déjà signalé comme non sûr dans le README), ou la liste de
  `CLASHPILOT_ALLOWED_HOSTS`. Les `TestClient` existants gardent `*` car ils construisent
  `Settings(...)` directement.
- **Piège** : documenter `CLASHPILOT_ALLOWED_HOSTS` dans le README (accès depuis le téléphone via
  Tailscale ou un nom local).
- **Effort** : S.

### S04 — Rien n'est servi hors de `frontend/dist` — 🟢

7 tentatives (`..`, `%2e%2e`, `%2f`, `%5c`, double encodage) : le fichier témoin n'est jamais
servi. `sondes/S04_spa_traversal_test.py`.

### S05a — Enregistrer la saisie manuelle conserve les événements — 🔴 haute

- **Hypothèse** : H05. **Fichier** : `sondes/S05a_manual_form_keeps_events.test.tsx` (à placer dans
  `frontend/src/pages/import/`).
- **Commande** : `node scripts\test-one.mjs frontend\src\pages\import\S05a_manual_form_keeps_events.test.tsx`
- **Preuve** : `S05a events erased by the manual form: expected undefined to deeply equal [ { …(6) } ]`.
  `ManualForm.tsx:125-130` n'envoie que `resources`, `builders_total`, `farm_rate`,
  `magic_items` ; `PUT /manual` (`villages.py:599-637`) remplace tout.
- **Impact** : chaque enregistrement efface les événements, `storage_override` et `gold_pass`
  saisis par l'API ; le plan les ignore ensuite sans le dire.
- **Correction validée** : `reparations/S05.diff` (avec S05b). 191 tests frontend verts, lint,
  types, build et `gen-api --check` verts.
- **Effort** : S.

### S05b — L'interface permet de saisir un événement — 🔴 haute

- **Hypothèse** : H05. **Fichier** : `sondes/S05b_events_input_exists.mjs` (lecture seule).
- **Commande** : `node docs\audit\2026-11-12\sondes\S05b_events_input_exists.mjs .`
- **Preuve** : `no game event input in the UI (no duration_pct/cost_pct outside the generated schema)`.
- **Impact** : l'exigence « le plan tient compte des événements qui réduisent coût et durée » n'est
  pas atteignable depuis l'application.
- **Correction validée** : `reparations/S05.diff` — section « Événements du jeu » dans la saisie
  manuelle (nom, file, début, fin, réduction de durée et de coût en %), relue depuis le serveur
  et renvoyée à chaque enregistrement. La sonde est une vérification statique : la tâche du
  backlog la complète par un test de comportement (saisie → corps du `PUT`).
- **Effort** : M.

### S05c — Un événement réduit durée et coût dans le plan — 🟢

Pour `baseline`, `rollout`, `cpsat` et `auto` : 10 h à 80 % → 8 h, 10 000 or → 8 000.

### S06 — Mode strict : plan valide, aucune fin pendant le sommeil — 🟢

4 moteurs × 3 graines, 14 tâches, une nuit par jour sur 10 jours : 12 plans valides, 0 fin
pendant le sommeil. La sonde a demandé deux corrections d'écriture (nom du module d'aide, champ
`metrics.ends_in_sleep`) sans rapport avec le projet.

### S07 — Aucun ouvrier inactif — 🟢

3 ouvriers, 6 tâches indépendantes, fonds abondants : chaque ouvrier démarre à `now` et enchaîne
sans trou, pour les 4 moteurs.

### S08 — Dépendances livrées sans vulnérabilité connue ni licence copyleft — 🟢

`npm audit --omit=dev` : 0 (frontend, e2e) ; `pip-audit` : 0. Les 6 alertes de `npm ci` (glob,
minimatch, deepmerge via `workbox-build`) ne concernent que la construction.

### S08b — Versions minimales de Node et Python supportées 6 mois — 🔴 basse

- **Commande** : `node docs\audit\2026-11-12\sondes\S08b_runtime_support.mjs .`
- **Preuve** : `Node 22 (engines ">=22.14.0") ends 2027-04-30` (endoflife.date, relevé le 2026-11-12).
- **Correction** : `engines.node` à `>=24` et README (Node 24 en support actif jusqu'au
  2026-10-20, puis maintenance jusqu'au 2028-04-30). Python 3.13 reste supporté jusqu'au
  2029-10-31. **Effort** : S.

### S09 — Aucun secret dans l'arbre ni l'historique — 🟢

80 commits, 56 188 lignes ajoutées : 0 correspondance. Le seul signalement initial était le faux
jeton `TOKEN-…` d'un test d'acceptation, exclu comme valeur factice.

### S10 — L'import d'un pack a une taille maximale — 🔴 basse

- **Commande** : `node scripts\test-one.mjs backend\tests\unit\S10_pack_size_limit_test.py`
- **Preuve** : `S10 oversized pack not refused: 422` : un corps de 6 Mo est lu en entier, puis
  rejeté comme pack invalide. L'import d'export, lui, s'arrête à 5 Mo (`villages.py:206-213`).
- **Correction** : lire le corps de `POST /api/gamedata/packs` comme `_read_body` (limite à fixer
  au-dessus du plus gros pack réel). **Effort** : S.

### S11 — Changement d'heure — 🟢

Europe/Paris : la nuit du 28 mars 2026 dure 7 h, celle du 24 octobre 9 h, toutes deux de 23:00
à 07:00 locales.

### S12 — Export `.ics` — 🟢

Un nom contenant CRLF + `BEGIN:VALARM` n'ajoute aucune ligne.

## 5. Mutations

**Taux de détection : 16 / 20 = 80 %.** Commande : `node scripts/verify.mjs` sur le SHA audité plus
`reparations/S01.diff` (sans cette réparation, la vérification échoue toujours et « détecterait »
toutes les mutations). Définitions : `sondes/mutations.json` ; résultats et voies en échec :
`raw/mutations_resultats.json`. Chaque mutation : environ 90 s.

Mutations détectées : M01, M02, M04, M05, M07, M08, M10, M11, M13, M15, M16, M17, M19, M20
(tests backend), M09 (voie `node` seulement : test d'acceptation figé de la fondation), M18
(voie `frontend`).

**Survivantes** (trous dans le filet) :

| ID | Fichier | Mutation | Ce que cela laisserait passer |
|----|---------|----------|-------------------------------|
| M03 | `backend/src/clashpilot/planning/simulate.py:86` | `m.start <= start < m.end` devient `<= m.end` | Une amélioration lancée à l'instant exact de fin d'un événement en profite encore. |
| M06 | `backend/src/clashpilot/planning/simulate.py:256` | `active > capacity` devient `active > capacity + 1` | Le contrôle de capacité tolère un chantier de plus que la file n'a d'ouvriers. |
| M12 | `backend/src/clashpilot/ingest/official_api.py:104` | `timeout=TIMEOUT_S` devient `timeout=None` | Une API officielle muette bloque la synchronisation sans limite. |
| M14 | `backend/src/clashpilot/api/ics.py:28` | l'échappement du `\n` seul est retiré | Un nom contenant un saut de ligne casse l'événement `.ics` (S12 ne couvre que CRLF). |

## 6. Technologies

Versions relevées le 2026-11-12 sur npm (`npm view`), PyPI (`pip index versions`) et
endoflife.date (API `nodejs.json`, `python.json`).

| Technologie | Utilisée | Dernière | Fin de support | Maintenance | Licence | Verdict | Alternative, coût |
|-------------|----------|----------|----------------|-------------|---------|---------|-------------------|
| Node.js (minimum) | 22.14 (`engines`) | 26.13 (LTS 24.22) | 22 : 2027-04-30 | maintenance | MIT | Mettre à jour (S08b) | Node 24, S |
| Python | 3.13 (`<3.14`) | 3.15.2 | 3.13 : 2029-10-31 (correctifs de sécurité seulement depuis 2026-10-01) | sécurité | PSF | Surveiller | 3.14, M (wheels OR-Tools) |
| FastAPI | 0.162.2 | 0.162.5 | — | active | MIT | Garder | — |
| Starlette | 1.10.0 | 1.10.0 | — | active | BSD-3 | Garder | — |
| uvicorn | 0.54.0 | 0.54.0 | — | active | BSD-3 | Garder | — |
| OR-Tools | 9.15.6755 | 9.15.6755 | — | active | Apache-2.0 | Garder | — |
| httpx | 0.28.1 | 0.28.1 | — | active | BSD-3 | Garder | — |
| pydantic | 2.14.3 | 2.14.3 | — | active | MIT | Garder | — |
| pytest / Hypothesis | 9.2.0 / 6.170.15 | idem | — | active | MIT / MPL-2.0 | Garder | — |
| ruff / mypy | 0.17.4 / 2.4.1 | 0.17.5 / 2.4.1 | — | active | MIT | Garder | — |
| React / React DOM | 19.3.1 | 19.3.1 | — | active | MIT | Garder | — |
| react-router | 7.18.5 | 8.5.1 | — | active | MIT | Surveiller (majeure de retard) | 8.x, M |
| react-aria-components | 1.22.1 | 1.22.1 | — | active | Apache-2.0 | Garder | — |
| Vite | 8.3.7 | 8.3.8 | — | active | MIT | Garder | — |
| Vitest | 5.1.0 | 5.1.0 | — | active | MIT | Garder | — |
| TypeScript | 5.9.3 | 7.0.2 | — | active | Apache-2.0 | Surveiller (majeure de retard) | 7.x, M |
| Biome | 2.6.6 | 2.6.7 | — | active | MIT / Apache-2.0 | Garder | — |
| Tailwind CSS | 4.3.5 | 4.3.5 | — | active | MIT | Garder | — |
| vite-plugin-pwa / workbox-build | 2.0.0 / 7.4.1 | idem | — | workbox-build : dernière publication 2026-05-04 | MIT | Surveiller (dépendances vulnérables de build) | — |
| openapi-fetch / openapi-typescript | 0.17.0 / 7.13.0 | idem | — | active | MIT | Garder | — |
| jsdom (tests) | 29.1.1 | 31.0.0 | — | active | MIT | Mettre à jour | 31, S |
| Playwright | 1.65.0 | 1.65.0 | — | active | Apache-2.0 | Garder | — |

Doublons : aucun constaté. Dépendances du verrou backend sans import direct (numpy, pandas,
protobuf, absl-py, immutabledict, opentelemetry-api) : tirées par OR-Tools, à conserver.

## 7. Avis (non prouvés)

1. **Le contrat d'API « remplacer tout » de `PUT /manual` est fragile** (non prouvé au-delà de
   S05a) : tout nouveau champ du `ManualState` sera effacé par un client qui ne le connaît pas.
   Un `PATCH` par champ, ou une réponse 409 sur version périmée, le rendrait robuste. Preuve
   possible : une sonde qui ajoute un champ et enregistre depuis le formulaire.
2. **Le Gold Pass est stocké mais n'agit pas** (`compile.py:719`) : le champ existe sans effet ni
   message. Preuve possible : sonde « un `gold_pass` saisi modifie le plan ou produit un
   avertissement ».
3. **Le mode `0.0.0.0` sans authentification** reste un risque documenté ; un jeton d'accès local
   (affiché au démarrage) serait proportionné. Non sondé : comportement voulu.
4. **`api/app.py` est le module le moins couvert (72 %)** : c'est aussi la porte d'entrée de
   sécurité ; les sondes S02 à S04 adoptées combleront une partie du trou.

## 8. À vérifier

- **Course de S01 sous Windows** : lancer `npm run verify` trois fois de suite sur le PC de DT avant
  et après la correction.
- **Tests lents et parcours Playwright** : `npm run verify:full` après les corrections S02/S03
  (le parcours e2e passe par 127.0.0.1 et doit rester vert).
- **Accès depuis le téléphone** après S03 : avec Tailscale Serve ou une IP locale, renseigner
  `CLASHPILOT_ALLOWED_HOSTS` et vérifier l'application.
- **Accessibilité WCAG 2.2 AA** du nouvel éditeur d'événements et des pages existantes : axe ou
  Lighthouse dans Chromium.
- **Gold Pass** : décider s'il doit devenir un événement permanent (voir Avis 2).

## 9. Feuille de route

- **Maintenant** : A01 (fiabiliser `npm run verify`, S01), A02 (événements du jeu, S05a + S05b),
  A03 (origine et hôte, S02 + S03).
- **Ensuite** : A04 (renforcer les tests du simulateur, de l'API officielle et de l'export `.ics` : M03, M06, M12, M14).
- **Plus tard** : A05 (taille maximale du pack, S10), A06 (Node 24 minimum, S08b), montée de
  react-router 8 et TypeScript 7.
