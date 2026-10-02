"""Diagnostic: dev truth chars that M2 can never emit (no pure-Han token, or reading not in single-syllable lexicon)."""
import os, sys, re
sys.path.insert(0, os.path.dirname(__file__))
from n0 import *
from transformers import AutoTokenizer
rows = load_dev()
lex = Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"))
rev = defaultdict(set)
for s, cs in char_readings(lex).items():
    for c in cs: rev[c].add(s)
for name, path in (("gemma", GEMMA), ("qwen", QWEN)):
    tok = AutoTokenizer.from_pretrained(path)
    single = set()
    for i in range(len(tok)):
        raw = tok.convert_ids_to_tokens(i)
        if isinstance(raw, str) and "▁" not in raw and "Ġ" not in raw:
            s = tok.decode([i])
            if len(s) == 1 and HAN.match(s): single.add(s)
    nt = nr = 0; ex = set()
    for f, ctx, t, syls in rows:
        bad = [c for c, s in zip(t, syls) if s not in rev.get(c, ())]
        notok = [c for c in t if c not in single]  # chars needing a multi-char token to exist; approximate
        nr += bool(bad); ex |= set(bad)
        nt += bool(notok)
    print(f"{name}: rows with a char whose reading is not in single-syllable lexicon: {nr}/{len(rows)} chars={''.join(sorted(ex))}; rows with a char lacking a single-char token: {nt}/{len(rows)}")
