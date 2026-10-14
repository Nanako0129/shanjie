# model-v6 建置指令

契約：`docs/contracts/model-v6.md` §2.1、§2.1b、§2.2、§2.2b、§2.3、§2.6 步驟 2。這份只記指令順序；尖括號是建置機上的路徑（`<REPO>` 是含契約 §2.6 前提的 checkout、`<WORK>` 是新的計數目錄、`<M5CLUSTER>` 是 model-v5 那次分群的輸出資料夾），不是現成可跑的批次檔。慣例同 `experiments/model-v5/README.md`：絕對路徑、`PYTHONUTF8=1`、188 綁 P-core。最後三個檔（`bigram.sjlm`、`classes.sjc`、`kn.sjkn`）是最終檔，各記 SHA-256。

名單參數：`--extra-overlay data/lexicon/names-add.tsv` 在 `build_lm.py`、`build_classes.py`、`kn_cont.py` 三個都要帶，而且是**同一份**檔（異體類、封頂與產得出的詞三處才一致）；`lm_eval.py` 做跨語言核對時也帶它（契約 §2.3）。重現核對（契約 §2.1）**不帶**這個參數。

## 補次數檔（§2.1b）的生成指令

（main 補上實際跑過的指令、`--chars`、帳本與輸出檔 SHA-256。）

## 步驟

1. 計數（188，配方照 `experiments/model-v5/count.bat`，`<WORK>` 是新目錄，`S2_WORK=<WORK>`）：維基 `build_counts.py --articles 200000 --procs 10 --expected --extra-lexicon <計數詞表>…`、口語 `build_counts_text.py --expected --extra-lexicon <計數詞表>…`（口語訓練檔、`synth.txt` 與補次數檔），各自記 SHA-256。
2. 建模型（`PRUNE=2`、`D=0.75` 不變）：

   ```
   cd <REPO>
   S2_WORK=<WORK> python3 tools/build_lm.py --extra-overlay data/lexicon/names-add.tsv --out <WORK>/bigram-v6-nofp.sjlm
   ```

3. 私有條目（main 的私有流程）：`<WORK>/bigram-v6-nofp.sjlm` → 加過條目的 `<WORK>/bigram.sjlm`，在算雜湊、分群、建詞類表、產生 KN 側檔之前加。之後三步都用這個最終模型。
4. 分群（188；`S2K_COUNTS=<WORK>`、`S2K_OUT=<CLUSTER>` 是新資料夾，不能是 `--keep-from` 的那個）：

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
