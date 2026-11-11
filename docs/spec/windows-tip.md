# Windows 版：核心移植與 TSF 文字服務

這份寫的是現在的行為。

範圍：核心（`core`）在 `x86_64-pc-windows-msvc` 上編譯與測試的做法、Windows 的 CI、TSF 文字服務 DLL（`windows/tip`，套件名 `shanjie-tip`）、常駐程序（`windows/host`）與兩者之間的線上格式（`windows/proto`）、以及微軟注音的量測參考（Windows 版按鍵與介面的對照依據）。按鍵規則本身見 `docs/spec/keys-and-composition.md`，核心的 C 介面見 `docs/spec/c-abi.md`，隱私規則見 `docs/spec/privacy.md`。

## 1. 組成與現況

### 1.1 組成

- `core`：同一份核心程式碼，Windows 上加少量 `cfg`，行為與 macOS、Linux 相同（§2）。
- `windows/host`：常駐程序 `shanjie-host.exe`，每個使用者的每個登入工作階段一支，只有它載入模型（§5）。
- `windows/proto`：DLL 與常駐程序之間的線上格式，純 Rust、不分平台（§5.3）。
- `windows/tip`：TSF 文字服務 DLL，常駐程序的薄客戶端，不相依核心，自己用 Direct2D 與 DirectWrite 畫候選窗（§3.15）。`crate-type = ["cdylib", "rlib"]`（rlib 給單元測試），除了兩個純函式模組（`keys`、`candlayout`，各平台都編、都測）之外都在 `cfg(windows)` 裡，在 macOS 與 Linux 上其餘是空的，不影響既有的建置與測試。
- CI 的 `windows` 與 `windows-cap` job，以及 `windows` 與 Linux `debug` job 的 rowstats 比對（`rowstats` job，§2.9）。
- 微軟注音量測（§4）：只是參考資料，程式不讀它。

### 1.2 現況

- 核心與它全部的測試在 Windows 上編得過、測得過，評測 CLI 的逐列結果和 Linux 逐位元組相同。
- TSF 文字服務可在測試 VM 的記事本與「執行」對話框打出一句注音並送出，送出的字和 Mac 上 `shanjie-replay --packs acg,it`（Windows 版的詞包，見 §3.4）用同一串鍵解出的字逐字相同。
- 模型只在常駐程序裡載一份；DLL 每按一鍵經 named pipe 同步問常駐程序（§5）。
- 組字中按空白鍵或 ↓ 開出直排候選窗（每頁 9 列、編號 1–9），數字鍵、Enter 選字，Esc 關窗；窗在 DLL 裡畫，所以開始功能表與 Windows 搜尋也看得到（§3.15）。
- 按鍵預設照微軟注音（核心的按鍵習慣 1，§3.4；不照微軟的列在 §4.6）；登錄值 `KeyScheme` 設 0 改回 Mac 版的規則，還沒有設定頁。中英切換尚未實作（§3.14）。
- 沒有預測列、候選窗的底部列與滑鼠操作、設定、學習檔、安裝程式與簽章（§3.14）。

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
3. `actions/upload-artifact` 上傳這六份（artifact `rowstats-linux`）。

`windows` job（映像 `windows-2025`，`needs: changes`，和其他 job 同時開跑，`if` 與其他 job 相同，逾時 45 分鐘，所有步驟用 bash）：

1. Rust 1.97.1（與 Linux 比對的那一邊相同）；
2. checkout（`.gitattributes` 讓資料是 LF）；
3. `scripts/fetch-models.sh` 取模型並核對 `data/*.sha256`；Git Bash 沒有 `shasum` 時改用 `sha256sum -c`（同一種檢查檔格式）；
4. 核心套件除了 `core/tests/engine_cap.rs` 以外的全部測試（`cargo test -p core --release --locked --lib --bins --test <每個其他測試檔> -- --skip …`，測試檔清單由 `core/tests/*.rs` 的檔名產生，再另跑 `--doc`；`engine_cap.rs` 在並行的 `windows-cap` job 跑），跳過七個在 release 下斷言每鍵 p95 < 16 ms 的測試：`replay_standard_chat_production_path`、`replay_standard_302_of_302`、`replay_eten_302_of_302`、`replay_with_english_on`、`replay_with_english_on_and_the_revert_switches_off`、`replay_with_english_and_digits_on`、`perf_two_thousand_new_words_per_key`（4 vCPU 的 runner 量不準；它們的選字斷言在 Linux 與 macOS job 照跑）；
5. 建 `shanjie-eval`，`scripts/ci-rowstats.sh rowstats-windows` 產生同樣六份；
6. 這六份以 artifact `rowstats-windows` 上傳；
7. 換行檢查：`git ls-files --eol data eval scripts core/tests cli/tests` 沒有 `w/crlf` 或 `w/mixed`，每個 `i/lf` 的檔工作區都是 `w/lf`；
8. 只編不測：`rustup target add i686-pc-windows-msvc aarch64-pc-windows-msvc` 後 `cargo build --locked -p core --target` 這兩個目標；
9. `shanjie-tip` 的建置與測試（§3.13）。

`windows-cap` job（映像 `windows-2025`，`needs: changes`，`if` 與其他 job 相同，逾時 30 分鐘，所有步驟用 bash）：Rust 1.97.1、checkout、`scripts/fetch-models.sh`，然後 `cargo test -p core --release --locked --test engine_cap`（這個檔沒有逐鍵 p95 的測試，一個都不跳過）。

`rowstats` job（ubuntu-24.04，`needs: [changes, debug, windows]`，`if` 與其他 job 相同，逾時 5 分鐘）下載兩邊的 artifact，逐檔 `cmp`，數量必須是 6 對 6，任何一份不同就失敗。`debug` 與 `windows` 都成功時它一定會跑；其中一個失敗時它被略過，但整個 run 照樣失敗。

其他 job 不受影響。

### 2.10 已知限制

- Windows 沒有學習檔（§2.5）。
- Windows 上的每鍵 p95 斷言沒有在 CI 驗證（§2.9 第 4 步）。
- 對映中的 trigram 表不能被就地改寫（§2.2）；Windows 另外擋住刪除與 rename 覆蓋，更新模型檔要在引擎釋放之後。

## 3. TSF 文字服務（`windows/tip`）

### 3.1 crate、相依與建置

- 套件名 `shanjie-tip`，workspace 成員。DLL **不相依核心**、不載模型，只相依 `shanjie-win-proto`（§5.1）；`cargo tree -p shanjie-tip` 沒有 `core`，`dumpbin /exports` 沒有 `shanjie_` 開頭的匯出。`dumpbin /dependents` 列得到 `d2d1.dll` 與 `dwrite.dll`：候選窗用靜態匯入，不 delay-load、不用裸名稱 `LoadLibrary`（兩者不在 KnownDLLs）。
- 核心在常駐程序裡（`windows/host`），以 path 相依靜態連入，相依名稱改為 `shanjie_core = { package = "core", path = "../../core" }`：核心的套件名 `core` 會遮住標準函式庫的 `::core`，而 `windows` 的程序巨集展開成 `::core::ffi::c_void` 這類路徑。常駐程序**只呼叫 C ABI**（`shanjie_core::ffi::shanjie_*`，語意以 `core/include/shanjie.h` 為準），不用 `Engine` 的 Rust API：Mac 殼也只用這一層，`handled`、`commit`、`preedit`、`cursor_utf16` 的語意與 `catch_unwind` 都已寫在那裡。
- COM 用 `windows` 0.62 與 `windows-core` 0.62（`#[implement]`），只開用到的 feature（候選窗另開 `Win32_Graphics_Direct2D`、`Win32_Graphics_Direct2D_Common`、`Win32_Graphics_DirectWrite`、`Win32_Graphics_Dxgi_Common`、`Win32_Graphics_Gdi`、`Win32_UI_Accessibility`、`Win32_UI_HiDpi`），放在 `[target.'cfg(windows)'.dependencies]`；這兩個以外沒有新相依，`Cargo.lock` 鎖版本。
- 只出 x64（`x86_64-pc-windows-msvc`）。
- cargo feature `timing`（預設關）：數字計時檔（§3.11），只用於 VM 量測，不用於給使用者的建置。
- C 執行階段靜態連結：`.cargo/config.toml` 對 `cfg(all(windows, target_env = "msvc"))` 設 `-C target-feature=+crt-static`。理由：動態 CRT 會讓 DLL 與常駐程序相依 `VCRUNTIME140.dll`，沒裝 Visual C++ 可轉散發套件的乾淨 Windows 11 上 `LoadLibrary` 失敗（`regsvr32` 回 3）。這個設定套用到 workspace 裡所有 Windows MSVC 建置，評測 CLI 的輸出由 §2.9 的 rowstats 比對核對。每次建完 DLL 與常駐程序要用 `dumpbin /dependents` 核對沒有 `VCRUNTIME*`、`MSVCP*`、`api-ms-win-crt-*`、`ucrtbase` 相依。
- 風格：`unsafe` 區塊都有 `// SAFETY:` 註解；`Drop` 只釋放資源（§3.9）。

### 3.2 COM 進入點與註冊

- 匯出：`DllMain`（只記下模組的 `HINSTANCE`，什麼都不做）、`DllGetClassObject`（只認開發版 CLSID，其他回 `CLASS_E_CLASSNOTAVAILABLE`）、`DllCanUnloadNow`、`DllRegisterServer`、`DllUnregisterServer`。`IClassFactory` 不支援聚合（`CLASS_E_NOAGGREGATION`）。
- GUID 全放在 `windows/tip/src/guids.rs`：開發版的 CLSID、設定檔 GUID、顯示屬性 GUID；語言 0x0404（zh-TW）；設定檔名稱「善解（開發版）」。正式版使用另一組。
- 註冊（`DllRegisterServer`，需要系統管理員）：
  - `HKLM\SOFTWARE\Classes\CLSID\{CLSID}`：預設值為設定檔名稱；`InprocServer32` 預設值為 DLL 的完整路徑，`ThreadingModel` 為 `Apartment`。
  - `ITfInputProcessorProfileMgr::RegisterProfile`（語言 0x0404，啟用）。
  - `ITfCategoryMgr::RegisterCategory` 登記四個類別：`GUID_TFCAT_TIP_KEYBOARD`、`GUID_TFCAT_TIPCAP_IMMERSIVESUPPORT`、`GUID_TFCAT_TIPCAP_COMLESS`、`GUID_TFCAT_DISPLAYATTRIBUTEPROVIDER`。
  - **不**登記 `TIPCAP_UIELEMENTENABLED`：候選窗是 DLL 自己畫的（§3.15），沒有 `ITfCandidateListUIElement`，不能宣稱支援 UILess。也不登記 `TIPCAP_SECUREMODE`（安全桌面）：要登記得先過資安審查。
- 取消註冊（`DllUnregisterServer`）：逐個 `UnregisterCategory`、`UnregisterProfile`（盡力而為，結果忽略），再 `RegDeleteTree` 刪 CLSID 鍵（鍵不存在不算錯）；回傳值只看登錄刪除的結果。
- 驗證：`regsvr32 /s <dll>` 結束碼 0、讀登錄核對 CLSID 與設定檔鍵存在；`regsvr32 /s /u <dll>` 後鍵不在、輸入法清單沒有善解。

### 3.3 安裝位置與信任

- TIP 會被載進每個程序（含提升權限的），所以 DLL、常駐程序 `shanjie-host.exe` 與資料放在同一個只有系統管理員能寫的資料夾（開發版為 `C:\Program Files\shanjie-dev\`），`regsvr32` 只註冊這個路徑上的 DLL。DLL、常駐程序或模型被一般使用者換掉就等於提權。
- 常駐程序的路徑從 DLL 自己的位置推，資料路徑從常駐程序自己的位置推：`GetModuleFileNameW`（緩衝不夠就加倍重取，最多 65536 單位，仍放不下或取不到就回 `None`，不拿截斷的前綴去推資料夾），資料在常駐程序旁的 `data\`（`lexicon`、`packs`、`lm\bigram.sjlm`、`lm\tinylm.sjtl`、`lm\colloquial.sjlm`）；正式版不讀環境變數（只有帶 `timing` 的量測版讀一個開關，§5.2）。模型檔要與 `data/*.sha256` 相符。
- 執行期不載入別的 DLL。
- 驗證：複製進 VM 後核對 DLL 與每個模型檔的雜湊；用 `Get-Acl` 核對 DLL 與整個資料夾樹，Users、Authenticated Users、Everyone、INTERACTIVE、ALL APPLICATION PACKAGES 都沒有寫入權（最多讀取與執行）。

### 3.4 引擎：在常駐程序裡

- 引擎只在常駐程序裡、只有一個（§5）。DLL 不載入任何模型。
- 常駐程序先建好 pipe 再載模型（§5.2）。建引擎的呼叫順序與值照 `cli::replay::build_engine_packs`（不是照 Mac App 的設定：App 的中英混打預設開，replay 是關的）：
  1. `shanjie_engine_new_packs(<data>\lexicon, layout 0, <data>\packs, 5)`（5 ＝ `SHANJIE_PACK_ACG | SHANJIE_PACK_IT`；replay 不給 `--packs` 時是 29，多了 Mac App 預設開的迷因與美妝詞包，Windows 版不帶它）；
  2. `load_lm(lm\bigram.sjlm)`、`load_tinylm(lm\tinylm.sjtl)`、`load_colloquial(lm\colloquial.sjlm)`（`load_lm` 一併讀同一資料夾的承接、trigram 與辨類檔，所以 `data\lm` 要放 `data/*.sha256` 列的全部模型）；
  3. `set_demote(1)`、`set_prediction(0)`、`set_abbreviation(0)`、`set_english(0)`、`set_candidate_vertical(1)`（候選窗一律是直排，§3.15）、`set_key_scheme(<按鍵習慣>)`（見下一條）、`set_rerank(0)`（各 setter 的回傳快照立刻釋放）；
  4. `set_learning(0)`。重排強度與設定檔類型用核心預設（標準、聊天）；不呼叫 `set_fuzzy`、`set_left_context`、`learning_open`。
- **按鍵習慣**（`set_key_scheme`，`docs/spec/keys-and-composition.md` §4.1.1）：0 是核心現行的規則（Mac 版），1 是微軟注音的做法（`m` 規則，§4.6）；沒有設定值時是 1，Windows 版的預設照微軟注音。值在 `server::run` 讀一次（§5.2：`HKCU\Software\shanjie-dev\KeyScheme`），當參數傳給 `engine::load`，`engine::load` 本身不讀登錄，所以測試不受跑測試那台機器的登錄影響。所有按鍵行為都由核心算，Windows 這一側只把值經 `Welcome` 轉給 DLL，供 `OnTestKeyDown` 判斷（§3.6）。
- 中英混打關，所以數字層（`digits.tsv`）也不開；`1`、`0` 鍵照注音處理，與 replay 相同。
- 與 replay 的差別：`set_rerank(0)`，會意與常談表照樣載入，但不跑背景重排工作；replay 帶 `--no-tinylm` 時連會意與常談表都不載。兩者解出的字相同，因為會意與常談表只在重排工作裡起作用；另一個差別是凍結視窗 W（有沒有小模型時不同），只影響約 45 字以上的組字。
- 載入失敗或引擎的鎖 poisoned：常駐程序結束（§5.2），不留任何錯誤文字（`shanjie.h` 規定不能記 LM 路徑）。
- 引擎指標需要 `unsafe impl Send`；旁邊有編譯期斷言「核心的引擎型別本身是 `Send`」，斷言不成立就不可寫這行 `unsafe impl`。
- **組字的擁有者**：好幾個 App、`explorer.exe` 這類程序的好幾個 UI 執行緒共用這個引擎。常駐程序記下目前組字的擁有者 `(連線 ID, ctx)`（§5.3）。別的擁有者送來按鍵時，先 `reset(mode 1)`（丟掉，**不能用 mode 0**：那會把前一個視窗的字送進這個視窗），擁有者換成新的，再照常處理，回應帶 `took_over`。DLL 在 `OnSetFocus`、`OnKillThreadFocus`、`Deactivate`、`OnCompositionTerminated` 與文件改動失敗時送 `Release`，panic 之後送 `Reset` 並關連線；常駐程序只在擁有者相符時 reset。
- 輸出開了候選窗（`candidate_selected >= 0`）時，常駐程序在同一個鎖內把可見的列、選取、`candidate_first`、`candidate_total` 複製進 `Out`（§5.3）；不補任何按鍵。`candidate_selected` 是 −1 時一律不複製候選（核心在 −1 而數量大於 0 的是預測列，Windows 版的預測關著）。候選窗的狀態在核心裡，窗開著時下一個按鍵照常進核心。
- 驗證：兩個視窗不串字（§3.13 (e2)），§5.11 第 10 點的單元測試。

### 3.5 TIP 實例與 TSF 介面

每個 UI 執行緒一個 `TextService` 實例（`#[implement]` 了 `ITfTextInputProcessorEx`、`ITfThreadMgrEventSink`、`ITfThreadFocusSink`、`ITfKeyEventSink`、`ITfCompositionSink`、`ITfDisplayAttributeProvider`），所有文件修改都在 edit session（`ITfEditSession`）裡做。

- `ActivateEx`：記下 thread manager 與 client id；開計時檔（§3.11）；旗標有 `TF_TMAE_SECUREMODE` 時這個執行緒不動作（不載引擎、不註冊 sink）；否則註冊 sink（`AdviseKeyEventSink`、`ITfThreadMgrEventSink`、`ITfThreadFocusSink`），向 `ITfCategoryMgr::RegisterGUID` 取顯示屬性的 atom，把鍵盤隔間設成開。註冊 sink 失敗會撤銷已註冊的並回傳失敗。
- `Deactivate`：結束自己的組字（§3.8）、撤銷 sink、清掉狀態，之後不動作。
- 實作的 sink 與用途：`OnTestKeyDown`／`OnKeyDown`（`OnKeyUp`、`OnTestKeyUp`、`OnPreservedKey` 一律不吃）、`OnCompositionTerminated`、`ITfThreadMgrEventSink::OnSetFocus`（換文件時結束組字）、`ITfThreadFocusSink::OnKillThreadFocus`（換到別的執行緒時結束組字）。
- **鍵盤開關隔間**（`GUID_COMPARTMENT_KEYBOARD_OPENCLOSE`）：`ActivateEx` 時設成開；每一份文件（document manager）**第一次**拿到焦點時（`OnSetFocus`、`OnSetThreadFocus`）也設成開，因為 Windows 的開關狀態跟著視窗走，新開的視窗可能是關的；之後回到同一份文件時保留當下的值，不覆蓋使用者自己改的狀態（例如在該視窗按 Ctrl+Space），與微軟注音的中英模式各視窗分開記一致。這個隔間為關時，`OnTestKeyDown`、`OnKeyDown` 一律直通。目前 TIP 沒有自己的中英切換，所以預設一直是中文模式。
- 停用的 context：`GUID_COMPARTMENT_KEYBOARD_DISABLED` 或 `GUID_COMPARTMENT_EMPTYCONTEXT` 為真時（例如密碼欄這類 context），`OnTestKeyDown`、`OnKeyDown` 都直通。輸入範圍含 `IS_PASSWORD` 的 context 不開候選窗（§3.15）；按鍵與組字的處理不變。
- 這個實例未啟用（`ActivateEx` 之前、`Deactivate` 之後、安全模式）、或客戶端被擋住（斷路器、`Refuse { 1 }` 之後、`Refuse { 3 }` 的冷卻，或組字區空時連不上常駐程序，§5.4）時，同樣直通。

### 3.6 按鍵對應與 `OnTestKeyDown`

- 虛擬鍵碼換成 `ShanjieKey`：`VK_SPACE` 2、`VK_RETURN` 3、`VK_BACK` 4、`VK_DELETE` 5、`VK_ESCAPE` 6、`VK_LEFT` 7、`VK_RIGHT` 8、`VK_UP` 9、`VK_DOWN` 10、`VK_HOME` 11、`VK_END` 12、`VK_TAB` 13。
- `VK_PRIOR`（kind 14）、`VK_NEXT`（kind 15）**只在候選窗開著、而且沒有任何修飾鍵（含 Caps Lock）時**才換成按鍵：核心只在直排候選窗開著且沒有修飾鍵時收這兩個鍵（`shanjie.h`）。「候選窗開著」是這個 TIP 實例上一個 `Out` 的 `candidate_selected >= 0`；它在組字結束的每一條路（直通、`took_over`、`Release`、換焦點、停用、`OnCompositionTerminated`、改文件失敗、panic）都清掉，窗關了就不再認這兩個鍵。表外的鍵（功能鍵、數字鍵盤、`VK_PROCESSKEY` 等）換不出來，直通。
- 字元鍵（`0`–`9`、`A`–`Z`、`VK_OEM_1`–`VK_OEM_7`、`VK_OEM_PLUS`、`VK_OEM_MINUS`、`VK_OEM_COMMA`、`VK_OEM_PERIOD`）送 kind 1，`ch` 是美式鍵盤上該鍵**不按 Shift** 的字元（`a`、`1`、`,`、`;`、`` ` ``、`\` …）。標準注音排列依實體鍵位，與目前的鍵盤配置無關，所以用固定的表，不呼叫 `ToUnicode`。
- 修飾鍵：Shift → bit0、Ctrl → bit1，Caps Lock 為開時帶 bit4（`GetKeyState` 的 toggle 位元；核心第 1 條直通）。帶 Alt 或 Win 的鍵（含 AltGr＝Ctrl+Alt）不送核心，`pfEaten = FALSE`。
- **`OnTestKeyDown` 不推進引擎**，只回答「可能會吃」，函式是 `may_eat(scheme, composing, window, key)`：這個實例有開著的組字時，任何換得出來的鍵都回 TRUE；沒有組字時，剛好是核心在組字區空時會處理的鍵才回 TRUE（中英混打、預測關、標準排列），照按鍵習慣 `scheme`：沒帶修飾鍵的字元鍵（注音、聲調、標點鍵，規則 2、14、22a）、`docs/spec/keys-and-composition.md` §8.1 那幾個 Shift＋標點鍵、Ctrl+\（核心第 1 條的例外，修飾位元恰為 Ctrl）；`scheme` 1 時 Shift+, 與 Shift+. 回 FALSE（核心照規則 2m 直通，在「執行」對話框這類 IMM32 宿主裡不能先回 TRUE 再放行），Ctrl+, 與 Ctrl+. 回 TRUE（規則 1m）；其他回 FALSE，鍵直接交給 App，包括核心會直通的空白鍵、Shift＋英文字母與 Caps Lock 開著時的鍵（規則 1、22）。理由見 §5.4：IMM32 宿主裡 `OnTestKeyDown` 回 TRUE 而 `OnKeyDown` 放行的鍵會消失。**候選窗開著時**照「有組字」算（`composing || window`）：換得出來的鍵都回 TRUE，其中核心規則 3v–8 要吃的鍵（數字 1–9、↑↓←→、空白、Enter、Esc、Backspace、Page Up、Page Down）一個都不漏；Page Up、Page Down 在窗沒開著時不換成按鍵，所以那時回 FALSE（組字中也一樣）。
- **`scheme` 只來自這條連線的 `Welcome`**（§5.3），DLL 不讀登錄：`client::scheme()` 回這個執行緒目前連線的值，沒有連線時是 `None`。組字區空而還不知道 `scheme` 時：兩種 `scheme` 下都回 FALSE 的鍵（`may_eat_either`）不連線、直接回 FALSE；其他鍵先 `client::ready()`（連上，§5.4），再用連線的 `scheme` 判斷。組字中與窗開著時照上面，換得出來的鍵都回 TRUE，核心規則 1 會直通的鍵（Ctrl＋字母等）與規則 21 送出組字再直通的鍵在 IMM32 宿主裡被吞掉，沿用 §3.14 已接受的限制。
- 驗證：`keys::tests`（`special_keys_map_to_their_kind`、`unlisted_keys_are_not_sent`、`page_keys_map_only_with_a_window_open_and_no_modifier`、`character_keys_carry_the_unshifted_keycap`、`modifier_bits`、`alt_and_win_keys_are_not_converted`、`on_test_key_down_with_an_empty_composition`、`on_test_key_down_with_a_composition`、`on_test_key_down_with_a_window_open`、`on_test_key_down_in_scheme_1_with_an_empty_composition`、`a_composition_or_a_window_eats_every_mapped_key_in_both_schemes`、`without_a_connection_only_a_key_both_schemes_refuse_is_refused`，各平台都跑）；常駐程序 crate 的 `window_keys_match_the_core` 與 `may_eat_matches_the_core_in_both_schemes` 對真的核心、兩種 `scheme` 逐組合核對：每個對照表裡的虛擬鍵、數字、字母、標點鍵、頁鍵與表外鍵，乘上 Shift、Ctrl、Caps Lock 的每種組合（Alt、Win 不送）。組字區空：雙向，`may_eat` 回 TRUE 而核心沒有處理（`handled` 0、`commit` 空）的 0 個，核心處理而 `may_eat` 回 FALSE 的 0 個；有未完成音節、組字中與窗開著：只核對「核心處理的鍵 `may_eat` 都回 TRUE」；頁鍵只有無修飾鍵時換得出來。

### 3.7 `OnKeyDown` 與文件的改動

1. 把鍵換成 `ShanjieKey`（§3.6）；換不出來、或前面任何直通條件成立：`pfEaten = FALSE`，不呼叫核心。
2. 用 `RequestEditSession(TF_ES_SYNC | TF_ES_READWRITE)` 開 edit session。**在 edit session 裡**依序：送 `Key` 給常駐程序、等 `Out`（§5.4）→ 照輸出改文件。同步請求被拒時不送、`pfEaten = FALSE`、引擎狀態不變。沒拿到能用的回應時照 §5.6。
3. 照輸出改文件，順序固定（`doc::apply`）：
   - `commit` 非空：有開著的組字就用它取代組字範圍（先清底線屬性）、游標移到其後、結束組字；沒有組字就插在選取處（`InsertTextAtSelection`）。
   - `preedit` 非空：沒有組字就在選取處開始組字（`InsertTextAtSelection(TF_IAS_QUERYONLY)` 取範圍再 `StartComposition`）；把組字範圍的文字設成 `preedit`、套上底線屬性（`GUID_PROP_ATTRIBUTE` 的值為顯示屬性 atom，`VT_I4`）、選取（游標）設在 `cursor_utf16`（核心與 TSF range 都以 UTF-16 單位計）。
   - `preedit` 為空且仍有開著的組字（例如組字中 Backspace 刪光、Esc 清空）：先清底線屬性，把組字範圍設成空字串，再結束組字；不先清空的話，結束組字會把範圍裡的文字留在文件裡。
4. `handled = 1`：`pfEaten = TRUE`。`handled = 0`：做完第 3 步（先插入 `commit`）之後 `pfEaten = FALSE`，App 接著收到這個鍵（「先送出組字再把按鍵交給 App」）。核心回非 0（`Out` 的 `core_error`）：當成 `handled = 0`、不改文件。
5. 輸出開了候選窗（`candidate_selected >= 0`）時，edit session 裡再用 `ITfContextView::GetTextExt` 讀組字範圍的矩形、用 `GetWnd` 取視窗，連同 `Out` 的候選字複製出來（`cand::capture`）；窗本身在 edit session **之後**才建、畫、顯示、移動或隱藏（§3.15）。這個 TIP 實例記下「窗開著」（§3.6）。沒有候選窗的輸出隱藏窗並清掉這個記錄。
6. 常駐程序在同一個呼叫裡複製完要用的欄位就 `shanjie_output_free`。
7. 文件改動失敗：結束手上的組字（每一條錯誤路徑都先結束取出的組字，§5.7）、送 `Release`（雙方都丟掉，避免文件與引擎狀態不一致）；edit session 的工作沒做完（例如 panic）同樣清掉。
8. 如果這個實例留著組字、但回應說擁有者換過（`took_over`）：先結束那個組字（同一 context 在目前的 edit session 內結束；不同 context 則排進非同步 edit session），再處理這個鍵。引擎狀態已經丟掉的組字不能沿用，否則會把使用者看得到的字換成新的 `preedit`。
- 驗證：§3.13。

### 3.8 結束組字

- **一律先拿掉底線屬性**：每一條會結束組字的路（`commit`、Backspace／Esc 刪光、`OnSetFocus`、`OnKillThreadFocus`、`Deactivate`）都在同一個 edit session 裡先清掉組字範圍的 `GUID_PROP_ATTRIBUTE`，再 `EndComposition`。不清的話，處理 TSF 的宿主可能照樣在送出的字下面畫虛線。
- `OnCompositionTerminated`（App 自己結束組字）：傳進來的組字是這個實例的組字（比原始指標）時，文字已照顯示留在文件裡，只清掉底線屬性、並送 `Release`（常駐程序只在擁有者相符時 reset）。
- `OnSetFocus` 換文件、`OnKillThreadFocus`、`Deactivate`：每個實例**自己**結束自己開的組字（用非同步寫入的 edit session：`TF_ES_ASYNCDONTCARE | TF_ES_READWRITE`，能立刻跑就立刻跑，否則排隊），顯示的文字留在文件裡（等於送出畫面上的字），並送 `Release`（擁有者相符時常駐程序 `reset(mode 1)`）。不論這個實例還是不是擁有者都要結束自己的組字。
- 失焦時是送出或取消，微軟注音沒有量到；目前行為是送出畫面上的字。

### 3.9 COM 邊界

- 每個 COM 方法的本體都包在同一個包裝裡（`guard`）：`Dll*` 匯出函式、`IClassFactory` 的方法、`DoEditSession`、各 sink。
- 包裝做三件事：進入時用 `Once` 裝上不印 payload 的 panic hook（`DllMain` 不做事，所以不能靠它裝；同 `core/src/ffi.rs` 的 `quiet_panics`）；`catch_unwind`；panic 時回 `E_FAIL`（按鍵方法則 `pfEaten = FALSE`、不回錯誤碼），接住 panic 之後送 `Reset` 給常駐程序、關連線，並丟掉手上的組字物件。
- 失敗一律只回不帶訊息的 HRESULT（`Error::from_hresult`），不用帶訊息字串的 `windows::core::Error`：帶訊息的錯誤會掛進 `IErrorInfo` 或交給偵錯器。
- `Drop` 在 windows-core 產生的 `Release` 裡執行，不在包裝裡，從那裡 panic 出去會讓宿主 abort，所以 `Drop` 只釋放資源、不做會失敗的事。
- 核心在 `panic = "abort"` 的設定下建置會失敗，所以這個 DLL 一直是 unwind 的 panic 策略。

### 3.10 顯示：組字底線與介面層級

- 組字底線用 TSF 的顯示屬性（`ITfDisplayAttributeProvider`、`IEnumTfDisplayAttributeInfo`、`ITfDisplayAttributeInfo`）：`lsStyle = TF_LS_DASH`（虛線）、文字／背景／線條顏色皆 `TF_CT_NONE`（由 App 照文字顏色畫）、`bAttr = TF_ATTR_INPUT`。虛線與微軟注音的量測一致（§4.4）。
- 組字底線屬於「系統原生元件」這一級；輸入法清單裡的名稱與切換方式都是系統的。
- 候選窗屬於「自己做、外觀與行為接近原生」這一級（§3.15）：Windows 沒有讓第三方 TIP 交出候選清單、由系統畫出的元件；XAML 介面自己畫的候選清單只在那個介面裡（UILess），一般 Win32 App 不畫。WinUI 3 控制項要把 .NET 與 Windows App SDK 載進每一個 App，不可行；獨立程序的視窗（WinUI 3 或純 Win32）疊不上開始功能表與 Windows 搜尋的介面，所以用 Win32 popup 加 Direct2D 與 DirectWrite 畫在 DLL 裡，和 App 同一個程序。
- 組字中「目前詞段」的藍底標示（微軟注音有，§4.4）與像素級的外觀尚未實作。

### 3.11 計時檔（量測用）

- 只在開了 cargo feature `timing` 的建置裡有（CI 兩種都建）。每行三欄 `<事件代碼>\t<開始>\t<結束>\n`，**只有數字、tab、換行**；開始與結束是同一個時鐘（`QueryPerformanceCounter` 換成微秒）。格式函式 `format_line` 永遠編譯，檔案只在 `timing` 下存在。
- 事件代碼：1 `ActivateEx`；2 引擎載入（只有常駐程序寫：`new_packs` 之前到 §3.4 最後一個呼叫回來）；3 每個送進常駐程序的按鍵（DLL：進入 `OnKeyDown` 到 edit session 改完文件、**候選窗更新完**、回傳之前（顯示、移動、重畫或隱藏都回來之後；沒有候選窗變化時照舊）；常駐程序：收到到送回）。60–65、69–71 見 §5.8；66–68 見 §3.15。
- 診斷事件（兩欄都是當下時間，除非另註）：40 `OnTestKeyDown` 回 TRUE（兩欄為 OS 執行緒 ID）；41–44 `OnTestKeyDown` 回 FALSE 的原因（實例未啟用、客戶端被擋住、鍵盤隔間關著、context 停用或為空）；45 `OnKeyDown` 的同步 edit session 沒拿到（兩欄為 HRESULT）；47 `ActivateEx` 註冊 sink 失敗；48 `ActivateEx` 在安全模式；49 `ActivateEx` 進來的執行緒（兩欄為執行緒 ID）；50 進入 `OnKeyDown`（兩欄為執行緒 ID）；51–55 `OnKeyDown` 在送出之前就回不吃的原因（實例未啟用、客戶端被擋住、鍵盤隔間關著、context 停用或為空、鍵沒有對應）；56 焦點換過來時鍵盤隔間讀到關、被 TIP 重新打開。都只有數字，不含任何按鍵或文字。
- **只有完整性等級剛好是 Medium 的程序才寫**（程序 token 的 `TokenIntegrityLevel`，RID 0x2000）：High、Low 與 AppContainer 的程序一律不寫。理由：同一個使用者的一般程序改得了該使用者的資料夾（例如把中間的資料夾換成 junction），提升權限的程序照那條路徑建檔就是跨權限寫檔；限定 Medium 後寫入不跨權限。
- 位置：目前使用者的 `%LOCALAPPDATA%\shanjie-dev\timing\timing-<pid>.txt`，資料夾路徑用 `SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_NO_PACKAGE_REDIRECTION)` 取，不讀環境變數。封裝的宿主（例如 MSIX 封裝的記事本）寫 AppData 可能被檔案系統虛擬化而導到 `%LOCALAPPDATA%\Packages\<套件>\LocalCache\Local\shanjie-dev\timing\`，讀取時兩個位置都要看。
- 資料夾由驅動腳本事先建立，TIP 不建；開檔用 `CREATE_NEW`（不開已存在的檔）。資料夾不存在、檔案已存在、開不了或程序不是 Medium 時，這個程序就不寫計時，`ActivateEx` 照常成功，不顯示、不記錄任何錯誤。
- DLL 的檔案在第一次 `ActivateEx` 時開、常駐程序的在啟動時開（每個程序一次），每行寫完就 flush；DLL 寫入用 `try_lock`，鎖忙時那一行直接略過，常駐程序等鎖（§5.8）；寫入失敗則關閉該程序的計時。
- 驗證：`timing::tests::a_line_has_three_numeric_columns`；VM 裡讀回每個計時檔，內容只有 `[0-9\t\n]`、每行三欄、事件代碼只有上面列出的、每行開始 ≤ 結束（事件 70、71 的兩欄不是時間，不套這一條）。

### 3.12 R2（隱私）

組字、送出的文字、候選、按鍵都不寫進任何 log、檔案、`OutputDebugString`、視窗標題或錯誤訊息；唯一的輸出是 §3.11 的計時檔，裡面只有數字。`Key` 含有打出的字元，所以不實作 `Debug`。panic 訊息可能夾帶打字內容，所以 panic hook 什麼都不印（§3.9）。

### 3.13 驗證方式

CI（`windows` job 最後幾步，§5.10）：

- `shanjie-tip` 與 `shanjie-host` 各建帶與不帶 `timing` 兩種，核對匯入與匯出；
- `cargo test --release --locked -p shanjie-win-proto -p shanjie-tip -p shanjie-host`：DLL 的單元測試只測純函式（鍵碼對應表、`OnTestKeyDown` 判斷、計時行格式、模擬等級）；常駐程序的測試對真的伺服器與引擎（§5.11 第 10 點）；COM 與 TSF 不在 CI 裡起，靠 VM 實測。
- R2 行為測試（`r2_tests`，DLL 與常駐程序各一份，子程序的寫法同 `core/src/ffi.rs` 的子程序測試）：
  - `r2_panic_marker_never_reaches_stdout_or_stderr`：子程序什麼都不先裝，直接呼叫正式的包裝（`guard::com`），在裡面 panic（訊息帶標記字串）。斷言包裝回 `E_FAIL`（panic 沒穿出去）、`GetErrorInfo` 回 `S_FALSE`（沒有掛上錯誤物件）、子程序的 stdout 與 stderr 都找不到標記；正向對照：另一個子程序用預設 hook、不經包裝 panic，標記必須出現在 stderr。
  - `r2_panic_marker_never_reaches_the_debug_output`：父程序以 `DEBUG_ONLY_THIS_PROCESS` 開子程序，收 `OUTPUT_DEBUG_STRING_EVENT`，斷言沒有標記；子程序先送一個 `R2CHILDRAN` 證明真的跑了；正向對照的子程序自己 `OutputDebugStringW(標記)`，父程序必須收到（UTF-8 與 UTF-16LE 兩種都找）。
  - `r2_candidate_text_never_reaches_stdout_stderr_or_the_debug_output`：子程序真的畫一次候選窗（標記字串當候選字），先斷言窗存在、可見、標題長度 0、`hide` 之後不可見；父程序在 stdout、stderr 與偵錯輸出（兩種編碼）裡都找不到標記。
- macOS 與 Linux 上 `shanjie-tip` 只有 `keys`、`candlayout` 兩個純函式模組（單元測試照跑），`shanjie-host` 是空的 bin，照樣編得過。

VM 實測（常駐程序加上的步驟見 §5.11；Hyper-V、zh-TW Windows 11 乾淨檢查點；按鍵從主機送進 VM 主控台、用縮圖截圖、PowerShell Direct 讀狀態；每次跑從同一個檢查點還原）：

- 測試句：取 `eval/dev/user-typing.txt` 第一個「前文空、沒有標點、至少 6 字，且 `shanjie-replay --no-tinylm --learn off --strict` 解對」的列；按鍵由讀音依標準排列換算。
- (a0) 安裝位置：複製到 `C:\Program Files\shanjie-dev\`，核對雜湊與 ACL（§3.3）。(a) `regsvr32 /s`（逾時 60 秒，結束碼 0）、讀登錄、把設定檔加進使用者清單、重開；切到善解後計時檔必須出現事件 1。
- (b) 記事本：打測試句，Enter 之前有底線的組字，Enter 之後文字在記事本裡、沒有底線，讀回與預期逐字相同。(c) 直通：組字區空時 Enter 交給 App（多一個換行）；打 ㄋㄧˇ 再按 Tab，內容結尾是「你\t」（先送出再放行）。(d) 組字中 Backspace、Esc 清空，文件不變。(e) 「執行」對話框（`explorer.exe`）打同一句，逐字相同。(e2) 同一程序的兩個記事本視窗不串字：第一個視窗打 ㄋㄧˇ 不送出，到第二個視窗打 ㄏㄠˇ Enter，第二個視窗只有「好」；回第一個視窗，「你」留在文件裡、沒有底線；再打 ㄏㄠˇ Enter 得到「你好」。
- (g) R2 掃描：計時檔格式（見 §3.11）；計時資料夾只有 `timing-*.txt`；安裝資料夾雜湊不變；`CrashDumps` 與 WER 的 `ReportQueue`、`ReportArchive` 沒有新項目；以 UTF-8 與 UTF-16LE 在 `%TEMP%`、計時資料夾與安裝資料夾搜尋測試句，都找不到。(h) 取消註冊後登錄鍵不在、重開後清單沒有善解、記事本照樣能用微軟注音。
- 對照量測（常駐程序之前、核心在 DLL 內同步載入時；延遲是 TIP 裡面花的時間，不含 App 畫字）：記事本與 `explorer.exe` 多佔的私有記憶體各約 300 MB（切到善解前到打完 12 字後：24.7 → 325.3 MB 與 57.7 → 368.9 MB），模型載入 2.0–2.7 秒（即切換的整段），首鍵延遲 0.09–12.5 ms，每鍵延遲中位數 0.06–0.88 ms、p95 3.4–5.5 ms、最大 4.3–12.5 ms；同一台 VM 的微軟注音在記事本打同一句，私有記憶體約 24.4 → 24.3 MB。

### 3.14 已知限制與範圍外

- 按鍵行為由核心的按鍵習慣（`set_key_scheme`，§3.4）決定：預設 1（微軟注音的做法，§4.6 的差異表），登錄值 `KeyScheme` 設 0 改回 Mac 版的做法（改了要重新登入或結束常駐程序才生效；沒有設定頁，§5.2）。單按 Shift 切中英、Ctrl+Space、各視窗各自記中英模式、Caps Lock 開著時打字母的行為都尚未實作；組字中核心照規則 21 送出組字再直通的鍵（兩種 `scheme` 都有：例如不在 `docs/spec/keys-and-composition.md` §8.1 的 Shift＋數字鍵、Shift+-；`scheme` 0 另有 Shift＋字母、Tab、Shift+Tab），在「執行」對話框這類 IMM32 宿主裡會被吞掉（`OnTestKeyDown` 回 TRUE 而 `OnKeyDown` 放行的鍵會消失，§5.4，已接受的限制）。
- 沒有預測列、UILess（`ITfCandidateListUIElement`）、完整的 UIA 候選清單（候選窗只送 `EVENT_OBJECT_IME_*`）；候選窗沒有底部列（▲▼、兩個圖示）、捲動點、展開成三欄與滑鼠點選（§3.15、§4.6）。
- 沒有背景重排（`rerank_take/run/apply`）。常駐程序的限制與範圍外見 §5.2、§5.12。
- 沒有學習檔（§2.5）、設定檔與設定頁、語言列或工作列的模式圖示。
- 沒有安裝程式、更新與簽章；DLL 未簽章。給使用者的建置不能開 `timing`（每個按鍵的時間戳就是打字節奏），要用看得到的方式驗證（例如 feature 開著時多匯出一個符號，發佈流程檢查它不存在），而不是掃原始碼。
- 只出 x64；x86、ARM64 的 TIP、Chrome、Office、遊戲等其他宿主的相容性尚未驗證；CI 裡沒有離螢幕的 TSF 測試宿主。
- 組字外觀的像素對照與「目前詞段」標示尚未實作。
- 候選窗（§3.15）在測試 VM 沒量到的宿主：小算盤（AppContainer，切不到善解）、以系統管理員身分執行的記事本與密碼欄（量測時宿主沒有跑到前景）；密碼欄不開窗是程式的行為，在 VM 裡沒有核對到。
- 候選窗的其他限制：`GetTextExt` 讀不到組字矩形時（例如 `TS_E_NOLAYOUT`），候選窗是開著的狀態但不顯示，只能盲選；`core_error` 時 DLL 收起窗，核心那邊的窗可能還開著；DPI 不感知的 App 在 150% 時 `GetTextExt` 的座標可能是虛擬化後的邏輯座標，窗會偏位（沒有換算，沒量到）；候選窗開著時，核心不處理但 `OnTestKeyDown` 回 TRUE 的鍵（例如 Ctrl＋字母）在 IMM32 宿主裡會被吞掉，和組字中相同（§5.4）。
- 有記憶體上限的程序裡配置失敗會直接 abort，crash dump 內會含組字；這是所有輸入法共有的限制。
- 顯示比例切換之後的慢鍵（原因未查明，候選窗之後那一片待查）：測試 VM 的驗收裡，顯示比例從 100% 切到 150% 再切回之後，記事本第一輪重播有 6 個鍵的 DLL 端事件 3 超過 15 ms（最慢 308.6 ms），記事本三輪合計的 p95 是 17.6 ms；接著量的「執行」對話框 p95 是 2.0 ms。準備完就重播的對照量測（這一版與前一版的建置各兩趟、交替）四趟的 p95 都在 2.5 ms 以內、最慢的鍵 9.3 ms，但沒有重做縮放，所以沒有重現，也沒有排除。

### 3.15 候選窗（`windows/tip/src/cand.rs`、`candlayout.rs`）

DLL 在 App 自己的程序裡畫候選窗：Win32 popup 加 Direct2D 與 DirectWrite。窗**只顯示**：候選字、選取列、第一列在全部清單裡的位置都是核心在常駐程序裡算好、經 `Out` 送來的（§5.3）；DLL 不挑字、不改順序、不從 `candidates` 送出任何字。選字一律把按鍵送給常駐程序，由核心決定 `commit`；之後的滑鼠點選也只能送列的位置，不能送字串。

- **視窗**：
  - 每條 UI 執行緒最多一個。狀態放 `thread_local!`，用 `HWND` 比對；不把任何指標放進 `GWLP_USERDATA` 或 `cbWndExtra`（沙箱 App 的 Low 完整性視窗可被別的 Low 程序跨程序改寫）。
  - 建立時用 `ITfContextView::GetWnd` 取得 owner（取 `GA_ROOT`），**只有**它屬於目前執行緒（`GetWindowThreadProcessId`）才用；否則用 `GetFocus` 的 root（一樣要屬於目前執行緒），再不行就不設 owner。owner 在 `CreateWindowExW` 的 `hWndParent` 給；owner 換了就銷毀重建（owner 不能事後移轉）。對視窗呼叫 API 前先確認 `IsWindow` 而且屬於這條執行緒；`WM_NCDESTROY` 清掉存下的 `HWND`（owner 被毀時系統連帶毀掉候選窗）。
  - `WS_POPUP`；`WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_LAYERED`；類別名稱 `ShanjieDevCandidate`，不設標題。`WM_MOUSEACTIVATE` 回 `MA_NOACTIVATE`；顯示用 `SetWindowPos(HWND_TOPMOST, SWP_SHOWWINDOW | SWP_NOACTIVATE)`（不用 `ShowWindow`：程序第一次的 `ShowWindow` 可能被父程序在 STARTUPINFO 裡給的顯示狀態蓋掉），移動不帶啟用。
  - 視窗程序整段包 `guard::run`；只處理 `WM_MOUSEACTIVATE`、`WM_NCDESTROY`、`WM_SETTINGCHANGE`（lParam 非 null 才讀、最多讀到名稱加結尾 NUL）與自己的 `WM_TIMER`，其餘交給 `DefWindowProcW`（`WM_GETOBJECT` 也是）；不處理帶指標的 `WM_USER`／`WM_APP` 自訂訊息、不用 `SetProp`。
  - 視窗類別在第一次建窗時用 `Once` 註冊（DLL 自己的 `HINSTANCE`、不用 `CS_GLOBALCLASS`），不取消註冊，也不在 `DllMain` 呼叫 user32（§3.2）；註冊過之後 `DllCanUnloadNow` 一律回 `S_FALSE`（視窗程序在這個 DLL 裡）。
  - 建窗、顯示、`UpdateLayeredWindow` 都在 edit session **之後**做（避免在 edit session 裡處理訊息而重入 TSF）；edit session 裡只讀 `GetTextExt` 的矩形，把矩形與候選字複製出來。`OnCompositionTerminated` 在 edit session 裡執行，窗不在那裡動：它用視窗自己的計時器（`WM_TIMER`，不帶指標）在訊息迴圈裡隱藏。
  - 建窗前後用 `SetThreadDpiAwarenessContext` 切到 Per-Monitor V2、再切回原值，RAII 包住，任何路徑（含 panic）都還原；DLL 本身不宣告 DPI 感知。
- **畫法**：
  - `ID2D1DCRenderTarget`，類型 `D2D1_RENDER_TARGET_TYPE_SOFTWARE`（不把 GPU 驅動載進每個 App、沒有裝置遺失），每次更新現建；畫進 32 bpp 的 DIB（預乘 alpha、灰階文字抗鋸齒），用 `UpdateLayeredWindow`（`ULW_ALPHA`）送上去，圓角才有每像素 alpha。render target 建不起來就不開窗，按鍵照常處理。
  - 文字用 DirectWrite 的系統字型集合，字型 Microsoft JhengHei UI、地區 zh-TW；不用自訂的 font file loader、不從路徑或環境變數載字型。`DWRITE_WORD_WRAPPING_NO_WRAP` 加字元截斷（省略號）；視窗寬度上限是所在螢幕工作區寬度的一半。
  - Direct2D factory（multi-threaded、`D2D1_DEBUG_LEVEL_NONE`，所有建置都一樣）與 DirectWrite shared factory 整個程序各一份，由**第一個被吃掉的鍵**在背景執行緒建好，並排版一段固定文字讓系統字型集合與 UI 字型先載入（預熱；每個 GUI 程序都會載 TIP，多數不打字，所以不在 `ActivateEx` 建）。預熱只在連上常駐程序之後才會發生，那時 DLL 已不會卸載（§3.2），所以執行緒不會比模組活得久。候選字與編號的兩個文字格式每條 UI 執行緒建一次、之後每次更新重用（只依字級重建）。
  - 尺寸與顏色是 §4.3 的值，放在 `candlayout.rs`（尺寸）與 `cand.rs`（顏色）的具名常數，註解寫那一列；像素尺寸依視窗所在螢幕的 DPI 縮放（100% 是 96、150% 是 144；寬度在兩個量到的點之間直線內插），去掉底部列的外框是 142 × 294（100%）與 215 × 442（150%）。§4.3 沒量到的是：字型大小（候選字 16 px、編號 14 px，100%）、編號與文字在列內的位置、選取色塊的圓角（4 px），它們是暫定值，要照並排截圖校正。顏色用 §4.3 的 24 位元值，選取條高 14 px。
  - 列數是這一頁的候選數（最多 9，窗高隨列數），編號 1–9，選取列有底色塊與左側選取條。沒有捲動點、底部列、展開成三欄。
- **主題**：讀 `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize` 的 `AppsUseLightTheme`（讀不到，例如沙箱 App 被拒，就用淺色、不當錯誤），收到 `WM_SETTINGCHANGE`（`ImmersiveColorSet`）時重讀，下一次畫就換色。和微軟注音一樣跟 App 模式（§4.3）。
- **位置**（`candlayout::place`，純函式、飽和運算，各平台單元測試）：窗的左上角放在組字範圍 `GetTextExt` 矩形的左下角（左緣對齊組字起點）；下方放不下（超出那個螢幕的工作區）就改放在矩形上方（窗的下緣在矩形的上緣）；不超出工作區上緣；左右超出就往內推（窗比工作區寬時左緣優先）。螢幕與工作區用 `MonitorFromRect`、`GetMonitorInfoW`，DPI 用窗在組字位置時的 `GetDpiForWindow`。矩形用 `GetTextExt` 給的座標，不另外換算 DPI 虛擬化。
- **顯示與隱藏**：
  - 每次 `Out` 回來：`candidate_selected >= 0` 就更新內容並顯示（先 `UpdateLayeredWindow` 換上新內容、再顯示，不閃出上一個欄位的候選）；畫不出來（factory、建窗、螢幕資訊、排版或 `UpdateLayeredWindow` 任何一步失敗）就隱藏，不留著上一頁的候選；−1 就隱藏。組字結束、`OnSetFocus` 換文件、`OnKillThreadFocus`、`OnCompositionTerminated`、改文件失敗、panic 之後都隱藏；`Deactivate` 銷毀。
  - 顯示、隱藏、內容變更時用 `NotifyWinEvent` 送 `EVENT_OBJECT_IME_SHOW`、`EVENT_OBJECT_IME_HIDE`、`EVENT_OBJECT_IME_CHANGE`（微軟輸入法規範的 light dismiss；窗不收輸入）。完整的 UIA 候選清單不做。
  - 密碼欄：context 的輸入範圍（`GUID_PROP_INPUTSCOPE`）含 `IS_PASSWORD` 時不開窗（組字與按鍵照常）。
  - 私人資料：輸入範圍含 `IS_PRIVATE`（61，Windows 10 起；瀏覽器的私密視窗會設）時照常開窗。這個值要文字服務不學習、不留下紀錄（Firefox 在私密視窗設它，是為了不讓輸入法學到私密視窗裡打的字；Windows 的 Recall 也照它不存紀錄），不是不顯示候選字；候選窗只顯示正在打的字，和組字底線一樣，關掉它私密視窗裡就打不了中文。Windows 版目前完全不學習（§2.5、§3.4），沒有要關的東西；之後 Windows 版開學習時，`IS_PRIVATE` 的 context 不學，寫進那一片的契約。
- **候選字的保存與清除**：`Out.candidates` 畫進 DIB 後就丟，不留到下一次重畫；DIB 與 DC 在窗收下之後立刻釋放，隱藏時先換上 1 × 1 的透明內容再 `SW_HIDE`；`Show` 不 derive `Debug`。R2：候選字不進任何 log、計時檔、錯誤訊息、視窗標題、視窗屬性、`OutputDebugString`；範圍涵蓋 DIB 與 `NotifyWinEvent`。崩潰傾印裡可能有候選字（和組字同一類，§3.14）。
- **計時事件**（DLL，只有 Medium 程序寫，§3.11）：66 一次候選窗更新（開始＝`Out` 到手，結束＝`UpdateLayeredWindow` 回來）；67 程序裡第一個窗的建立（開始＝取得 Direct2D 與 DirectWrite factory 之前，結束＝`CreateWindowExW` 之後；factory 通常已由預熱建好，所以多半不含建 factory 的時間，和預熱加入前的數字不能直接比）；68 每次顯示的位置，連續六行、兩欄相同、各是一個 `i32` 的 `u32` 表示（負的座標用二補數）：組字矩形的 left、top、right、bottom，然後窗左上角的 x、y（螢幕像素）。只有數字。
- **驗證**：
  - 單元測試：`candlayout::tests`（尺寸、寬度、擺位與極端值，各平台）；`window_keys_match_the_core`（§3.6）；常駐程序 crate 的 `the_window_the_host_sends_is_the_one_the_core_gives`（對直接呼叫核心）、`a_connection_never_gets_the_candidates_of_a_window_it_does_not_own`（兩條連線）、`replay_keys_vertical`（一串鍵，從開窗、↓、數字選、Enter 選到送出，算出的字等於寫定的預期字；預期字由 `print_replay_keys_vertical` 產生）；R2 子程序（§3.13）。
  - 測試 VM（乾淨檢查點）：記事本與「執行」對話框送 `replay_keys_vertical` 那一串鍵，讀回的字逐字相同；開窗位置對（組字前後截圖的範圍框對照事件 68，開再關的差異核對畫面上真的有窗）；開始功能表搜尋框與 Win+S 搜尋看得到窗；開、關、更新前後前景與焦點視窗不變；100%、150%、淺色、深色的外框與顏色；記事本多佔的私有記憶體仍小於 10 MB；DLL 端事件 3 的 p95 小於 16 ms；小算盤與系統管理員的記事本、密碼欄、R2 掃描另記。

## 4. 微軟注音參考（量測）

用途：Windows 版按鍵與介面的對照依據。這些是內建微軟注音的實測行為，不是善解的行為。

### 4.1 量測環境

- Windows 11 25H2 專業版（zh-TW），乾淨的 Hyper-V Gen2 測試 VM，每一輪從同一個檢查點還原；內建的微軟注音，預設版本（沒有切「使用舊版」），VM 上只有這一個輸入法；解析度 1920×1080。
- 按鍵從主機送進 VM 主控台（和實體鍵盤一樣經過輸入法），VM 裡沒有安裝任何工具。
- 截圖來自 Hyper-V 的縮圖介面，是 16 位元 RGB565：顏色只能精確到這個精度，下面的色值是讀值，不是精確的十六進位值。
- 其他確認過的行為：「執行」對話框（Win32 文字框）開起來是英文模式，新開的記事本（Windows 11 新版）是中文模式，亦即中英模式**每個視窗各自記**；單按 Shift 切中英，在組字中按時組字留著、只切到英文；組字中按 ↓ 叫出候選窗。
- 輸入法會學最近選的字：在候選窗選了某字，下次同一個讀音它排第一；預設不顯示聯想詞。所以會選字的格子要每格從檢查點還原再量；照同一個順序量兩輪排除不了這個狀態（2026-11-10 補量因此更正了 S2 Shift+Enter 與 S4 兩格，細節與補量的其他結果在 `docs/windows/ms-zhuyin-ref.md` 最後「補量」）。

### 4.2 按鍵表

記事本，淺色、100%。狀態：S0 組字區空；S1 有未完成音節（ㄋ）；S2 組字中（ㄋㄧˇ＝「你」）；S3 候選窗開著（S2 再按 ↓）。「組字」＝還在輸入法裡、有底線；「送出」＝進了記事本。

| 鍵 | S0 組字區空 | S1 未完成音節 ㄋ | S2 組字中「你」 | S3 候選窗開著 |
|---|---|---|---|---|
| 空白 | 直接送出半形空白 | 不動作 | 在組字裡接一個空白 | 翻到下一頁 |
| Enter | 換行 | 不動作（ㄋ 留著） | 送出「你」 | 選目前那個字、關窗，**仍在組字**（不送出） |
| Shift+Enter | 換行 | 不動作 | 照原樣送出組字，不換行 | 關窗，組字留著 |
| Esc | 不動作 | 清掉 ㄋ | 清掉整段組字 | 關窗，組字留著 |
| Backspace | 不動作 | 清掉 ㄋ | 刪掉「你」（整個音節） | 不動作 |
| Delete | 不動作 | 不動作 | 不動作 | 不動作 |
| Tab | 插入 Tab | 不動作 | 不動作 | 候選窗展開成三欄（全部 25 項，最後一項是注音「ㄋㄧˇ」本身） |
| Shift+Tab | 插入 Tab | 不動作 | 不動作 | 同 Tab，展開 |
| ↑ | 不動作 | 不動作 | 換游標前那個字：第一次換成單字清單的第 3 項、第二次換成第 2 項，第三次開窗（目前的字排第一） | 跳到最後一頁、選最後一項 |
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
| Ctrl+, | 組字放全形「，」（Ctrl+. 是「。」） | 丟掉 ㄋ、組字放「，」 | 組字後面接「，」 | 關窗，組字後面接「，」 |
| Caps Lock | 不動作 | 不動作 | 不動作 | 不動作 |
| Ctrl+Space | 切英文 | 清掉 ㄋ、切英文 | 切英文，組字留著 | 關窗、切英文，組字留著 |
| Win+Space | 不動作（只有一個輸入法） | 不動作 | 不動作 | 不動作 |

Windows 版的按鍵預設照這張表（核心的按鍵習慣 1，`docs/spec/keys-and-composition.md` §4.1.1 的 `m` 規則）。習慣 1 下不照微軟的只有 §4.6「不照微軟的列」那張表：S2 ↑ 與 ↓ 窗裡多字詞的排法、組字區上限、S3 Tab 與 → 的展開、S1 聲調鍵，加上還沒做的單按 Shift、Ctrl+Space（上表的「單按 Shift」「Ctrl+Space」兩列），以及候選窗的底部列、捲動點、三欄展開、滑鼠點選（§3.15）。把登錄值 `KeyScheme` 設成 0 時，Windows 版照核心現行的規則（Mac 版）：直排窗用規則 3v–5p（§4.6 的左右欄對照）。

送出「你」之後（S4）和一般打字相同：空白、Enter、Tab、方向鍵、Home、End 交給記事本；注音鍵、數字鍵、`,` `.` 開新的組字；Shift+A、Shift+, Shift+. 直接送出；Ctrl+, 開組字放標點。

組字區上限：31 個音節都還在組字裡，第 32 個起開始把前段送出，第 35 個起一次送出好幾個字（補量直接讀回已送出的字）。畫面上輸入法用藍底標示目前的詞段。

### 4.3 候選窗外觀

候選窗跟著 Windows 設定裡的 App 模式（`AppsUseLightTheme`），不跟 Windows 模式（`SystemUsesLightTheme`，工作列）、也不跟宿主 App 自己的配色：深色時記事本是深色、「執行」對話框（Win32）仍是淺色，但兩邊的候選窗都是深色；App 模式深色、Windows 模式淺色時候選窗是深色，反過來是淺色（測試 VM，記事本）。直排，每頁 9 列，編號 1–9；選取列左側有一條選取條；右側有捲動點，底部有 ▲▼ 兩個翻頁鈕和兩個圖示。窗出現在插入點正下方，左緣和組字的字對齊；「執行」對話框在螢幕下半部時，候選窗開在組字的**上方**。尺寸（像素）：

| | 100% | 150% |
|---|---|---|
| 候選窗外框 | 142 × 323 | 215 × 486（約 1.5 倍） |
| 邊框 | 1 | 2 |
| 邊框到內容的留白 | 4 | 6 |
| 每列間距 | 32 | 48 |
| 選取列的底色塊 | 高 28（上下各留 2）、寬 132、圓角 | 高 42 |
| 選取條（左側直條） | 寬 3、高 14 | 沒量 |
| 底部列（▲▼、兩個圖示） | 高 28，上方 1 px 分隔線 | 高 42，分隔線 2 |
| 外框圓角 | 約 8 | 沒量 |
| 展開成三欄後的寬 | 約 306（高不變） | 約 461（高不變） |

沒有陰影：邊框外一圈就是底下 App 的顏色（測試 VM 的 24 位元截圖，100% 與 150%、淺色與深色）。

直排每頁 9 列：留白 4＋9 × 32＝292，加分隔線與底部列，正好是外框高 323；150% 同樣的算式。展開（Tab 或 →）：同一個窗往右長成三欄、共 25 項，選取色塊只蓋住第一欄那一格，底部列的翻頁鈕從 ▲▼ 換成 ◀▶。

顏色（24 位元，測試 VM 的截圖，記事本、100%）：

| | 淺色 | 深色 |
|---|---|---|
| 窗底 | #F9F9F9 | #2C2C2C |
| 邊框 | #EAEAEA | #1F1F1F |
| 選取列底色 | #F0F0F0 | #383838 |
| 選取條 | #0067C0 | #4CC2FF |
| 候選字 | #1A1A1A | #FFFFFF |
| 編號 | #5F5F5F（選取列 #5B5B5B） | #CFCFCF（選取列 #D2D2D2） |

都是中性灰。較早的 RGB565 縮圖讀值（下表）因為綠色多一個位元而偏色（淺色偏粉紅、深色偏綠），只留作底部列分隔線的參考：

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

左欄是上面量到的微軟注音，右欄是善解 Mac 版現行的按鍵規則（按鍵習慣 0，見 `docs/spec/keys-and-composition.md`）。Windows 版的候選窗開成直排（§3.15），按鍵取決於按鍵習慣（`set_key_scheme`，§3.4）：**習慣 0** 照核心的直排規則（`docs/spec/keys-and-composition.md` §4.1、§4.3：1–9 選、↓↑ 移、空白＝下一個、Page Down／Up 移 9 個），也就是下表右欄；**習慣 1**（Windows 的預設，登錄值 `KeyScheme` 沒有值或為 1）照微軟注音的整頁直排（§4.1.1 的 `m` 規則：↑↓ 循環、空白與 Page Down 翻整頁並選那一頁第一項、Page Up 往前翻、End 到最後一頁、Shift+Enter 關窗），也就是下表左欄，只有後面「不照微軟的列」那張表的幾列例外。兩邊相同的格不列（例如 S2 的 Enter、Esc、Backspace，S3 的 Esc、數字鍵，S0 的聲調鍵）。Mac 版的做法由登錄值 `KeyScheme` 選（§3.4、§5.2），這一版先只有登錄值、沒有設定頁。

| 情境 | 微軟注音 | 善解 Mac 版 |
|---|---|---|
| 候選窗預設 | 直排、每頁 9 個 | 橫排（設定「直排」預設關） |
| 候選窗的底部列（▲▼、兩個圖示）、捲動點、展開成三欄、滑鼠點選 | 有 | Windows 版現在都沒有（§3.15）；Mac 版有自己的橫排、直排與展開 |
| S1 Enter | 不動作，ㄋ 留著 | 送出畫面上的整串，含未完成的符號 |
| S1 Shift＋字母 | 丟掉 ㄋ、送出那個字母 | 忽略（中英混打關著時） |
| S2 空白 | 在組字裡接一個空白 | 開候選窗 |
| S2 Shift+Enter | 照原樣送出組字，不換行 | 送出組字，再把換行交給 App |
| S2 ↑ | 換游標前那個字：第一次換成單字清單的第 3 項、第二次換成第 2 項，第三次開窗（目前的字排第一） | 忽略 |
| S2 Shift＋字母 | 字母接進組字 | 送出組字，再把按鍵交給 App（中英混打關著時） |
| S0／S2 Shift+, 、Shift+. | 送出或接進半形「<」「>」 | 全形標點「，」「。」插進組字區 |
| Ctrl+, 、Ctrl+. | 組字放全形「，」「。」 | 不處理，直接交給 App |
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
| 組字區上限 | 31 個音節，第 32 個起把前段送出 | 128 個單位（`MAX_SYLLABLES`），到了送出整段並清空 |

不照微軟的列（Windows 版在習慣 1 下，也包括還沒做的功能）：

| 情境 | 微軟注音 | Windows 版（習慣 1） |
|---|---|---|
| S2 ↑ 換字、S2 ↓ 開窗的清單 | 窗只列游標前那個字的單字，目前的字排第一 | 照規則 16u、15d（`docs/spec/keys-and-composition.md` §4.1.1）：換字的序號、第三次開窗、目前的字排第一都照微軟；窗照 §6.1 先列多字詞，所以排第一的是「和顯示的那一段相同」的項目（「我們」按 ↓ 時是「我們」）。換字在核心裡等於改選，學習規則相同，但 Windows 版的引擎關掉學習（§3.4 的 `set_learning(0)`、沒有學習檔，§2.5），所以目前不會學；單字清單的字和順序來自善解的詞庫，所以換出來的字可能不同（ㄨㄛˇ 是 婐、䂺，微軟是 捰、婐） |
| 組字區上限 | 31 個音節，第 32 個起把前段送出 | 128 個單位，到了整段送出（核心沒有「送出前段」的機制） |
| S3 Tab、→ | 候選窗展開成三欄 | 吃掉、不動作，窗開著（展開成三欄在候選窗那一片，§3.15） |
| S1 聲調鍵 | ㄋ＋聲調，整段反藍、不成字 | 照規則 10（完成音節，詞庫沒有的音節不收） |
| 單按 Shift、Ctrl+Space | 切中英（各視窗各自記） | 還沒做（中英切換在之後一片）；Ctrl+Space 核心不處理（規則 1） |
| 候選窗底部列（▲▼、圖示）、捲動點、展開成三欄、滑鼠點選 | 有 | 都沒有（§3.15） |
| 組字中或窗開著時的標點鍵（`[`、`]`、`\`、`'`、`=`、`` ` ``、Shift+/、Shift+1、Ctrl+\ 等，規則 2） | 沒有量 | 照規則 2：關窗（若有）、丟掉未完成音節，插入全形標點；與微軟注音是否相同沒有量 |

## 5. 常駐程序（`windows/host`）

每個使用者的每個登入工作階段一支 x64 常駐程序 `shanjie-host.exe`，**只有它**載入模型、只有一個引擎；TSF 文字服務 DLL 是薄客戶端，不相依核心，每按一鍵經 named pipe 同步往返一次。連不上就直通，不退回 DLL 內核心。候選窗由 DLL 在 App 的程序裡畫（§3.15），資料走同一條連線（`Out` 的候選欄位）；協定預留了常駐程序主動推送的訊息型別，這一版沒有用。

### 5.1 程式放哪裡

- 三個 crate，都是 workspace 成員：
  - `windows/proto`（`shanjie-win-proto`）：線上格式。純 Rust、不分平台、不相依 `windows` crate，三個平台的 CI 都編、都測。
  - `windows/host`（`shanjie-host`，產出 `shanjie-host.exe`）：pipe 伺服器、引擎、擁有者切換、計時檔。`#![cfg(windows)]` 包住主程式，macOS 與 Linux 上是空的 bin。相依 `shanjie_core`（§3.1 的改名寫法，只放在 `cfg(windows)`）、`shanjie-win-proto`、`windows`／`windows-core` 0.62。只呼叫核心的 C ABI。
  - `windows/tip`：不相依核心，改相依 `shanjie-win-proto`。`client.rs`（連線、伺服器核對、逾時、斷路器、啟動常駐程序）取代 DLL 內的引擎；`tip.rs`、`doc.rs`、`keys.rs` 只改呼叫引擎的地方。`pipeio.rs`（有期限的 overlapped I/O）、`token.rs`（權杖查詢）與 `timing.rs` 兩端共用，常駐程序以 `#[path]` 引入。
- DLL、常駐程序、資料都在同一個安裝資料夾（開發版 `C:\Program Files\shanjie-dev\`，§3.3）；常駐程序從自己的 `GetModuleFileNameW` 推資料夾，資料在旁邊的 `data\`。
- 只出 x64。

### 5.2 程序模型

- pipe 名稱 `\\.\pipe\shanjie-dev-<使用者 SID>-<工作階段 ID>`。名稱不帶協定版本：版本不同的兩端要碰得到彼此，才會走到 `Refuse { 1 }`。提升權限的 App 也連這一支（§5.5 第 5 點）。
- **實例**：啟動時一次建好全部 N＝64 個實例（`nMaxInstances = N`，第一個帶 `FILE_FLAG_FIRST_PIPE_INSTANCE`），之後以 `DisconnectNamedPipe`、`ConnectNamedPipe` 重複使用、永遠不關。第一個實例建不起來（已經有一支在跑）就結束。實例數已滿，誰都加不了實例，與 DACL 無關。不另外用具名 mutex。VM 驗收讀事件 65（同時連線數的最高值）核對 N。
- **先建 pipe、再載模型**：載入期間收到 `Hello` 或 `Key` 立刻回 `NotReady`，客戶端直通。模型載入失敗、或引擎的鎖 poisoned，常駐程序就結束，讓下一個客戶端啟動新的一支。
- **預熱**：模型載完之後、`install()` 之前，常駐程序用同一個引擎送一串固定的假按鍵（`a u / 6 w u 0 space y l 3 g ; 4`，ㄇㄧㄥˊ ㄊㄧㄢ ㄗㄠˇ ㄕㄤˋ，不按 ↓、不開候選窗、不送出），讓第一個真的按鍵不必面對還沒進記憶體的模型頁面（這是推論，量測見 §5.8 的事件 69）；不論成敗，最後一律 `reset`（模式 1），所以引擎之後沒有組字、沒有前文，學習本來就關。預熱期間 `ready()` 仍是假，客戶端照 `NotReady` 直通。某一鍵被核心拒絕就停止預熱、記事件 70，照常 `install()`，不讓常駐程序起不來；`reset` 本身失敗時，這個引擎不放上線，釋放後重新載入一次（不再預熱），再失敗就照模型載入失敗結束。只有帶 `timing` 的量測版讀環境變數 `SHANJIE_HOST_NO_WARM=1` 來跳過預熱，正式版不讀、設定頁也沒有；DLL 啟動常駐程序時環境區塊只有 `SystemRoot`，使用者環境裡的這個變數傳不進產品的常駐程序。事件 2 的結束仍是載入完成的時刻（預熱之前），它在 `install()` 之後才寫。
- **按鍵習慣**：載模型之前讀一次 `HKCU\Software\shanjie-dev\KeyScheme`（DWORD），用 `RegGetValueW(HKEY_CURRENT_USER, "Software\shanjie-dev", "KeyScheme", RRF_RT_REG_DWORD, …)` 讀進一個 `u32`：值是 0 或 1 就照用；沒有這個值、型別不對、`ERROR_MORE_DATA` 等任何錯誤、或值不是 0 與 1，一律當 1（Windows 預設照微軟注音）；夾完再傳給 `engine::load`，不讓 `set_key_scheme` 回 2 而讓常駐程序結束。`engine::load` 本身不讀登錄。值只在啟動時讀一次，改了要重新登入或結束常駐程序才生效；之後每條連線的 `Welcome` 帶這個值（§5.3）。信任：寫得進 `HKCU\Software` 的只有同一個使用者的 Medium 以上程序，它們本來就能影響常駐程序（§5.5 第 1、5 點已接受），所以這個值沒有跨過權限邊界。事件 71（§5.8）記讀到的結果。
- **誰啟動**：DLL 連線得到 `ERROR_FILE_NOT_FOUND`，而且呼叫端同時符合以下全部條件時，才 `CreateProcessW`；`ERROR_PIPE_BUSY` 不啟動、直通。
  - 呼叫端完整性剛好是 Medium、不是 AppContainer、不是提升權限、不是受限權杖、工作階段不是 0、使用者不是 SYSTEM／LocalService／NetworkService、沒有套件身分（`GetCurrentPackageFullName` 回 `APPMODEL_ERROR_NO_PACKAGE`；封裝 App 開的子程序會留在它的套件執行環境裡）。
  - 不等：送出 `CreateProcessW` 就讓這一鍵直通，不在 `OnKeyDown` 或 `ActivateEx` 裡等 pipe 出現。每個程序同時只啟動一次；啟動後 10 秒內不再啟動，期間的鍵直通。啟動 15 秒後、或它啟動的常駐程序已經結束，連線仍是 `ERROR_FILE_NOT_FOUND`，記一次「啟動後仍找不到」（事件 63 原因 5），照退避重試；永久直通只由 `Refuse { 1 }` 觸發。
  - `lpApplicationName` 是 DLL 旁的 `shanjie-host.exe` 的絕對路徑；命令列加引號、只有一個固定旗標 `--resident`，不帶任何從宿主取得的字串；工作目錄是安裝資料夾；環境區塊只有 `SystemRoot`。
  - 只繼承 NUL 的三個標準 handle（`EXTENDED_STARTUPINFO_PRESENT`、`STARTF_USESTDHANDLES`、`PROC_THREAD_ATTRIBUTE_HANDLE_LIST`），啟動後關掉。旗標 `CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB | CREATE_UNICODE_ENVIRONMENT`；宿主的 Job 不准脫離時不啟動、這一鍵直通。
- **生命週期**：活到登出，沒有連線也不自己結束（載入一次約 2 秒）。沒有看門狗：誰連不上、符合條件，誰啟動。
- **已知限制**：登入時預先啟動屬於安裝程式；在那之前，只在有套件身分的 App（例如 Windows 11 的記事本）或 AppContainer 裡打字時不會啟動常駐程序，那些 App 只有英文，要有一個一般的桌面程式（例如「執行」對話框、多數 Win32 App）先打過字。
- **自我檢查**（`main` 一開始）：權杖不是剛好 Medium、提升權限、AppContainer、受限權杖、工作階段 0、SYSTEM 或服務帳號、或有套件身分，就結束。接著 `SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32)`、`WerAddExcludedApplication`（寫 HKCU；失敗就結束：記憶體裡有所有 App 的組字，dump 不能落地），裝不印 payload 的 panic hook。

### 5.3 線上格式（`windows/proto`）

- 固定寬度、小端，不用自描述的序列化。每則：`u32 len`、`u16 proto`、`u16 type`、`u64 seq`，後面是型別專屬的內容。字串一律 UTF-16 長度加資料。
- 客戶端 → 常駐程序：`Hello { build_hash, pid, tid }`（pid、tid 只是資訊，不拿來授權）；`Key { ctx, ShanjieKey }`；`Release { ctx }`；`Reset { ctx }`（DLL 接住 panic 之後；送出後客戶端關掉這條連線，因為 panic 可能發生在一次交換的中間）。
- 常駐程序 → 客戶端：`Welcome { build_hash, key_scheme }`（`key_scheme` 是一個位元組，0 或 1：這支常駐程序引擎的按鍵習慣，§5.2；DLL 的 `OnTestKeyDown` 只從這裡得知，§3.6；其他值解碼就拒絕）；`Refuse { reason }`（1 協定版本不同、2 這個客戶端的連線數已滿、3 客戶端核對不過；之後再加的碼也只是數字）；`NotReady`；`Busy`；`Out { ran, core_error, took_over, handled, commit, preedit, cursor_utf16, candidate_selected, candidate_first, candidate_total, candidates }`（`core_error` 為真表示核心回非 0：客戶端不改文件，照 `handled = 0` 處理；`candidates` 是直排候選窗**可見的列**，最多 9 項，`candidate_selected` 是可見列裡的位置，`candidate_first` 是第一列在全部清單裡的位置，`candidate_total` 是全部清單的長度；窗沒開時 `candidate_selected` 是 −1、`candidates` 為空、兩個數字都是 0）；預留 `Pushed`，客戶端收到就丟掉。
- `proto` 不同 → `Refuse { 1 }`（`PROTO` 目前是 3：2 是 `Out` 加了 `candidate_first`、`candidate_total` 並填入 `candidates`，3 是 `Welcome` 加了 `key_scheme`）；`build_hash` 不同不拒絕、不記錄。PROTO 3 的 DLL 遇到還在跑的 PROTO 2 常駐程序（PROTO 2 的常駐程序對 PROTO 3 的 `Hello` 也回 `Refuse { 1 }`）會收到 `Refuse { 1 }`，那個 App 直通到重開；更新時要先結束舊的常駐程序（安裝與更新流程另寫，這一版沒有）。收到 `NotReady`、`Busy` 時客戶端關掉連線，下一鍵再連。`Release`、`Reset` 沒有回應。
- **擁有者由常駐程序指定**：`owner = (連線 ID, ctx)`，連線 ID 單調遞增、不重用；客戶端只送自己的 `ctx`（`ITfContext` 的指標值），它只在自己那條連線裡有意義。換擁有者先 `reset(mode 1)`（§3.4 的那一套：一個引擎）；新擁有者的 `Out` 不帶舊擁有者的候選窗（reset 之後窗是關的）。`Release` 要 `(連線, ctx)` 完全相符才 reset；`Reset` 與斷線只看連線，是目前的擁有者才 reset 並清掉擁有者。
- **解析規則**（兩端共用）：
  - 讀到 `len` 之後、配置記憶體之前，先檢查 16 ≤ `len` ≤ 64 KiB，而且等於這則訊息實際的位元組數。
  - 型別依方向用白名單；第一則一定是 `Hello` 而且只有一次，之前收到別的就斷線。
  - 字串長度不得超過剩下的內容，結尾不能有多出來的位元組，旗標只收 0 或 1。
  - 任何一條不符就斷線、不回應。
  - 候選清單在解碼時就拒絕超過 9 項（不先配置）。
  - 客戶端另外核對（`out_ok`，所有加法用 `u64`）：`seq` 等於送出的那一則；`ran` 為真；`cursor_utf16` 不大於 `preedit` 的長度；`commit`、`preedit` 不含 C0、C1 控制字元與落單的代理字元（偽造的伺服器打換行進終端機的縱深防禦；核心的送出路徑本來就不送控制字元）；`candidate_selected == -1` 時 `candidates` 為空而且 `candidate_first == candidate_total == 0`；否則 `0 <= candidate_selected < candidates 的項數 <= 9`、`preedit` 非空、`candidate_first + 項數 <= candidate_total`，每項非空、不超過 `CAND_MAX_UTF16`（64 個 UTF-16 單位；量到詞庫與詞包最長的詞條是 10 個單位，取 64 是寧鬆勿緊：被拒絕的 `Out` 會結束使用者的組字）、不含控制字元、落單的代理字元、U+2028、U+2029 與雙向控制字元（U+202A–202E、U+2066–2069）。
- 驗證：`shanjie-win-proto` 的單元測試（每個型別編解碼往返、長度上限、壞的或截斷的訊息被拒絕、超過 9 項的候選清單被拒絕、`out_ok` 的每個邊界：−1 但有候選或 first／total 非 0、selected 為 −2 與 `i32::MIN`、超出、10 項、空字串、超長、控制字元、U+2028、雙向字元、first 為 `u32::MAX`、total 為 0、有候選但 `preedit` 空，亂數位元組解碼不會 panic），三個平台都跑。

### 5.4 通訊

- **直通在 `OnTestKeyDown` 決定**：斷路器生效、`Refuse { 1 }` 之後的永久直通、`Refuse { 3 }` 的 60 秒冷卻期間一律回 FALSE。組字區是空的、照 §3.6 會吃的鍵，`OnTestKeyDown` 先確認這條執行緒有能用的連線：沒有就當場連線（開 pipe、核對伺服器、`Hello`；pipe 不存在時照 §5.2 的條件啟動常駐程序、不等），連不上（含 `NotReady`）就回 FALSE，鍵照原樣交給 App。理由：IMM32（CUAS）的宿主（例如「執行」對話框）裡，`OnTestKeyDown` 回 TRUE 的鍵會變成 `VK_PROCESSKEY`，`OnKeyDown` 再放行也收不回原本的字元，鍵就消失了（VM 實測：常駐程序還沒起來時在「執行」對話框送的鍵沒有出現）。explorer 會先呼叫 `OnTestKeyDown`，所以啟動也從這裡發生。組字區空時核心一定會直通的鍵（空白鍵、Shift＋英文字母、Caps Lock 開著時的鍵）在 §3.6 就回 FALSE。留著的舊連線在 `OnTestKeyDown` 先以零位元組的 `PeekNamedPipe` 核對（不等待），常駐程序已經結束就丟掉重連。剩下的已知限制：`OnTestKeyDown` 之後才知道的失敗（`OnKeyDown` 收到 `Busy`、讀取逾時、常駐程序剛好在兩次呼叫之間結束）在 IMM32 宿主裡仍會讓那一鍵消失，應該很少見。
- **第一個鍵才連線**（不在 `ActivateEx` 連）；一個 TIP 實例最多一條連線。組字區是空的而且閒置 60 秒，客戶端自己關連線（UI 執行緒上 15 秒一次的 `SetTimer`）。連線曾經存在後 `DllCanUnloadNow` 一律回 `S_FALSE`。
- 常駐程序每條連線一條執行緒，本體包 `catch_unwind`。引擎一把鎖：`Key` 一律 `try_lock`，拿不到就立刻回 `Busy`（客戶端直通）；`Release`、`Reset` 與斷線清理等鎖（沒有人在等它們的回應，跳過會留下錯的擁有者、讓已經送出的字再出現一次），等的時間最多是別人一次核心呼叫。先放掉鎖再寫回應，寫入逾時 1 秒就斷那條連線。
- 閒置逾時只用在 `Hello` 之前（1 秒）。依客戶端的 AppContainer SID 或 PID 限制每個客戶端的連線數（4）：只拿來限流，不拿來授權。送出最後一則（`Refuse`、`NotReady`）之後等客戶端關閉最多 1 秒再 `DisconnectNamedPipe`，免得客戶端讀不到。
- **每鍵同步**：`OnKeyDown` 在同步 edit session 裡送 `Key`、等 `Out`，拿到就照 §3.7 改文件；edit session 沒拿到時不送。
- **逾時 200 ms**（寫入與讀取共用一個期限）。overlapped I/O，不做可警示的等待、不 pump 訊息；逾時就 `CancelIoEx`，再 `GetOverlappedResult(bWait = TRUE)` 等取消完成，才釋放 `OVERLAPPED` 與緩衝。
- 寫入失敗：組字區是空的時候重連一次、重送一次；組字開著時照 §5.6 第一列。寫完之後讀取失敗或逾時不重送。
- **斷路器**：連續兩次逾時，這個程序 10 秒內不再連線、直通。
- 常駐程序是組字狀態的唯一來源；DLL 不留按鍵紀錄、不重放。

### 5.5 安全

1. **客戶端核對伺服器**（連上之後、送任何訊息之前；任一步失敗就斷線、直通，Medium 客戶端記事件 63 原因 9）：
   - (a) 只用 handle：`GetSecurityInfo` 讀 pipe 的擁有者 SID，必須等於客戶端自己的 `TokenUser`；`GetNamedPipeServerSessionId` 必須等於自己的工作階段。每個客戶端都做，AppContainer 也是。
   - (b) 不是 AppContainer 的客戶端再查伺服器程序的權杖（`GetNamedPipeServerProcessId` → `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` → `OpenProcessToken(TOKEN_QUERY)`）：使用者相同、完整性剛好是 Medium、不是 AppContainer、不是受限權杖。
   - (c) 映像路徑（`QueryFullProcessImageNameW`，不分大小寫）必須等於 DLL 旁的 `shanjie-host.exe`，只當補充、查不到就拒絕；和 (b) 一樣只對非 AppContainer 客戶端做（AppContainer 開不了常駐程序的程序）。
   - 受限權杖的判斷直接讀 `TokenRestrictedSids` 的群組數，查詢失敗就拒絕。
   - 邊界：同一個使用者、同為 Medium 的程式本來就能裝鍵盤鉤子或注入，對 Medium 客戶端這是縱深防禦。擋得住的是其他使用者冒充（擁有者 SID、工作階段不同），以及對非 AppContainer 客戶端的 Low／AppContainer 冒充者（伺服器權杖不是 Medium）。AppContainer 客戶端只做得到 (a)：同一個使用者的 Low 程序搶先建的 pipe，擁有者與工作階段都相同，(a) 分不出來，這是接受的殘餘風險（需要同一使用者的 Low 程式先在跑、搶在常駐程序之前）。任何本機使用者都能搶先佔用別人的 pipe 名稱讓對方只剩英文；登入時預先啟動會縮小這兩個空檔。
   - 客戶端的使用者是 SYSTEM、LocalService、NetworkService，或完整性是 System 以上，一律不連線、直通。
2. **SDDL**（程式裡一個常數）：`D:P(A;;0x12019F;;;<使用者 SID>)(A;;0x120083;;;AC)(A;;0x120083;;;S-1-15-2-2)S:(ML;;NW;;;LW)`。
   - 使用者 SID 那條是 `FILE_GENERIC_READ | FILE_GENERIC_WRITE`：留著建實例要的權限，沒有 `WRITE_DAC`、`WRITE_OWNER`、`DELETE`。
   - `AC` 與 `S-1-15-2-2` 只有 `FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE | READ_CONTROL`，沒有 `FILE_CREATE_PIPE_INSTANCE`。沒有 Everyone、Users；`PIPE_REJECT_REMOTE_CLIENTS`；位元組讀取模式（客戶端不呼叫 `SetNamedPipeHandleState`）。
   - 客戶端 `CreateFileW` 要的權限剛好是 `FILE_READ_DATA | FILE_WRITE_DATA | SYNCHRONIZE | READ_CONTROL`（要 `GENERIC_READ`／`GENERIC_WRITE` 會多出 AppContainer 拿不到的權限）。
3. **常駐程序不能冒用客戶端身分**：客戶端開 pipe 一律帶 `SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION`。測試伺服器模擬客戶端之後，斷言模擬等級是 `SecurityIdentification`。
4. **常駐程序核對客戶端**：讀到 `Hello` 之後 `ImpersonateNamedPipeClient` → `OpenThreadToken(OpenAsSelf = TRUE)` 讀 `TokenUser`（等於常駐程序的使用者）、`TokenSessionId`（等於常駐程序的工作階段）、AppContainer、完整性、提升權限 → `RevertToSelf`。任何一項查不到就拒絕（`Refuse { 3 }`）；`RevertToSelf` 失敗就結束程序。查到的類別記在連線上。不用 `GetNamedPipeClientProcessId` 再開程序查授權。常駐程序永遠不以客戶端身分做任何事。
5. **提升權限的 App**：連同一支 Medium 常駐程序，絕不啟動它（§5.2 的條件）；常駐程序的自我檢查保證它不會以提升權限執行；客戶端照第 1 點核對伺服器。**接受的風險**：管理員 App 的按鍵經 pipe 進到一般權限的常駐程序，送出的字再回到管理員 App，同一個帳號下的一般權限惡意程式可以注入或讀取常駐程序，因此能讀到、也能偽造打進管理員視窗的字（Windows 的 UIPI 原本擋住的事）。Mozc、小狼毫、PIME 都是同樣的設計。縮小的措施：安裝資料夾只有系統管理員能寫（§3.3）、第 1 點的伺服器核對、§5.3 的控制字元檢查。常駐程序維持 Medium（之後的學習要寫 `%LOCALAPPDATA%`，Low 寫不了）。
6. **R2**：線上訊息就是組字內容。兩端都不得把它寫進任何 log、檔案、錯誤訊息或 `OutputDebugString`；型別不 derive `Debug`；錯誤只有 Win32 碼與結束碼；計時檔只有數字，事件 62、63 只放錯誤碼或原因碼。R2 行為測試（§3.13）在常駐程序也跑一份。常駐程序關掉自己的 WER dump（§5.2）。
7. 常駐程序只用 `LOAD_LIBRARY_SEARCH_SYSTEM32`，正式版不讀環境變數（只有帶 `timing` 的量測版讀 `SHANJIE_HOST_NO_WARM`，§5.2），不開網路。
8. **接受並記錄**：單一引擎共用，任何連得上的客戶端都能讓正在打字那個 App 的組字被丟掉（換擁有者就 reset），也能從 `took_over` 看出別的 App 有沒有在打字。

### 5.6 故障時怎麼做

| 情況 | DLL 看到什麼 | DLL 做什麼 | 使用者看到什麼 |
|---|---|---|---|
| **組字開著時任何讀寫錯誤**（斷線、逾時、重連得到 `ERROR_FILE_NOT_FOUND`），或收到 `NotReady`、`Busy`、`Refuse`、格式不對的回應 | 讀寫失敗或不能用的回應 | 在同一個 edit session 裡結束組字（先清底線、字留著），**吃掉這一鍵**，關連線；之後的鍵照 §5.2 的條件連線或啟動常駐程序（不等） | 打到一半的字留在文件、沒有底線；之後的鍵依常駐程序的狀態直通或恢復 |
| 常駐程序沒在跑（組字區是空的） | `ERROR_FILE_NOT_FOUND`（在 `OnTestKeyDown`） | 符合 §5.2 的條件就啟動（不等），`OnTestKeyDown` 回 FALSE，這一鍵直通 | 前幾個字母以英文出現，之後恢復 |
| 常駐程序載入中 | `NotReady`（在 `OnTestKeyDown` 的 `Hello`） | `OnTestKeyDown` 回 FALSE，直通 | 同上 |
| 引擎忙碌 | `Busy` | 直通 | 那一鍵是英文 |
| 常駐程序當掉或卡住（組字區是空的） | 讀寫失敗、逾時 | 寫入失敗重送一次（§5.4）；讀取失敗或逾時：取消並等完成、關連線、這一鍵直通；連續兩次逾時觸發斷路器 | 那一鍵是英文，之後恢復 |
| 版本不同（更新後還載著舊 DLL） | `Refuse { 1 }` | 這個程序從此直通、不再重試（只有這一種會永久直通） | 這個 App 只剩英文，重開 App 恢復 |
| 限流 | `Refuse { 2 }` | 這一鍵直通、關連線，下一鍵再連 | 那一鍵是英文 |
| 常駐程序拒絕這個客戶端 | `Refuse { 3 }` | 直通，60 秒內不再連 | 那個 App 只有英文 |
| 沙箱 App 連不上 | `ERROR_ACCESS_DENIED` | 直通。AppContainer 寫不了計時檔，連不上的這一端沒有紀錄；常駐程序在 AppContainer 客戶端連上時記事件 64 | 那個 App 只有英文 |
| 伺服器核對不過 | 擁有者、工作階段或權杖不符 | 斷線、直通，Medium 客戶端記事件 63 原因 9 | 那個 App 只有英文 |

都不顯示任何視窗、對話框或通知。

### 5.7 文字服務的三項修正

- `OnUninitDocumentMgr` 時把那個 document manager 從「已開過鍵盤隔間」的集合拿掉（§3.5）。
- `doc::apply` 中途失敗時，取出的組字在每一條錯誤路徑上都先結束（清底線、`EndComposition`），不留下開著的組字。
- `OnCompositionTerminated`：只有傳進來的組字和這個實例的組字是同一個（比原始指標）時才清掉並通知常駐程序。

### 5.8 計時檔

- DLL 與常駐程序各自照 §3.11 寫（同樣的 Medium 閘門、`CREATE_NEW`、只有數字）；常駐程序的檔名 `host-<pid>.txt`，同一個資料夾。DLL 寫入用 `try_lock`（UI 執行緒不等計時檔），常駐程序等鎖（64 條連線執行緒共用一個檔，略過會丟掉事件 2）。
- 常駐程序記 2（模型載入）、3（每鍵：收到到送回）、64（一個 AppContainer 客戶端連上，兩欄都是 1）、65（同時連線數創新高時的值，兩欄都是那個數）。DLL 不再寫 2 與 46。
- 常駐程序另記 69（預熱：開始＝第一鍵之前、結束＝`reset` 之後，單位 µs；沒預熱時不寫）、70（預熱的某一鍵被核心拒絕：兩欄都是數字，依序是那一鍵在按鍵串裡的位置（從 0 起）與核心的回傳碼；只有失敗時寫）、71（啟動時的按鍵習慣：兩欄都是數字，不是時間，依序是交給核心的值（0 或 1）與來源碼（0 沒有值、1 有效的值、2 無效的值而當 1）；70、71 都不套「開始 ≤ 結束」）。只有數字、不含任何按鍵或文字（R2）。
- DLL 記：60 等常駐程序回應（開始＝送出、結束＝收到）；61 `CreateProcessW` 成功的時刻（同一個 QPC 時鐘；冷啟動秒數＝常駐程序事件 2 的結束減這個時刻）；62 逾時或斷線（兩欄都是 Win32 錯誤碼）；63 不符條件而直通（兩欄都是原因碼：1 不准連線、2 不符啟動條件、3 退避中或另一條執行緒正在啟動、4 `CreateProcessW` 失敗（含 Job 不准脫離）、5 啟動後仍找不到、6 `ERROR_PIPE_BUSY`、7 `NotReady`、8 `Busy`、9 伺服器核對不過、10 `Refuse { 1 }`、11 `Refuse { 2 }`（含不認得的原因碼）、12 `Refuse { 3 }`、13 回應型別不對）。42 的意思是「被擋住」（斷路器、`Refuse { 1 }` 之後、`Refuse { 3 }` 的冷卻，或組字區空時連不上常駐程序，§5.4），52 是同樣的擋住出現在 `OnKeyDown`。

### 5.9 介面的層級

常駐程序不畫任何介面。組字底線仍是 TSF 顯示屬性（§3.10）；故障時也不顯示任何東西。

### 5.10 CI

- `windows` job：建 `shanjie-tip`、`shanjie-host`（各自先帶 `timing`、再不帶，檢查的是不帶的那一份），`dumpbin /dependents` 核對 DLL 與 exe 都沒有 C 執行階段的匯入、DLL 的匯入表列得到 `d2d1.dll` 與 `dwrite.dll` 而且沒有 delay-load 的匯入，`dumpbin /exports` 核對 DLL 沒有 `shanjie_` 開頭的匯出（另核對 `DllGetClassObject` 在），`cargo tree -p shanjie-tip` 沒有 `core`，印出大小；跑三個 crate 的測試（線上格式、客戶端的模擬等級、對真的伺服器與引擎的兩條連線與限流、DLL 與常駐程序的 R2 行為測試）。
- Linux `debug` job 跑 `shanjie-win-proto` 的測試；macOS 的 core job 跑整個 workspace。

### 5.11 驗證方式

測試 VM 同 §3.13（從乾淨檢查點開始、結束還原）。驗收步驟：

1. CI 全綠。
2. **暖機**（在 (b) 之前）：先在記事本（有套件身分）送一鍵，核對沒有啟動常駐程序；再開「執行」對話框（`explorer.exe`，沒有套件身分）送 `s`，等常駐程序的計時檔出現事件 2、而且只有一支 `shanjie-host`（上限 60 秒），核對對話框裡是直通的 `s`，Esc 關掉。記錄冷啟動秒數（事件 61 到常駐程序事件 2 的結束），核對常駐程序的父程序是 explorer、沒有套件身分、權杖是 Medium。
3. **逐字相同**：§3.13 的 (b)、(c)、(d)、(e)、(e2) 用同樣的鍵與預期字。
4. **模型只載一份**：每個檢查點只有一支常駐程序；DLL 的計時檔都沒有事件 2，每個 `host-*.txt` 剛好一筆事件 2。
5. **記憶體**：記事本與 explorer 各做兩組（微軟注音、善解），取樣點相同、兩組之間開「執行」對話框的次數相同。判準：善解的差減掉微軟注音的差 < 10 MB。常駐程序的私有記憶體照實報。
6. **延遲**：DLL 的每鍵（事件 3）中位數、p95、最大值，p95 < 16 ms；事件 60 的 p95 另報；首鍵分常駐程序已在跑（目標 < 16 ms）與冷啟動兩種報。另報事件 65 的最高值。
7. **故障**：(k1) 記事本組字中結束常駐程序，下一鍵照 §5.6 第一列（組字結束、字留著、這一鍵被吃掉），記事本沒有當掉、沒有啟動常駐程序；在「執行」對話框送一鍵重起常駐程序後，記事本打同一句逐字相同。只記錄、不擋：(k2) 設定 App 的搜尋框（字對不對、事件 64）；(k3) 以系統管理員身分開的記事本（字對不對、常駐程序沒有換、仍是 Medium；常駐程序沒在跑時直通、不啟動）；(k4) 模型載入期間打字（不卡 200 ms、之後不重複出字）；(k5) Low 完整性的測試程序對真的 pipe 呼叫 `CreateNamedPipeW`，應該被拒絕或拿到 `ERROR_PIPE_BUSY`。
8. **R2 掃描**照 §3.13 (g)，計時資料夾收 `timing-*.txt` 與 `host-*.txt`、事件代碼收 60～71；`%LOCALAPPDATA%\shanjie-dev\` 底下只有計時檔。
9. **取消註冊**照 §3.13 (h)，另外核對重開之後沒有 `shanjie-host` 在跑。
10. 單元測試（CI）：兩條連線，B 用 A 的 `ctx` 送 `Release`、`Reset`，A 的狀態不變；B 用 A 的 `ctx` 送 `Key`，B 只拿到自己那一鍵、拿不到 A 的 `preedit`（單一引擎下這一鍵接手，A 下一鍵看到 `took_over`）；限流（`Refuse { 2 }`）之後下一條連線照常可用；模擬等級是 `SecurityIdentification`；`proto` 的解析規則。
11. **量測結果**（測試 VM，常駐程序版；延遲是 DLL 裡花的時間，不含 App 畫字）：
    - 記憶體（私有位元組，善解組減微軟注音組，取樣點與動作相同）：記事本 +1.17 − (−0.03) = 1.2 MB；explorer −8.9 − (+10.1) MB（explorer 自己的私有記憶體在兩次取樣之間會上下浮動約 10 MB，所以只說明它沒有增加到門檻）。常駐程序本身約 311～314 MB（DLL 內核心時每個打字的 App 各多約 300 MB，§3.13）。
    - 每鍵延遲（DLL 事件 3）：記事本中位數 1.1 ms、p95 4.5 ms、最大 10.7 ms；「執行」對話框 p95 4.0 ms；設定 App 搜尋框 p95 4.1 ms。等常駐程序回應（事件 60）p95 2.9～3.9 ms。常駐程序已在跑時的首鍵 0.7～3.5 ms。
    - 冷啟動（`CreateProcessW` 到模型載完）2.3～2.9 秒；同時連線數最高 2。
    - 記錄、不擋的格：常駐程序還沒起來時與載入期間，「執行」對話框裡直通的鍵照原樣出現；常駐程序在跑時，空白鍵、Shift＋A 也照原樣出現；設定 App 的搜尋框字對（它是封裝 App、不是 AppContainer，沒有事件 64）；Low 完整性的程序對真的 pipe 建實例得到 `ERROR_PIPE_BUSY`；以系統管理員身分開的記事本沒有跑到前景，沒有量到。

### 5.12 範圍外

- 登入時預先啟動的排程工作、安裝程式、更新流程、簽章。
- 學習檔（含 AppContainer 客戶端的學習政策，以及輸入範圍含 `IS_PRIVATE` 的 context 不學，§3.15）；背景重排與 `Pushed` 的實際使用；預測列；候選窗的底部列、捲動點、展開成三欄與滑鼠點選；聊天／書面、分級等設定。
- x86、ARM64 的 DLL 與 ARM64 的常駐程序；每個客戶端一個引擎；可 mmap 的零拷貝模型格式；常駐程序在沒有連線時自己結束。
- 之後新增送往常駐程序的資料（前文回傳、學習的查詢）時，要重審 §5.5 第 1、5 點與 AppContainer 客戶端的政策。
