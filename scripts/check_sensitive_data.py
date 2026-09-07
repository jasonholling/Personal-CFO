#!/usr/bin/env python3
"""
Regression guard: fails if data known to be real/personal (leaked once,
fixed on 2026-08-26) ever reappears in tracked source, and if files that
must stay local-only (cfo.db, .env, Finder/backup duplicates) are ever
committed.

This is NOT a general secret scanner — gitleaks (run alongside this in CI)
covers API-key-shaped secrets. This script only catches the specific class
of leak this repo already had once: real financial figures, names, and
identifiers hardcoded directly in source instead of living only in the
gitignored cfo.db. If you ever find a new instance of that, add it to
SENSITIVE_STRINGS below rather than assuming this script already covers it.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that legitimately reference some of these tokens as user-editable
# config rather than secrets (e.g. the Quicken CSV account-name mapping is
# keyed to real address/institution substrings so it can match your export —
# a friend edits this file for their own accounts, it isn't a leak).
EXCLUDED_FILES = {
    "backend/quicken_importer.py",
    "scripts/check_sensitive_data.py",  # the denylist below necessarily contains itself
}

SENSITIVE_STRINGS = [
    # Real dollar figures (salary, mortgage, SS benefits, insurance, pensions)
    "35700", "50940", "506750", "211977", "1222000", "424000", "489000",
    "27996", "38033", "36496", "8792", "22588", "173.33", "251.33",
    "25470", "25464", "331837", "89639", "190000", "2526375", "985913", "79058",
    "0.756", "2975", "4245",
    # 2026-09-07: real RMD figures from a live sanity-check run leaked into
    # a doc comment before being caught during the pre-sharing audit below
    "292324", "292,324", "115509", "115,509",
    # Real names / employer / insurers / places
    "Holling-Karas", "Cheryl Karas", "ConAgra", "Aflac", "SBLI", "MetLife",
    "EMC National", "NY Life", "Battlecreek", "Omaha",
    # Real stock holdings
    "AMZN", "NFLX", "BRKB",
    # Real property/street identifiers used as literal display text
    "9013 House", "3155 Jackson", "9315 Sterling Circle", "Jackson St",
]

SCAN_EXTENSIONS = {".py", ".jsx", ".js", ".json", ".md"}

FORBIDDEN_FILE_PATTERNS = [
    re.compile(r"(^|/)cfo\.db$"),
    re.compile(r"(^|/)\.env(\..+)?$"),
    re.compile(r" 2(\.\w+)?$"),
    re.compile(r"_backup_\d+"),
]


def tracked_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [REPO_ROOT / p for p in out.stdout.splitlines() if p]


def main():
    failures = []
    files = tracked_files()

    # 1. Forbidden files must never be tracked, regardless of .gitignore
    for f in files:
        rel = f.relative_to(REPO_ROOT).as_posix()
        for pat in FORBIDDEN_FILE_PATTERNS:
            if pat.search(rel):
                failures.append(f"Forbidden file is tracked: {rel}")

    # 2. Sensitive string regression scan
    for f in files:
        rel = f.relative_to(REPO_ROOT).as_posix()
        if rel in EXCLUDED_FILES or f.suffix not in SCAN_EXTENSIONS:
            continue
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        for s in SENSITIVE_STRINGS:
            if s in text:
                failures.append(f"Sensitive string '{s}' found in {rel}")

    if failures:
        print("❌ Sensitive-data check FAILED:\n")
        for f in failures:
            print(" -", f)
        print(
            "\nIf this is a real personal number/name, move it into cfo.db "
            "(gitignored) instead of hardcoding it in source. If it's a "
            "false positive, add the file to EXCLUDED_FILES or remove the "
            "string from SENSITIVE_STRINGS in scripts/check_sensitive_data.py."
        )
        sys.exit(1)

    print(f"✅ Sensitive-data check passed ({len(files)} tracked files scanned).")


if __name__ == "__main__":
    main()
