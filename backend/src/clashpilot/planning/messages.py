"""French texts of the explanations, one template per code. Pure module.

Every value a text cites is passed in, computed by the caller from the trace and the simulator
result: no number is ever written here by hand. No template says the plan is optimal: the proof
kind of a plan is shown on its own.
"""

from __future__ import annotations

from typing import Any

RESOURCE_LABELS = {
    "gold": "or",
    "elixir": "élixir",
    "dark_elixir": "élixir noir",
    "gems": "gemmes",
    "shiny_ore": "minerai brillant",
    "glowy_ore": "minerai luisant",
    "starry_ore": "minerai étoilé",
}

TEMPLATES = {
    # why.reasons
    "no_trace": "Le moteur n'a pas détaillé ce choix : aucune comparaison n'est disponible.",
    "value_per_hour": "Rapporte {value:.2f} point(s) de valeur par heure d'amélioration.",
    "options_compared": "Choisie parmi {value} option(s) évaluée(s) à cet instant.",
    "chosen_score": "Score du plan complété avec ce choix : {value:.4f}.",
    "ahead_of_alternative": "Devance la meilleure autre option de {value:.4f} point(s) de score.",
    "wait_until": "Aucune amélioration ne doit démarrer avant la prochaine connexion prévue.",
    # advantages
    "unlocks": "Débloque {value} amélioration(s) suivante(s).",
    "ends_awake": "Se termine pendant une période d'éveil.",
    "ends_in_preferred": "Se termine dans un créneau préféré.",
    # drawbacks
    "ends_in_sleep": "Se termine pendant le sommeil : la voie reste libre jusqu'au réveil.",
    "spends": "Coûte {amount} {label}.",
    # reliability factors
    "fictional_pack": "Les données de jeu sont fictives : les durées et coûts sont indicatifs.",
    "estimated_resource": "Le stock de {label} est estimé, pas confirmé.",
    "stale_resource": "Le stock de {label} a été saisi il y a plus de 7 jours.",
    "aging_resource": "Le stock de {label} a été saisi il y a 7 jours.",
}

WAIT_NAME = "Attendre"


def entry(code: str, value: Any = None, **fields: Any) -> dict[str, Any]:
    """`{code, message, value}`: the message is the template of `code` filled with the values."""
    return {"code": code, "message": TEMPLATES[code].format(value=value, **fields), "value": value}


def resource_label(name: str) -> str:
    return RESOURCE_LABELS.get(name, name)
