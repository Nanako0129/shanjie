"""S7f: the invade mainland-term list (https://github.com/caris-events/invade, text CC0; docs/methodology.md lists it).

  python invade.py --repo CLONE      # prints the term count, the list's SHA-256 and the clone's commit (record them before generating)

Terms are the `word:` field of database/vocabs/*.yml. The entries marked `deprecation:` are dropped: the site shows them struck
through (withdrawn). The SHA-256 is over the sorted terms joined by newlines, so it freezes the list itself, not the YAML layout.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

WORD = re.compile(r"^word\s*:\s*(.*?)\s*$", re.M)
DEPRECATED = re.compile(r"^deprecation\s*:", re.M)


def load(repo):
    """Returns (frozenset of terms, info {commit, sha256, terms, deprecated})."""
    terms, deprecated = set(), []
    for p in sorted(glob.glob(os.path.join(repo, "database", "vocabs", "*.yml"))):
        text = open(p, encoding="utf-8").read()
        w = WORD.findall(text)
        if len(w) != 1 or not w[0]:
            sys.exit(f"invade: {p}: expected one word field, found {w}")
        (deprecated.append if DEPRECATED.search(text) else terms.add)(w[0])
    if not terms:
        sys.exit(f"invade: no terms under {repo}/database/vocabs")
    r = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True)
    info = {"repo": "https://github.com/caris-events/invade", "commit": r.stdout.strip() if r.returncode == 0 else None,
            "sha256": list_sha256(terms), "terms": len(terms), "deprecated": sorted(deprecated)}
    return frozenset(terms), info


def list_sha256(terms):
    return hashlib.sha256("\n".join(sorted(terms)).encode("utf-8")).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    a = ap.parse_args(argv)
    _, info = load(a.repo)
    print(json.dumps(info, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
