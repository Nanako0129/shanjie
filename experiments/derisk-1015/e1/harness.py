"""E1/E4(b) evaluation harness: reference/proto/lm_eval.py plus (a) a trigram table, (b) lambda / KN-beta overrides, (c) the E4 (b)
"digit / English in the left context is <X>" start state. reference/proto is imported, never edited.

Differences from lm_eval.py, and only these:
  --tri FILE        trigram table (trimodel.py, SJT30001) bound to --lm's SHA-256. The decoder state carries two history words (u, v)
                    where lm.decode carries one; paths are recombined on (surface, previous word, last word) instead of (surface, last word).
  --lam X           lambda instead of the profile's (chat 0.5, formal 0.7).
  --beta X          KN beta instead of the side file's header value (the grid needs 3 betas from one kn.sjkn).
  --x-context       E4 (b): when the left context ends in a digit / Latin run (mapping.ends_with_x) the first word's history is "<X>";
                    only for a model whose vocabulary has <X> (a model built from the mapped counts).
Null configuration (no --tri, no --lam / --beta / --x-context): the code path, the arithmetic and the summary line are lm_eval.py's, byte for
byte (anchor.py checks it, README "anchor"). Non-null options append tags at the very end of the profile column:
  +tri:<sha8 of the table>  +lam:<x>  +beta:<x>  +xctx

Same options as lm_eval.py otherwise (--lm --profile --rows/--dev --limit --name --dump --rowstats --context --no-demote --no-classes
--kn --kn-beta --no-kn --extra-overlay) and the same output files (--rowstats via evalstats.format_rowstats, --dump the top 64).
"""
import argparse
import glob
import hashlib
import os

import common
import mapping
from trimodel import TriLM, TriTable

import ime  # noqa: E402
import lm as L  # noqa: E402
import lm_eval as LE  # noqa: E402
from eval import lenient  # noqa: E402
from evalstats import format_rowstats  # noqa: E402

X = mapping.X
ROOT = LE.ROOT


def start_state(ctx, lm, context=True, x_context=False):
    """(u, v): the two history words the first word is scored from. No context, or a context with no usable history, is a sentence
    start (<s>, <s>); a context supplies one word, so u is unknown (None) and the trigram level is skipped for the first word."""
    if not context:
        return ("<s>", "<s>")
    if x_context and mapping.ends_with_x(ctx):
        xi = lm.ids.get(X, -1)
        v = X if lm.ctx.get(xi) is not None else "<s>"
    else:
        v = L.history(L.context_key(ctx), lm)
    return ("<s>", "<s>") if v == "<s>" else (None, v)


def decode3(lex, syls, lm, lam, start, tri_lm=None, beam=64, demote=True, ctx_u="keep"):
    """lm.decode with the history (u, v). tri_lm None: exactly lm.decode (state key, arithmetic, order of operations); start is (u, v).
    ctx_u (trigram-core research, 2026-10-16): when the start came from a left context (su is None, sv the context word), "keep" uses
    the context word as u for the second word and for a one-word stretch's eos (the shipped rule); "eos" drops it only for a one-word
    stretch's eos; "none" never uses it as u (u unknown -> bigram for that term)."""
    table = getattr(lex, "demote", None) if demote else None
    su, sv = start
    syls = tuple(syls); n = len(syls)
    ctx_start = su is None and sv != "<s>"
    u1 = None if (ctx_start and ctx_u == "none") else sv
    hist = lambda ws: (su, sv) if not ws else ((ws[-2] if len(ws) >= 2 else u1), ws[-1])
    hyps = [[] for _ in range(n + 1)]; hyps[0] = [(0.0, ())]
    for i in range(1, n + 1):
        cand = {}
        for Ln in range(1, min(lex.max_len, i) + 1):
            key = syls[i - Ln:i]
            entries = lex.by_reading.get(key)
            if not entries or not hyps[i - Ln]:
                continue
            reading = "-".join(key) if table else None
            for word, lp in entries[:ime.PER_KEY]:
                d = L.demote_delta(table, reading, word)
                for s, ws in hyps[i - Ln]:
                    u, v = hist(ws)
                    if tri_lm is None:
                        sc = s + (lm.word(lam, v, word, lp) - d)
                        ck = ("".join(ws) + word, word)
                    else:
                        sc = s + (tri_lm.word(lam, u, v, word, lp) - d)
                        ck = ("".join(ws) + word, v, word)   # the next score depends on the last two words
                    if ck not in cand or sc > cand[ck][0]:
                        cand[ck] = (sc, ws + (word,))
        hyps[i] = sorted(cand.values(), key=lambda x: -x[0])[:beam]
    out = []
    for s, ws in hyps[n]:
        u, v = hist(ws)
        if ctx_start and ctx_u == "eos" and len(ws) == 1:
            u = None
        out.append((s + (lm.eos(lam, v) if tri_lm is None else tri_lm.eos(lam, u, v)), ws))
    best, seen = [], set()
    for x in sorted(out, key=lambda x: -x[0]):
        surface = "".join(x[1])
        if surface not in seen:
            seen.add(surface)
            best.append(x)
    return best


class Evaluator:
    """Everything one process needs to decode rows: lexicon, model, optional trigram table."""

    def __init__(self, lm_path, *, tri_path=None, classes=True, kn=None, kn_beta=None, no_kn=False, extra_overlay=None,
                 demote=True, context=True, x_context=False, ctx_u="keep"):
        self.kn_path = LE.kn_side(lm_path, kn, no_kn)
        self.classes = classes
        self.extra_overlay, self.demote, self.context, self.x_context, self.ctx_u = extra_overlay, demote, context, x_context, ctx_u
        base = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.overlays(extra_overlay))
        self.lm = LE.load_lm(lm_path, base, classes, self.kn_path, kn_beta)
        self.lex = L.cap_overlay(base, ime.capping_words(extra_overlay), self.lm)
        self.lm_sha = hashlib.sha256(open(lm_path, "rb").read()).digest()
        self.tri = TriTable.from_path(tri_path, self.lm_sha) if tri_path else None
        self.tri_lm = TriLM(self.lm, self.tri) if self.tri else None
        self.base_kn = self.lm.kn
        if x_context and self.lm.ids.get(X) is None:
            raise ValueError("--x-context needs a model built from the mapped counts: its vocabulary has no <X>")

    def set_beta(self, beta):
        """KN beta override (the grid's second axis); None restores the side file's own value."""
        if self.base_kn is not None:
            new = self.base_kn if beta is None else (beta, self.base_kn[1], self.base_kn[2])
            if new != self.lm.kn:
                self.lm.kn = new
                if self.tri_lm is not None:
                    self.tri_lm.word.cache_clear()   # cached word scores were made with the previous beta

    def row(self, ctx, syls, lam):
        """Candidates of one row: [(score, words)] sorted, distinct surfaces."""
        start = start_state(ctx, self.lm, self.context, self.x_context)
        return decode3(self.lex, syls, self.lm, lam, start, self.tri_lm, demote=self.demote, ctx_u=self.ctx_u)

    def judge(self, truth, syls, ctx, lam):
        """(ok, first surface, [all surfaces], n-best): the numbers lm_eval.py keeps per row."""
        nb = self.row(ctx, syls, lam)
        surf = ["".join(ws) for _, ws in nb]
        return lenient(surf[0]) == lenient(truth), surf[0], surf, nb


def evaluate(ev, rows, lam, dump=None):
    """rows: [(truth, syls, ctx)]. Returns (top1, o64, firsts, rowstats-tuples)."""
    top1, o64, firsts, rs = 0, 0, [], []
    for i, (t, syls, ctx) in enumerate(rows, 1):
        ok, first, surf, nb = ev.judge(t, syls, ctx, lam)
        firsts.append(first)
        top1 += ok
        rs.append((ok, first, t))
        o64 += lenient(t) in {lenient(s) for s in surf}
        if dump:
            for r, (sc, ws) in enumerate(nb, 1):
                dump.write(f"{i}\t{r}\t{''.join(ws)}\t{sc!r}\n")
    return top1, o64, firsts, rs


def header_beta(kn_path, kn_beta=None):
    """beta of a KN side file (the value lm.kn[0] holds after loading): the header's for SJKN0002; SJKN0001 has none, so it is the
    --kn-beta the model was loaded with."""
    import struct
    with open(kn_path, "rb") as f:
        b = f.read(56)
    if b[:8] == b"SJKN0001" and kn_beta is not None:
        return kn_beta
    if b[:8] != b"SJKN0002":
        raise ValueError("not an SJKN0002 side file (SJKN0001 needs --kn-beta)")
    return struct.unpack_from("<d", b, 48)[0]


def summary_line(name, profile, n, top1, o64, sha, *, kn_path=None, context=True, demote=True, classes=True, extra_overlay=None,
                 tri_path=None, lam=None, beta=None, x_context=False, kn_beta=None):
    """The line lm_eval.py prints (profile column built the same way); the tags for what this harness adds go last and are empty in the
    null configuration. lam / beta are the overrides (None = the profile's / the side file's own)."""
    hb = header_beta(kn_path, kn_beta) if kn_path else None
    extra = (f"+tri:{common.sha256_file(tri_path)[:8]}" if tri_path else "") \
        + (f"+lam:{lam:g}" if lam is not None and lam != L.PROFILES[profile] else "") \
        + (f"+beta:{beta:g}" if beta is not None and hb is not None and beta != hb else "") \
        + ("+xctx" if x_context else "")
    return (f"## {name}  lm-{profile}{'+ctx' if context else ''}{'-nodemote' if not demote else ''}{'-noclasses' if not classes else ''}"
            f"{LE.kn_tag(kn_path, hb) if kn_path else ''}{LE.xo_tag(extra_overlay)}{extra}  "
            f"{{'n': {n}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'}}")


def load_rows(rows=None, dev=None, limit=None, name=None):
    if dev:
        return LE.rows_of(sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))))[:dev], name or f"dev{dev}"
    r = LE.rows_of([rows])
    return (r[:limit] if limit else r), name or os.path.basename(rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--lm", default=os.path.join(ROOT, "data", "lm", "bigram.sjlm"))
    ap.add_argument("--profile", choices=sorted(L.PROFILES), required=True)
    ap.add_argument("--rows"); ap.add_argument("--dev", type=int); ap.add_argument("--limit", type=int)
    ap.add_argument("--name"); ap.add_argument("--dump"); ap.add_argument("--rowstats"); ap.add_argument("--context", action="store_true")
    ap.add_argument("--no-demote", action="store_true"); ap.add_argument("--no-classes", action="store_true")
    ap.add_argument("--kn"); ap.add_argument("--kn-beta", type=float); ap.add_argument("--no-kn", action="store_true")
    ap.add_argument("--extra-overlay")
    ap.add_argument("--tri"); ap.add_argument("--lam", type=float); ap.add_argument("--beta", type=float); ap.add_argument("--x-context", action="store_true")
    a = ap.parse_args(argv)
    rows, name = load_rows(a.rows, a.dev, a.limit, a.name)
    if a.kn and a.no_kn:
        ap.error("--kn and --no-kn")
    ev = Evaluator(a.lm, tri_path=a.tri, classes=not a.no_classes, kn=a.kn, kn_beta=a.kn_beta, no_kn=a.no_kn, extra_overlay=a.extra_overlay,
                   demote=not a.no_demote, context=a.context, x_context=a.x_context)
    if not ev.kn_path and a.kn_beta is not None:
        ap.error("--kn-beta needs --kn")
    if a.beta is not None and not ev.kn_path:
        ap.error("--beta needs a KN side file")
    lam = L.PROFILES[a.profile] if a.lam is None else a.lam
    ev.set_beta(a.beta)
    dump = open(a.dump, "w", encoding="utf-8") if a.dump else None
    rsf = open(a.rowstats, "w", encoding="utf-8", newline="") if a.rowstats else None
    top1, o64, firsts, rs = evaluate(ev, rows, lam, dump)
    if rsf:
        with rsf:
            rsf.write(format_rowstats(rs))
    sha = hashlib.sha256("\n".join(firsts).encode("utf-8")).hexdigest()
    print(summary_line(name, a.profile, len(rows), top1, o64, sha, kn_path=ev.kn_path, context=a.context, demote=not a.no_demote,
                       classes=not a.no_classes, extra_overlay=a.extra_overlay, tri_path=a.tri, lam=a.lam, beta=a.beta, x_context=a.x_context, kn_beta=a.kn_beta))


if __name__ == "__main__":
    main()
