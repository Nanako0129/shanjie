#!/usr/bin/env python3
"""Second half of scripts/build-paper-html.sh: turns pandoc's HTML of the paper into the reading page and checks it.

  python3 scripts/paper_html.py --prepare TEX OUT_TEX
  python3 scripts/paper_html.py HTML TEX AUX PDF FIGURES_TSV FIGDIR PANDOC_LOG REFS_JSON

--prepare writes the copy of the source that pandoc reads: three constructs pandoc 3 gets wrong are rewritten
(see `prepare`). PANDOC_LOG is the stderr of `pandoc --verbose` on that copy: every command pandoc skipped must be
one of BARE or PREAMBLE (layout and fonts only), because a skipped command can take text with it without any warning.
Citations follow the PDF as well: it cites by number ([35], [50, 51], sorted and compressed), and the numbers are in
AUX (`\\bibcite`). pandoc writes author-year citations, so each is rewritten to the PDF's numbers and the reference
list is put in that order with its numbers.
REFS_JSON is the bibliography as CSL JSON (`pandoc refs.bib -t csljson`): the PDF prints each entry's `note`
field (access dates, the commit a link is pinned to), and each note is appended to its entry here.

HTML is rewritten in place. pandoc numbers nothing the way LaTeX does (appendix letters, equations, tables), so
every visible number is put here: sections, tables, figures and display equations are counted in document order,
and every \\ref / \\eqref link gets the number LaTeX printed, read from AUX (`\\newlabel`). The two are independent,
so the checks at the end compare them: a link's text must equal the number shown at its target.

Figures are TikZ, which pandoc drops: each one is cut out of PDF with `pdftocairo -svg` (one page, then the
page is cropped to the box in FIGURES_TSV: id, file, page, x, y, width, height in PDF points, alt text).

Two citation forms are refused rather than rewritten: \\citet of several keys (natbib prints one author group per
key) and \\cite with a page or prefix argument (natbib prints it inside the brackets).

Exits 1 when any check fails. Standard library only. `python3 scripts/paper_html.py --self-test` runs the
numbering and the checks on a small synthetic paper, and breaks each check once (`make test-python` runs it).
"""
import html
import json
import re
import subprocess
import sys
import tempfile

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Commands pandoc may skip. BARE ones take no argument and carry no text (spacing, font size, page layout): the
# skipped text must be the command alone, because pandoc reports `\centering64` or `\quad{text}` when it swallows
# what follows. PREAMBLE ones set fonts and lengths before the text starts. \begin is checked separately (only a
# tikzpicture, which becomes an image, may be skipped).
BARE = set("appendix centering clearpage else endlastfoot fi footnotesize maketitle quad small".split())
PREAMBLE = set("""XeTeXlinebreaklocale XeTeXlinebreakskip arxivbuild bibliographystyle emergencystretch
    enlargethispage ifdefined newCJKfontfamily pgfplotsset setCJKmainfont setlength simpfont urlstyle usetikzlibrary
    xeCJKDeclareCharClass""".split())


def prepare(tex):
    """The source as pandoc can read it. Each rewrite undoes a loss seen with pandoc 3.13:
    - `>{\\raggedright\\arraybackslash}p{..}` columns: a cell that starts with a number loses the number
      (`64 x` becomes `x`, `1,133` becomes `,133`), so \\arraybackslash is removed;
    - a row that is one \\multicolumn across a longtable is dropped: its text goes into the first cell;
    - longtable repeats the head between \\endfirsthead and \\endhead as a body row: the repeat is removed."""
    tex = tex.replace(r"\arraybackslash", "")
    tex = re.sub(r"(?m)^\\multicolumn\{(\d+)\}\{(?:@\{\})?[lcr](?:@\{\})?\}\{(.*)\} *\\\\$",
                 lambda m: m.group(2) + " &" * (int(m.group(1)) - 1) + r" \\", tex)
    return re.sub(r"\\endfirsthead.*?\\endhead\n", r"\\endhead" "\n", tex, flags=re.S)


def skipped(log):
    """What pandoc skipped that may have carried text, from the stderr of `pandoc --verbose`."""
    out = []
    for m in re.finditer(r"(?ms)^\[INFO\] Skipped '(\\([A-Za-z]+).*?)' at ", log):
        text, name = m.group(1), m.group(2)
        if name == "begin":
            ok = text.startswith(r"\begin{tikzpicture}")
        else:
            ok = name in PREAMBLE or (name in BARE and text.strip() == "\\" + name)
        if not ok:
            out.append(text[:60].replace("\n", " "))
    return out


def aux_labels(path):
    """label -> the number LaTeX printed for it."""
    out = {}
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\\newlabel\{([^}]*)\}\{\{([^}]*)\}", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def aux_bibcite(path):
    """citation key -> the number the PDF prints for it."""
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"\\bibcite\{([^}]*)\}\{\{(\d+)\}", open(path, encoding="utf-8").read())}


def cite_numbers(keys, numbers):
    """natbib's `numbers,sort&compress` form of one citation: [3], [50, 51], [4–6], each number linked to its entry."""
    ordered = sorted(keys, key=numbers.get)
    link = lambda k: f'<a href="#ref-{k}">{numbers[k]}</a>'
    parts, i = [], 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and numbers[ordered[j + 1]] == numbers[ordered[j]] + 1:
            j += 1
        parts.append(f"{link(ordered[i])}–{link(ordered[j])}" if j - i >= 2 else ", ".join(link(k) for k in ordered[i:j + 1]))
        i = j + 1
    return "[" + ", ".join(parts) + "]"


def tex_body(path):
    """The source without comments, the preamble's macro definitions and the TikZ pictures (pandoc drops them)."""
    s = open(path, encoding="utf-8").read()
    s = re.sub(r"(?<!\\)%.*", "", s)
    s = s[s.index(r"\title"):]
    s = re.sub(r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}", "", s, flags=re.S)
    return s


def tex_counts(body):
    sections = len(re.findall(r"\\section\{", body))
    main = len(re.findall(r"\\section\{", body.split(r"\appendix")[0]))
    figures = len(re.findall(r"\\begin\{figure\}", body))
    cited = set()
    for keys in re.findall(r"\\cite[tp]?\*?(?:\[[^\]]*\])*\{([^}]*)\}", body):
        cited.update(k.strip() for k in keys.split(","))
    m = body.replace(r"\ngram", "$n$-gram").replace(r"\$", "")
    numbered = len(re.findall(r"\\begin\{equation\}", m))
    display = len(re.findall(r"\\begin\{(?:equation|align|gather|multline)\*?\}", m)) + len(re.findall(r"(?<!\\)\\\[", m))
    m = re.sub(r"\\begin\{(equation|align|gather|multline)(\*?)\}.*?\\end\{\1\2\}", "", m, flags=re.S)
    dollars = m.count("$")
    if dollars % 2:
        sys.exit("STOP: odd number of $ in the source")
    # Rows of each table's head: those between \toprule and the first \midrule (pandoc keeps only a one-row head).
    # None when a table lacks either rule: its head cannot be read, and a check stops the build.
    heads = [len(re.findall(r"\\\\", t.split(r"\midrule")[0].split(r"\toprule")[-1]))
             if r"\toprule" in t and r"\midrule" in t else None
             for t in re.findall(r"\\begin\{(?:tabular|longtable)\}.*?\\end\{(?:tabular|longtable)\}", body, flags=re.S)]
    cite_commands = len(re.findall(r"\\cite[tp]?\*?(?:\[[^\]]*\])*\{", body))
    # natbib prints a page or prefix argument ([5, p. 3]); the rewrite to numbers does not carry it over.
    cite_arguments = len(re.findall(r"\\cite[tp]?\*?\[", body))
    return (sections, main, figures, cited, display + dollars // 2 + m.count(r"\("), numbered, cite_commands, heads,
            cite_arguments)


def crop_figure(pdf, page, box, dest):
    """`box` (x, y, width, height in points) of one PDF page as an SVG file."""
    x, y, w, h = box
    with tempfile.TemporaryDirectory() as tmp:
        cut = f"{tmp}/cut.pdf"
        subprocess.run(["pdftocairo", "-pdf", "-f", page, "-l", page, "-x", x, "-y", y, "-W", w, "-H", h,
                        "-paperw", w, "-paperh", h, pdf, cut], check=True)
        # ponytail: cairo keeps the glyph outlines of the whole page, so a figure on a text page is about 0.4 MB;
        # prune the unused <defs> if the size ever matters.
        subprocess.run(["pdftocairo", "-svg", cut, dest], check=True)


def self_test():
    """A two-section paper with one appendix, through main(): the checks pass on it, and each check fails on an input
    made to break it (the STOP message must name that check)."""
    tex = (r"\title{T}" "\n" r"\section{One}\label{sec:one} $a$ \cite{k} \cite{k} \citet{k}"
           r" \begin{equation}x\label{eq:x}\end{equation}"
           "\n" r"\subsection{Sub}\label{sec:sub} \ref{app:a} \eqref{eq:x} \ref{tab:t}" "\n" r"\appendix"
           "\n" r"\section{App}\label{app:a}"
           "\n" r"\begin{tabular}{l}\toprule H \\ I \\ \midrule x \\ \end{tabular}")
    page = ('<li><a href="#sec:one" id="toc-sec:one">One</a></li>\n<li><a href="#app:a" id="toc-app:a">App</a></li>\n'
            '<section id="sec:one" class="level2">\n<h2>One</h2><math display="inline">a</math>'
            '<span class="citation" data-cites="k">(<a href="#ref-k" role="doc-biblioref">K 2020</a>)</span>'
            '<span\nclass="citation" data-cites="k">(<a href="#ref-k"\nrole="doc-biblioref"><span\nclass="nocase">k</span> 2020</a>)</span>'
            '<span class="citation" data-cites="k"><em>K</em> (ROC) (<a href="#ref-k" role="doc-biblioref">2020</a>)</span>'
            '<math display="block"><annotation>x\\label{eq:x}</annotation></math>\n'
            '<section id="sec:sub" class="level3">\n<h3>Sub</h3>'
            '<a href="#app:a" data-reference-type="ref" data-reference="app:a">2</a>'
            '<a href="#eq:x" data-reference-type="eqref" data-reference="eq:x">[eq:x]</a>'
            '<a href="#tab:t" data-reference-type="ref" data-reference="tab:t">1</a>'
            '<table id="tab:t">\n<caption>c</caption>\n<tbody>\n<tr>\n<td>H</td>\n</tr><tr class="even"><td>I</td></tr>'
            '<tr class="odd"><td>x</td></tr>\n</tbody>\n</table></section></section>\n'
            '<section id="app:a" class="level2">\n<h2>App</h2>'
            '<div id="refs" class="references">\n<div id="ref-k" class="csl-entry" role="listitem">\nK\n</div>\n</div>\n'
            '</section>')
    aux = "".join(f"\\newlabel{{{k}}}{{{{{v}}}{{1}}}}\n" for k, v in
                  (("sec:one", "1"), ("eq:x", "1"), ("sec:sub", "1.1"), ("tab:t", "1"), ("app:a", "A")))
    aux += "\\bibcite{k}{{7}{2020}{{K}}{{}}}\n"
    refs = '[{"id": "k", "note": "Seen at https://e.org/x"}, {"id": "u", "note": "n"}]'
    with tempfile.TemporaryDirectory() as tmp:
        def run(page=page, tex=tex, aux=aux, tsv="", log="", refs=refs):
            for name, text in (("p.html", page), ("m.tex", tex), ("m.aux", aux), ("f.tsv", tsv), ("log", log),
                               ("r.json", refs)):
                with open(f"{tmp}/{name}", "w", encoding="utf-8") as f:
                    f.write(text)
            sys.argv[1:] = [f"{tmp}/p.html", f"{tmp}/m.tex", f"{tmp}/m.aux", "", f"{tmp}/f.tsv", tmp, f"{tmp}/log",
                            f"{tmp}/r.json"]
            main()
            return open(f"{tmp}/p.html", encoding="utf-8").read()
        out = run()
        assert '<a href="#app:a">A</a><a href="#eq:x">(1)</a><a href="#tab:t">1</a>' in out, out
        assert '<span class="secno">A</span> App' in out and '<span class="secno">1.1</span> Sub' in out, out
        assert 'id="toc-app:a"><span class="secno">附錄 A</span>' in out, out
        assert out.count('<span class="citation">[<a href="#ref-k">7</a>]</span>') == 2 and "2020" not in out, out
        assert '<span class="citation"><em>K</em> (ROC) [<a href="#ref-k">7</a>]</span>' in out, out
        assert '<span class="csl-num">[7]</span> K' in out, out
        assert ('<thead>\n<tr>\n<th>H</th>\n</tr>\n<tr class="even"><th>I</th></tr>\n</thead>\n<tbody>\n'
                '<tr class="odd"><td>x</td></tr>\n</tbody>') in out, out
        nums = {"a": 3, "b": 4, "c": 5, "d": 9, "e": 10}
        assert re.sub(r"<[^>]+>", "", cite_numbers(["d", "b", "a", "c", "e"], nums)) == "[3–5, 9, 10]"
        assert 'K <span class="csl-note">Seen at <a href="https://e.org/x">https://e.org/x</a>.</span>' in out, out
        assert out.index('id="references"') < out.index('<section id="app:a"') and '表 1：' in out
        one, app, ref_k = "<h2>One</h2>", "<h2>App</h2>", '<div id="ref-k"'
        tab = r"\begin{tabular}{l}\toprule H \\ I \\ \midrule x \\ \end{tabular}"
        for expect, broken in (
                ("commands pandoc skipped", dict(log="[INFO] Skipped '\\pageref{sec:one}' at m.tex line 2\n")),
                ("commands pandoc skipped", dict(log="[INFO] Skipped '\\begin{tabular}' at m.tex line 2\n")),
                ("commands pandoc skipped", dict(log="[INFO] Skipped '\\centering64' at m.tex line 2\n")),
                ("commands pandoc skipped", dict(log="[INFO] Skipped '\\quad{text}' at m.tex line 2\n")),
                ("table rows without any text", dict(page=page.replace("</table>", "<tr><td></td></tr></table>"))),
                ("sections (", dict(tex=tex + "\n" + r"\section{Z}")),
                ("math (", dict(page=page.replace(one, one + '<math display="inline">z</math>'))),
                ("references (.csl-entry", dict(tex=tex.replace(r"\citet{k}", r"\citet{k} \cite{q}"))),
                ("contents entries", dict(page=page.replace('<li><a href="#sec:one" id="toc-sec:one">One</a></li>\n', ""))),
                ("display equations (numbered", dict(tex=tex + "\n" + r"\begin{equation}y\end{equation}")),
                ("not an equation environment", dict(page=page.replace(one, one + '<math display="block">y</math>'))),
                ("tables (numbered", dict(page=page.replace(one, one + "<table><tr><td>x</td></tr></table>"))),
                ("tables (on the page", dict(tex=tex + "\n" + tab)),
                ("not between \\toprule and \\midrule", dict(tex=tex.replace(r"\midrule ", ""))),
                ("not between \\toprule and \\midrule", dict(tex=tex.replace(r"\toprule H \\ I \\ \midrule", r"\toprule \midrule H \\ I \\"))),
                ("tables without a head", dict(page=page.replace("<tbody>", "").replace("</tbody>", ""))),
                ("head rows differ", dict(page=page.replace("<tbody>\n<tr>\n<td>H</td>\n</tr>",
                                                            "<thead>\n<tr>\n<th>H</th>\n</tr>\n</thead>\n<tbody>\n"))),
                ("no body row", dict(tex=tex.replace(r"\midrule x \\", r"x \\ \midrule"))),
                ("figures (", dict(tex=tex + "\n" + r"\begin{figure}\end{figure}")),
                ("figures in the list", dict(tsv="fig:x\tx.svg\t1\t0\t0\t1\t1\talt\n")),
                ("has no \\newlabel", dict(page=page.replace(
                    one, one + '<a href="#sec:zz" data-reference-type="ref" data-reference="sec:zz">9</a>'))),
                ("neither \\ref nor \\eqref", dict(page=page.replace(
                    one, one + '<a href="#sec:one" data-reference-type="ref+label" data-reference="sec:one">1</a>'))),
                ("links whose text differs", dict(aux=aux.replace("{A}", "{B}"))),
                ("labels whose visible number differs", dict(aux=aux.replace("{A}", "{B}"))),
                ("labels in the aux file without an element", dict(aux=aux + "\\newlabel{sec:ghost}{{9}{1}}\n")),
                ("no number for", dict(aux=aux.replace("bibcite{k}", "bibcite{z}"))),
                ("\\citet of several keys", dict(page=page.replace(
                    'data-cites="k"><em>K</em> (ROC) (<a href="#ref-k" role="doc-biblioref">2020</a>)',
                    'data-cites="k q">A and B (<a href="#ref-k" role="doc-biblioref">2020</a>; '
                    '<a href="#ref-q" role="doc-biblioref">C 2021</a>)'))),
                ("page or prefix argument", dict(tex=tex.replace(r"\citet{k}", r"\cite[p.~3]{k}"))),
                ("not rewritten whole", dict(page=page.replace('role="doc-biblioref">K 2020</a>',
                                                              'role="doc-biblioref"><span\nclass="x">k</span> 2020</a>'))),
                ("author-year citation text left", dict(page=page.replace(one, one + '<a role="doc-biblioref">x</a>'))),
                ("rewritten to numbers", dict(page=page.replace('class="citation"', 'class="cit"'))),
                ("reference notes", dict(page=page.replace('class="csl-entry"', 'class="csl-entry x"'))),
                ("reference entries without a number", dict(page=page.replace(
                    ref_k, '<div id="ref-q" class="csl-entry" role="listitem">\nQ\n</div>\n' + ref_k))),
                ("links to an id that is not on the page", dict(page=page.replace(one, one + '<a href="#nope">x</a>'))),
                ("unresolved \\ref", dict(page=page.replace(app, app + "[sec:zz]"))),
                ("load something from another site", dict(page=page.replace(one, one + '<script src="//h/x.js"></script>'))),
                ("odd number of $", dict(tex=tex + " $"))):
            try:
                run(**broken)
            except SystemExit as e:
                assert expect in str(e), (expect, str(e))
            else:
                raise AssertionError(f"passed although: {expect}")
        assert skipped("[INFO] Skipped '\\small' at x\n[INFO] Skipped '\\quad ' at x\n"
                       "[INFO] Skipped '\\setlength{\\tabcolsep}{3pt}' at x\n"
                       "[INFO] Skipped '\\begin{tikzpicture}[a]\n  \\node' at x\n") == []
        row = prepare(">{\\raggedright\\arraybackslash}p{3cm}\n\\multicolumn{2}{@{}l}{\\emph{G}} \\\\\n"
                      "\\endfirsthead\nH \\\\\n\\endhead\n")
        assert row == ">{\\raggedright}p{3cm}\n\\emph{G} & \\\\\n\\endhead\n", row
    print("self-test ok")


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    if sys.argv[1:2] == ["--prepare"]:
        with open(sys.argv[3], "w", encoding="utf-8") as f:
            return f.write(prepare(open(sys.argv[2], encoding="utf-8").read())) and None
    page, tex, aux, pdf, tsv, figdir, log, refs_json = sys.argv[1:]
    s = open(page, encoding="utf-8").read()
    labels = aux_labels(aux)
    (n_sections, n_main, n_figures, cited, n_math, n_numbered, n_cite_commands, heads,
     n_cite_arguments) = tex_counts(tex_body(tex))
    figs = {}
    for line in open(tsv, encoding="utf-8"):
        if line.strip() and not line.startswith("#"):
            fid, name, pg, x, y, w, h, alt = line.rstrip("\n").split("\t")
            figs[fid] = (name, pg, (x, y, w, h), alt)
    shown = {}          # id -> the number visible at that element
    errors = []

    def check(name, got, want):
        print(f"{name}: {got} / {want}")
        if got != want:
            errors.append(name)

    lost = skipped(open(log, encoding="utf-8").read())
    for item in lost:
        print(f"  pandoc skipped {item}")
    check("commands pandoc skipped that may carry text", len(lost), 0)
    check("table rows without any text", len(re.findall(r"<tr[^>]*>\s*(?:<t[dh][^>]*>\s*</t[dh]>\s*)+</tr>", s)), 0)
    check("sections (section.level2 / \\section)", len(re.findall(r'<section id="[^"]*" class="level2', s)), n_sections)
    check("math (<math / formulas in the source)", s.count("<math"), n_math)
    check("references (.csl-entry / cited keys)", s.count('class="csl-entry"'), len(cited))
    check("contents entries (nav li / \\section)", len(re.findall(r'<li>\s*<a href="#[^"]+"\s+id="toc-', s)), n_sections)

    # Sections: LaTeX numbers \section and \subsection; \paragraph (level 5 here) has no number of its own, and a
    # \ref to one prints the enclosing subsection's.
    state = {"sec": 0, "sub": 0, "cur": ""}

    def section(m):
        sid, level = m.group(1), int(m.group(2))
        if level == 2:
            state["sec"] += 1
            state["sub"] = 0
            k = state["sec"]
            state["cur"] = str(k) if k <= n_main else LETTERS[k - n_main - 1]
            label = state["cur"]             # the PDF prints the appendix letter alone ("A"), as the contents do not
        elif level == 3:
            state["sub"] += 1
            state["cur"] = f"{state['cur'].split('.')[0]}.{state['sub']}"
            label = state["cur"]
        else:
            shown[sid] = state["cur"]
            return m.group(0)
        shown[sid] = state["cur"]
        return f'{m.group(0)}<span class="secno">{label}</span> '

    s = re.sub(r'<section id="([^"]*)" class="level(\d)[^"]*">\s*<h\d>', section, s)

    first_appendix = [None]

    def toc(m):
        sid = m.group(1)
        number = shown[sid]
        extra = ""
        if number == "A":
            first_appendix[0] = sid
            extra = '<li><a href="#references">參考文獻</a></li>\n'
        label = number if number[0].isdigit() else f"附錄 {number}"
        return f'{extra}{m.group(0)}<span class="secno">{label}</span> '

    s = re.sub(r'<li>\s*<a href="#([^"]+)"\s+id="toc-[^"]+">', toc, s)

    # Display equations are numbered in order, which is right only while every one is an `equation` environment
    # (an unnumbered \[ \] or a multi-line align would shift the numbers): the check below stops otherwise.
    count = {"eq": 0, "tab": 0, "fig": 0, "img": 0}

    def equation(m):
        count["eq"] += 1
        label = re.search(r"\\label\{([^}]*)\}", m.group(0))
        anchor = ""
        if label:
            shown[label.group(1)] = str(count["eq"])
            anchor = f' id="{label.group(1)}"'
        return f'<span class="eqn"{anchor}>{m.group(0)}<span class="eqno">({count["eq"]})</span></span>'

    s = re.sub(r'<math display="block".*?</math>', equation, s, flags=re.S)
    check("display equations (numbered on the page / equation environments)", count["eq"], n_numbered)
    check("display formulas that are not an equation environment", s.count('<math display="block"') - n_numbered, 0)

    def table(m):
        count["tab"] += 1
        if m.group(2):
            shown[m.group(2)] = str(count["tab"])
        return f'<div class="table">\n<table{m.group(1) or ""}>\n<caption><span class="capno">表 {count["tab"]}：</span>'

    s = re.sub(r'<table( id="([^"]*)")?>\s*<caption>', table, s)
    check("tables (numbered / <table)", count["tab"], s.count("<table"))
    check("tables (on the page / tabular and longtable environments)", count["tab"], len(heads))

    # A head of no rows (\toprule right before \midrule) is refused too: the move below would take a data row.
    check("tables whose head is not between \\toprule and \\midrule", sum(1 for h in heads if not h), 0)

    # A head of two rows comes out of pandoc as body rows: they are moved into <thead> as <th>.
    it = iter(heads)

    def head(m):
        t, rows = m.group(0), next(it, None) or 1
        if "<thead>" in t or "<tbody>" not in t:
            return t
        before, body = t.split("<tbody>", 1)
        body, after = body.split("</tbody>", 1)
        trs = re.findall(r"<tr\b.*?</tr>", body, flags=re.S)
        top = "\n".join(trs[:rows]).replace("<td", "<th").replace("</td>", "</th>")
        return f"{before}<thead>\n{top}\n</thead>\n<tbody>\n" + "\n".join(trs[rows:]) + f"\n</tbody>{after}"

    if len(heads) == count["tab"]:
        s = re.sub(r"<table.*?</table>", head, s, flags=re.S)
    check("tables without a head", s.count("<table") - s.count("<thead>"), 0)
    on_page = [len(re.findall(r"<tr\b", t)) for t in re.findall(r"<thead>.*?</thead>", s, flags=re.S)]
    check("tables whose head rows differ from the source", sum(1 for a, b in zip(on_page, heads) if b and a != b), 0)
    check("tables with no body row", len(re.findall(r"<tbody>\s*</tbody>", s)), 0)
    s = s.replace("</table>", "</table>\n</div>")

    def figure(m):
        count["fig"] += 1
        fid = m.group(1)
        shown[fid] = str(count["fig"])
        if fid not in figs:
            errors.append(f"figure {fid} is not in {tsv}")
            return m.group(0)
        name, pg, box, alt = figs[fid]
        crop_figure(pdf, pg, box, f"{figdir}/{name}")
        count["img"] += 1
        return (f'<figure id="{fid}">\n<a href="/paper/{name}"><img src="/paper/{name}" '
                f'alt="{html.escape(alt, quote=True)}" width="{box[2]}" height="{box[3]}"></a>\n'
                f'<figcaption><span class="capno">圖 {count["fig"]}：</span>')

    s = re.sub(r'<figure id="([^"]*)"[^>]*>\s*<figcaption>', figure, s)
    check("figures (<figure> given an image / figure environments)", count["img"], n_figures)
    check("figures in the list that the page does not have", sum(1 for k in figs if k not in shown), 0)

    # Cross-references: the text LaTeX printed, from the aux file.
    bad = {"aux": 0, "target": 0}

    def ref(m):
        label, kind = m.group(1), m.group(2)
        if label not in labels:
            errors.append(f"\\ref{{{label}}} has no \\newlabel")
            return m.group(0)
        if shown.get(label) != labels[label]:
            bad["target"] += 1
            print(f"  {label}: the page shows {shown.get(label)}, LaTeX printed {labels[label]}")
        text = f"({labels[label]})" if kind == "eqref" else labels[label]
        return f'<a href="#{label}">{text}</a>'

    s, n_refs = re.subn(r'<a\s+href="#([^"]+)"\s+data-reference-type="(ref|eqref)"\s+data-reference="[^"]+">.*?</a>',
                        ref, s, flags=re.S)
    # \autoref, \cref, \pageref and \nameref come out of pandoc with its own numbers or with no text at all.
    check("cross-references that are neither \\ref nor \\eqref", s.count("data-reference-type="), 0)
    for label, number in labels.items():            # every label LaTeX knows, referenced or not
        if label in shown and shown[label] != number:
            bad["aux"] += 1
            print(f"  {label}: the page shows {shown[label]}, LaTeX printed {number}")
    check(f"links whose text differs from the number at their target (of {n_refs} \\ref links)", bad["target"], 0)
    check(f"labels whose visible number differs from the aux file (of {len(labels)})", bad["aux"], 0)
    check("labels in the aux file without an element on the page", sum(1 for k in labels if k not in shown), 0)

    # Citations by number, as in the PDF. A \citet keeps the authors pandoc wrote in front of the bracket.
    numbers = aux_bibcite(aux)
    unknown = set()
    truncated = []
    several = []

    def citation(m):
        keys = m.group(1).split()
        if not m.group(2).rstrip().endswith(")") or "<span" in m.group(2):
            truncated.append(keys)                     # the match stopped at an inner </span>: text would be left over
            return m.group(0)
        # the authors end at the parenthesis that holds the links, not at one inside a name ("Taiwan (ROC) Ministry")
        authors = re.match(r"\s*(.*?)\s*\((?=\s*<a\b)", m.group(2), flags=re.S)
        if not authors:
            truncated.append(keys)
            return m.group(0)
        authors = authors.group(1)
        if authors and len(keys) > 1:
            several.append(keys)                       # natbib: "A and B [3], C [4]"; pandoc gives one author group
            return m.group(0)
        unknown.update(k for k in keys if k not in numbers)
        if unknown:
            return m.group(0)
        return f'<span class="citation">{authors + " " if authors else ""}{cite_numbers(keys, numbers)}</span>'

    # pandoc may break a tag across lines (`<span\nclass="nocase">`): unwrapped first, so that citation spans do not nest
    s = re.sub(r'<span\s+class="nocase">(.*?)</span>', r"\1", s, flags=re.S)
    s, n_cites = re.subn(r'<span\s+class="citation"\s+data-cites="([^"]*)">(.*?)</span>', citation, s, flags=re.S)
    check("citations of a key the aux file has no number for", len(unknown), 0)
    check("\\citet of several keys (not supported)", len(several), 0)
    check("\\cite with a page or prefix argument (not supported)", n_cite_arguments, 0)
    check("citations not rewritten whole (an inner span ended the match)", len(truncated), 0)
    check("author-year citation text left on the page", len(re.findall(r'doc-biblioref|</a>\)</span>', s)), 0)
    check("citations (rewritten to numbers / \\cite commands in the source)", n_cites, n_cite_commands)

    # Notes of the bibliography entries (access dates, the commit a link is pinned to): the PDF prints them.
    notes = {e["id"]: e["note"] for e in json.load(open(refs_json, encoding="utf-8")) if e.get("note")}
    added = [0]

    def entry(m):
        note = notes.get(m.group(1))
        if not note:
            return m.group(0)
        added[0] += 1
        note = note if note.endswith(("。", ".")) else note + "."
        note = re.sub(r"(https?://[^\s<]+?)(?=\.?(?:\s|$))", r'<a href="\1">\1</a>', html.escape(note, quote=False))
        return f'{m.group(0)} <span class="csl-note">{note}</span>'

    s = re.sub(r'<div id="ref-([^"]+)" class="csl-entry"[^>]*>(?:(?!</div>).)*?(?=\s*</div>)', entry, s, flags=re.S)
    check("reference notes (on the page / cited entries with a note)", added[0], sum(1 for k in cited if k in notes))

    # The reference list goes where the PDF has it: after the last numbered section, before the appendices.
    start = s.index('<div id="refs"')
    end = s.index("</section>", start)
    refs = s[start:end]
    entries = re.findall(r'<div id="ref-([^"]+)" class="csl-entry".*?\n</div>', refs, flags=re.S)
    check("reference entries without a number in the aux file", sum(1 for k in entries if k not in numbers), 0)
    blocks = {m.group(1): m.group(0).replace('role="listitem">\n', f'role="listitem">\n<span class="csl-num">[{numbers.get(m.group(1), 0)}]</span> ', 1)
              for m in re.finditer(r'<div id="ref-([^"]+)" class="csl-entry".*?\n</div>', refs, flags=re.S)}
    refs = (refs[:refs.index('<div id="ref-')] + "\n".join(blocks[k] for k in sorted(blocks, key=lambda k: numbers.get(k, 0)))
            + "\n</div>\n")
    s = s[:start] + s[end:]
    at = s.index(f'<section id="{first_appendix[0]}"')
    s = s[:at] + f'<section id="references">\n<h2>參考文獻</h2>\n{refs}</section>\n' + s[at:]

    ids = set(re.findall(r'\bid="([^"]+)"', s))
    check("links to an id that is not on the page", sum(1 for h in re.findall(r'href="#([^"]+)"', s) if h not in ids), 0)
    check("unresolved \\ref left in the text", len(re.findall(r"\[(?:sec|eq|tab|fig|app):", s)), 0)
    # Anything a browser fetches must be a path on this site ("/x", not "//host/x"); <a href> may point anywhere.
    local = r'(?:/(?!/)|https://shanjie\.nyanako\.com/)'
    outside = re.findall(rf'\b(?:src|srcset|poster|data|xlink:href)="(?!{local})[^"]*"|<link[^>]*\bhref="(?!{local})[^"]*"'
                         r'|url\(|@import|<iframe|<object|<embed|<image\b', s)
    check("attributes that load something from another site", len(outside), 0)

    with open(page, "w", encoding="utf-8") as f:
        f.write(s)
    if errors:
        sys.exit("STOP: " + "; ".join(errors))


if __name__ == "__main__":
    main()
