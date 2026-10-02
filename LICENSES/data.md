# 資料與模型授權清單

| 路徑 | 來源 | 授權 | 備註 |
|---|---|---|---|
| `data/lexicon/mcbpmf-data.txt` | 小麥注音 McBopomofo 3.1.1 的 `data.txt`（片語源自 libtabe `tsi.src`） | MIT（全文 `LICENSES/McBopomofo-MIT.txt`）；libtabe 為 BSD | 可再散布 |
| `eval/sets/trap.txt`、`eval/sets/daily.txt` | 本專案自寫 | CC0 | |
| `eval/dev/*.txt`、`eval/holdout/*`、`eval/learn/cases.tsv` | 本專案自寫 | CC0 | |
| `eval/dev/user-reported.txt` | 使用者回報的真實句子 | CC0（使用者 2026-10-03 同意） | |
| `eval/sets/moedict.txt` | 教育部《重編國語辭典修訂本》例句抽樣（經 g0v/moedict-data） | CC BY-ND 3.0 TW | **只用於評測，不得用來建詞庫或改作** |
| 執行期模型（S5 起） | Gemma 4 E2B 等 | 各自授權（Gemma 4：Apache-2.0） | 不隨安裝檔散布，第一次啟用時下載並顯示授權 |
