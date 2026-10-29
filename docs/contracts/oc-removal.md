# 契約：拿掉核心裡的 0c 推論程式（0c 實驗開關退場）

狀態：READY（2026-10-29，plan-verifier 一次 READY）。已實作（分支 `feat/oc-removal`）：§1 全部拿掉；§2 的第 2 條在 Mac 上量過，拿掉前後 6 格（聊天、書面 × dev302、打字測驗、錯字回報）的 rowstats 與摘要行逐位元組相同。

## 0. 起因與範圍

- 研究紀錄 2026-10-29「X0C-6」：在 v0.6.0 的候選上，出貨的重排明顯好過 0c 融合（discordtune 0c 對出貨 +39／−110，精確 McNemar p 5.0e-09）。
- 維護者同日選「退場，核心的 0c 程式也拿掉（推薦）」：`docs/contracts/oc-experimental.md` 的 B 片（App 端）不做；第一片合併進核心、沒有任何出貨路徑在用的 0c 推論程式拿掉。
- 0c 仍是量尺與教師：`experiments/x0c/`（`score_0c.py`、`versus.py`、它們的測試與 `requirements.txt`）、`eval/bench/external/x0c.json`、`docs/contracts/x0c-compare.md` 都不動。
- **要做到**：核心、CLI、App、C ABI 的行為逐位元組不變；repo 裡不再有沒人呼叫的 0c 推論程式、它的 golden 與只為它存在的 Python 工具。
- **怎麼看到**：§2 的驗收。

## 1. 拿掉的東西

| 類別 | 檔案 |
|---|---|
| Rust 模組 | `core/src/oc.rs`，`core/src/lib.rs` 的 `pub mod oc;` |
| Rust 測試與範例 | `core/tests/oc_tiny.rs`、`core/examples/oc_score.rs` |
| golden | `eval/golden/oc-tiny/scores.tsv`、`eval/golden/oc-tiny/tiny.sjoc` |
| Python 工具 | `tools/convert_oc.py`、`tools/gen_oc_tiny.py`、`tools/oc_ref.py`、`tools/test_oc_ref.py`，以及 `Makefile` 裡跑 `tools/test_oc_ref.py` 的那一行 |

- 連帶整理：
  - `core/src/tinylm.rs` 的 `gemm`、`exp_into`、`layer_norm` 只有 `tinylm.rs` 自己與 `oc.rs` 在用：拿掉 `oc.rs` 後改回私有（`fn`），`read_bounded`（`engine.rs` 也用）與 `trigram::Mapped`（`lm.rs` 也用）不動。
  - 兩處提到 `oc.rs` 的註解（`tinylm.rs` 的 LayerNorm 說明、`trigram.rs` 的 `OcError::Io` 比較）改成不再指向已不存在的模組。
  - 拿掉之後再 grep 一次 `oc::`、`oc_tiny`、`oc_score`、`oc_ref`、`convert_oc`、`gen_oc_tiny`、`sjoc`、`OcError`，repo 裡除了研究紀錄、本契約、`oc-experimental.md` 的歷史內容以外不再出現。
- `docs/contracts/oc-experimental.md` 的狀態句加上「核心程式已在本契約的 PR 拿掉」；`docs/PLAN.md` 的 X0c 條同步一句；研究紀錄記一筆。

## 2. 驗收

1. `cargo build --release` 全部目標不出新的警告（CI 沒有跑 clippy，這片不加）；`make test-python`、`make test-swift` 通過；完整 Rust 測試在 188 的 WSL 通過；CI 全綠（含 C ABI smoke）。
2. 行為不變：拿掉前後的評測 CLI 在出貨設定（聊天與書面，`--context --packs acg,it --tinylm`，聊天加 `--colloquial-core`）跑 dev302、打字測驗、錯字回報，rowstats 與摘要行的 `top1_sha256` 逐位元組相同（Mac 上的少量量測，公開集合）。
3. 程式改動的關卡：`/code-review` 沒有未處置的問題、fresh verifier CONFIRMED、CI 全綠，merge commit。

## 3. 停止條件與範圍外

- **停止條件**：拿掉之後有任何出貨路徑的編譯或測試要改行為才能過（代表 0c 程式其實被用到），停下來回報維護者。
- **範圍外**：`experiments/x0c/` 與它的 CI 步驟（X0c 的 Python 測試照跑）；App 端（本來就沒有做）；`tinylm.rs` 其他重構。
