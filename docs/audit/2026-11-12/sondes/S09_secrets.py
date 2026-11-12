"""Sonde S09 (H09) : aucun secret dans l'arbre suivi ni dans l'historique git.

Comportement sûr attendu : zéro correspondance. En cas d'échec, seuls le commit ou le fichier,
la ligne et le type sont affichés, la valeur est masquée.
Exécution en lecture seule contre le dépôt : python -I <ce fichier> <racine du dépôt>
"""

from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = {
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "openai_or_anthropic": re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{24,}\b"),
    "slack_token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    "coc_token_assignment": re.compile(r"CLASHPILOT_COC_TOKEN\s*=\s*['\"]?[A-Za-z0-9._-]{20,}"),
    "generic_secret": re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|token)\b\s*[:=]\s*['\"][A-Za-z0-9/+_.-]{16,}['\"]"
    ),
}


# Obvious placeholders used by the test fixtures are not secrets.
PLACEHOLDER = re.compile(r"['\"](?:TOKEN-|test|fake|dummy|example|placeholder)", re.IGNORECASE)


def mask(value: str) -> str:
    return f"{value[:4]}…{value[-3:]}" if len(value) > 8 else "…"


def scan(lines: list[str], where: str) -> list[str]:
    hits = []
    for number, line in enumerate(lines, 1):
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                if kind == "generic_secret" and PLACEHOLDER.search(match.group(0)):
                    continue
                hits.append(f"{where}:{number} {kind} {mask(match.group(0))}")
    return hits


def main(root: str) -> int:
    hits: list[str] = []
    files = subprocess.run(
        ["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True
    ).stdout.split(b"\0")
    for name in filter(None, (f.decode() for f in files)):
        try:
            with open(f"{root}/{name}", encoding="utf-8") as handle:
                hits += scan(handle.read().splitlines(), name)
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
    log = subprocess.run(
        ["git", "-C", root, "log", "--all", "-p", "--no-color", "--format=commit %H"],
        capture_output=True, check=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    commit = "?"
    added: list[str] = []
    for line in log.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1][:12]
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line)
            hits += [f"commit {commit} {h.split(' ', 1)[1]}" for h in scan([line], "x")]
    commits = subprocess.run(
        ["git", "-C", root, "rev-list", "--all", "--count"], capture_output=True, text=True
    ).stdout.strip()
    if hits:
        print(f"S09 FAIL: {len(hits)} possible secrets")
        print("\n".join(sorted(set(hits))))
        return 1
    print(f"S09 ok: 0 match in tracked files and {commits} commits ({len(added)} added lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
