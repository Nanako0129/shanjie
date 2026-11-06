# WIN-CORE：核心在 Windows 編得過、測得過（官方 Windows 版第一片）

狀態：草稿（2026-11-06），等 plan-verifier。

## 0. 官方 Windows 版的路線（維護者 2026-11-06）

- 官方 Windows 版由這個 repo 自己做，不收社群移植版的功能（維護者：「我們的不能收」）。
- 各平台 UI 先用原生；Windows 的設計語言是 Windows 11，要盡量和微軟注音一致。
- 三位顧問（Fable、Astra、Argon，2026-11-06）之後，維護者選了：
  - 候選窗與預測列：用獨立的 WinUI 3 程式畫。先做探針確認不搶焦點、疊層順序、沙箱 App 都行得通；行不通就停下回報，備案是 DLL 內用 Direct2D。
  - 核心：先直接連進輸入法 DLL，在 Windows 上量每個 App 的記憶體與首鍵延遲，再決定要不要改成常駐程序。
- 切片順序（每片各自寫契約）：
  1. WIN-CORE（這一片）：核心在 Windows 編得過、測得過。
  2. 量微軟注音：按鍵行為與外觀數字，在 188 的測試 VM 上量。
  3. 最小 TIP：記事本打得出字。
  4. 候選窗與預測列。
  5. 學習。
  6. 相容性。
  7. 設定。
  8. 安裝、更新、簽章。

## 1. 要做到什麼

`core` 在 `x86_64-pc-windows-msvc` 上編得過，`cargo test -p core` 在 Windows 上通過。同一份模型、同一份評測集，Windows 上評測 CLI 的逐列結果和 Linux 逐位元組相同。macOS 與 Linux 的程式行為、測試與保護不變。

現在的狀況（188，2026-11-06，`cargo check -p core --target x86_64-pc-windows-msvc`）：21 個錯誤，全在三個檔：

| 檔案 | 原因 |
|---|---|
| `core/src/learn_store.rs` | `std::os::unix`、`std::os::fd`、`O_NOFOLLOW`、`O_NONBLOCK`、`ELOOP`、`mode`、`uid`、`from_mode`、`custom_flags`、`barrier` |
| `core/src/trigram.rs` | `std::os::unix`、`std::os::fd`、`custom_flags`、`tinylm::O_NONBLOCK` |
| `core/src/tinylm.rs` | `O_NONBLOCK` 只在 Unix 定義 |

此外，`core/src/learn.rs` 的 `local_day()` 自己宣告了 `time`／`localtime_r` 與 macOS／glibc 的 `tm` 版面，連結時會失敗。

## 2. 做法

### 2.1 原則

- 平台差異只放在 `#[cfg(unix)]`／`#[cfg(windows)]` 的小段落，不複製整個模組。Unix 的程式碼與測試逐行不變；只能加 `cfg` 屬性，以及把共用部分抽成函式（抽出前後的行為與測試相同）。
- 不在建置時改寫原始碼，也不加會改變選字或按鍵行為的 Windows 專用邏輯。
- Windows 才需要的系統呼叫用 `windows-sys`。它只是 `[target.'cfg(windows)'.dependencies]`，Unix 的相依不變；版本固定並寫進 `Cargo.lock`，只開用到的 feature。

### 2.2 各檔

1. **`trigram.rs`**：唯讀 mmap 在 Windows 上改用 `CreateFileW`（`GENERIC_READ`、`FILE_SHARE_READ`）＋`CreateFileMappingW(PAGE_READONLY)`＋`MapViewOfFile(FILE_MAP_READ)`，`Drop` 時 `UnmapViewOfFile` 與 `CloseHandle`。
   - 和 Unix 相同的語意要照樣成立：開檔後在同一個 handle 上驗證大小與檔頭，內容唯讀，錯誤照樣回傳。
   - 檔案不是一般檔（目錄、裝置、管線）照樣拒絕。Windows 上用 `GetFileType` 與 `FILE_ATTRIBUTE_DIRECTORY`，對應 Unix 的 `O_NONBLOCK`＋`is_file` 檢查。
2. **`tinylm.rs`**：`O_NONBLOCK` 與它的使用處只在 `cfg(unix)`；Windows 讀檔照一般 `File::open`，加上同樣的「必須是一般檔」檢查。Accelerate 路徑本來就只在 macOS，Windows 用現有的 Plain 路徑，和 Linux 相同。
3. **`learn.rs` 的 `local_day()`**：Unix 版不動。Windows 版用 `GetLocalTime`，再換算成 1970-01-01 起的日數（`SYSTEMTIME` 的年、月、日 → 日數，自己算，不經時區偏移的乘除）。語意相同：使用者當地的日曆日，含夏令時間。
4. **`learn_store.rs`**：這一片在 Windows 上**不做學習檔**。
   - `LearnStore::open` 在 `cfg(not(unix))` 時不碰檔案系統，直接回傳 `Err(StoreError::Io)`。殼照現在的契約，在沒有學習檔的狀態下運作：選字照常、不記學習，`shanjie_engine_learning_status` 照實回報沒有開成。
   - 其餘函式只在 `cfg(unix)` 編譯。用到它們的測試也只在 `cfg(unix)`。
   - `core/include/shanjie.h` 的 `shanjie_engine_learning_open` 註解加一句：Windows 版目前一律失敗。
   - 理由：Windows 的學習檔要另外設計 ACL、reparse point、多個 App 同時寫入（三位顧問都指出單純加鎖仍會互相覆蓋），屬於資安相關的一片，要先過 security-reviewer，不放在這片。
5. **測試**：`core/src/ffi.rs`、`core/src/lm.rs` 裡用 `std::os::unix::fs::symlink` 的測試，以及斷言 Unix 權限的測試，標 `#[cfg(unix)]`。其他測試在 Windows 上照跑；有路徑分隔或換行假設而失敗的，修測試本身（用 `Path::join`、讀檔不假設 `\n`），不改被測的程式。改了哪些測試逐條列在 PR。

### 2.3 CI

`.github/workflows/ci.yml` 加一個 `windows-2025` 的 job（固定映像版本，不用 `-latest`）：

- Rust 1.97.1（和其他 job 相同）。
- 用和 macOS／Linux job 相同的方式取模型並核對 `data/*.sha256`。
- `cargo test -p core --release`。
- `cargo build -p cli --release`，接著跑一份 dev302 與打字測驗的 `--rowstats`（聊天、`--context`），和 Linux job 產生的同一份 rowstats 比對（Linux job 上傳 artifact，Windows job 下載後 `cmp`）。不同就失敗。

另外只編不測：`i686-pc-windows-msvc` 與 `aarch64-pc-windows-msvc` 的 `cargo build -p core`（`rustup target add`）。

既有的 macOS 與 Linux job 一個字都不改，除了 Linux job 多上傳一份 rowstats 當 artifact。

## 3. 驗收（怎麼看到它有效）

1. **188 的原生 Windows**（main 在 188 上跑，不經 WSL）：
   - `cargo test -p core --release` 全過。
   - 列出標成 `cfg(unix)` 而在 Windows 沒跑的測試名稱與數量。
2. **逐位元組相同**：188 原生 Windows 與 188 WSL（Linux）各用同一個 commit、同一份 `data/lm`，跑：
   - dev302、打字測驗、錯字回報；
   - 聊天與書面兩種設定；
   - 出貨旗標：`--context --packs acg,it --tinylm data/lm/tinylm.sjtl`，聊天另加 `--colloquial-core data/lm/colloquial.sjlm`。

   六組 `--rowstats` 逐位元組相同。不同就停，列出哪些列不同、差在哪一層。
3. **Unix 不變**：macOS 上 `make test-rust`（只跑改到的部分）與 CI 的 macOS、Linux job 全綠；golden 不變。
4. **CI**：新的 Windows job 綠，三個 Windows 目標都編得過。
5. **記錄**：Windows 上評測 CLI 的峰值記憶體與 dev302 的每列時間（單執行緒、綁 P-core、關降速），寫進研究紀錄，給「核心放 DLL 還是常駐程序」的決定用。

## 4. 停止條件、預算、範圍外

- **停止條件**：
  - §3.2 有任何一組不同，而且原因不是已知的平台浮點差異。若是浮點差異，停下來報告差在哪一層、差多少，不改容差去湊。
  - Unix 的任何測試或 golden 改變。
  - `windows-sys` 以外還需要新的相依。
- **預算**：executor 實作 1 回合加 1 次修正；188 的量測由 main 做。
- **範圍外**：
  - Windows 的學習檔（另一片，資安審查）。
  - TSF 殼、候選窗、設定、安裝。
  - ARM64／x86 的測試（只編不測）。
  - 效能最佳化。

## 5. executor 不可以做的事

- 不推分支、不開 PR、不連網（`windows-sys` 由 main 加進 `Cargo.lock`）、不安裝。
- 不 ssh 到 188 或其他機器，188 的部分由 main 做。
- 不改 `macos/`、`data/`、`eval/`。
- 不讀 `eval/holdout/`、私人資料。
- 不啟動 App、不碰鑰匙圈與 `~/Library`。
- Mac 上只跑改到的 Rust 測試檔，不跑完整測試套件。
- 新檔名不含空白。
