# ACG pack audit: the root cause of "pack word loses to a lexicon word" (report only, no rule proposed)

Pack: the pack of commit c84e1ac (version 20261008-cf9f1719, 30,048 words, 30,399 rows; later commits changed a few orderings and removed the credit-bracket names, see the research log). Model: data/lm/bigram.sjlm. 
Files here: `dropped-c.tsv` (item ii, the 9 words), `analyze.py` and `acg_audit.rs`. The scripts are kept as a record of how the numbers were produced, not as a rerunnable pipeline: they ran on files in a session scratch directory (paths hard-coded) that is not kept, and `losers-pack.tsv` (item i, 16,082 rows) and the raw decode dumps were not committed. `acg_audit.rs` builds as a cargo example once copied to `cli/examples/`.

## Root cause, in numbers

1. **The decode total of a word that is not in the bigram model's vocabulary is `capped entry lp + K`, exactly.** K = lambda * log10(back(<s>) * p_eos) is a per-profile constant: chat -1.4233, formal -1.9926. Measured on 25,401 (chat) and 22,568 (formal) rows, min and max differ by 1e-6. So an OOV word has no probability of its own: its whole score is its entry score. For a pack word that is the per-length p25 score (2 chars -7.17, 3 chars -7.04, longer values in `analyze.py` output) minus the 1.0 UNSEEN_OVERLAY_PENALTY, i.e. -8.17 for every 2-character name, -8.04 for every 3-character name. 宿儺: -8.17 - 1.42 = -9.59 (chat), as in the dump.
2. **All 30,399 pack rows are OOV for the bigram model** (`lm.word_id` unknown), so every pack word gets the flat score above. The competitor is usually not a lexicon word at all: in **88.7%** of the 16,082 losing rows (26.5% of 60,798 word x profile rows; chat 21.1%, formal 31.8%) o is a composition of several in-vocabulary words, and in 49.4% of all losers just single characters (露娜 -> 路+那, 凱特 -> 凱+特, 宿儺 -> 素+娜; 素娜 is not a lexicon entry). Another 10.7% are a single in-vocabulary lexicon entry (莉莉絲 -> 莉莉斯, 美美 -> 每每, 艾爾 -> 愛爾), 0.6% are both-OOV cases decided by the entry score (亞莉莎 vs 亞利砂), 92 rows are ties with another pack word.
3. **It is the LM term, not the entry term.** In 99.9% of losers w's LM term is below o's (median deficit -1.97); w's entry term alone beats o's in 62.7% of losers (chat 50.1%, formal 71.0%). Median total gap -1.58 (p10 -3.28, p90 -0.26).
4. **Length decides.** Loss rate by word length: 2 chars 98.7% (7,564 of 7,662 rows; chat 98.2%, formal 99.2%), 3 chars 38.9% (chat 24.6%, formal 53.1%), 4 chars 7.9%, 5+ chars 2.6%. A flat -8.17 cannot beat the product of two common characters' probabilities. Almost every 2-character pack name is dead in both profiles.
5. **Sensitivity (not a proposal):** the share of losers that would win with +1.0 on w's score (= no unseen penalty) is 34.0%, with +2.0 60.9%, with +3.0 85.1%.

## Hypothesis test

"Pack words lose mainly because they are OOV in the LM and get only the fixed p25 entry score, while the competing lexicon word has a real unigram probability."

- Supported for the first half: 100% of pack words are OOV and their score is exactly the flat entry score + K; 99.9% of the losses are LM-term losses. Fraction of losers with w OOV and o made only of in-vocabulary words: 99.4% (M1 + M2). In the 14,262 M1 rows the winner is a composition whose characters have corpus counts mostly in the thousands (o min count >= 1000 in 11,465 of all 16,082 losers; 0 in only 104).
- Corrected for the second half: "a real unigram probability" is too narrow. The winner is usually not a word with a unigram probability of its own but a chain of common characters scored through the bigram/class model, and o is rarely obscure (only 245 of 16,082 losers have o with min count < 10; the obscure-o list below is the exception, not the rule).
- OOV status alone does not separate winners from losers in the lexicon: in the pack-OFF sample, base words that are OOV lose 25.1% vs 20.2% in vocabulary; overlay-wikt words 54.8% vs 51.3%; overlay-zhwiki 38.0% vs 24.5%. What predicts the loss is length (above) and whether the entry score is a flat p25 (overlay, pack) or a real score (base).

## Same mechanism in the main lexicon (pack OFF, random samples, 2-10 Han chars)

| Sample | rows (word x profile) | lose | 2 chars | 3 chars | 4 chars |
|---|---|---|---|---|---|
| overlay-wikt (15,000 of 270,801 entries) | 30,000 | 52.8% | 78.1% | 27.0% | 7.5% |
| overlay-zhwiki (15,000 of 70,324) | 30,000 | 28.5% | (none in sample) | 29.2% | 20.3% |
| base, not in overlay (15,000 of 145,449) | 30,000 | 20.8% | 34.5% | 8.9% | 3.2% |

So the effect is lexicon-wide: overlay-add.tsv words get the same flat p25 entry score (capped further by corpus frequency) and 2-character wikt entries lose 78% of the time, against 34.5% for base words, whose scores are real. Issue #74's pair, measured here: 鈴谷 (overlay, count 45, in vocab, capped lp -7.17) wins in chat and loses to 鈴鼓 in formal; 零股 (count 5, capped lp -7.47) loses to 鈴谷 in chat and to 鈴鼓 in formal. A loss rate for overlay words is not by itself a defect (many wikt entries are junk that should lose); this audit measures only the mechanism, not which of those losses are wrong.

## (ii) the 9 words dropped by the (c) rule

See `dropped-c.tsv` and the list in `summary-raw.md`. 5 of 9 are exact ties with an OOV lexicon (overlay) word (gap 0.000: 涅薩, 小馬鎮, 朱涅, 座布團, 歐兜賣; the lexicon word is OOV too, so ordering decides); the other 4 beat a composition of common characters by a margin of 0.28-1.64 (拉斐亞, 卡札克, 亞美莉亞) or beat the in-vocabulary overlay word 伊普西隆 (count 5) by 0.42. They differ from the losers above because they are 3-4 characters long, and the longer the name, the smaller the character-chain advantage. Their source pages are not recorded (dropped words never reach `acg-sources.tsv`).

## Caveats

- Items (i) and the tables count rows (word x profile). "Loses" = the top-1 is not exactly the word w (any other string, composition or entry).
- The w score of a loser is `lp + K` (checked exactly on every in-beam OOV row; a beam of 200 holds only 20% of the losers' own candidates, which is why the numbers do not rely on the beam).
- o "single:pack" rows (92) are ties between two pack words that stay in the pack by decision, not lexicon words.
- Entry term = (1 - lambda) * sum of capped entry lp; LM term = total - entry term; lambda 0.5 chat, 0.7 formal. Corpus frequency = `lm.count` (unigram count of the model's training corpus).

## Detail (generated)

### (i) all pack rows (30,399 rows x 2 profiles)

- rows (word x profile): 60798; w decodes to itself: 44716; loses: 16082 / 60798 = 26.5%
- w OOV in the bigram model: 60798 rows, lose 16082 / 60798 = 26.5%
- loss rate by length: 2 chars: 7564 / 7662 = 98.7%, 3 chars: 6301 / 16208 = 38.9%, 4 chars: 1882 / 23896 = 7.9%, 5+ chars: 335 / 13032 = 2.6%
- losers by mechanism:
  - M1 w OOV, o = several in-vocab words: 14262 / 16082 = 88.7%; e.g. 露娜→路+那, 露娜→露+娜, 凱特→凱+特, 凱特→凱+特
  - M2 w OOV, o a single in-vocab entry: 1716 / 16082 = 10.7%; e.g. 莉莉絲→莉莉斯, 莉莉絲→莉莉斯, 美美→每每, 美美→每每
  - M3 both OOV, entry score decides: 102 / 16082 = 0.6%; e.g. 亞莉莎→亞利砂, 史黛菈→史黛拉, 奧爾加→奧爾迦, 一之瀬→一之瀨
  - M6 w OOV, o = several words, some OOV: 2 / 16082 = 0.0%; e.g. 禁止項目→禁止+項目, 禁止項目→禁止+項目
- losers whose w score is inside the beam (200): 16082
  - entry term of w beats o's: 10078 / 16082 = 62.7% (so w loses only through the LM term)
  - LM term of w below o's: 16063 / 16082 = 99.9%; both terms against w: 5990 / 16082 = 37.2%
  - score gap (w total - top total): median -1.58, p10 -3.28, p90 -0.26
  - median LM-term deficit -1.97, median entry-term difference 0.23
  - frequency band of o (min unigram count of its words): 0: 104, 1-9: 141, 10-99: 1050, 100-999: 3322, >=1000: 11465
  - o origin: composed: 14264, single:base: 887, single:overlay:wikt: 760, single:pack: 92, single:overlay:zhwiki: 75, single:sandhi: 4

### (i) chat only

- rows (word x profile): 30399; w decodes to itself: 23981; loses: 6418 / 30399 = 21.1%
- w OOV in the bigram model: 30399 rows, lose 6418 / 30399 = 21.1%
- loss rate by length: 2 chars: 3762 / 3831 = 98.2%, 3 chars: 1997 / 8104 = 24.6%, 4 chars: 588 / 11948 = 4.9%, 5+ chars: 71 / 6516 = 1.1%
- losers by mechanism:
  - M1 w OOV, o = several in-vocab words: 5470 / 6418 = 85.2%; e.g. 露娜→路+那, 凱特→凱+特, 卡蓮→卡+蓮, 妮娜→尼+那
  - M2 w OOV, o a single in-vocab entry: 877 / 6418 = 13.7%; e.g. 莉莉絲→莉莉斯, 美美→每每, 艾爾→愛爾, 克莉絲→克利斯
  - M3 both OOV, entry score decides: 70 / 6418 = 1.1%; e.g. 亞莉莎→亞利砂, 史黛菈→史黛拉, 奧爾加→奧爾迦, 一之瀬→一之瀨
  - M6 w OOV, o = several words, some OOV: 1 / 6418 = 0.0%; e.g. 禁止項目→禁止+項目
- losers whose w score is inside the beam (200): 6418
  - entry term of w beats o's: 3214 / 6418 = 50.1% (so w loses only through the LM term)
  - LM term of w below o's: 6406 / 6418 = 99.8%; both terms against w: 3195 / 6418 = 49.8%
  - score gap (w total - top total): median -1.59, p10 -2.97, p90 -0.23
  - median LM-term deficit -1.74, median entry-term difference 0.00
  - frequency band of o (min unigram count of its words): 0: 71, 1-9: 67, 10-99: 414, 100-999: 1138, >=1000: 4728
  - o origin: composed: 5471, single:base: 452, single:overlay:wikt: 392, single:pack: 63, single:overlay:zhwiki: 38, single:sandhi: 2

### (i) formal only

- rows (word x profile): 30399; w decodes to itself: 20735; loses: 9664 / 30399 = 31.8%
- w OOV in the bigram model: 30399 rows, lose 9664 / 30399 = 31.8%
- loss rate by length: 2 chars: 3802 / 3831 = 99.2%, 3 chars: 4304 / 8104 = 53.1%, 4 chars: 1294 / 11948 = 10.8%, 5+ chars: 264 / 6516 = 4.1%
- losers by mechanism:
  - M1 w OOV, o = several in-vocab words: 8792 / 9664 = 91.0%; e.g. 露娜→露+娜, 凱特→凱+特, 卡蓮→卡+蓮, 妮娜→尼+娜
  - M2 w OOV, o a single in-vocab entry: 839 / 9664 = 8.7%; e.g. 莉莉絲→莉莉斯, 美美→每每, 克莉絲→克利斯, 莉茲→利茲
  - M3 both OOV, entry score decides: 32 / 9664 = 0.3%; e.g. 中原忠也→中原中也, 亞斯塔錄→亞斯塔路, 今井綠→今井律, 克勞帝亞→克勞蒂亞
  - M6 w OOV, o = several words, some OOV: 1 / 9664 = 0.0%; e.g. 禁止項目→禁止+項目
- losers whose w score is inside the beam (200): 9664
  - entry term of w beats o's: 6864 / 9664 = 71.0% (so w loses only through the LM term)
  - LM term of w below o's: 9657 / 9664 = 99.9%; both terms against w: 2795 / 9664 = 28.9%
  - score gap (w total - top total): median -1.57, p10 -3.43, p90 -0.29
  - median LM-term deficit -2.19, median entry-term difference 0.38
  - frequency band of o (min unigram count of its words): 0: 33, 1-9: 74, 10-99: 636, 100-999: 2184, >=1000: 6737
  - o origin: composed: 8793, single:base: 435, single:overlay:wikt: 368, single:overlay:zhwiki: 37, single:pack: 29, single:sandhi: 2

- distinct (reading, word) that lose in at least one profile: 9664 of 30399; in both: 6418

- pack loss rate by word length: 2 chars: 7564 / 7662 = 98.7%, 3 chars: 6301 / 16208 = 38.9%, 4 chars: 1882 / 23896 = 7.9%, 5+ chars: 335 / 13032 = 2.6%

- all 16082 pack losers with an estimated/actual w total (w total = capped lp + K, K chat -1.423, formal -1.993; checked on in-beam rows):
  - w would win with a score removing the UNSEEN_OVERLAY_PENALTY (+1.0): 5469 / 16082 = 34.0%
  - w would win with a score +2.0: 9797 / 16082 = 60.9%
  - w would win with a score +3.0: 13679 / 16082 = 85.1%
  - gap percentiles p10/p25/p50/p75/p90: -3.28 / -2.56 / -1.58 / -0.70 / -0.26
  - of those, o is a composition of several words: 14264 / 16082 = 88.7%; o consists only of single characters: 7952 / 16082 = 49.4%

### lexicon check, pack OFF: random sample of 15000 overlay-wikt entries (2-10 Han chars)

- rows (word x profile): 30000; w decodes to itself: 14146; loses: 15854 / 30000 = 52.8%
- w OOV in the bigram model: 13280 rows, lose 7273 / 13280 = 54.8%
- w in the bigram vocabulary: 16720 rows, lose 8581 / 16720 = 51.3%
- loss rate by length: 2 chars: 13171 / 16856 = 78.1%, 3 chars: 2352 / 8708 = 27.0%, 4 chars: 331 / 4436 = 7.5%
- losers by mechanism:
  - M1 w OOV, o = several in-vocab words: 5405 / 15854 = 34.1%; e.g. 材罩→才+照, 材罩→才+照, 螖蠌→華+則, 螖蠌→華+澤
  - M2 w OOV, o a single in-vocab entry: 1633 / 15854 = 10.3%; e.g. 碼兒→馬兒, 碼兒→馬兒, 砥厲→砥礪, 砥厲→砥礪
  - M3 both OOV, entry score decides: 223 / 15854 = 1.4%; e.g. 稀稀罕兒→希希罕兒, 稀稀罕兒→希希罕兒, 手摺簿仔→手折簿仔, 手摺簿仔→手折簿仔
  - M4 both in vocab (context / unigram): 2584 / 15854 = 16.3%; e.g. 注力→助力, 注力→助力, 茶箱→茶香, 茶箱→茶香
  - M5 other (w in vocab, o OOV): 61 / 15854 = 0.4%; e.g. 戮辱→僇辱, 高大石調→高大食調, 高大石調→高大食調, 聯立政權→連立政権
  - M6 w OOV, o = several words, some OOV: 12 / 15854 = 0.1%; e.g. 可擴展→可+擴展, 可擴展→可+擴展, 通信機→通信+機, 通信機→通信+機
  - M7 w in vocab, o = several words: 5936 / 15854 = 37.4%; e.g. 級聯→及+聯, 忠款→中+款, 忠款→中+款, 抵帳→底+障
- losers whose w score is inside the beam (200): 10849
  - entry term of w beats o's: 3274 / 10849 = 30.2% (so w loses only through the LM term)
  - LM term of w below o's: 10408 / 10849 = 95.9%; both terms against w: 7134 / 10849 = 65.8%
  - score gap (w total - top total): median -1.49, p10 -3.48, p90 -0.22
  - median LM-term deficit -1.37, median entry-term difference -0.21
  - frequency band of o (min unigram count of its words): 0: 296, 1-9: 383, 10-99: 1596, 100-999: 2675, >=1000: 10904
  - o origin: composed: 11353, single:base: 2970, single:overlay:wikt: 1501, single:overlay:zhwiki: 24, single:sandhi: 6

### lexicon check, pack OFF: random sample of 15000 overlay-zhwiki entries (2-10 Han chars)

- rows (word x profile): 30000; w decodes to itself: 21442; loses: 8558 / 30000 = 28.5%
- w OOV in the bigram model: 8920 rows, lose 3392 / 8920 = 38.0%
- w in the bigram vocabulary: 21080 rows, lose 5166 / 21080 = 24.5%
- loss rate by length: 3 chars: 8074 / 27614 = 29.2%, 4 chars: 484 / 2386 = 20.3%
- losers by mechanism:
  - M1 w OOV, o = several in-vocab words: 3159 / 8558 = 36.9%; e.g. 出血位→出血+為, 出血位→出血+為, 余微分→於+微分, 余微分→於+微分
  - M2 w OOV, o a single in-vocab entry: 181 / 8558 = 2.1%; e.g. 金西湖→金溪湖, 金西湖→金溪湖, 王政忠→王正中, 王政忠→王正中
  - M3 both OOV, entry score decides: 39 / 8558 = 0.5%; e.g. 高寒蚓→高寒隱, 高寒蚓→高寒隱, 李子卿→李子青, 李子卿→李子青
  - M4 both in vocab (context / unigram): 408 / 8558 = 4.8%; e.g. 波蘭杯→波蘭盃, 波蘭杯→波蘭盃, 栗太子→戾太子, 栗太子→戾太子
  - M5 other (w in vocab, o OOV): 19 / 8558 = 0.2%; e.g. 瓜子松→栝子松, 瓜子松→栝子松, 陳拾遺→沈時宜, 龜頭球→亀頭球
  - M6 w OOV, o = several words, some OOV: 13 / 8558 = 0.2%; e.g. 循環制→循環+至, 循環制→循環+至, 自信息→自+信息, 自信息→自+信息
  - M7 w in vocab, o = several words: 4739 / 8558 = 55.4%; e.g. 王明星→王+明星, 王明星→王+明星, 金魚三→金魚+三, 金魚三→金+於+三
- losers whose w score is inside the beam (200): 5460
  - entry term of w beats o's: 4104 / 5460 = 75.2% (so w loses only through the LM term)
  - LM term of w below o's: 5367 / 5460 = 98.3%; both terms against w: 1267 / 5460 = 23.2%
  - score gap (w total - top total): median -0.56, p10 -1.92, p90 -0.09
  - median LM-term deficit -0.96, median entry-term difference 0.28
  - frequency band of o (min unigram count of its words): 0: 73, 1-9: 219, 10-99: 534, 100-999: 2498, >=1000: 5234
  - o origin: composed: 7911, single:overlay:zhwiki: 397, single:overlay:wikt: 136, single:base: 114

### lexicon check, pack OFF: random sample of 15000 base entries (2-10 Han chars)

- rows (word x profile): 30000; w decodes to itself: 23751; loses: 6249 / 30000 = 20.8%
- w OOV in the bigram model: 3940 rows, lose 989 / 3940 = 25.1%
- w in the bigram vocabulary: 26060 rows, lose 5260 / 26060 = 20.2%
- loss rate by length: 2 chars: 5393 / 15654 = 34.5%, 3 chars: 637 / 7144 = 8.9%, 4 chars: 207 / 6450 = 3.2%, 5+ chars: 12 / 752 = 1.6%
- losers by mechanism:
  - M1 w OOV, o = several in-vocab words: 623 / 6249 = 10.0%; e.g. 錦箋→僅+間, 錦箋→僅+間, 智慧錶→智慧+表, 智慧錶→智慧+表
  - M2 w OOV, o a single in-vocab entry: 317 / 6249 = 5.1%; e.g. 九缸→酒缸, 九缸→酒缸, 彫刻刀→雕刻刀, 彫刻刀→雕刻刀
  - M3 both OOV, entry score decides: 47 / 6249 = 0.8%; e.g. 臥下去→握下去, 臥下去→握下去, 圜鑿方枘→圓鑿方枘, 圜鑿方枘→圓鑿方枘
  - M4 both in vocab (context / unigram): 2460 / 6249 = 39.4%; e.g. 四氯→四路, 四氯→四路, 調校→調教, 調校→調教
  - M5 other (w in vocab, o OOV): 36 / 6249 = 0.6%; e.g. 益無忌憚→一無忌憚, 益無忌憚→一無忌憚, 不值一文→不直一文, 不值一文→不直一文
  - M6 w OOV, o = several words, some OOV: 2 / 6249 = 0.0%; e.g. 新社區→新+社區, 新社區→新+社區
  - M7 w in vocab, o = several words: 2764 / 6249 = 44.2%; e.g. 無日→吳+日, 無日→吳+日, 探探→碳+碳, 什麼病→什麼+病
- losers whose w score is inside the beam (200): 4288
  - entry term of w beats o's: 1483 / 4288 = 34.6% (so w loses only through the LM term)
  - LM term of w below o's: 4026 / 4288 = 93.9%; both terms against w: 2543 / 4288 = 59.3%
  - score gap (w total - top total): median -0.83, p10 -2.40, p90 -0.12
  - median LM-term deficit -0.79, median entry-term difference -0.09
  - frequency band of o (min unigram count of its words): 0: 85, 1-9: 184, 10-99: 802, 100-999: 1229, >=1000: 3949
  - o origin: composed: 3389, single:base: 2440, single:overlay:wikt: 403, single:overlay:zhwiki: 11, single:sandhi: 6

### (ii) the 9 words dropped by the (c) rule (w put into the pack for the measurement; see dropped-c.tsv)

- chat ㄋㄧㄝˋ ㄙㄚˋ: 涅薩 (count 0, in vocab 0) would win over 躡馺 (origin overlay:wikt, counts 0, in vocab 0); gap 0.000
- formal ㄌㄚ ㄈㄟˇ ㄧㄚˇ: 拉斐亞 (count 0, in vocab 0) would win over 拉+菲+亞 (origin base|base|base, counts 124589|21090|95471, in vocab 1|1|1); gap 0.277
- chat ㄎㄚˇ ㄓㄚˊ ㄎㄜˋ: 卡札克 (count 0, in vocab 0) would win over 卡+扎+克 (origin base|base|base, counts 85230|9634|123402, in vocab 1|1|1); gap 1.635
- formal ㄒㄧㄠˇ ㄇㄚˇ ㄓㄣˋ: 小馬鎮 (count 0, in vocab 0) would win over 小馬圳 (origin overlay:zhwiki, counts 0, in vocab 0); gap 0.000
- chat ㄓㄨ ㄋㄧㄝˋ: 朱涅 (count 0, in vocab 0) would win over 株櫱 (origin overlay:wikt, counts 0, in vocab 0); gap 0.000
- formal ㄗㄨㄛˋ ㄅㄨˋ ㄊㄨㄢˊ: 座布團 (count 0, in vocab 0) would win over 座布団 (origin overlay:wikt, counts 0, in vocab 0); gap 0.000
- formal ㄡ ㄉㄡ ㄇㄞˋ: 歐兜賣 (count 0, in vocab 0) would win over 歐兜邁 (origin overlay:wikt, counts 0, in vocab 0); gap 0.000
- formal ㄧ ㄆㄨˇ ㄒㄧ ㄌㄨㄥˊ: 伊普西龍 (count 0, in vocab 0) would win over 伊普西隆 (origin overlay:wikt, counts 5, in vocab 1); gap 0.420
- formal ㄧㄚˇ ㄇㄟˇ ㄌㄧˋ ㄧㄚˇ: 亞美莉亞 (count 0, in vocab 0) would win over 亞美+利+亞 (origin base|base|base, counts 655|90288|95471, in vocab 1|1|1); gap 0.376

### 20 notable rows: obscure o (a single lexicon entry seen at most 10 times in the corpus) blocking the best-sourced pack words

| w | pages | profile | o | o origin | o counts | gap | w entry lp |
|---|---|---|---|---|---|---|---|
| 烏瑟 | 3 | chat | 烏色 | overlay:wikt | 5 | -1.94 | -8.17 |
| 瑪莉卡 | 3 | chat | 馬利卡 | overlay:zhwiki | 5 | -2.02 | -8.04 |
| 藤卷 | 3 | chat | 藤巻 | overlay:wikt | 3 | -1.77 | -8.17 |
| 龜吉 | 3 | chat | 歸集 | overlay:wikt | 7 | -2.03 | -8.17 |
| 烏瑟 | 3 | formal | 烏色 | overlay:wikt | 5 | -2.44 | -8.17 |
| 瑪莉卡 | 3 | formal | 馬利卡 | overlay:zhwiki | 5 | -2.60 | -8.04 |
| 龜吉 | 3 | formal | 歸集 | overlay:wikt | 7 | -2.50 | -8.17 |
| 亞莉莎 | 2 | chat | 亞利砂 | overlay:wikt | 0 | 0.00 | -8.04 |
| 史黛菈 | 2 | chat | 史黛拉 | pack | 0 | -0.00 | -8.04 |
| 奧爾加 | 2 | chat | 奧爾迦 | pack | 0 | 0.00 | -8.04 |
| 數位版 | 2 | chat | 數位板 | overlay:wikt | 9 | -2.23 | -8.04 |
| 熊仔 | 2 | chat | 雄仔 | overlay:wikt | 9 | -2.35 | -8.17 |
| 琪拉拉 | 2 | chat | 奇拉拉 | overlay:zhwiki | 10 | -2.37 | -8.04 |
| 瑪姆 | 2 | chat | 馬母 | overlay:wikt | 5 | -2.10 | -8.17 |
| 畢古 | 2 | chat | 髀骨 | base | 3 | -2.10 | -8.17 |
| 維羅妮卡 | 2 | chat | 韋羅妮卡 | overlay:wikt | 8 | -1.78 | -7.61 |
| 菲洛 | 2 | chat | 飛落 | overlay:wikt | 6 | -2.03 | -8.17 |
| 蟲巢 | 2 | chat | 崇朝 | overlay:wikt | 5 | -2.03 | -8.17 |
| 阿卡漢 | 2 | chat | 阿卡汗 | overlay:wikt | 8 | -2.07 | -8.04 |
| 澤渡 | 2 | formal | 沢渡 | overlay:wikt | 8 | -2.90 | -8.17 |

### 10 best-sourced pack words that lose in chat (o is usually two common characters)

| w | pages | profile | o | o origin | o counts | gap | w entry lp |
|---|---|---|---|---|---|---|---|
| 露娜 | 15 | chat | 路+那 | base|base | 82606/46098 | -2.20 | -8.17 |
| 凱特 | 10 | chat | 凱+特 | base|base | 22359/156416 | -2.93 | -8.17 |
| 卡蓮 | 9 | chat | 卡+蓮 | base|base | 85230/8080 | -2.20 | -8.17 |
| 妮娜 | 9 | chat | 尼+那 | base|base | 78522/46098 | -1.90 | -8.17 |
| 莉莉絲 | 8 | chat | 莉莉斯 | overlay:zhwiki | 83 | -2.73 | -8.04 |
| 米娜 | 7 | chat | 米+那 | base|base | 56639/46098 | -2.96 | -8.17 |
| 羅德 | 7 | chat | 羅+德 | base|base | 102651/153264 | -3.04 | -8.17 |
| 美美 | 7 | chat | 每每 | base | 187 | -3.84 | -8.17 |
| 艾爾 | 7 | chat | 愛爾 | overlay:wikt | 288 | -2.84 | -8.17 |
| 萊拉 | 7 | chat | 來+啦 | base|base | 123787/56666 | -3.10 | -8.17 |

