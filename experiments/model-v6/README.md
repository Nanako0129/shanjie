# model-v6 建置指令

契約：`docs/contracts/model-v6.md` §2.1、§2.1b、§2.2、§2.2b、§2.3、§2.6 步驟 2。這份只記指令順序；尖括號是建置機上的路徑（`<REPO>` 是含契約 §2.6 前提的 checkout、`<WORK>` 是新的計數目錄、`<M5CLUSTER>` 是 model-v5 那次分群的輸出資料夾），不是現成可跑的批次檔。慣例同 `experiments/model-v5/README.md`：絕對路徑、`PYTHONUTF8=1`、建置機綁 P-core。最後三個檔（`bigram.sjlm`、`classes.sjc`、`kn.sjkn`）是最終檔，各記 SHA-256。

名單參數：`--extra-overlay data/lexicon/names-add.tsv` 在 `build_lm.py`、`build_classes.py`、`kn_cont.py` 三個都要帶，而且是**同一份**檔（異體類、封頂與產得出的詞三處才一致）；`lm_eval.py` 做跨語言核對時也帶它（契約 §2.3）。重現核對（契約 §2.1）**不帶**這個參數。

PR 2（名單層常開）之後：`ime.OVERLAYS` 與核心的預設載入已經在 `sandhi-add.tsv` 之後讀 `names-add.tsv`，在那之後的 checkout 重跑上面這些指令要**拿掉** `--extra-overlay data/lexicon/names-add.tsv`：同一個檔再給一次是重複的列，`ime.Lexicon` 與核心都會報錯。拿掉之後，Python 工具讀到的疊加層清單和原本帶參數時相同（`ime.overlays()` 的結果是同一串檔案，讀程式碼得知，沒有重跑）；Rust CLI 不開詞包時也相同，開詞包時名單改在詞包之前。要量「沒有名單層」，Rust CLI 用 `--no-names`。

## `overlay-add.tsv` 的兩個版本（契約 §2.8 規則 2）

| 用途 | 版本 | SHA-256 |
|---|---|---|
| 計數（model-v6、classes-v4、kn-v1 的建置輸入） | commit `4d47a50` 的檔，`MANUAL_BONUS` 0.5 | `96b8db8914d7f71afbc74f3997ac661354997881f50b4aba0f163976bede1620` |
| 執行時、golden 與評測 | `MANUAL_BONUS` 0.75 重建的檔（只有 7 列手動列的分數不同） | `34f7bbeb281512b912065684af96f7fc42def71e30d586e9af62111bf28e638e` |

照這份 README 重現計數時，疊加層要用舊檔：`git show 4d47a50:data/lexicon/overlay-add.tsv`，不是 HEAD 的檔。兩個版本（都加上 `names-add.tsv`）的 `build_lm.variant_classes` 相同（研究紀錄 2026-10-14「修訂一的疊加層」）。

## 補次數檔（§2.1b）的生成指令

在 Mac 跑（只呼叫 API，計算量小），金鑰只放進這一個程序的環境變數：

```
cd experiments/s7f-corpus
CEREBRAS_API_KEY=… python3 generate.py --provider cerebras --model gpt-oss-120b --price-in 0.5 --price-out 1.0 \
  --seeds ../model-v6/seeds-topup.txt --invade <INVADE_CLONE> --invade-sha256 f5406195a719b29c6f61c61919d45bbb52b3f95d811c8fd4eb0a55a14ff01cb6 \
  --tune <S7C2C_TUNE> --chars 500000 --budget-usd 15 --dedupe-against <S7F 的 cerebras-gpt-oss-120b.txt> \
  --ledger <DIR>/spend.json --out <DIR>
```

中途一次 HTTP 504（第 9,376 個提示）後，同一個指令加 `--resume` 接著跑。結果：23,928 個回應、留下 501,062 字、抽成剛好 500,000 字（22,745 列），帳本 US$2.8733；濾掉的：重複 258、和評測集重疊 90、入侵詞 312、長度 1、沒用到種子詞 405、簡體 67。輸出 `cerebras-gpt-oss-120b.txt` SHA-256 `315939d7391205a2ea738504e71c4a7fd702f7ef3ee892d197ec177205814d54`（建置機上的檔名是 `topup.txt`，雜湊相同）。

## 步驟

1. 計數（建置機，配方照 `experiments/model-v5/count.bat`，`<WORK>` 是新目錄，`S2_WORK=<WORK>`）：維基 `build_counts.py --articles 200000 --procs 10 --expected --extra-lexicon <計數詞表>…`、口語 `build_counts_text.py --expected --extra-lexicon <計數詞表>…`（口語訓練檔、`synth.txt` 與補次數檔），各自記 SHA-256。
   計數之前，每份訓練文字先過 `python3 tools/holdout_guard.py --hashes eval/holdout/hashes.sha256 <輸入> --out <輸出>`（契約 `holdout-refresh.md` §8；下一次建模型時才執行；雜湊檔還不存在，隨保留集換新的資料 PR 一起到）。
2. 建模型（`PRUNE=2`、`D=0.75` 不變）：

   ```
   cd <REPO>
   S2_WORK=<WORK> python3 tools/build_lm.py --extra-overlay data/lexicon/names-add.tsv --out <WORK>/bigram-v6-nofp.sjlm
   ```

3. 私有條目（main 的私有流程）：`<WORK>/bigram-v6-nofp.sjlm` → 加過條目的 `<WORK>/bigram.sjlm`，在算雜湊、分群、建詞類表、產生 KN 側檔之前加。之後三步都用這個最終模型。
4. 分群（建置機；`S2K_COUNTS=<WORK>`、`S2K_OUT=<CLUSTER>` 是新資料夾，不能是 `--keep-from` 的那個）：

   ```
   S2K_COUNTS=<WORK> S2K_OUT=<CLUSTER> python3 experiments/s2-classes/cluster.py --N 40000 --K 512 --keep-from <M5CLUSTER>
   ```

5. 量詞詞類（§2.2b）：把 `classifiers.txt` 裡加權 `uni` 少於 1,000、類別和「個」不同的詞搬進「個」的類別，寫到**新**檔（印出它的 SHA-256 與搬了哪些詞，存進研究紀錄）：

   ```
   python3 experiments/model-v6/force_classes.py --dir <CLUSTER> --N 40000 --K 512 --out <CLUSTER>/cls-40000-512-forced.npz
   ```

6. 詞類表（classes-v4）：

   ```
   python3 tools/build_classes.py --edges <CLUSTER>/edges.npz --cls <CLUSTER>/cls-40000-512-forced.npz --lm <WORK>/bigram.sjlm --out <WORK>/classes.sjc --extra-overlay data/lexicon/names-add.tsv
   ```

7. KN 側檔（kn-v1，用最終模型與同一份計數）：

   ```
   S2_WORK=<WORK> python3 tools/kn_cont.py --lm <WORK>/bigram.sjlm --theta 1 --beta 1 --out <WORK>/kn.sjkn --extra-overlay data/lexicon/names-add.tsv
   ```

8. 跨語言核對（契約 §2.3）：最終模型、`classes.sjc`、`kn.sjkn` 放進 `data/lm/`，Rust CLI 與 `reference/proto/lm_eval.py` 都帶 `--extra-overlay data/lexicon/names-add.tsv`，摘要行（含 `+kn:`、`+xo:`、`top1_sha256`）逐字相同。
