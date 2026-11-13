# Rejeu de l'audit — ClashPilot — 2026-11-13

Rejeu des sondes de `docs/audit/2026-11-12/` sur `main`, SHA
`2cfa48ccad15d5cf4542abd03aa039495198eadf`, après livraison des 6 tâches du backlog (A01 à A06).

## Verdict

**Prêt** (avant : prêt sous réserve).

- **Sondes** : 0 rouge, 15 vertes, 0 non concluante. 7 résolues, 8 tenues, **aucune régression**.
- **Mutations** : les 4 survivantes de l'audit (M03, M06, M12, M14) sont maintenant détectées. Les
  16 autres, détectées le 2026-11-12, n'ont pas été rejouées ; leurs tests sont toujours dans la
  suite, qui passe. Taux estimé : 20 / 20.

## Sondes

Une sonde adoptée dans la suite du projet a été rejouée dans sa version adoptée. Sorties dans `raw/`.

| Sonde | Avant | Après | État | Version rejouée |
|-------|-------|-------|------|-----------------|
| S01 Vérification sur un clone propre | 🔴 haute | 🟢 | résolue | `docs/audit/2026-11-12/sondes/S01_verify_clean_clone.mjs` |
| S02 Écriture depuis un autre site | 🔴 moyenne | 🟢 | résolue | `backend/tests/unit/test_cross_site_requests.py` |
| S03 Hôte étranger (rebond DNS) | 🔴 moyenne | 🟢 | résolue | `backend/tests/unit/test_cross_site_requests.py` |
| S04 Fichier servi hors de `dist` | 🟢 | 🟢 | tenue | `backend/tests/unit/test_spa_traversal.py` |
| S05a Saisie manuelle conserve les événements | 🔴 haute | 🟢 | résolue | `frontend/src/pages/import/ManualFormEvents.test.tsx` |
| S05b Saisie des événements dans l'interface | 🔴 haute | 🟢 | résolue | `docs/audit/2026-11-12/sondes/S05b_events_input_exists.mjs` + test de comportement dans `ManualFormEvents.test.tsx` |
| S05c Un événement réduit durée et coût | 🟢 | 🟢 | tenue | `backend/tests/unit/test_events_reduce_plan.py` |
| S06 Mode strict, aucune fin pendant le sommeil | 🟢 | 🟢 | tenue | `backend/tests/unit/test_strict_sleep_engines.py` |
| S07 Aucun ouvrier inactif | 🟢 | 🟢 | tenue | `backend/tests/unit/test_no_idle_builder.py` |
| S08 Dépendances livrées | 🟢 | 🟢 | tenue | `docs/audit/2026-11-12/sondes/S08_dependencies.mjs` (pip-audit relancé) |
| S08b Support de Node et Python | 🔴 basse | 🟢 | résolue | `docs/audit/2026-11-12/sondes/S08b_runtime_support.mjs` |
| S09 Secrets | 🟢 | 🟢 | tenue | `docs/audit/2026-11-12/sondes/S09_secrets.py` (95 commits) |
| S10 Taille maximale d'un pack | 🔴 basse | 🟢 | résolue | `backend/tests/unit/test_pack_size_limit.py` |
| S11 Changement d'heure | 🟢 | 🟢 | tenue | `backend/tests/unit/test_dst_sleep_window.py` |
| S12 Export `.ics` | 🟢 | 🟢 | tenue | `backend/tests/unit/test_ics_injection.py` |

## Mutations survivantes rejouées

| ID | Mutation | Résultat |
|----|----------|----------|
| M03 | Fin d'événement incluse | détectée (tests backend) |
| M06 | Capacité de file + 1 | détectée (tests backend) |
| M12 | Plus de délai vers l'API officielle | détectée (tests backend) |
| M14 | `\n` seul non échappé dans l'agenda | détectée (tests backend) |

Commande : `node scripts/verify.mjs`, une mutation à la fois, dans une copie temporaire.

## Points d'attention

- **Durée de la voie des tests backend** : 98 à 125 s ici (2 processeurs) depuis l'adoption des
  sondes, pour une limite de 180 s par voie dans `scripts/verify.mjs`. La marge reste correcte,
  mais `test_strict_sleep_engines.py` (≈ 28 s) est le premier candidat si elle se resserre.
- **Restent à vérifier par DT** (rien de nouveau depuis l'audit) : `npm run verify` sous Windows,
  `npm run verify:full` (tests lents et Playwright, non lancés dans le cloud), accès depuis le
  téléphone avec `CLASHPILOT_ALLOWED_HOSTS`, rendu de la section « Événements du jeu », import du
  plus gros pack réel sous la limite de 5 Mo.

## Backlog

`backlog.json` ne contient aucune tâche : aucune sonde n'est persistante ni régressée.
