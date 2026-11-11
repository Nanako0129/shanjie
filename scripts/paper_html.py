#!/usr/bin/env python3
"""Second half of scripts/build-paper-html.sh: turns pandoc's HTML of the paper into the reading page and checks it.

  python3 scripts/paper_html.py --prepare TEX OUT_TEX
  python3 scripts/paper_html.py HTML TEX AUX PDF FIGURES_TSV FIGDIR PANDOC_LOG

--prepare writes the copy of the source that pandoc reads: three constructs pandoc 3 gets wrong are rewritten
(see `prepare`). PANDOC_LOG is the stderr of `pandoc --verbose` on that copy: every command pandoc skipped must be
one of SKIPPABLE (layout and preamble only), because a skipped command can take text with it without any warning.

HTML is rewritten in place. pandoc numbers nothing the way LaTeX does (appendix letters, equations, tables), so
every visible number is put here: sections, tables, figures and display equations are counted in document order,
and every \\ref / \\eqref link gets the number LaTeX printed, read from AUX (`\\newlabel`). The two are independent,
so the checks at the end compare them: a link's text must equal the number shown at its target.

Figures are TikZ, which pandoc drops: each one is cut out of PDF with `pdftocairo -svg` (one page, then the
page is cropped to the box in FIGURES_TSV: id, file, page, x, y, width, height in PDF points, alt text).

Exits 1 when any check fails. Standard library only. `python3 scripts/paper_html.py --self-test` runs the
numbering and the checks on a small synthetic paper (`make test-python` runs it).
"""
import html
import re
import subprocess
import sys
import tempfile

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Commands pandoc may skip: they set fonts, spacing or page layout and carry no text. \begin is checked separately
# (only a tikzpicture, which becomes an image, may be skipped).
SKIPPABLE = set("""XeTeXlinebreaklocale XeTeXlinebreakskip appendix arxivbuild bibliographystyle centering clearpage
    else emergencystretch endlastfoot enlargethispage fi footnotesize ifdefined maketitle newCJKfontfamily pgfplotsset
    quad setCJKmainfont setlength simpfont small urlstyle usetikzlibrary xeCJKDeclareCharClass""".split())


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
    """What pandoc skipped that is not in SKIPPABLE, from the stderr of `pandoc --verbose`."""
    out = []
    for m in re.finditer(r"(?m)^\[INFO\] Skipped '(\\([A-Za-z]+)[^\n]*)", log):
        ok = m.group(1).startswith(r"\begin{tikzpicture}") if m.group(2) == "begin" else m.group(2) in SKIPPABLE
        if not ok:
            out.append(m.group(1)[:60])
    return out


def aux_labels(path):
    """label -> the number LaTeX printed for it."""
    out = {}
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\\newlabel\{([^}]*)\}\{\{([^}]*)\}", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


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
    return sections, main, figures, cited, display + dollars // 2 + m.count(r"\("), numbered


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
    """A two-section paper with one appendix, through main(): the checks pass, and fail when the aux file disagrees."""
    tex = (r"\title{T}" "\n" r"\section{One}\label{sec:one} $a$ \cite{k} \begin{equation}x\label{eq:x}\end{equation}"
           "\n" r"\subsection{Sub}\label{sec:sub} \ref{app:a} \eqref{eq:x} \ref{tab:t}" "\n" r"\appendix"
           "\n" r"\section{App}\label{app:a}")
    global page, log
    log = ""
    page = ('<li><a href="#sec:one" id="toc-sec:one">One</a></li>\n<li><a href="#app:a" id="toc-app:a">App</a></li>\n'
            '<section id="sec:one" class="level2">\n<h2>One</h2><math display="inline">a</math>'
            '<math display="block"><annotation>x\\label{eq:x}</annotation></math>\n'
            '<section id="sec:sub" class="level3">\n<h3>Sub</h3>'
            '<a href="#app:a" data-reference-type="ref" data-reference="app:a">2</a>'
            '<a href="#eq:x" data-reference-type="eqref" data-reference="eq:x">[eq:x]</a>'
            '<a href="#tab:t" data-reference-type="ref" data-reference="tab:t">1</a>'
            '<table id="tab:t">\n<caption>c</caption></table></section></section>\n'
            '<section id="app:a" class="level2">\n<h2>App</h2>'
            '<div id="refs"><div id="ref-k" class="csl-entry">K</div></div></section>')
    aux = "".join(f"\\newlabel{{{k}}}{{{{{v}}}{{1}}}}\n" for k, v in
                  (("sec:one", "1"), ("eq:x", "1"), ("sec:sub", "1.1"), ("tab:t", "1"), ("app:a", "A")))
    with tempfile.TemporaryDirectory() as tmp:
        def run(aux_text):
            for name, text in (("p.html", page), ("m.tex", tex), ("m.aux", aux_text), ("f.tsv", ""), ("log", log)):
                with open(f"{tmp}/{name}", "w", encoding="utf-8") as f:
                    f.write(text)
            sys.argv[1:] = [f"{tmp}/p.html", f"{tmp}/m.tex", f"{tmp}/m.aux", "", f"{tmp}/f.tsv", tmp, f"{tmp}/log"]
            main()
            return open(f"{tmp}/p.html", encoding="utf-8").read()
        out = run(aux)
        assert '<a href="#app:a">A</a><a href="#eq:x">(1)</a><a href="#tab:t">1</a>' in out, out
        assert '<span class="secno">附錄 A</span> App' in out and '<span class="secno">1.1</span> Sub' in out
        assert out.index('id="references"') < out.index('<section id="app:a"') and '表 1：' in out
        good = page
        one = "<h2>One</h2>"
        for expect, bad_aux, bad_page, bad_log in (
                ("labels whose visible number differs", aux.replace("{A}", "{B}"), good, ""),
                ("load something from another site", aux, good.replace(one, one + '<script src="//h/x.js"></script>'), ""),
                ("not an equation environment", aux, good.replace(one, one + '<math display="block">y</math>'), ""),
                ("neither \\ref nor \\eqref", aux, good.replace(
                    one, one + '<a href="#sec:one" data-reference-type="ref+label" data-reference="sec:one">1</a>'), ""),
                ("links to an id that is not on the page", aux, good.replace(one, one + '<a href="#nope">x</a>'), ""),
                ("table rows without any text", aux, good.replace("</table>", "<tr><td></td></tr></table>"), ""),
                ("tables (numbered", aux, good.replace(one, one + "<table><tr><td>x</td></tr></table>"), ""),
                ("commands pandoc skipped", aux, good, "[INFO] Skipped '\\pageref{sec:one}' at m.tex line 2\n"),
                ("commands pandoc skipped", aux, good, "[INFO] Skipped '\\begin{tabular}' at m.tex line 2\n")):
            page, log = bad_page, bad_log
            try:
                run(bad_aux)
            except SystemExit as e:
                assert expect in str(e), (expect, str(e))
            else:
                raise AssertionError(f"passed although: {expect}")
        assert skipped("[INFO] Skipped '\\small' at x\n[INFO] Skipped '\\begin{tikzpicture}[a]\n  \\node' at x\n") == []
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
    page, tex, aux, pdf, tsv, figdir, log = sys.argv[1:]
    s = open(page, encoding="utf-8").read()
    labels = aux_labels(aux)
    n_sections, n_main, n_figures, cited, n_math, n_numbered = tex_counts(tex_body(tex))
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
            label = state["cur"] if k <= n_main else f"附錄 {state['cur']}"
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

    # The reference list goes where the PDF has it: after the last numbered section, before the appendices.
    start = s.index('<div id="refs"')
    end = s.index("</section>", start)
    refs = s[start:end]
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
