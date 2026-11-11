#!/usr/bin/env bash
# Builds the reading page of the paper, site/paper/zh.html, and its figures from the LaTeX source.
#
#   scripts/build-paper-html.sh MAIN_TEX REFS_BIB MAIN_AUX PAPER_PDF
#
# MAIN_TEX is the Traditional Chinese source, MAIN_AUX the .aux file LaTeX wrote when PAPER_PDF was built (the
# numbers on the page come from it), PAPER_PDF the PDF the figures are cut from. The source is not in this
# repository; the page is committed as a static file and rebuilt with this script when the paper is revised.
#
# Needs pandoc and pdftocairo (poppler). scripts/paper_html.py first writes a copy of the source without the
# constructs pandoc gets wrong; pandoc converts the copy with scripts/paper-zh.template.html (the whole page: head,
# navigation, footer) and scripts/paper.csl (reference entries with every author); paper_html.py then adds the numbers LaTeX printed, cuts the figures listed in
# scripts/paper-zh.figures.tsv out of the PDF, and prints the checks. Any failed check, a command pandoc skipped
# that could carry text, or a formula pandoc could not convert to MathML, ends with a non-zero status. The page
# loads nothing from another site.
set -euo pipefail
[ $# -eq 4 ] || { echo "usage: $0 MAIN_TEX REFS_BIB MAIN_AUX PAPER_PDF" >&2; exit 2; }
tex=$1 bib=$2 aux=$3 pdf=$4
here=$(cd "$(dirname "$0")" && pwd)
out=$here/../site/paper
# Everything is built in a scratch directory and moved into site/paper/ only after every check passed, so a failed
# run leaves the committed page and figures as they were.
work=$(mktemp -d)
log=$work/pandoc.log
trap 'rm -rf "$work"' EXIT

# -s is required: without it --toc is ignored and the title, author, date and abstract are not written.
# Sections become <h2> (the page's <h1> is the title); the contents list has one entry per \section.
python3 -B "$here/paper_html.py" --prepare "$tex" "$work/main.tex"
pandoc "$bib" --from bibtex --to csljson -o "$work/refs.json"      # for the entries' notes, which the style omits
if ! pandoc "$work/main.tex" --verbose --from latex --to html5 -s --template "$here/paper-zh.template.html" \
     --math-method=mathml --citeproc --csl "$here/paper.csl" --bibliography "$bib" -M link-citations=true \
     --section-divs --toc --toc-depth=2 --shift-heading-level-by=1 -o "$work/zh.html" 2> "$log"; then
  cat "$log" >&2
  exit 1
fi
grep -v '^\[INFO\]' "$log" | grep '^\[' >&2 || true      # warnings; what was skipped is judged by paper_html.py
if grep -c "Could not convert TeX math" "$log" > /dev/null; then
  echo "STOP: pandoc could not convert a formula to MathML" >&2
  exit 1
fi

# The date in the head and the footer is the paper's own (\paperdate).
date=$(sed -n 's/^\\newcommand{\\paperdate}{\([0-9-]*\)}.*/\1/p' "$tex")
[[ $date =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "STOP: no \\paperdate in $tex" >&2; exit 1; }
sed -i.bak "s/@DATE@/$date/g" "$work/zh.html" && rm "$work/zh.html.bak"

python3 -B "$here/paper_html.py" "$work/zh.html" "$tex" "$aux" "$pdf" "$here/paper-zh.figures.tsv" "$work" "$log" "$work/refs.json"
rm "$log" "$work/main.tex" "$work/refs.json"
mkdir -p "$out"
mv "$work"/* "$out"/
