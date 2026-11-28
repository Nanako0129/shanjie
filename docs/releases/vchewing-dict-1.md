給[唯音輸入法](https://github.com/vChewing/vChewing-macOS)（vChewing）用的辭典檔，條目、讀音與分數全部從善解的詞庫與模型轉出來，不含唯音原廠辭典的任何一筆。兩個專案合作的經過與唯音那邊怎麼載入，見 issue #143。

## 內容

- `VanguardFactoryDict4Typing.txtMap`：唯音 TextMap 2.0 格式，620,219 條、478,784 個讀音鍵，33,844,725 位元組，SHA-256 `7f5d0a9228835464eb30743e2e16c90013383674e529ae71931a32909a10d130`。建置工具的格式驗證（`--validate`）通過。
- `VanguardFactoryDict4Typing.txtMap.sha256`：上面那個雜湊。
- `NOTICE.txt`：授權與署名。

唯音辭典資料夾裡另外那些配套檔（`phonic_table_Z.txt`、標點表、`template-*.txt` 等）是唯音自己的檔，這裡不附，用唯音內附的就好。

## 從哪裡來

從善解 repo 的 commit `1a9899ac` 建出來：小麥注音基底與它推出的變調讀音、疊加層、名單層（含「善解」「唯音」等軟體名稱與車站、醫院、公司等專有名詞）、動漫、資訊、迷因、美妝四個詞包，以及通語 SJ-LM-7-9.7M 與承接 SJ-KN-2-397K 的詞頻。單字與詞的分數來自這些詞庫與通語的詞頻，另外收 40 萬條「前一個詞 → 下一個詞」的二元組，讓唯音在常見的前後文裡選對。

重現方式：用 `tools/vanguard_textmap.py`（PR #140 加入），把 `--shanjie-root` 指向 commit `1a9899ac` 的 worktree、帶上定版旗標，會建出逐位元組相同的檔。步驟與旗標見 [`docs/spec/vanguard-textmap.md`](https://github.com/Nanako0129/shanjie/blob/main/docs/spec/vanguard-textmap.md) 第 10 節。

## 量到的效果

和唯音 5.0.6 原廠辭典比：兩邊都用唯音 5.0.6 的引擎、預設設定，逐鍵打大千注音，每句從空白打起（不帶前文），送出的整句和正解逐字相同才算對：

| 集合 | 原廠辭典 | 這份辭典 |
|---|---:|---:|
| 口語調參集（Common Voice 與 Tatoeba 的句子，2,992 句） | 65.61%（1,963 句） | 77.54%（2,320 句） |
| 書面調參集（中文維基百科的句子，2,182 句） | 36.21%（790 句） | 54.90%（1,198 句） |

這兩份集合也是挑建置參數時看的集合，數字會偏樂觀。善解另外三份公開評測集的數字之後補上。

## 授權

整份檔以 CC BY-SA 4.0 散布（https://creativecommons.org/licenses/by-sa/4.0/ ）：轉載或改作都要署名，改作也要用同樣的授權釋出。署名與各來源的授權（含小麥注音的 MIT、name-suggestion-index 的 BSD-3、政府資料開放授權條款）寫在 `NOTICE.txt`；逐檔的說明在善解 repo 的 `LICENSES/data.md`。
