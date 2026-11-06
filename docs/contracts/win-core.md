# WIN-CORE：核心在 Windows 編得過、測得過（官方 Windows 版第一片）

狀態：**修訂一**（2026-11-06）：plan-verifier 第一次 REVISE 的九項照最小修改處理。
- 錯誤盤點改用含測試的 `--tests`，fixture 的 symlink 改走 Windows 分支，不再整檔擋掉。
- 呼叫 `learning_open` 的測試有明確處置，另加 Windows 專用測試。
- `LearnStore` 的公開方法在所有平台都編譯。
- CI 只建評測 CLI。
- `.gitattributes` 固定 LF。
- 三個 f64 golden 測試有事先定好的處置。
- 指定由哪個 Linux job 產生 rowstats。
- executor 由 main 在 188 回報 Windows 的編譯結果。
- 拿掉要單執行緒計時的量測，改到最小 TIP 那片。

草稿 1103dff6。

## 0. 官方 Windows 版的路線（維護者 2026-11-06）

- 官方 Windows 版由這個 repo 自己做，不收社群移植版的功能（維護者：「我們的不能收」）。
- 各平台 UI 先用原生；Windows 的設計語言是 Windows 11，盡量和微軟注音一致。
- 三位顧問（Fable、Astra、Argon，2026-11-06）之後，維護者選定：
  - **候選窗與預測列**：用獨立的 WinUI 3 程式畫。先做探針確認不搶焦點、疊層、沙箱 App 都行得通；行不通就停下回報，備案是 DLL 內的 Direct2D。
  - **核心**：先直接連進輸入法 DLL，量完每個 App 的記憶體與首鍵延遲再決定要不要改成常駐程序。
- 切片順序（每片各自寫契約）：
  1. WIN-CORE（這一片）
  2. 量微軟注音（188 的測試 VM）
  3. 最小 TIP（記事本打得出字，並量每個 App 的記憶體與首鍵延遲）
  4. 候選窗與預測列
  5. 學習
  6. 相容性
  7. 設定
  8. 安裝、更新、簽章

## 1. 要做到什麼

- `core` 的程式與它全部的測試在 `x86_64-pc-windows-msvc` 上編得過，`cargo test -p core --release` 在 Windows 上通過。
- 評測 CLI（`shanjie-eval`）在 Windows 上建得起來，同一份模型、同一份評測集的逐列結果和 Linux 逐位元組相同。
- macOS 與 Linux 的程式行為、測試與保護不變。

**錯誤盤點的基準指令**：`cargo check -p core --tests --target x86_64-pc-windows-msvc`。實作的第一步是在 188 上跑這條，把完整清單列進 PR。現在已知的錯誤：

| 位置 | 原因 |
|---|---|
| `core/src/learn_store.rs` | `std::os::unix`、`std::os::fd`、`O_NOFOLLOW`、`O_NONBLOCK`、`ELOOP`、`mode`、`uid`、`from_mode`、`custom_flags`、`barrier` |
| `core/src/trigram.rs` | `std::os::unix`、`std::os::fd`、`custom_flags`、`tinylm::O_NONBLOCK` |
| `core/src/tinylm.rs` | `O_NONBLOCK` 只在 Unix 定義 |
| `core/src/learn.rs` 的 `local_day()` | 自己宣告 `time`、`localtime_r` 與 macOS／glibc 的 `tm`，連結時會失敗 |
| `core/tests/common/toy.rs` | `std::os::unix`（`lm_path` 用 symlink 組模型資料夾；engine_rerank、engine_fuzzy、engine_fuzzy_rerank 都靠它） |
| `core/tests/engine_learn.rs` | 自己宣告 `getrusage` |
| 其他 `core/tests/*.rs` | tri_tiny、engine_predict、engine_lm、engine_pack、engine_fuzzy_rerank、engine_digits_off、engine_demote、engine_rerank、reading_decode、kn_tiny、engine_learn、learn_store、engine_english_next（由審查找到，實作時以基準指令的結果為準） |

## 2. 做法

### 2.1 原則

- 平台差異只放在 `#[cfg(unix)]`／`#[cfg(windows)]` 的小段落，不複製整個模組。
- Unix 的程式碼與測試行為不變。允許的改動只有三種：
  1. 加 `cfg` 屬性；
  2. 把共用部分抽成函式，抽出前後的行為與測試相同；
  3. §2.2 第 6 點寫明的三個測試條件，每個只改一個 token。
- 不在建置時改寫原始碼，也不加會改變選字或按鍵行為的 Windows 專用邏輯。
- Windows 才需要的系統呼叫用 `windows-sys`。它放在 `[target.'cfg(windows)'.dependencies]`，版本固定、只開用到的 feature。由 main 加進 `Cargo.toml` 與 `Cargo.lock`，executor 開工前就已存在。

### 2.2 各處

1. **`trigram.rs`**：唯讀 mmap 在 Windows 上用 `CreateFileW`（`GENERIC_READ`、`FILE_SHARE_READ`）、`CreateFileMappingW(PAGE_READONLY)`、`MapViewOfFile(FILE_MAP_READ)`；`Drop` 時 `UnmapViewOfFile` 與 `CloseHandle`。
   - 和 Unix 相同的語意照樣成立：在同一個 handle 上驗證大小與檔頭；內容唯讀；錯誤照樣回傳。
   - 不是一般檔（目錄、裝置、管線）就拒絕：用 `GetFileType` 與 `FILE_ATTRIBUTE_DIRECTORY`。
2. **`tinylm.rs`**：`O_NONBLOCK` 與它的使用處只在 `cfg(unix)`。Windows 用一般的 `File::open`，加上同樣的「必須是一般檔」檢查。Accelerate 只在 macOS；Windows 用現有的 Plain 路徑，和 Linux 相同。
3. **`learn.rs` 的 `local_day()`**：Unix 版不動。Windows 版用 `GetLocalTime`，把 `SYSTEMTIME` 的年月日自己換算成 1970-01-01 起的日數。語意同 Unix 版：使用者當地的日曆日，含夏令時間。
4. **`learn_store.rs`：這一片 Windows 不做學習檔。**
   - `LearnStore` 的型別與公開方法在所有平台都編譯，`engine.rs` 不用改。
   - Unix 專用的內容放進 `cfg(unix)`。`cfg(not(unix))` 版的 `open` 不碰檔案系統，直接回 `Err(StoreError::Io)`；其他方法也回 `Err(StoreError::Io)`。因為 open 一定失敗，實際走不到這些方法。
   - 結果是 `shanjie_engine_learning_open` 在 Windows 上回 3，殼照現有契約在沒有學習檔的狀態下運作：選字照常，不記學習。
   - `core/include/shanjie.h` 的 `shanjie_engine_learning_open` 註解加一句：Windows 版目前一律回 3。
   - 理由：Windows 的學習檔要另外處理 ACL、reparse point、多個 App 同時寫入。三位顧問都指出，單純加鎖仍會互相覆蓋。這屬於資安相關的一片，要先過 security-reviewer。
5. **測試**：
   - 測 symlink 或 Unix 權限**行為本身**的測試標 `#[cfg(unix)]`，例如 `ffi.rs`、`lm.rs` 裡的 symlink 測試、`tests/learn_store.rs`。
   - 只拿 symlink **組模型資料夾**的 fixture（`tests/common/toy.rs::lm_path` 等）加 `#[cfg(windows)]` 分支，改用 `fs::copy`，Unix 那行不動。所以 engine_rerank、engine_fuzzy、engine_fuzzy_rerank 在 Windows 上照跑。
   - `engine_learn.rs` 的 `getrusage` 那段標 `cfg(unix)`。
   - 會呼叫 `learning_open` 而且預期成功的測試，在 Windows 一律標 `cfg(unix)`，例如 engine_newword、engine_learn、engine_predict、engine_english、engine_english_next、engine_fuzzy_rerank 裡的那些。逐個列進 PR。
   - 另加一個只在 Windows 跑的測試：`shanjie_engine_learning_open` 回 3，之後打一段鍵選字照常。
   - 標成 `cfg(unix)` 的測試清單（檔名、測試名、原因）列進 PR。engine_rerank、engine_fuzzy、engine_fuzzy_rerank 不能整檔被擋掉。
6. **f64 逐位元比對的 golden**：`core/tests/engine_digits.rs`（約第 136–138、588 行）與 `core/tests/pack_oov.rs`（約第 159–163 行）有三個測試只在 Linux 跳過。理由是它們拿 macOS libm 的位元當答案，glibc 的 log10 已知在最後一位不同。
   - 把跳過條件從「是 Linux」改成 `!cfg!(target_os = "macos")`。每個只改一個 token，Linux 上的行為不變（照樣跳過），Windows 也跳過。
   - 其他測試在 Windows 上的浮點失敗一律是停止條件（§4）。
7. **換行**：`.gitattributes` 加上 `data/** text eol=lf`、`eval/** text eol=lf`、`core/tests/** text eol=lf`、`cli/tests/** text eol=lf`。
   - Unix 上沒有作用；repo 裡這些檔現在都是 LF（`git ls-files --eol`：179 個 `i/lf`，其他是二進位或空檔）。
   - 理由：Windows 預設 `core.autocrlf=true`，checkout 會變成 CRLF，`DigitTable::parse` 遇到 `\r` 失敗，數字表會默默關掉。
   - 被測的程式不改。
8. **評測 CLI**：只建 `shanjie-eval`（`cargo build -p cli --release --bin shanjie-eval`）。`shanjie-replay` 用 Unix 專用的 API，不在這一片，Windows 上不建。

### 2.3 CI

- **Linux 那邊**：在既有的 `debug` job（映像 `ubuntu-24.04`）最後加三步，前面的步驟不動：
  1. `cargo build --release --locked -p cli --bin shanjie-eval`；
  2. 跑兩條評測，都是聊天、`--context --packs acg,it --tinylm data/lm/tinylm.sjtl --colloquial-core data/lm/colloquial.sjlm`：dev302（`--dev 302`）與打字測驗（`--rows eval/dev/user-typing.txt`），各寫一份 `--rowstats`；
  3. `actions/upload-artifact` 上傳這兩份。
- **新的 `windows` job**：映像 `windows-2025`，`needs: [changes, debug]`，`if:` 和其他 job 相同（`needs.changes.outputs.run == 'true'`）。步驟如下：
  1. Rust 1.97.1；
  2. checkout，`.gitattributes` 讓資料是 LF；
  3. 取模型並核對 `data/*.sha256`，用和其他 job 相同的 `scripts/fetch-models.sh`，這一步加 `shell: bash`；
  4. `cargo test -p core --release --locked`；
  5. `cargo build --release --locked -p cli --bin shanjie-eval`；
  6. 用同樣兩條指令產生 rowstats，下載 Linux 的 artifact 後逐檔 `cmp`，不同就失敗；
  7. 檢查 `git ls-files --eol data eval` 工作區全是 `w/lf`。
- **只編不測**：在 windows job 裡加兩個目標，`rustup target add` 之後 `cargo build -p core --target i686-pc-windows-msvc` 與 `--target aarch64-pc-windows-msvc`。
- 其他 job 不改。

## 3. 驗收（怎麼看到它有效）

1. **188 的原生 Windows**（main 在 188 上跑，不經 WSL；188 的 clone 先照 `.gitattributes` 重新 checkout，`git ls-files --eol data eval` 全是 `w/lf`）：
   - `cargo test -p core --release` 全過。
   - 列出被 `cfg(unix)` 擋掉、或因 §2.2 第 6 點跳過的測試名稱與數量。
   - 有一個測試證明 Windows 上載入了數字表（engine 的 digits 測試在 Windows 有跑、通過）。
2. **逐位元組相同**：188 原生 Windows 與 188 WSL（Linux）各用同一個 commit、同一份 `data/lm`，跑：
   - 集合：dev302、打字測驗、錯字回報；
   - 兩種設定：聊天、書面；
   - 出貨旗標：`--context --packs acg,it --tinylm data/lm/tinylm.sjtl`，聊天另加 `--colloquial-core data/lm/colloquial.sjlm`。

   六組 `--rowstats` 要逐位元組相同。這一步用多執行緒（`--threads 12`，綁 P-core、關降速），照 188 的負載規則。
3. **Unix 不變**：macOS 上只跑改到的 Rust 測試檔；CI 的 macOS、Linux job 全綠；golden 不變。
4. **CI**：新的 windows job 綠（含 rowstats 的 `cmp` 與換行檢查），三個 Windows 目標都編得過。

## 4. 停止條件、預算、範圍外

- **停止條件**：
  - §3.2 任何一組不同；
  - §2.2 第 6 點以外的測試在 Windows 上因浮點不同而失敗；
  - Unix 的任何測試或 golden 改變；
  - `windows-sys` 以外還需要新的相依。

  遇到浮點不同時停下，報告差在哪一層、差幾 ulp，不改容差去湊。
- **預算與分工**：
  - executor 實作 1 回合，加至多 2 次修正。
  - executor 在 Mac 上沒有 Windows 目標，每一回合交件後由 main 在 188 跑 `cargo check -p core --tests --target x86_64-pc-windows-msvc` 與 `cargo test -p core --release`，把完整輸出交回 executor。
  - executor 交件前在 Mac 上跑改到的 Rust 測試檔，證明 Unix 不受影響。
  - 188 的量測（§3.1、§3.2）由 main 做。
- **範圍外**：
  - Windows 的學習檔（另一片，要過資安審查）
  - `shanjie-replay`
  - TSF 殼、候選窗、設定、安裝
  - ARM64／x86 的測試（只編不測）
  - 記憶體與延遲量測（移到最小 TIP 那片）
  - 效能最佳化

## 5. executor 不可以做的事

- 不推分支、不開 PR、不連網、不安裝，也不要 `rustup target add`。
- 不 ssh 到 188 或其他機器；188 的部分由 main 做。
- 不改 `macos/`、`data/`、`eval/` 的內容；`.gitattributes` 照 §2.2 第 7 點改。
- 不讀 `eval/holdout/`、私人資料。
- 不啟動 App、不碰鑰匙圈與 `~/Library`。
- Mac 上只跑改到的 Rust 測試檔，不跑完整測試套件。
- 新檔名不含空白。
