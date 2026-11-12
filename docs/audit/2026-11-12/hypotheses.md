# Pré-mortem — ClashPilot, audit du 2026-11-12

SHA audité : `2d560b82f8efbacd37d29ed5cf63405fb6df3144` (branche `main`).

Question posée : « Dans six mois, ClashPilot a subi un incident grave. Lequel ? »

Ces hypothèses ont été écrites avant toute sonde et ne sont plus modifiées ensuite. Les hypothèses
découvertes pendant le sondage sont ajoutées à la fin, marquées « émergente ».

Profil de risque retenu : application locale mono-utilisateur, sans authentification, qui écoute
`127.0.0.1` par défaut (option `0.0.0.0` documentée). Données de jeu peu sensibles, pas de données
de paiement ; le seul secret est le jeton facultatif de l'API officielle (variable
d'environnement). Le risque principal est donc la confiance dans le plan (exigences de DT : aucun
ouvrier inactif, aucune fin pendant le sommeil, prise en compte des événements du jeu) et la
fiabilité de la chaîne de vérification que Backlog Autopilot utilise pour livrer.

| ID | Scénario d'incident | Gravité potentielle | Probabilité | Indice | Sonde envisagée |
|----|---------------------|---------------------|-------------|--------|-----------------|
| H01 | La vérification unique (`npm run verify`) échoue sur un clone propre : Backlog Autopilot ne peut plus livrer, ou on prend l'habitude d'ignorer un rouge. | haute | haute | `scripts/foundation.acceptance.test.mjs:312-319` écrit des `*.test.ts` dans `frontend/src` pendant que la voie `frontend` lance Vitest en parallèle (`scripts/verify.mjs:84-109`) ; `raw/02_verify*.log` : 3 échecs sur 3. | Lancer `npm run verify` sur une copie propre et exiger le code 0, sans fichier `e01-*` dans la sortie. |
| H02 | Une page web malveillante ouverte dans le navigateur de DT envoie un `POST` « simple » (text/plain, sans preflight) vers `http://127.0.0.1:8000` et écrase le village importé (ou supprime des scénarios). | moyenne | basse | `backend/src/clashpilot/api/routers/villages.py:206` lit le corps « quel que soit son type » ; aucun contrôle d'`Origin` dans `api/app.py`. | Test FastAPI : `POST /api/villages/import` en `text/plain` avec `Origin: https://evil.example` doit être refusé (403). |
| H03 | Rebond DNS : un site tiers fait résoudre son nom vers 127.0.0.1 et lit toute l'API (villages, plans) depuis le navigateur de DT. | moyenne | basse | Aucun `TrustedHostMiddleware` dans `api/app.py`. | Test FastAPI : `GET /api/health` avec `Host: evil.example` doit être refusé (400/421). |
| H04 | Le service des fichiers de l'application (route attrape-tout) sert un fichier hors de `frontend/dist` (par exemple `backend/pyproject.toml` ou la base SQLite) via `..` encodé. | haute | basse | `api/app.py:39-50` résout le chemin à la main. | Test FastAPI : chemins `..`, `%2e%2e`, absolus et antislash doivent renvoyer 404 sans contenu du fichier visé. |
| H05 | Les événements du jeu (réductions de coût et de durée) ne sont jamais pris en compte dans le plan : aucune saisie n'existe dans l'interface et l'enregistrement de la saisie manuelle efface ceux qui existent. | haute | haute | `frontend/src/pages/import/ManualForm.tsx:125-130` n'envoie ni `events`, ni `gold_pass`, ni `storage_override` ; `PUT /manual` remplace tout (`villages.py:599-637`) ; aucune référence à `duration_pct` dans `frontend/src` hors schéma généré. | (a) Vitest : enregistrer le formulaire conserve les `events` déjà enregistrés. (b) Script : l'interface propose une saisie des événements. (c) pytest : un événement saisi réduit bien coût et durée dans le plan calculé. |
| H06 | En mode « Interdire les fins pendant le sommeil », un moteur rend un plan invalide : l'API répond une erreur au lieu d'un plan, ou une fin tombe pendant le sommeil. | haute | moyenne | Le contrôle a posteriori `simulate` rejette le plan (`service.py:343-345`) au lieu de le corriger. | pytest + Hypothesis : sur des problèmes générés en mode `strict`, chaque moteur rend un plan valide sans fin pendant le sommeil. |
| H07 | Un ouvrier reste inactif alors qu'une amélioration est possible et finançable (exigence « aucun ouvrier inactif »). | haute | moyenne | Plusieurs moteurs heuristiques (`baseline`, `rollout`, `cpsat`, `auto`). | pytest : ressources abondantes, aucun sommeil, tâches indépendantes → chaque ouvrier démarre une tâche dès `now`, pour chaque moteur. |
| H08 | Une dépendance embarque une vulnérabilité atteignable, ou une technologie arrive en fin de support sans qu'on le voie. | moyenne | moyenne | `npm ci` signale 6 vulnérabilités (`raw/01_setup.log`). | `npm audit --omit=dev` = 0 ; `pip-audit` = 0 ; script de fin de support (dates vérifiées en ligne). |
| H09 | Un secret (jeton de l'API officielle, clé) a été commité dans le code ou l'historique. | haute | basse | Jeton lu depuis l'environnement ; 80 commits. | Recherche de motifs de secrets (JWT, clés, `CLASHPILOT_COC_TOKEN=`) dans l'arbre et l'historique : zéro correspondance. |
| H10 | L'import d'un pack de données volumineux sature la mémoire (pas de limite de taille, contrairement à l'import d'export). | basse | basse | `gamedata.py:157-160` : `Body()` sans limite ; `villages.py:28` limite l'export à 5 Mo. | Test FastAPI : un corps de 6 Mo sur `POST /api/gamedata/packs` est refusé en 413. |
| H11 | Au changement d'heure (Europe/Paris), la fenêtre de sommeil se décale ou se troue : une fin tombe à 3 h du matin réel. | haute | basse | `schedule/expand.py:38-71` gère les plis à la main. | pytest : profil 23 h–7 h sur la nuit du 25 octobre 2026 → le sommeil couvre exactement 23:00 à 07:00 heure locale. |
| H12 | Un nom d'amélioration contenant un retour à la ligne casse ou injecte des lignes dans l'export `.ics` (alarme ou événement fantôme). | moyenne | basse | `api/ics.py:25-28`. | pytest : un nom avec `\r\nBEGIN:VALARM` ne crée aucune ligne commençant par `BEGIN:VALARM` en trop. |
