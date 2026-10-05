# 參與開發

歡迎三種貢獻：

- 回報選錯的字；
- 修正詞庫；
- 改 Swift 殼或 Rust 核心。

先看 README 的「進度」與「路線圖」，知道現在做到哪裡。

## 回報選錯的字

開一個 issue，用「選字錯誤」範本，附上下面幾項：

- **實際按的鍵**：例如 `su3cl3`（標準鍵盤）。我們要分辨「讀音對、但選錯字」和「讀音打錯」，這兩種要修的地方不同。
- **想打的句子**、**善解給的結果**。
- **前一句**（如果有）。整句轉換會看前文。
- **App 與鍵盤**：標準或倚天。

回報的句子會加進開發用的評測集 `eval/dev/user-reported.txt`，並以 CC0 釋出。只貼你願意公開的句子。

## 建置與測試

需要：

| 項目 | 版本 |
|---|---|
| macOS | 26 以上，Apple 晶片 |
| Xcode | 27（CI 用 `xcode-27`） |
| Rust | 1.97.1（和 CI 相同；評測的 golden 檔是用這一版產生的） |
| GitHub CLI（已 `gh auth login`）或 curl | 下載語言模型 |

步驟：

```sh
# 語言模型不在 repo 裡，從 Release 下載（CC BY-SA 4.0）
gh release download model-v1 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
# 沒有登入 gh 的話改用：
# curl -L --create-dirs -o data/lm/bigram.sjlm https://github.com/Nanako0129/shanjie/releases/download/model-v1/bigram.sjlm
shasum -a 256 data/lm/bigram.sjlm   # 要和 data/bigram.sjlm.sha256 相同

make test      # Rust 核心測試，再跑 Swift 殼的測試
make bundle    # 組出 build/善解輸入法.app
```

需要模型的測試，找不到模型時會直接失敗並說明怎麼取得，不會跳過。

**想實際裝起來試**：

```sh
scripts/install-ime.sh build/善解輸入法.app
```

- 會覆蓋 `~/Library/Input Methods/` 裡的善解；上一版留在 `.shanjie-previous`。
- 從 Homebrew 版換成自己建置的版本前，先 `brew uninstall --cask shanjie`，否則之後的 `brew upgrade` 會蓋掉你的建置。

各項檢查的細節在 `docs/verification.md`。

## 程式結構

| 目錄 | 內容 |
|---|---|
| `core/` | Rust 核心：詞庫、候選、語言模型、按鍵引擎、選字記憶；C ABI 在 `core/include/shanjie.h` |
| `macos/` | Swift 殼：`ShanjieKit`（可測試的邏輯，一律接真的 C 核心）、`Shanjie`（InputMethodKit、候選窗、選單） |
| `cli/` | `shanjie-eval`：評測工具 |
| `data/` | 詞庫；授權見 `LICENSES/data.md` |
| `eval/` | 評測集，規則見 `eval/README.md` |
| `tools/`、`scripts/` | 詞庫與模型的建置工具、打包與安裝腳本 |
| `docs/` | 計畫、契約、方法、研究紀錄 |

## 怎麼提改動

**小改動**直接開 PR，例如錯字、詞庫的一行修正、明顯的 bug。

**改到行為的改動**先開 issue 討論，例如按鍵規則、候選排序、C ABI、隱私相關的資料流。

- 這個專案每一片功能都先寫契約（`docs/contracts/`），寫清楚要做到什麼、怎麼驗收、哪些不做，然後才實作。
- 大的改動我們會一起把契約寫出來。

PR 需要：

- **CI 全綠**：Rust 測試、Swift 測試、bundle 檢查。
- **說明怎麼看到它有效**：測試名稱、評測數字，或實機操作的步驟。
- **改到選字結果的改動，附開發集的前後數字**：
  - 在改動前後各跑一次，聊天與書面兩種設定都跑：

    ```sh
    cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile chat --dev 302
    cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile formal --dev 302
    ```

    輸出只有一行摘要，`top1` 是 302 句裡整句對的句數（寬鬆比對：`eval/variants.tsv` 裡的異體寫法也算對）；
  - 要知道哪幾句變了，前後各加 `--dump before.tsv`／`--dump after.tsv`。每列是「句號、名次、候選、分數」，名次 1 是善解給的結果。只比句號和候選（分數幾乎每次都會變）：

    ```sh
    diff <(awk -F'\t' '$2==1{print $1"\t"$3}' before.tsv) <(awk -F'\t' '$2==1{print $1"\t"$3}' after.tsv)
    ```

  - `--dev 302` 是 `eval/dev/` 依檔名排序後的前 302 列，只涵蓋 `user-reported.txt` 的前幾句。修的是回報的錯字時，另外跑那個檔：

    ```sh
    cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile chat --rows eval/dev/user-reported.txt
    ```

  - 只要求不退步；
  - 有進步的話寫出是哪幾句。
- **對應的文件一起更新**：契約、`docs/PLAN.md`、README。
- **commit 加上 DCO 簽署**（見下一節）。

PR 以 merge commit 合併（不 squash，保留每個 commit 方便 `git bisect`）。合併後 GitHub 會自動刪掉本 repo 裡的 PR 分支，但不會刪你 fork 裡的分支；fork 的分支、本機的分支與 worktree 請自己清掉：

```sh
git push origin --delete <分支>   # 你 fork 裡的分支
git fetch --prune
git branch -d <分支>
git worktree remove <worktree 路徑>   # 有用 worktree 的話
```

### 保留集不要拿來調參數

`eval/holdout/` 是保留集，只在一片（`docs/PLAN.md` 的一個切片）做完時，由沒參與實作的人跑一次，用來確認沒有對開發集過度調整（規則見 `eval/README.md`）。

- 請不要讀它的內容來找錯字或調參數；
- 也不要把它的句子加進其他地方。

你的 PR 只需要附開發集的數字；保留集由維護者在那一片收尾時跑。

## DCO 簽署

每個 commit 都要加 `Signed-off-by`，表示你同意 [Developer Certificate of Origin 1.1](https://developercertificate.org/) ：你有權以本專案的授權提交這段內容。

```sh
git commit -s
```

會在 commit 訊息最後加上一行：

```text
Signed-off-by: 你的名字 <你的 email>
```

忘了加的話，用 `git commit --amend -s`，或 `git rebase --signoff <你的分支開始的地方>`（例如 `upstream/main`）補上。

## 授權

- 程式碼：Apache-2.0（`LICENSE`），你的貢獻也以此授權。
- 詞庫與資料：各自的授權見 `LICENSES/data.md`。新增資料來源時，要附來源與授權，而且必須可以合法再散布。
- 授權有疑慮的資料不要提交，例如字典網站的內容、別的輸入法的詞庫。

## 隱私

輸入法看得到使用者打的每一個字，以下規則沒有例外：

- 日誌只能記靜態字串與回傳碼，不能記組字、候選、按鍵或 App 名稱（`docs/contracts/s3b.md` §9）。
- 不能新增連網，除非是已經規劃、預設關閉、使用者明確開啟的功能（`docs/PLAN.md` 的 S6，以及 §7 安全審查表的 R1–R9）。
- 不申請「輸入監控」或「輔助使用」權限；InputMethodKit 不需要。
