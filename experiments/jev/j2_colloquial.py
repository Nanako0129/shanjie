"""J2：Jev 判斷「日常打字有多常用到這個詞」，看能不能修正新聞語料造成的詞頻偏差。

詞：同音組清單裡的詞（公開詞彙）＋幾個已知偏差探針。送出的只有詞本身。
評估：(a) 探針組的排序；(b) 事先宣告、不調參的調整 score' = score + W*(jev-2)，看開發集 unigram 變化。
用法：python3 experiments/jev/j2_colloquial.py
"""
import glob
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "reference", "proto"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ime  # noqa: E402
from jev import MODEL, Client  # noqa: E402

GROUPS = os.path.expanduser("~/side-project/ime-research/plan/homophone_groups.tsv")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
LIMIT, BATCH, W = 2000, 50, 0.5   # W 事先宣告，不在開發集上調
PROBES = ["公視", "公式", "公事", "攻勢", "那邊", "納編", "其中", "期中", "常常", "嚐嚐", "權力", "權利",
          "程式", "城市", "部署", "部屬", "意義", "異議", "精力", "經歷", "反映", "反應"]
INSTR = ("How often would an ordinary person in Taiwan type the word `words[{i}]` in everyday writing such as "
         "chat messages, emails, and social media posts? Judge everyday personal typing, not news reports.")
LEVELS = ["Almost never: a rare, technical, archaic, or news-only term that most people never type in daily life.",
          "Rarely: a specialist or formal word, seen in news or documents but seldom typed in personal messages.",
          "Sometimes: a normal word that comes up now and then in everyday messages.",
          "Often: a common everyday word that people type regularly in casual messages.",
          "Very often: one of the most frequent everyday words, typed almost every day."]


def main():
    os.makedirs(OUT, exist_ok=True)
    words = list(dict.fromkeys(PROBES + [w for line in list(open(GROUPS, encoding="utf-8"))[1:]
                                         for w in line.rstrip("\n").split("\t")[2].split("/")]))[:LIMIT]
    client, scores, used = Client(), {}, 0
    for b in range(0, len(words), BATCH):
        chunk = words[b:b + BATCH]
        qs = {f"w{k}": {"type": "score", "instructions": INSTR.format(i=k), "criteria": LEVELS}
              for k in range(len(chunk))}
        answers, usage, _ = client.ask({"words": chunk}, qs)
        used += usage.get("input_tokens", 0)
        for k, w in enumerate(chunk):
            scores[w] = answers[f"w{k}"]["score"]
    with open(os.path.join(OUT, "j2_scores.tsv"), "w", encoding="utf-8") as f:
        f.write("word\tjev_score\n" + "".join(f"{w}\t{s:.3f}\n" for w, s in scores.items()))

    lex = ime.Lexicon(os.path.join(REPO, "data", "lexicon", "mcbpmf-data.txt"))
    lines = [f"## words scored {len(scores)}  input_tokens={used}  est_cost_usd={used * 0.042 / 1e6:.4f}  model={MODEL}"]
    for a in range(0, len(PROBES), 2):
        pair = PROBES[a:a + 2]
        lines.append("   " + "  vs  ".join(f"{w} jev={scores[w]:.2f} lex={lex.by_word[w][1] if w in lex.by_word else '—'}"
                                           for w in pair))
    group = [w for w in ("公視", "公式", "公事", "攻勢")]
    lines.append("   ㄍㄨㄥ ㄕˋ 依 Jev 排序：" + " > ".join(sorted(group, key=lambda w: -scores[w])))

    def run(lexicon):
        out = {}
        for f in sorted(glob.glob(os.path.join(REPO, "eval", "dev", "*.txt"))):
            rows = [l.rstrip("\n").split("|") for l in open(f, encoding="utf-8") if l.count("|") == 2]
            ok = sum("".join(ime.decode(lexicon, r[2].split())[0][1]) == r[1] for r in rows)
            out[os.path.basename(f)[:-4]] = (ok, len(rows))
        return out

    base = run(lex)
    for syls, entries in lex.by_reading.items():   # 事先宣告的調整：只動有 Jev 分數的詞
        lex.by_reading[syls] = sorted(((w, s + W * (scores[w] - 2) if w in scores else s) for w, s in entries),
                                      key=lambda x: -x[1])
    adj = run(lex)
    for name in base:
        lines.append(f"## dev/{name}  unigram {base[name][0]}/{base[name][1]}  →  jev-adjusted(W={W}) {adj[name][0]}/{adj[name][1]}")
    open(os.path.join(OUT, "j2.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
