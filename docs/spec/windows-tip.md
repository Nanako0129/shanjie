# Windows 版：核心移植與 TSF 文字服務

這份寫的是現在的行為。

範圍：核心（`core`）在 `x86_64-pc-windows-msvc` 上編譯與測試的做法、Windows 的 CI、TSF 文字服務 DLL（`windows/tip`，套件名 `shanjie-tip`）、以及微軟注音的量測參考（Windows 版按鍵與介面的對照依據）。按鍵規則本身見 `docs/spec/keys-and-composition.md`，核心的 C 介面見 `docs/spec/c-abi.md`，隱私規則見 `docs/spec/privacy.md`。

## 1. 組成與現況

### 1.1 組成

- `core`：同一份核心程式碼，Windows 上加少量 `cfg`，行為與 macOS、Linux 相同（§2）。
- `windows/tip`：TSF 文字服務 DLL。`crate-type = ["cdylib", "rlib"]`（rlib 給單元測試），`lib.rs` 開頭 `#![cfg(windows)]`，在 macOS 與 Linux 上是空的 crate，不影響既有的建置與測試。
- CI 的 `windows` job 與 Linux `debug` job 的 rowstats 比對（§2.9）。
- 微軟注音量測（§4）：只是參考資料，程式不讀它。

### 1.2 現況

- 核心與它全部的測試在 Windows 上編得過、測得過，評測 CLI 的逐列結果和 Linux 逐位元組相同。
- TSF 文字服務可在測試 VM 的記事本與「執行」對話框打出一句注音並送出，送出的字和 Mac 上 `shanjie-replay` 用同一串鍵解出的字逐字相同。
- 目前的按鍵行為就是核心現行的規則（Mac 版的規則）。微軟注音的規則組與 Mac 做法的切換設定尚未實作（§3.14）。
- 沒有候選窗、預測列、設定、學習檔、安裝程式與簽章（§3.14）。

## 2. 核心在 Windows

### 2.1 原則

- 平台差異只放在 `#[cfg(unix)]`／`#[cfg(windows)]` 的小段落，不複製整個模組。
- Unix 的程式碼與測試行為不變；不在建置時改寫原始碼，也沒有會改變選字或按鍵行為的 Windows 專用邏輯。
- Windows 才需要的系統呼叫用 `windows-sys`（`core/Cargo.toml` 的 `[target."cfg(windows)".dependencies]`，版本 0.59，開 `Win32_Foundation`、`Win32_Storage_FileSystem`、`Win32_System_Memory`、`Win32_System_SystemInformation`、`Win32_System_IO`、`Win32_Security`）。`windows-sys` 之外沒有別的 Windows 專用相依。
- 核心的測試在 macOS、x86_64 Linux 與 x86_64 Windows 上跑；i686 與 aarch64 的 Windows 目標只編譯、不測試（§2.9）。

### 2.2 唯讀 mmap（trigram 表）

`core/src/trigram.rs` 的 `Mapped::open` 有 Unix 與 Windows 兩個版本，語意相同：

- 在同一個 handle 上驗證「是一般檔、不是空檔」，內容唯讀，失敗回 `Err(())`（呼叫端把它報成 trigram 表的載入錯誤，錯誤不帶內容，R2）。
- Windows：用 std 的 `OpenOptions` 加 `share_mode(FILE_SHARE_READ)` 開檔（等於 `CreateFileW(GENERIC_READ, FILE_SHARE_READ)`），以 `meta.is_file()`、長度非 0 與 `GetFileType == FILE_TYPE_DISK` 拒絕目錄、裝置、管線與空檔，再 `CreateFileMappingW(PAGE_READONLY)` 與 `MapViewOfFile(FILE_MAP_READ)`；對映物件的 handle 立刻關掉，由 view 撐住 section，`Drop` 時 `UnmapViewOfFile`。
- 平台差異：Windows 在 view 存在時不讓人刪除、截斷或以 rename 覆蓋該檔，卻擋不住別的程序之後以寫入開檔就地改位元組。所以「對映期間不就地改寫」仍是呼叫端的契約（`open` 是 `unsafe fn`）；更新檔案要等引擎放掉對映之後。

### 2.3 小模型與其他檔案讀取

- `core/src/tinylm.rs`：`O_NONBLOCK` 與它的使用只在 `cfg(unix)`（Linux 0o4000，其他 Unix 0x0004）。Windows 的 `read_bounded` 用一般的 `File::open`，同樣先開檔、再檢查「是一般檔且不超過上限」、再從同一個 handle 讀到上限為止。
- Accelerate 只在 macOS；Windows 與 Linux 一樣走 Plain 路徑。

### 2.4 當地日期

`core/src/learn.rs` 的 `local_day()`（自 1970-01-01 起算的當地日數）：Unix 版用 `time`／`localtime_r`；Windows 版用 `GetLocalTime`，把年月日以 `days_from_civil` 換成日數。語意相同：使用者當地的日曆日，含夏令時間。

### 2.5 學習檔：Windows 不做

- `LearnStore` 的型別與公開方法在所有平台都編譯，`engine.rs` 不必分平台。Unix 專用的內容在 `cfg(unix)`；`cfg(not(unix))` 的 `open` 不碰檔案系統，直接回 `Err(StoreError::Io)`，其他方法（`save`、`save_forgetting`、`append`、`clear`）同樣回 `Err(StoreError::Io)`（因為 `open` 必失敗，實際走不到）。
- 結果：`shanjie_engine_learning_open` 在 Windows 上，對有效參數回 3、不建立目錄；`dir` 為 NULL 仍回 1；`shanjie_engine_learning_clear` 回 3。殼照 C 介面的規定在沒有學習檔的狀態下運作：選字照常，學習只在這次執行的記憶體裡，不寫進檔案。`core/include/shanjie.h` 的 `shanjie_engine_learning_open` 註解寫明這一點。
- 設計取捨：Windows 的學習檔需要另外處理 ACL、reparse point 與多個 App 同時寫入，單純加鎖仍會互相覆蓋；屬資安相關，需要先過 security-reviewer 才做。
- 驗證：`ffi::tests::learning_open_is_3_on_windows_and_picking_still_works`（只在 Windows 跑）。

### 2.6 測試的平台差異

- 測 symlink 或 Unix 權限行為本身的測試標 `#[cfg(unix)]`（例如 `core/tests/learn_store.rs` 整檔、`ffi.rs` 與 `lm.rs` 的 symlink 步驟）。
- 同一個測試只有幾步用 symlink 的，只把那幾步標 `#[cfg(unix)]`，其餘照跑：`lm::tests::load_opts_maps_the_trigram_table`（驗證 trigram 真的有 mmap、與讀進記憶體的分數逐位元相同、空檔／截斷／壞檔頭／目錄都拒絕）與 `ffi.rs` 的 `child_lm_tables`（損壞、截斷，懸空 symlink 只在 Unix）。
- 只拿 symlink 組模型資料夾的 fixture（`core/tests/common/toy.rs::lm_path` 等）有 `#[cfg(windows)]` 分支，改用 `fs::copy`；所以 engine_rerank、engine_fuzzy、engine_fuzzy_rerank 在 Windows 上照跑。
- 會呼叫 `learning_open` 而且預期成功的測試（engine_learn、engine_newword、engine_predict、engine_english、engine_english_next、engine_fuzzy_rerank 裡的那些）在 Windows 一律 `#[cfg(unix)]`；`engine_learn.rs` 自己宣告的 `getrusage` 那段也是。
- Windows 不能刪除或改寫正在被 mmap 的檔（Unix 可以）。三個測試在還持有 `Lm`／engine 時刪或改寫 trigram 表：`tri_tiny.rs` 的 `load_reads_each_profiles_table_beside_the_model`、`engine_lm.rs` 的 `load_lm_refuses_a_bad_trigram_table`、`ffi.rs` 的 `child_lm_tables`。處置：刪除或改寫之前，用只在 Windows 編譯的 `drop`（`#[cfg(windows)]`）放掉持有映射的物件，之後還要用到它的斷言在 Windows 上改成重新載入（`child_lm_tables` 另備一份沒被動過的複本資料夾載入引擎）。Unix 的行不動。
- f64 逐位元比對的 golden：`engine_digits.rs`（兩處）與 `pack_oov.rs`（一處）拿 macOS libm 的位元當答案，glibc 與 Windows 的 `log10` 在最後一位不同，所以跳過條件是 `!cfg!(target_os = "macos")`（Linux 照樣跳過）。其他測試在 Windows 上的浮點不同一律視為缺陷，不改容差。

### 2.7 換行

`.gitattributes` 對 `data/**`、`eval/**`、`core/tests/**`、`cli/tests/**`、`scripts/**` 設 `text=auto eol=lf`。

- 理由：Windows 預設 `core.autocrlf=true`，checkout 會變成 CRLF，`DigitTable::parse` 遇到 `\r` 會失敗，數字表會在沒有訊息的情況下關掉。
- 用 `text=auto`，git 判定為二進位的檔（`eval/golden/` 下的 `.sjlm`、`.sjc`、`.sjkn`、`.sjtl`、`.sjt3`）不當文字處理，位元組不會被改。
- Unix 上沒有作用。驗證：`git ls-files --eol` 對上述二進位檔顯示 index 欄 `-text`、同資料夾的 `.tsv`／`.txt` 顯示 `i/lf`（CI 的換行檢查見 §2.9）。

### 2.8 評測 CLI

Windows 只建 `shanjie-eval`（`cargo build -p cli --release --bin shanjie-eval`）。`shanjie-replay` 用 Unix 專用的 API，Windows 上不建。

### 2.9 CI

Linux `debug` job 在既有步驟之後加三步：

1. `cargo build --release --locked -p cli --bin shanjie-eval`；
2. `scripts/ci-rowstats.sh rowstats-linux`：跑六組評測（dev302 `--dev 302`、打字測驗 `eval/dev/user-typing.txt`、錯字回報 `eval/dev/user-reported.txt`，各在聊天與書面設定下），Windows 輸入法出貨的旗標 `--context --packs acg,it --tinylm data/lm/tinylm.sjtl`，聊天另加 `--colloquial-core data/lm/colloquial.sjlm`，`--threads 4`，各寫一份 `--rowstats`（`--threads` 不改輸出的任何位元組）；
3. `actions/upload-artifact` 上傳這六份。

`windows` job（映像 `windows-2025`，`needs: [changes, debug]`，`if` 與其他 job 相同，逾時 45 分鐘，所有步驟用 bash）：

1. Rust 1.97.1（與 Linux 比對的那一邊相同）；
2. checkout（`.gitattributes` 讓資料是 LF）；
3. `scripts/fetch-models.sh` 取模型並核對 `data/*.sha256`；Git Bash 沒有 `shasum` 時改用 `sha256sum -c`（同一種檢查檔格式）；
4. `cargo test -p core --release --locked -- --skip …`，跳過七個在 release 下斷言每鍵 p95 < 16 ms 的測試：`replay_standard_chat_production_path`、`replay_standard_302_of_302`、`replay_eten_302_of_302`、`replay_with_english_on`、`replay_with_english_on_and_the_revert_switches_off`、`replay_with_english_and_digits_on`、`perf_two_thousand_new_words_per_key`（4 vCPU 的 runner 量不準；它們的選字斷言在 Linux 與 macOS job 照跑）；
5. 建 `shanjie-eval`，`scripts/ci-rowstats.sh rowstats-windows` 產生同樣六份；
6. 下載 Linux 的 artifact，逐檔 `cmp`，數量必須是 6 對 6，任何一份不同就失敗；
7. 換行檢查：`git ls-files --eol data eval scripts core/tests cli/tests` 沒有 `w/crlf` 或 `w/mixed`，每個 `i/lf` 的檔工作區都是 `w/lf`；
8. 只編不測：`rustup target add i686-pc-windows-msvc aarch64-pc-windows-msvc` 後 `cargo build --locked -p core --target` 這兩個目標；
9. `shanjie-tip` 的建置與測試（§3.13）。

其他 job 不受影響。

### 2.10 已知限制

- Windows 沒有學習檔（§2.5）。
- Windows 上的每鍵 p95 斷言沒有在 CI 驗證（§2.9 第 4 步）。
- 對映中的 trigram 表不能被就地改寫（§2.2）；Windows 另外擋住刪除與 rename 覆蓋，更新模型檔要在引擎釋放之後。

## 3. TSF 文字服務（`windows/tip`）

### 3.1 crate、相依與建置

- 套件名 `shanjie-tip`，workspace 成員。核心以 path 相依靜態連進同一顆 DLL，相依名稱改為 `shanjie_core = { package = "core", path = "../../core" }`：核心的套件名 `core` 會遮住標準函式庫的 `::core`，而 `windows` 的程序巨集展開成 `::core::ffi::c_void` 這類路徑。呼叫寫 `shanjie_core::ffi::shanjie_*`。
- TIP **只呼叫 C ABI**（`shanjie_core::ffi::shanjie_*`，語意以 `core/include/shanjie.h` 為準），不用 `Engine` 的 Rust API：Mac 殼也只用這一層，`handled`、`commit`、`preedit`、`cursor_utf16` 的語意與 `catch_unwind` 都已寫在那裡。
- COM 用 `windows` 0.62 與 `windows-core` 0.62（`#[implement]`），只開用到的 feature，放在 `[target.'cfg(windows)'.dependencies]`；這兩個以外沒有新相依，`Cargo.lock` 鎖版本。
- 只出 x64（`x86_64-pc-windows-msvc`）。
- cargo feature `timing`（預設關）：數字計時檔（§3.11），只用於 VM 量測，不用於給使用者的建置。
- C 執行階段靜態連結：`.cargo/config.toml` 對 `cfg(all(windows, target_env = "msvc"))` 設 `-C target-feature=+crt-static`。理由：動態 CRT 會讓 DLL 相依 `VCRUNTIME140.dll`，沒裝 Visual C++ 可轉散發套件的乾淨 Windows 11 上 `LoadLibrary` 失敗（`regsvr32` 回 3）。這個設定套用到 workspace 裡所有 Windows MSVC 建置，評測 CLI 的輸出由 §2.9 的 rowstats 比對核對。每次建完 DLL 要用 `dumpbin /dependents` 核對沒有 `VCRUNTIME*` 相依。
- 風格：`unsafe` 區塊都有 `// SAFETY:` 註解；`Drop` 只釋放資源（§3.9）。

### 3.2 COM 進入點與註冊

- 匯出：`DllMain`（只記下模組的 `HINSTANCE`，什麼都不做）、`DllGetClassObject`（只認開發版 CLSID，其他回 `CLASS_E_CLASSNOTAVAILABLE`）、`DllCanUnloadNow`、`DllRegisterServer`、`DllUnregisterServer`。`IClassFactory` 不支援聚合（`CLASS_E_NOAGGREGATION`）。
- GUID 全放在 `windows/tip/src/guids.rs`：開發版的 CLSID、設定檔 GUID、顯示屬性 GUID；語言 0x0404（zh-TW）；設定檔名稱「善解（開發版）」。正式版使用另一組。
- 註冊（`DllRegisterServer`，需要系統管理員）：
  - `HKLM\SOFTWARE\Classes\CLSID\{CLSID}`：預設值為設定檔名稱；`InprocServer32` 預設值為 DLL 的完整路徑，`ThreadingModel` 為 `Apartment`。
  - `ITfInputProcessorProfileMgr::RegisterProfile`（語言 0x0404，啟用）。
  - `ITfCategoryMgr::RegisterCategory` 登記四個類別：`GUID_TFCAT_TIP_KEYBOARD`、`GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT`、`GUID_TFCAT_TIPCAP_COMLESS`、`GUID_TFCAT_DISPLAYATTRIBUTEPROVIDER`。
  - **不**登記 `TIPCAP_UIELEMENTENABLED`：沒有候選 UI，不能宣稱支援 UILess。也不登記 `TIPCAP_SECUREMODE`（安全桌面）：要登記得先過資安審查。
- 取消註冊（`DllUnregisterServer`）：逐個 `UnregisterCategory`、`UnregisterProfile`（盡力而為，結果忽略），再 `RegDeleteTree` 刪 CLSID 鍵（鍵不存在不算錯）；回傳值只看登錄刪除的結果。
- 驗證：`regsvr32 /s <dll>` 結束碼 0、讀登錄核對 CLSID 與設定檔鍵存在；`regsvr32 /s /u <dll>` 後鍵不在、輸入法清單沒有善解。

### 3.3 安裝位置與信任

- TIP 會被載進每個程序（含提升權限的），所以 DLL 與資料放在同一個只有系統管理員能寫的資料夾（開發版為 `C:\Program Files\shanjie-dev\`），`regsvr32` 只註冊這個路徑上的 DLL。DLL 或模型被一般使用者換掉就等於提權。
- 資料路徑從 DLL 自己的位置推：`GetModuleFileNameW`（緩衝不夠就加倍重取，最多 65536 單位，仍放不下或取不到就回 `None`，不拿截斷的前綴去推資料夾），資料在 DLL 旁的 `data\`（`lexicon`、`packs`、`lm\bigram.sjlm`、`lm\tinylm.sjtl`、`lm\colloquial.sjlm`）；不讀環境變數。模型檔要與 `data/*.sha256` 相符。
- 執行期不載入別的 DLL。
- 驗證：複製進 VM 後核對 DLL 與每個模型檔的雜湊；用 `Get-Acl` 核對 DLL 與整個資料夾樹，Users、Authenticated Users、Everyone、INTERACTIVE、ALL APPLICATION PACKAGES 都沒有寫入權（最多讀取與執行）。

### 3.4 引擎：一個程序一個

- 程序層級的單例（`Mutex`），第一次 `ActivateEx` 時**同步載入**，之後的 `ActivateEx` 沿用，直到程序結束都不釋放。載入期間宿主的 UI 執行緒會等。
- 建引擎的呼叫順序與值照 `cli::replay::build_engine_packs`（不是照 Mac App 的設定：App 的中英混打預設開，replay 是關的）：
  1. `shanjie_engine_new_packs(<data>\lexicon, layout 0, <data>\packs, 5)`（5 ＝ `SHANJIE_PACK_ACG | SHANJIE_PACK_IT`）；
  2. `load_lm(lm\bigram.sjlm)`、`load_tinylm(lm\tinylm.sjtl)`、`load_colloquial(lm\colloquial.sjlm)`；
  3. `set_demote(1)`、`set_prediction(0)`、`set_abbreviation(0)`、`set_english(0)`、`set_candidate_vertical(0)`、`set_rerank(0)`（各 setter 的回傳快照立刻釋放）；
  4. `set_learning(0)`。重排強度與設定檔類型用核心預設（標準、聊天）；不呼叫 `set_fuzzy`、`set_left_context`、`learning_open`。
- 中英混打關，所以數字層（`digits.tsv`）也不開；`1`、`0` 鍵照注音處理，與 replay 相同。
- 與 replay 的差別：TIP 呼叫 `set_rerank(0)`，會意與常談表照樣載入（記憶體是真實的），但不跑背景重排工作（`rerank_take/run/apply` 不使用）；replay 帶 `--no-tinylm` 時連會意與常談表都不載。兩者解出的字相同，因為會意與常談表只在重排工作裡起作用；另一個差別是凍結視窗 W（有沒有小模型時不同），只影響約 45 字以上的組字。
- 載入失敗（任何呼叫回非 0，例如缺檔、沙箱程序讀不到）：`shanjie_engine_free` 掉載入一半的引擎，這個程序的 TIP 從此不動作（`OnTestKeyDown` 一律回 FALSE），不重試，不留任何錯誤文字（`shanjie.h` 規定不能記 LM 路徑），`ActivateEx` 照樣回 `S_OK`。
- 狀態有一份不用鎖的鏡像（`UNLOADED`、`READY`、`INERT`），給 `OnTestKeyDown` 與 `DllCanUnloadNow` 讀。
- `DllCanUnloadNow`：只要曾嘗試載入引擎（不論成敗）就一律回 `S_FALSE`，因為 DLL 被卸載再載入會讓 static 從頭來、模型再載一次、舊的一份漏掉；否則在沒有存活物件與 `LockServer` 鎖時回 `S_OK`。
- **鎖**：只用 `try_lock`。拿不到（例如別的執行緒正在同步載入）就當作不吃，絕不讓 UI 執行緒等。拿鎖範圍只有「呼叫核心、複製輸出」，改文件時已放掉鎖，所以 TSF 同步回呼我們的 sink 時不會在同一執行緒重複拿鎖。鎖 poisoned 時，這個程序的 TIP 從此不動作，不 `unwrap`。
- 引擎指標需要 `unsafe impl Send`；旁邊有編譯期斷言「核心的引擎型別本身是 `Send`」，斷言不成立就不可寫這行 `unsafe impl`。
- **組字的擁有者**：TSF 是每個執行緒一個 thread manager，`explorer.exe` 這類程序有好幾個 UI 執行緒共用這個引擎。單例旁記下目前組字的擁有者（OS 執行緒 ID 加上 `ITfContext` 的指標值）。別的擁有者送來按鍵時，先 `reset(mode 1)`（丟掉，**不能用 mode 0**：那會把前一個視窗的字送進這個視窗），擁有者換成新的，再照常處理。`OnSetFocus`、`OnKillThreadFocus`、`Deactivate`、`OnCompositionTerminated` 與文件改動失敗只在擁有者相符時才 reset 引擎。panic 之後則無條件 reset 並清掉擁有者。
- 驗證：兩個視窗不串字（§3.13 (e2)）。

### 3.5 TIP 實例與 TSF 介面

每個 UI 執行緒一個 `TextService` 實例（`#[implement]` 了 `ITfTextInputProcessorEx`、`ITfThreadMgrEventSink`、`ITfThreadFocusSink`、`ITfKeyEventSink`、`ITfCompositionSink`、`ITfDisplayAttributeProvider`），所有文件修改都在 edit session（`ITfEditSession`）裡做。

- `ActivateEx`：記下 thread manager 與 client id；開計時檔（§3.11）；旗標有 `TF_TMAE_SECUREMODE` 時這個執行緒不動作（不載引擎、不註冊 sink）；否則 `ensure_loaded`，註冊 sink（`AdviseKeyEventSink`、`ITfThreadMgrEventSink`、`ITfThreadFocusSink`），向 `ITfCategoryMgr::RegisterGUID` 取顯示屬性的 atom，把鍵盤隔間設成開。註冊 sink 失敗會撤銷已註冊的並回傳失敗。
- `Deactivate`：結束自己的組字（§3.8）、撤銷 sink、清掉狀態，之後不動作。
- 實作的 sink 與用途：`OnTestKeyDown`／`OnKeyDown`（`OnKeyUp`、`OnTestKeyUp`、`OnPreservedKey` 一律不吃）、`OnCompositionTerminated`、`ITfThreadMgrEventSink::OnSetFocus`（換文件時結束組字）、`ITfThreadFocusSink::OnKillThreadFocus`（換到別的執行緒時結束組字）。
- **鍵盤開關隔間**（`GUID_COMPARTMENT_KEYBOARD_OPENCLOSE`）：`ActivateEx` 時設成開；每一份文件（document manager）**第一次**拿到焦點時（`OnSetFocus`、`OnSetThreadFocus`）也設成開，因為 Windows 的開關狀態跟著視窗走，新開的視窗可能是關的；之後回到同一份文件時保留當下的值，不覆蓋使用者自己改的狀態（例如在該視窗按 Ctrl+Space），與微軟注音的中英模式各視窗分開記一致。這個隔間為關時，`OnTestKeyDown`、`OnKeyDown` 一律直通。目前 TIP 沒有自己的中英切換，所以預設一直是中文模式。
- 停用的 context：`GUID_COMPARTMENT_KEYBOARD_DISABLED` 或 `GUID_COMPARTMENT_EMPTYCONTEXT` 為真時（例如密碼欄這類 context），`OnTestKeyDown`、`OnKeyDown` 都直通。輸入範圍 `IS_PASSWORD` 的處理尚未做。
- 這個實例未啟用（`ActivateEx` 之前、`Deactivate` 之後、安全模式）、或引擎不可用時，同樣直通。

### 3.6 按鍵對應與 `OnTestKeyDown`

- 虛擬鍵碼換成 `ShanjieKey`：`VK_SPACE` 2、`VK_RETURN` 3、`VK_BACK` 4、`VK_DELETE` 5、`VK_ESCAPE` 6、`VK_LEFT` 7、`VK_RIGHT` 8、`VK_UP` 9、`VK_DOWN` 10、`VK_HOME` 11、`VK_END` 12、`VK_TAB` 13。
- `VK_PRIOR`、`VK_NEXT` 不送核心：核心只在直排候選窗開著時才收這兩個鍵（`shanjie.h`），目前沒有候選窗。表外的鍵（功能鍵、數字鍵盤、`VK_PROCESSKEY` 等）換不出來，直通。
- 字元鍵（`0`–`9`、`A`–`Z`、`VK_OEM_1`–`VK_OEM_7`、`VK_OEM_PLUS`、`VK_OEM_MINUS`、`VK_OEM_COMMA`、`VK_OEM_PERIOD`）送 kind 1，`ch` 是美式鍵盤上該鍵**不按 Shift** 的字元（`a`、`1`、`,`、`;`、`` ` ``、`\` …）。標準注音排列依實體鍵位，與目前的鍵盤配置無關，所以用固定的表，不呼叫 `ToUnicode`。
- 修飾鍵：Shift → bit0、Ctrl → bit1，Caps Lock 為開時帶 bit4（`GetKeyState` 的 toggle 位元；核心第 1 條直通）。帶 Alt 或 Win 的鍵（含 AltGr＝Ctrl+Alt）不送核心，`pfEaten = FALSE`。
- **`OnTestKeyDown` 不推進引擎**，只回答「可能會吃」：這個實例有開著的組字時，任何換得出來的鍵都回 TRUE；沒有組字時，只有沒帶 Ctrl 的字元鍵與空白鍵、以及 Ctrl+\（核心第 1 條的例外，修飾位元恰為 Ctrl）回 TRUE，其他回 FALSE，鍵直接交給 App。
- 驗證：`keys::tests`（`special_keys_map_to_their_kind`、`page_keys_and_unlisted_keys_are_not_sent`、`character_keys_carry_the_unshifted_keycap`、`modifier_bits`、`alt_and_win_keys_are_not_converted`、`on_test_key_down_with_an_empty_composition`、`on_test_key_down_with_a_composition`）。

### 3.7 `OnKeyDown` 與文件的改動

1. 把鍵換成 `ShanjieKey`（§3.6）；換不出來、或前面任何直通條件成立：`pfEaten = FALSE`，不呼叫核心。
2. 用 `RequestEditSession(TF_ES_SYNC | TF_ES_READWRITE)` 開 edit session。**在 edit session 裡**依序：拿鎖 → `shanjie_engine_key` → 複製輸出 → 放鎖 → 照輸出改文件。同步請求被拒時不呼叫核心、`pfEaten = FALSE`、引擎狀態不變。
3. 照輸出改文件，順序固定（`doc::apply`）：
   - `commit` 非空：有開著的組字就用它取代組字範圍（先清底線屬性）、游標移到其後、結束組字；沒有組字就插在選取處（`InsertTextAtSelection`）。
   - `preedit` 非空：沒有組字就在選取處開始組字（`InsertTextAtSelection(TF_IAS_QUERYONLY)` 取範圍再 `StartComposition`）；把組字範圍的文字設成 `preedit`、套上底線屬性（`GUID_PROP_ATTRIBUTE` 的值為顯示屬性 atom，`VT_I4`）、選取（游標）設在 `cursor_utf16`（核心與 TSF range 都以 UTF-16 單位計）。
   - `preedit` 為空且仍有開著的組字（例如組字中 Backspace 刪光、Esc 清空）：先清底線屬性，把組字範圍設成空字串，再結束組字；不先清空的話，結束組字會把範圍裡的文字留在文件裡。
4. `handled = 1`：`pfEaten = TRUE`。`handled = 0`：做完第 3 步（先插入 `commit`）之後 `pfEaten = FALSE`，App 接著收到這個鍵（「先送出組字再把按鍵交給 App」）。`shanjie_engine_key` 回非 0：當成 `handled = 0`、不改文件。
5. 輸出開了候選窗（`candidate_selected >= 0`）時，因為沒有候選 UI，馬上在同一個鎖內再送一個 ESC（核心第 7 條：關閉候選、不改變），把兩個輸出合併（`commit` 串接，`preedit` 與游標用 ESC 的），原本的鍵算已處理。所以組字中按空白鍵或 ↓ 看起來不動作。ESC 失敗則 reset 引擎並清空 `preedit`。
6. 每個 `ShanjieOutput` 在同一個呼叫裡複製完要用的欄位就 `shanjie_output_free`。
7. 文件改動失敗：結束手上的組字、`reset` 引擎（雙方都丟掉，避免文件與引擎狀態不一致）；edit session 的工作沒做完（例如 panic）同樣清掉。
8. 如果這個實例留著組字、但單例的擁有者已不是它（`took_over`）：先結束那個組字（同一 context 在目前的 edit session 內結束；不同 context 則排進非同步 edit session），再處理這個鍵。引擎狀態已經丟掉的組字不能沿用，否則會把使用者看得到的字換成新的 `preedit`。
- 驗證：§3.13。

### 3.8 結束組字

- **一律先拿掉底線屬性**：每一條會結束組字的路（`commit`、Backspace／Esc 刪光、`OnSetFocus`、`OnKillThreadFocus`、`Deactivate`）都在同一個 edit session 裡先清掉組字範圍的 `GUID_PROP_ATTRIBUTE`，再 `EndComposition`。不清的話，處理 TSF 的宿主可能照樣在送出的字下面畫虛線。
- `OnCompositionTerminated`（App 自己結束組字）：文字已照顯示留在文件裡，只清掉底線屬性、並 `reset(mode 1)`（僅當擁有者相符）。
- `OnSetFocus` 換文件、`OnKillThreadFocus`、`Deactivate`：每個實例**自己**結束自己開的組字（用非同步寫入的 edit session：`TF_ES_ASYNCDONTCARE | TF_ES_READWRITE`，能立刻跑就立刻跑，否則排隊），顯示的文字留在文件裡（等於送出畫面上的字），擁有者相符時 `reset(mode 1)`。不論這個實例還是不是擁有者都要結束自己的組字。
- 失焦時是送出或取消，微軟注音沒有量到；目前行為是送出畫面上的字。

### 3.9 COM 邊界

- 每個 COM 方法的本體都包在同一個包裝裡（`guard`）：`Dll*` 匯出函式、`IClassFactory` 的方法、`DoEditSession`、各 sink。
- 包裝做三件事：進入時用 `Once` 裝上不印 payload 的 panic hook（`DllMain` 不做事，所以不能靠它裝；同 `core/src/ffi.rs` 的 `quiet_panics`）；`catch_unwind`；panic 時回 `E_FAIL`（按鍵方法則 `pfEaten = FALSE`、不回錯誤碼），接住 panic 之後對引擎 `reset(mode 1)` 並丟掉手上的組字物件。
- 失敗一律只回不帶訊息的 HRESULT（`Error::from_hresult`），不用帶訊息字串的 `windows::core::Error`：帶訊息的錯誤會掛進 `IErrorInfo` 或交給偵錯器。
- `Drop` 在 windows-core 產生的 `Release` 裡執行，不在包裝裡，從那裡 panic 出去會讓宿主 abort，所以 `Drop` 只釋放資源、不做會失敗的事。
- 核心在 `panic = "abort"` 的設定下建置會失敗，所以這個 DLL 一直是 unwind 的 panic 策略。

### 3.10 顯示：組字底線與介面層級

- 組字底線用 TSF 的顯示屬性（`ITfDisplayAttributeProvider`、`IEnumTfDisplayAttributeInfo`、`ITfDisplayAttributeInfo`）：`lsStyle = TF_LS_DASH`（虛線）、文字／背景／線條顏色皆 `TF_CT_NONE`（由 App 照文字顏色畫）、`bAttr = TF_ATTR_INPUT`。虛線與微軟注音的量測一致（§4.4）。
- 屬於「系統原生元件」這一級；輸入法清單裡的名稱與切換方式都是系統的。目前 TIP 不畫任何視窗。
- 組字中「目前詞段」的藍底標示（微軟注音有，§4.4）與像素級的外觀尚未實作。

### 3.11 計時檔（量測用）

- 只在開了 cargo feature `timing` 的建置裡有（CI 兩種都建）。每行三欄 `<事件代碼>\t<開始>\t<結束>\n`，**只有數字、tab、換行**；開始與結束是同一個時鐘（`QueryPerformanceCounter` 換成微秒）。格式函式 `format_line` 永遠編譯，檔案只在 `timing` 下存在。
- 事件代碼：1 `ActivateEx`（第一次含引擎載入）；2 引擎載入（`new_packs` 之前到 §3.4 最後一個呼叫回來）；3 每個送進核心的按鍵（進入 `OnKeyDown` 到 edit session 改完文件、回傳之前）。
- 診斷事件（兩欄都是當下時間，除非另註）：40 `OnTestKeyDown` 回 TRUE（兩欄為 OS 執行緒 ID）；41–44 `OnTestKeyDown` 回 FALSE 的原因（實例未啟用、引擎不可用、鍵盤隔間關著、context 停用或為空）；45 `OnKeyDown` 的同步 edit session 沒拿到（兩欄為 HRESULT）；46 引擎的鎖拿不到；47 `ActivateEx` 註冊 sink 失敗；48 `ActivateEx` 在安全模式；49 `ActivateEx` 進來的執行緒（兩欄為執行緒 ID）；50 進入 `OnKeyDown`（兩欄為執行緒 ID）；51–55 `OnKeyDown` 在呼叫核心之前就回不吃的原因（實例未啟用、引擎不可用、鍵盤隔間關著、context 停用或為空、鍵沒有對應）；56 焦點換過來時鍵盤隔間讀到關、被 TIP 重新打開。都只有數字，不含任何按鍵或文字。
- **只有完整性等級剛好是 Medium 的程序才寫**（程序 token 的 `TokenIntegrityLevel`，RID 0x2000）：High、Low 與 AppContainer 的程序一律不寫。理由：同一個使用者的一般程序改得了該使用者的資料夾（例如把中間的資料夾換成 junction），提升權限的程序照那條路徑建檔就是跨權限寫檔；限定 Medium 後寫入不跨權限。
- 位置：目前使用者的 `%LOCALAPPDATA%\shanjie-dev\timing\timing-<pid>.txt`，資料夾路徑用 `SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_NO_PACKAGE_REDIRECTION)` 取，不讀環境變數。封裝的宿主（例如 MSIX 封裝的記事本）寫 AppData 可能被檔案系統虛擬化而導到 `%LOCALAPPDATA%\Packages\<套件>\LocalCache\Local\shanjie-dev\timing\`，讀取時兩個位置都要看。
- 資料夾由驅動腳本事先建立，TIP 不建；開檔用 `CREATE_NEW`（不開已存在的檔）。資料夾不存在、檔案已存在、開不了或程序不是 Medium 時，這個程序就不寫計時，`ActivateEx` 照常成功，不顯示、不記錄任何錯誤。
- 檔案在第一次 `ActivateEx` 時開（每個程序一次），每行寫完就 flush；寫入用 `try_lock`，鎖忙時那一行直接略過，寫入失敗則關閉該程序的計時。
- 驗證：`timing::tests::a_line_has_three_numeric_columns`；VM 裡讀回每個計時檔，內容只有 `[0-9\t\n]`、每行三欄、事件代碼只有上面列出的、每行開始 ≤ 結束。

### 3.12 R2（隱私）

組字、送出的文字、候選、按鍵都不寫進任何 log、檔案、`OutputDebugString` 或錯誤訊息；唯一的輸出是 §3.11 的計時檔，裡面只有數字。`Key` 含有打出的字元，所以不實作 `Debug`。panic 訊息可能夾帶打字內容，所以 panic hook 什麼都不印（§3.9）。

### 3.13 驗證方式

CI（`windows` job 最後兩步）：

- `cargo build --release --locked -p shanjie-tip`，再加 `--features timing` 建一次；
- `cargo test --release --locked -p shanjie-tip`：單元測試只測純函式（鍵碼對應表、`OnTestKeyDown` 判斷、計時行格式）；COM 與 TSF 不在 CI 裡起，靠 VM 實測。
- R2 行為測試（`r2_tests`，子程序的寫法同 `core/src/ffi.rs` 的子程序測試）：
  - `r2_panic_marker_never_reaches_stdout_or_stderr`：子程序什麼都不先裝，直接呼叫正式的包裝（`guard::com`），在裡面 panic（訊息帶標記字串）。斷言包裝回 `E_FAIL`（panic 沒穿出去）、`GetErrorInfo` 回 `S_FALSE`（沒有掛上錯誤物件）、子程序的 stdout 與 stderr 都找不到標記；正向對照：另一個子程序用預設 hook、不經包裝 panic，標記必須出現在 stderr。
  - `r2_panic_marker_never_reaches_the_debug_output`：父程序以 `DEBUG_ONLY_THIS_PROCESS` 開子程序，收 `OUTPUT_DEBUG_STRING_EVENT`，斷言沒有標記；子程序先送一個 `R2CHILDRAN` 證明真的跑了；正向對照的子程序自己 `OutputDebugStringW(標記)`，父程序必須收到（UTF-8 與 UTF-16LE 兩種都找）。
- macOS 與 Linux 上 `shanjie-tip` 是空的 crate，照樣編得過。

VM 實測（Hyper-V、zh-TW Windows 11 乾淨檢查點；按鍵從主機送進 VM 主控台、用縮圖截圖、PowerShell Direct 讀狀態；每次跑從同一個檢查點還原）：

- 測試句：取 `eval/dev/user-typing.txt` 第一個「前文空、沒有標點、至少 6 字，且 `shanjie-replay --no-tinylm --learn off --strict` 解對」的列；按鍵由讀音依標準排列換算。
- (a0) 安裝位置：複製到 `C:\Program Files\shanjie-dev\`，核對雜湊與 ACL（§3.3）。(a) `regsvr32 /s`（逾時 60 秒，結束碼 0）、讀登錄、把設定檔加進使用者清單、重開；切到善解後計時檔必須出現事件 1。
- (b) 記事本：打測試句，Enter 之前有底線的組字，Enter 之後文字在記事本裡、沒有底線，讀回與預期逐字相同。(c) 直通：組字區空時 Enter 交給 App（多一個換行）；打 ㄋㄧˇ 再按 Tab，內容結尾是「你\t」（先送出再放行）。(d) 組字中 Backspace、Esc 清空，文件不變。(e) 「執行」對話框（`explorer.exe`）打同一句，逐字相同。(e2) 同一程序的兩個記事本視窗不串字：第一個視窗打 ㄋㄧˇ 不送出，到第二個視窗打 ㄏㄠˇ Enter，第二個視窗只有「好」；回第一個視窗，「你」留在文件裡、沒有底線；再打 ㄏㄠˇ Enter 得到「你好」。
- (g) R2 掃描：計時檔格式（見 §3.11）；計時資料夾只有 `timing-*.txt`；安裝資料夾雜湊不變；`CrashDumps` 與 WER 的 `ReportQueue`、`ReportArchive` 沒有新項目；以 UTF-8 與 UTF-16LE 在 `%TEMP%`、計時資料夾與安裝資料夾搜尋測試句，都找不到。(h) 取消註冊後登錄鍵不在、重開後清單沒有善解、記事本照樣能用微軟注音。
- 量測（延遲是 TIP 裡面花的時間，不含 App 畫字；同步載入、DLL 內核心的量測值）：記事本與 `explorer.exe` 多佔的私有記憶體各約 300 MB（切到善解前到打完 12 字後：24.7 → 325.3 MB 與 57.7 → 368.9 MB），模型載入 2.0–2.7 秒（即切換的整段），首鍵延遲 0.09–12.5 ms，每鍵延遲中位數 0.06–0.88 ms、p95 3.4–5.5 ms、最大 4.3–12.5 ms；同一台 VM 的微軟注音在記事本打同一句，私有記憶體約 24.4 → 24.3 MB。

### 3.14 已知限制與範圍外

- 按鍵行為目前是核心現行的規則（Mac 版）。Windows 版的預設應照微軟注音（§4.6 的差異表），Mac 做法留成設定可選；這組規則與開關、單按 Shift 切中英、Ctrl+Space、各視窗各自記中英模式、Caps Lock 開著時打字母的行為都尚未實作。
- 沒有候選窗、預測列、UILess（`ITfCandidateListUIElement`）、UIA、`EVENT_OBJECT_IME_*`，所以組字中按空白鍵或 ↓ 看起來不動作（§3.7 第 5 步）。
- 沒有背景重排（`rerank_take/run/apply`）、背景載入或常駐程序：引擎在每個宿主程序內同步載入，約 300 MB 私有記憶體與 2–2.7 秒的首次載入（§3.13 量測）；程序不結束，模型不釋放。
- 沒有學習檔（§2.5）、設定檔與設定頁、語言列或工作列的模式圖示。
- 沒有安裝程式、更新與簽章；DLL 未簽章。給使用者的建置不能開 `timing`（每個按鍵的時間戳就是打字節奏），要用看得到的方式驗證（例如 feature 開著時多匯出一個符號，發佈流程檢查它不存在），而不是掃原始碼。
- 只出 x64；x86、ARM64 的 TIP、Chrome、Office、遊戲等其他宿主的相容性尚未驗證；CI 裡沒有離螢幕的 TSF 測試宿主。
- 組字外觀的像素對照與「目前詞段」標示尚未實作。
- 有記憶體上限的程序裡配置失敗會直接 abort，crash dump 內會含組字；這是所有輸入法共有的限制。

## 4. 微軟注音參考（量測）

用途：Windows 版按鍵與介面的對照依據。這些是內建微軟注音的實測行為，不是善解的行為。

### 4.1 量測環境

- Windows 11 25H2 專業版（zh-TW），乾淨的 Hyper-V Gen2 測試 VM，每一輪從同一個檢查點還原；內建的微軟注音，預設版本（沒有切「使用舊版」），VM 上只有這一個輸入法；解析度 1920×1080。
- 按鍵從主機送進 VM 主控台（和實體鍵盤一樣經過輸入法），VM 裡沒有安裝任何工具。
- 截圖來自 Hyper-V 的縮圖介面，是 16 位元 RGB565：顏色只能精確到這個精度，下面的色值是讀值，不是精確的十六進位值。
- 其他確認過的行為：「執行」對話框（Win32 文字框）開起來是英文模式，新開的記事本（Windows 11 新版）是中文模式，亦即中英模式**每個視窗各自記**；單按 Shift 切中英，在組字中按時組字留著、只切到英文；組字中按 ↓ 叫出候選窗。
- 輸入法會學最近選的字：在候選窗選了某字，下次同一個讀音它排第一；預設不顯示聯想詞。

### 4.2 按鍵表

記事本，淺色、100%。狀態：S0 組字區空；S1 有未完成音節（ㄋ）；S2 組字中（ㄋㄧˇ＝「你」）；S3 候選窗開著（S2 再按 ↓）。「組字」＝還在輸入法裡、有底線；「送出」＝進了記事本。

| 鍵 | S0 組字區空 | S1 未完成音節 ㄋ | S2 組字中「你」 | S3 候選窗開著 |
|---|---|---|---|---|
| 空白 | 直接送出半形空白 | 不動作 | 在組字裡接一個空白 | 翻到下一頁 |
| Enter | 換行 | 不動作（ㄋ 留著） | 送出「你」 | 選目前那個字、關窗，**仍在組字**（不送出） |
| Shift+Enter | 換行 | 不動作 | 送出「妳」（第二個候選） | 關窗，組字留著 |
| Esc | 不動作 | 清掉 ㄋ | 清掉整段組字 | 關窗，組字留著 |
| Backspace | 不動作 | 清掉 ㄋ | 刪掉「你」（整個音節） | 不動作 |
| Delete | 不動作 | 不動作 | 不動作 | 不動作 |
| Tab | 插入 Tab | 不動作 | 不動作 | 候選窗展開成三欄（全部 25 項，最後一項是注音「ㄋㄧˇ」本身） |
| Shift+Tab | 插入 Tab | 不動作 | 不動作 | 同 Tab，展開 |
| ↑ | 不動作 | 不動作 | 「你」換成「擬」 | 跳到最後一頁、選最後一項 |
| ↓ | 不動作 | 不動作 | 開候選窗 | 選下一項 |
| ← | 不動作 | 不動作 | 游標移到「你」前面 | 不動作 |
| → | 不動作 | 不動作 | 不動作 | 展開成三欄（同 Tab） |
| Home | 不動作 | 不動作 | 游標移到組字開頭 | 不動作 |
| End | 不動作 | 不動作 | 不動作 | 跳到最後一頁、選最後一項 |
| Page Up | 不動作 | 不動作 | 不動作 | 跳到最後一頁（循環）、選第一項 |
| Page Down | 不動作 | 不動作 | 不動作 | 下一頁 |
| 1 2 5 8 9 | 開新音節（ㄅ ㄉ ㄓ ㄚ ㄞ） | 聲母鍵取代 ㄋ（ㄅ ㄉ ㄓ）；韻母鍵併進音節（ㄋㄚ、ㄋㄞ） | 組字後面開新音節 | 選第 n 個、關窗，仍在組字，選的字排到第一 |
| 3 4 6 7（聲調） | 組字放聲調符號（ˇ ˋ ˊ ˙） | ㄋ＋聲調，整段反藍標示（不成字） | 組字後面接聲調符號 | 選第 3、4、6、7 個（同數字鍵） |
| 單按 Shift | 切英文 | 清掉 ㄋ、切英文 | 切英文，「你」留在組字裡 | 關窗、切英文，組字留著 |
| Shift+A | 送出「A」 | 丟掉 ㄋ、送出「A」 | 組字裡接「A」 | 不動作 |
| `,`（ㄝ） | 組字 ㄝ | 併成 ㄋㄝ | 組字後面接 ㄝ | 不動作 |
| `.`（ㄡ） | 組字 ㄡ | 併成 ㄋㄡ | 組字後面接 ㄡ | 不動作 |
| Shift+, | 送出「<」 | 丟掉 ㄋ、送出「<」 | 組字裡接「<」 | 不動作 |
| Shift+. | 送出「>」 | 丟掉 ㄋ、送出「>」 | 組字裡接「>」 | 不動作 |
| Ctrl+, | 組字放一個全形標點 | 丟掉 ㄋ、組字放同一個標點 | 組字後面接同一個標點 | 關窗，組字後面接同一個標點 |
| Caps Lock | 不動作 | 不動作 | 不動作 | 不動作 |
| Ctrl+Space | 切英文 | 清掉 ㄋ、切英文 | 切英文，組字留著 | 關窗、切英文，組字留著 |
| Win+Space | 不動作（只有一個輸入法） | 不動作 | 不動作 | 不動作 |

送出「你」之後（S4）和一般打字相同：空白、Enter、Tab、方向鍵、Home、End 交給記事本；注音鍵、數字鍵、`,` `.` 開新的組字；Shift+A、Shift+, Shift+. 直接送出；Ctrl+, 開組字放標點。

組字區上限：連打音節到第 31 個之前，狀態列都停在第 1 欄，之後陸續前移；推論（沒有直接量到組字裡有哪些字）約 31 個音節，超過時把前面的字送出。畫面上輸入法用藍底標示目前的詞段。

### 4.3 候選窗外觀

候選窗跟著系統主題，不跟著 App：深色時記事本是深色、「執行」對話框（Win32）仍是淺色，但兩邊的候選窗都是深色。直排，每頁 9 列，編號 1–9；選取列左側有一條選取條；右側有捲動點，底部有 ▲▼ 兩個翻頁鈕和兩個圖示。窗出現在插入點正下方，左緣和組字的字對齊；「執行」對話框在螢幕下半部時，候選窗開在組字的**上方**。尺寸（像素）：

| | 100% | 150% |
|---|---|---|
| 候選窗外框 | 142 × 323 | 215 × 486（約 1.5 倍） |
| 邊框 | 1 | 2 |
| 邊框到內容的留白 | 4 | 6 |
| 每列間距 | 32 | 48 |
| 選取列的底色塊 | 高 28（上下各留 2）、寬 132、圓角 | 高 42 |
| 選取條（左側直條） | 寬 3 | 沒量 |
| 底部列（▲▼、兩個圖示） | 高 28，上方 1 px 分隔線 | 高 42，分隔線 2 |
| 外框圓角 | 約 8 | 沒量 |
| 展開成三欄後的寬 | 約 306（高不變） | 約 461（高不變） |

直排每頁 9 列：留白 4＋9 × 32＝292，加分隔線與底部列，正好是外框高 323；150% 同樣的算式。展開（Tab 或 →）：同一個窗往右長成三欄、共 25 項，選取色塊只蓋住第一欄那一格，底部列的翻頁鈕從 ▲▼ 換成 ◀▶。

顏色（RGB565 讀值）：

| | 淺色 | 深色 |
|---|---|---|
| 窗底 | #FFFBFF | #292C29 |
| 邊框 | #EFEBEF | #181C18 |
| 選取列底色 | #F7F3F7 | #393839 |
| 選取條 | #0065C6 | #4AC3FF |
| 候選字 | #181818 | #FFFFFF |
| 編號 | #5A5D5A（選取列 #5A595A） | #CECFCE（選取列 #D6D3D6） |
| 底部列的分隔線 | #EFEBEF | #393C39 |

### 4.4 組字底線

組字底線是虛線：100% 時高 2 px，線段 3 px、間隔 3 px，顏色和字相同（淺色黑、深色白），在字的正下方、中間沒有空隙。組字中目前的詞段另外用藍底標示。

### 4.5 設定頁預設值

微軟注音的設定在「設定 › 時間與語言 › 語言與地區 › 微軟注音」，分五個子頁：一般、按鍵指派、學習、進階、使用資料收集。登錄（`HKCU\Software\Microsoft\IME\15.0\IMETC`）的預設值：

| 登錄值 | 預設 |
|---|---|
| Default Input Mode | 0 |
| Left Shift Usage／Right Shift Usage | 0／1 |
| Enable Fuzzy Input | 0 |
| Prompt Associate Phrase.Phonetic | 1 |
| Fixed Candidate Order.New Phonetic | 0 |
| Enable New Phrase Learning、Enable Personal Regulating、Enable User Defined Phrases | 1 |
| Enable Punctuation Auto Finalize | 0 |
| Enable Intelligent Auto Input Switch | 0 |
| Output Big5 Only | 0 |
| Include Extension A／B Characters、Include HKSCS Characters | 0 |
| Keyboard Layout Setting.New Phonetic | 0x00020010 |

各子頁選項的預設（畫面上的字）：

| 子頁 | 選項 | 預設 |
|---|---|---|
| 一般 | 預設輸入模式 | 中文 |
| 一般 | 選取輸入字元類型 | 繁體中文 |
| 一般 | 使用 Shift + 空格鍵切換字元寬度 | 開啟 |
| 一般 | 用標點符號完成輸入字元 | 關閉 |
| 一般 | 自動將中文模式中的按鍵順序切換為英數字元 | 關閉 |
| 一般 | 選取候選字型大小 | 普通 |
| 一般 | 提示輸入字元的相關片語 | 關閉 |
| 一般 | 根據相近讀音組轉換注音 | 關閉 |
| 一般 | 使用嗶聲來通知輸入錯誤 | 開啟 |
| 一般 | 使用 IME 工具列 | 關閉 |
| 一般 | 使用舊版的微軟注音 | 關閉 |
| 按鍵指派 | 鍵盤配置 | 標準 |
| 按鍵指派 | 使用左側／右側 Shift 鍵 | 切換輸入模式 |
| 學習 | 根據我輸入的字調整第一個候選字 | 開啟 |
| 學習 | 固定候選字視窗中的候選字順序 | 關閉 |
| 學習 | 讓 IME 根據我輸入的字學習造詞 | 開啟 |
| 學習 | 啟用使用者造詞詞典 | 開啟 |
| 進階 | 字元集 | 標準 Unicode（Big5 為另一選項） |
| 進階 | 包含擴充 A、擴充 B、香港增補字符集、更多 CNS11643 字元 | 都不勾 |
| 使用資料收集 | 將資料儲存到檔案、自動將資料傳送給 Microsoft | 關閉（灰的，需到系統的「改進手寫筆跡與鍵入辨識」調整） |

對照：「提示輸入字元的相關片語」預設關閉，所以送出後沒有聯想詞（登錄的 `Prompt Associate Phrase.Phonetic` 是 1，可見它對應的不是這個畫面選項，推論為舊版注音用的值）；「根據我輸入的字調整第一個候選字」預設開、「固定候選字順序」預設關，所以選過的字排到第一；左右 Shift 預設都是切換輸入模式，與單按 Shift 切英文一致。

### 4.6 與善解 Mac 版不同的地方

左欄是上面量到的微軟注音，右欄是善解 Mac 版現行的按鍵規則（見 `docs/spec/keys-and-composition.md`）。兩邊相同的格不列（例如 S2 的 Enter、Esc、Backspace，S3 的 Esc、數字鍵，S0 的聲調鍵）。Windows 版的預設應照微軟注音，Mac 版的做法留成設定可選（尚未實作，§3.14）。

| 情境 | 微軟注音 | 善解 Mac 版 |
|---|---|---|
| 候選窗預設 | 直排、每頁 9 個 | 橫排（設定「直排」預設關） |
| S1 Enter | 不動作，ㄋ 留著 | 送出畫面上的整串，含未完成的符號 |
| S1 Shift＋字母 | 丟掉 ㄋ、送出那個字母 | 忽略（中英混打關著時） |
| S2 空白 | 在組字裡接一個空白 | 開候選窗 |
| S2 Shift+Enter | 送出第二個候選（「妳」） | 送出組字，再把換行交給 App |
| S2 ↑ | 把字換成另一個候選（「擬」） | 忽略 |
| S2 Shift＋字母 | 字母接進組字 | 送出組字，再把按鍵交給 App（中英混打關著時） |
| S0／S2 Shift+, 、Shift+. | 送出或接進半形「<」「>」 | 全形標點「，」「。」插進組字區 |
| Ctrl+, | 組字放一個全形標點 | 不處理，直接交給 App |
| S3 Backspace | 不動作 | 關候選窗 |
| S3 ↑ | 跳到最後一頁的最後一項（循環） | 上一個；直排在第一個時不動 |
| S3 Tab、→ | 候選窗展開成三欄 | Tab：關窗、送出組字、把 Tab 交給 App；→：下一個 |
| S3 Page Up | 跳到最後一頁（循環） | 直排：往前 9 個，已在最前面時不動；橫排（預設）：核心不處理，走表外鍵路徑 |
| S3 ↓ | 選下一項 | 橫排（預設）：展開成網格；直排：下一個 |
| S3 空白 | 下一頁 | 橫排：下一頁；直排：下一個 |
| S1 空白 | 不動作 | 完成音節 |
| S1 Shift+Enter | 不動作 | 送出 ㄋ，再把換行交給 App |
| S2 Tab | 不動作 | 送出組字，再把 Tab 交給 App |
| Ctrl+Space | 任何狀態都切英文（S1 清掉 ㄋ、S3 關窗，組字留著） | 不處理，直接交給 App |
| 組字區上限 | 約 31 個音節（推論） | 128 個單位（`MAX_SYLLABLES`），到了送出整段並清空 |
