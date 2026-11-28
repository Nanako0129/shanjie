#!/usr/bin/env bash
set -euo pipefail

# Stage site/ plus out-of-tree WASM/model/data runtime assets into <out_dir>
# without placing ~95 MiB binaries inside tracked site/.
#
# Usage:
#   scripts/stage-site-wasm.sh <wasm_bundle_dir> [out_dir]
#   SHANJIE_WASM_BUNDLE_DIR=<dir> scripts/stage-site-wasm.sh [out_dir]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ASSET_SUBDIR="wasm-assets/v1-p35-2ff55dcc"

if [ "$#" -eq 0 ]; then
  WASM_BUNDLE_DIR="${SHANJIE_WASM_BUNDLE_DIR:-}"
  OUT_DIR="/tmp/shanjie-site-dist"
elif [ "$#" -eq 1 ]; then
  if [ -n "${SHANJIE_WASM_BUNDLE_DIR:-}" ]; then
    WASM_BUNDLE_DIR="$SHANJIE_WASM_BUNDLE_DIR"
    OUT_DIR="$1"
  else
    WASM_BUNDLE_DIR="$1"
    OUT_DIR="/tmp/shanjie-site-dist"
  fi
elif [ "$#" -eq 2 ]; then
  WASM_BUNDLE_DIR="$1"
  OUT_DIR="$2"
else
  echo "error: expected at most 2 arguments" >&2
  echo "usage: scripts/stage-site-wasm.sh <wasm_bundle_dir> [out_dir]" >&2
  echo "       SHANJIE_WASM_BUNDLE_DIR=<dir> scripts/stage-site-wasm.sh [out_dir]" >&2
  exit 1
fi

if [ -z "$WASM_BUNDLE_DIR" ]; then
  echo "error: wasm_bundle_dir is required (pass as \$1 or SHANJIE_WASM_BUNDLE_DIR)" >&2
  exit 1
fi
if [ ! -d "$WASM_BUNDLE_DIR" ]; then
  echo "error: wasm_bundle_dir does not exist or is not a directory: $WASM_BUNDLE_DIR" >&2
  exit 1
fi
if [ -z "$OUT_DIR" ]; then
  echo "error: out_dir must not be empty" >&2
  exit 1
fi
if [ ! -d "$REPO_ROOT/site" ]; then
  echo "error: missing repo site directory: $REPO_ROOT/site" >&2
  exit 1
fi

# 1. Validate out_dir safety AND all 22 source inputs (existence, <=25 MiB cap,
#    and all 5 SHA-256 header bindings) strictly BEFORE any filesystem mutation.
OUT_ABS="$(
  REPO_ROOT="$REPO_ROOT" \
  WASM_BUNDLE_DIR="$WASM_BUNDLE_DIR" \
  OUT_DIR="$OUT_DIR" \
  HOME_DIR="${HOME:-}" \
  python3 - <<'EOF'
import hashlib
import os
import pathlib
import sys

repo_phys = os.path.realpath(os.environ['REPO_ROOT'])
bundle_phys = os.path.realpath(os.environ['WASM_BUNDLE_DIR'])
out_raw = os.environ['OUT_DIR']
home_raw = os.environ.get('HOME_DIR', '')
home_phys = os.path.realpath(home_raw) if home_raw else ''
tmp_phys = os.path.realpath('/tmp')

base = os.path.basename(out_raw.rstrip('/'))
if out_raw.strip() == '' or out_raw.rstrip('/') == '' or base in ('', '.', '..'):
    print(f'error: refusing unsafe out_dir: {out_raw}', file=sys.stderr)
    sys.exit(1)

out_phys = os.path.realpath(os.path.abspath(out_raw))
out_path = pathlib.Path(out_phys)


def is_same_or_inside(child: str, parent: str) -> bool:
    if not parent:
        return False
    try:
        pathlib.Path(child).relative_to(pathlib.Path(parent))
        return True
    except ValueError:
        return False


def is_same_or_ancestor(cand: str, target: str) -> bool:
    return is_same_or_inside(target, cand)


if out_phys == '/' or out_path.parent == pathlib.Path('/'):
    print(f'error: refusing root or top-level out_dir: {out_phys}', file=sys.stderr)
    sys.exit(1)
if out_phys in ('/tmp', tmp_phys):
    print(f'error: refusing shared temp root as out_dir: {out_phys}', file=sys.stderr)
    sys.exit(1)
if home_phys and is_same_or_ancestor(out_phys, home_phys):
    print(f'error: refusing out_dir equal to or ancestor of HOME: {out_phys}', file=sys.stderr)
    sys.exit(1)
if is_same_or_inside(out_phys, repo_phys) or is_same_or_ancestor(out_phys, repo_phys):
    print(f'error: refusing out_dir inside or overlapping repo root: {out_phys}', file=sys.stderr)
    sys.exit(1)
if is_same_or_inside(out_phys, bundle_phys) or is_same_or_ancestor(out_phys, bundle_phys):
    print(f'error: refusing out_dir inside or overlapping wasm_bundle_dir: {out_phys}', file=sys.stderr)
    sys.exit(1)

max_bytes = 26_214_400

lexicon_files = [
    'mcbpmf-data.txt',
    'overlay-add.tsv',
    'sandhi-add.tsv',
    'names-add.tsv',
    'variant-order.tsv',
    'rerank-protect.tsv',
    'demote.tsv',
    'std-exempt.tsv',
    'digits.tsv',
    'english.tsv',
    'mixed-words.tsv',
]
pack_files = [
    'acg-add.tsv',
    'it-add.tsv',
    'meme-add.tsv',
    'beauty-add.tsv',
]
p35_files = [
    'bigram.sjlm',
    'classes.sjc',
    'kn.sjkn',
    'trigram-chat.sjt3',
    'tinylm-cm.sjtl',
    'colloquial.sjlm',
]

sources = []
for f in lexicon_files:
    sources.append((f'data/{f}', os.path.join(repo_phys, 'data', 'lexicon', f)))
for f in pack_files:
    sources.append((f'data/{f}', os.path.join(repo_phys, 'data', 'packs', f)))
sources.append(('wasm/shanjie_wasm_simd128.wasm', os.path.join(bundle_phys, 'shanjie_wasm_simd128.wasm')))
for f in p35_files:
    sources.append((f'p35/{f}', os.path.join(bundle_phys, 'bundles', 'p35', f)))

src_map = {}
for rel, path in sources:
    if not os.path.isfile(path):
        print(f'error: missing required source file: {path}', file=sys.stderr)
        sys.exit(1)
    size = os.path.getsize(path)
    if size <= 0:
        print(f'error: empty required source file: {path}', file=sys.stderr)
        sys.exit(1)
    if size > max_bytes:
        print(f'error: source file exceeds {max_bytes} bytes ({size}): {rel}', file=sys.stderr)
        sys.exit(1)
    src_map[rel] = path


def sha256_path(path: str) -> bytes:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.digest()


def read_slice(path: str, rel: str, start: int, end: int) -> bytes:
    with open(path, 'rb') as fh:
        fh.seek(start)
        data = fh.read(end - start)
    if len(data) != end - start:
        print(f'error: {rel} truncated before offset {end}', file=sys.stderr)
        sys.exit(1)
    return data


bigram_sha = sha256_path(src_map['p35/bigram.sjlm'])
kn_sha = sha256_path(src_map['p35/kn.sjkn'])

bindings = [
    ('p35/classes.sjc', 8, 40, bigram_sha, 'p35/bigram.sjlm'),
    ('p35/kn.sjkn', 12, 44, bigram_sha, 'p35/bigram.sjlm'),
    ('p35/trigram-chat.sjt3', 20, 52, bigram_sha, 'p35/bigram.sjlm'),
    ('p35/tinylm-cm.sjtl', 68, 100, bigram_sha, 'p35/bigram.sjlm'),
    ('p35/tinylm-cm.sjtl', 212, 244, kn_sha, 'p35/kn.sjkn'),
]

for rel, start, end, expected_sha, target_rel in bindings:
    actual = read_slice(src_map[rel], rel, start, end)
    if actual != expected_sha:
        print(
            f'error: SHA-256 header mismatch in {rel}[{start}:{end}] '
            f'(expected sha256 of {target_rel})',
            file=sys.stderr,
        )
        sys.exit(1)

print(out_phys)
EOF
)"

# 2. Stage atomically into a sibling temp directory with cleanup trap, then
#    swap into OUT_ABS only after copying completes cleanly.
OUT_PARENT="$(dirname "$OUT_ABS")"
mkdir -p "$OUT_PARENT"
TMP_STAGE="$(mktemp -d "${OUT_PARENT%/}/.stage-site-wasm.XXXXXX")"
cleanup() {
  if [ -n "${TMP_STAGE:-}" ] && [ -d "$TMP_STAGE" ]; then
    rm -rf "$TMP_STAGE"
  fi
}
trap cleanup EXIT

cp -R "$REPO_ROOT/site/." "$TMP_STAGE/"

STAGE_DIR="$TMP_STAGE/$ASSET_SUBDIR"
mkdir -p "$STAGE_DIR/data" "$STAGE_DIR/wasm" "$STAGE_DIR/p35"

LEXICON_FILES=(
  mcbpmf-data.txt
  overlay-add.tsv
  sandhi-add.tsv
  names-add.tsv
  variant-order.tsv
  rerank-protect.tsv
  demote.tsv
  std-exempt.tsv
  digits.tsv
  english.tsv
  mixed-words.tsv
)
PACK_FILES=(
  acg-add.tsv
  it-add.tsv
  meme-add.tsv
  beauty-add.tsv
)
P35_FILES=(
  bigram.sjlm
  classes.sjc
  kn.sjkn
  trigram-chat.sjt3
  tinylm-cm.sjtl
  colloquial.sjlm
)
WASM_FILE="shanjie_wasm_simd128.wasm"

for f in "${LEXICON_FILES[@]}"; do
  cp "$REPO_ROOT/data/lexicon/$f" "$STAGE_DIR/data/$f"
done
for f in "${PACK_FILES[@]}"; do
  cp "$REPO_ROOT/data/packs/$f" "$STAGE_DIR/data/$f"
done
cp "$WASM_BUNDLE_DIR/$WASM_FILE" "$STAGE_DIR/wasm/$WASM_FILE"
for f in "${P35_FILES[@]}"; do
  cp "$WASM_BUNDLE_DIR/bundles/p35/$f" "$STAGE_DIR/p35/$f"
done

# Post-copy sanity verification inside TMP_STAGE before publishing to OUT_ABS.
STAGE_DIR="$STAGE_DIR" python3 - <<'EOF'
import os
import sys

stage_dir = os.environ['STAGE_DIR']
max_bytes = 26_214_400
expected_files = [
    'data/mcbpmf-data.txt',
    'data/overlay-add.tsv',
    'data/sandhi-add.tsv',
    'data/names-add.tsv',
    'data/variant-order.tsv',
    'data/rerank-protect.tsv',
    'data/demote.tsv',
    'data/std-exempt.tsv',
    'data/digits.tsv',
    'data/english.tsv',
    'data/mixed-words.tsv',
    'data/acg-add.tsv',
    'data/it-add.tsv',
    'data/meme-add.tsv',
    'data/beauty-add.tsv',
    'wasm/shanjie_wasm_simd128.wasm',
    'p35/bigram.sjlm',
    'p35/classes.sjc',
    'p35/kn.sjkn',
    'p35/trigram-chat.sjt3',
    'p35/tinylm-cm.sjtl',
    'p35/colloquial.sjlm',
]
for rel in expected_files:
    p = os.path.join(stage_dir, rel)
    if not os.path.isfile(p) or os.path.getsize(p) <= 0 or os.path.getsize(p) > max_bytes:
        print(f'error: staged verification failed for {rel}', file=sys.stderr)
        sys.exit(1)
EOF

rm -rf "$OUT_ABS"
mv "$TMP_STAGE" "$OUT_ABS"
TMP_STAGE=""

echo "staged and verified 22 runtime files in $OUT_ABS/$ASSET_SUBDIR"
