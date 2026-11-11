# 逐項拿掉表（v0.7.0）

善解 v0.7.0 標準檔逐項拿掉一個元件的結果，以及兩個閘門列的離線重算。這張表是描述性的：三個集合在選值時都看過，不是確認。

## 量測條件

- 程式：tag `v0.7.0`（`56979b2e2f6c7fd7a7288c38ed772d26f297e0e4`）。跨版本基準量的是 `f3bd77e7`，它和 tag 在 `docs/spec/evaluation.md` §7.10 的綁定路徑上沒有差異。模型照 `data/*.sha256` 全部核對過。
- 一台 Mac 跑一次，評測 CLI `--threads 10`，標準檔（不給 `--rerank-level` 就是標準檔）。
- 集合：

  | 集合 | 設定 | 列數 | SHA-256 |
  |---|---|---|---|
  | cvtune-clean-tw0 | 聊天 | 2,992 | `91e7097d36b3812d58c8672501153d51116bef8f57a48c9350876523a7fbbe04` |
  | discordtune | 聊天 | 4,919 | 私人聊天開發集（過濾後），不公開，只報數字 |
  | wikitune-clean-tw0 | 書面 | 2,182 | `ccddaa4f0ace8f17d69290a2b0f06dcd0fbcfeb54d6bd21242385dde5bf2cbe2` |

  兩個公開集合不在 repo 裡，建法見 `eval/README.md`「調參集 TW0」。

- 出貨旗標：`--lm data/lm/bigram.sjlm --context --packs acg,it,beauty`。完整系統另加 `--tinylm data/lm/tinylm.sjtl`，聊天再加 `--colloquial-core data/lm/colloquial.sjlm`。完整系統的設定標記：
  - 聊天：`lm-chat+ctx+kn:880ee87c:θ1:β1+tl:356a37be+acg+it+beauty+tri:24ee8645+colloq:81c2470d+rd+rx:a821a206+rm`
  - 書面：`lm-formal+ctx+kn:880ee87c:θ1:β0.75+tl:356a37be+acg+it+beauty+tri:34305a2d+rd+rx:a821a206+rm`

## 先確認的兩件事

1. **拿掉口語項時**，聊天照樣用聊天三元表 `24ee8645`；重排改用 `.sjtl` 檔頭的聊天標準組 (0.3, 2, 0.25)，不是口語融合那一格 `colloquial::STANDARD` (1.1, 2, 0)（`docs/spec/semantic-rerank.md` §4.4）。所以「拿掉口語項」那一列是口語項加上它那一格的合併效果。
2. **`--no-classes`、`--no-kn` 不能和 `--tinylm` 一起用**：CLI 直接拒絕，因為重排模型是在詞類、降權表與 KN 側檔都開著時調的（`cli/src/main.rs` 的 `load_tinylm`）。所以「拿掉詞類」「拿掉 KN 接續值」兩列是在只有 n-gram 的系統（不給 `--tinylm`、`--colloquial-core`）上拿掉，基準是「拿掉重排模型」那一列。

## 結果

對的列數；括號裡是對基準的改對／改壞、淨值與精確 McNemar 雙尾 p（`tools/evalstats.py compare --net`）。

| 列 | 基準 | cvtune-clean-tw0（聊天） | discordtune（聊天） | wikitune-clean-tw0（書面） |
|---|---|---|---|---|
| 完整系統 | — | 2,771 | 4,633 | 1,677 |
| 拿掉重排模型（含口語項） | 完整 | 2,635（+21／−157，淨 −136，p<0.0001） | 4,516（+39／−156，淨 −117，p<0.0001） | 1,553（+43／−167，淨 −124，p<0.0001） |
| 拿掉口語項 | 完整 | 2,697（+19／−93，淨 −74，p<0.0001） | 4,582（+34／−85，淨 −51，p<0.0001） | 不適用（書面沒有口語項） |
| 拿掉分差閘門（離線） | 完整 | 2,771（+0／−0，淨 0，p=1） | 4,632（+0／−1，淨 −1，p=1） | 1,675（+0／−2，淨 −2，p=0.5） |
| 拿掉軟閘門（離線，口語項的 σ 固定為 1） | 完整 | 2,775（+11／−7，淨 +4，p=0.48） | 4,628（+5／−10，淨 −5，p=0.30） | 不適用（書面沒有口語項） |
| 拿掉三元表 | 完整 | 2,768（+3／−6，淨 −3，p=0.51） | 4,628（+3／−8，淨 −5，p=0.23） | 1,617（+14／−74，淨 −60，p<0.0001） |
| 拿掉讀音項 | 完整 | 2,763（+0／−8，淨 −8，p=0.0078） | 4,616（+0／−17，淨 −17，p<0.0001） | 1,655（+0／−22，淨 −22，p<0.0001） |
| 只有 n-gram 再拿掉詞類 | 拿掉重排 | 2,632（+4／−7，淨 −3，p=0.55） | 4,513（+7／−10，淨 −3，p=0.63） | 1,544（+4／−13，淨 −9，p=0.049） |
| 只有 n-gram 再拿掉 KN 接續值 | 拿掉重排 | 2,606（+15／−44，淨 −29，p=0.0002） | 4,499（+21／−38，淨 −17，p=0.036） | 1,510（+17／−60，淨 −43，p<0.0001） |
| unigram 基準（只剩詞庫，不帶詞包） | — | 2,186 | 3,884 | 953 |

- unigram 那一列走 CLI 不給 `--lm` 的路徑：沒有逐列結果，只有對的列數；這條路徑不收詞包。
- 兩個閘門列是離線重算的（下一節），三個集合都算。discordtune 是私人集合，只報彙總數字，分數檔與逐列結果不公開。融合格是在軟閘門開著時選的，「拿掉軟閘門」那一列也只是描述。
- 標準檔的格本來就是 δ＝0，所以分差閘門列只量到 τ。完整系統裡 τ 擋下重排的列：cvtune-clean-tw0 802／2,992、discordtune 2,012／4,919、wikitune-clean-tw0 509／2,182；拿掉 τ 後選字不同的只有 0 列、1 列與 2 列。

## 分差閘門列與軟閘門列（離線）

`gate_offline.py` 從完整系統那一跑的 `--tinylm-scores` 照 v0.7.0 核心的規則重算每一列的選擇：書面是 `tinylm::pick_masked`（檔頭書面標準組 (0.8, 2, 0)），聊天是 `colloquial::guarded_pick`（先 `fuse`，μ 0.5、T 1.0，再用 (1.1, 2, 0) 做 `pick_masked`；口語分數不是有限值時退回檔頭的 (0.3, 2, 0.25)）。評測 CLI 沒有學習項，重算也沒有。

1. 先核對：重算的選擇必須逐列等於分數檔的 `c`，推出的 rowstats 必須和同一跑的 `--rowstats` 逐位元組相同，否則以非 0 結束。三個集合都逐列相同，都沒有退回的列；被遮罩的候選只在 discordtune 有 1 列，兩個公開集合沒有。
2. 再改一個條件重算：
   - `--off gate`（預設）：τ＝∞、δ＝0，α、口語融合、遮罩照舊。結果是上表的「拿掉分差閘門」那一列。
   - `--off soft`（只有聊天）：口語項的軟閘門 σ((ll_i−ll_0)/T) 固定為 1，其他照舊。結果是上表的「拿掉軟閘門」那一列。

兩個公開集合用 `--out DIR` 寫出的兩份 rowstats，再用 `tools/evalstats.py compare --net` 比，數字和腳本印的相同；discordtune 只跑了腳本本身。

## 怎麼重跑

在 tag `v0.7.0` 的 checkout 根目錄，先編評測 CLI、核對模型：

```
cargo build --release --locked -p cli --bin shanjie-eval
shasum -a 256 -c data/*.sha256
```

`<集合>` 是集合檔，`<輸出>` 是 rowstats 檔（只有數字）。每一格一行；聊天用 `--profile chat`，書面用 `--profile formal`。

```
E=target/release/shanjie-eval
LM="--lm data/lm/bigram.sjlm --context --packs acg,it,beauty"

# 完整系統（聊天；書面拿掉 --colloquial-core）。另加 --tinylm-scores <分數檔>，給兩個閘門列用
$E $LM --profile chat --tinylm data/lm/tinylm.sjtl --colloquial-core data/lm/colloquial.sjlm --threads 10 --rows <集合> --rowstats <輸出>
# 拿掉重排模型（含口語項）＝只有 n-gram
$E $LM --profile chat --threads 10 --rows <集合> --rowstats <輸出>
# 拿掉口語項（只有聊天）
$E $LM --profile chat --tinylm data/lm/tinylm.sjtl --threads 10 --rows <集合> --rowstats <輸出>
# 拿掉三元表、拿掉讀音項：完整系統加 --no-tri 或 --no-reading-decode
$E $LM --profile chat --tinylm data/lm/tinylm.sjtl --colloquial-core data/lm/colloquial.sjlm --no-tri --threads 10 --rows <集合> --rowstats <輸出>
$E $LM --profile chat --tinylm data/lm/tinylm.sjtl --colloquial-core data/lm/colloquial.sjlm --no-reading-decode --threads 10 --rows <集合> --rowstats <輸出>
# 只有 n-gram 再拿掉詞類、再拿掉 KN 接續值
$E $LM --profile chat --no-classes --threads 10 --rows <集合> --rowstats <輸出>
$E $LM --profile chat --no-kn --threads 10 --rows <集合> --rowstats <輸出>
# unigram 基準（只有摘要行）
$E --rows <集合>
```

改對、改壞對基準那一格算：

```
python3 tools/evalstats.py compare <基準的輸出> <這一格的輸出> --label <名稱> --net
```

分差閘門列與軟閘門列，用完整系統那一跑的分數檔與 rowstats：

```
python3 eval/ablation/v0.7.0/gate_offline.py --self-test
python3 eval/ablation/v0.7.0/gate_offline.py chat   <cvtune 的分數檔>   <cvtune 的輸出>   [--out DIR]
python3 eval/ablation/v0.7.0/gate_offline.py chat   <cvtune 的分數檔>   <cvtune 的輸出>   --off soft [--out DIR]
python3 eval/ablation/v0.7.0/gate_offline.py formal <wikitune 的分數檔> <wikitune 的輸出> [--out DIR]
```

discordtune 的兩格用同一支腳本、同樣的兩個 `chat` 指令算。

腳本只用標準函式庫；自我檢查的案例取自核心的單元測試，`make test-python` 會跑它。
