"""Error-path checks (contract §8.2 e, f), public dev302 only. Run under venv A: python selftest.py
(e) a prefix-only scorer must trip the degeneracy stop (exit 3); (f) a wrong dev302 hash must exit non-zero
with the fixed string. Both go through the real run_ll.main(); only the scorer / the expected hash is replaced.
"""
import subprocess
import sys

E = """
import sys, s5k, run_ll
def prefix_only(model, prefix_ids, cand_id_lists):
    import mlx.core as mx
    from mlx_lm.models.cache import make_prompt_cache
    lg = model(mx.array([prefix_ids]))[0].astype(mx.float32)
    lp = lg - mx.logsumexp(lg, axis=-1, keepdims=True)
    v = float(sum(lp[i, prefix_ids[i + 1]] for i in range(len(prefix_ids) - 1)))
    return [v] * len(cand_id_lists)  # log P(prefix) only: the candidate never contributes
run_ll.cand_scores = prefix_only
sys.argv = ['run_ll.py', '--model', 'Q-ll', '--set', 'dev302', '--limit', '19', '--ctx', 'none']
run_ll.main()
"""
F = """
import sys, s5k, run_ll
k = ('dev302', 'rows.jsonl')
s5k.HASHES[k] = (s5k.HASHES[k][0], '0' * 64)
sys.argv = ['run_ll.py', '--model', 'Q-ll', '--set', 'dev302', '--limit', '20', '--ctx', 'none']
run_ll.main()
"""
for tag, code, want in (("e prefix-only", E, 3), ("f wrong hash", F, 1)):
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    print(f"{tag}: rc={p.returncode} expected={want} stdout={p.stdout.strip()!r} stderr={p.stderr.strip()!r}")
    if p.returncode != want:
        sys.exit("selftest FAILED")
print("selftest ok")
