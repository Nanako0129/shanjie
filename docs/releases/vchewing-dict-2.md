給[唯音輸入法](https://github.com/vChewing/vChewing-macOS)（vChewing）用的辭典檔，條目、讀音與分數全部從善解的詞庫與模型轉出來，不含唯音原廠辭典的任何一筆。兩個專案合作的經過與唯音那邊怎麼載入，見 issue #143。

**需要唯音 5.1.0 以上。** 這一版依唯音 5.1.0 的第三方建置規範（VanguardLexicon 5.1.0 的 `3rdPartyBuildToolCompliance.md`）建出來，繁體、簡體兩種模式都能用；規範裡有一條沒有照做，見下面「和規範不同的地方」。這份檔只放三個表：meta、簡體、繁體（前後文二元組放在簡體、繁體兩個表裡）；其他類別這份檔一筆都沒有，唯音 5.1.0 會逐表改用它內建的辭典回答（見下面「沒有提供的類別」）。舊版唯音沒有這個逐表回退，請先更新唯音。

第一版 `vchewing-dict-1` 只填了繁體欄、已撤下；繁體模式打出來的結果，這一版和第一版逐句相同。

## 內容

- `VanguardFactoryDict4Typing.txtMap`：唯音 TextMap 2.0 格式，673,936 條、478,787 個讀音鍵，43,858,659 位元組，SHA-256 `edcb34f5dd35ee1104ac746d5235cf0cf80cc8c42de7b5cd0ac8bc844b0cfae1`。建置工具的格式驗證（`--validate`）通過。
- `VanguardFactoryDict4Typing.txtMap.sha256`：上面那個雜湊。
- `NOTICE.txt`：授權與署名。

檔頭的作者標記：`_VENDOR` 是 `BopoMola_善解輸入法`，`_BUILD_TIMESTAMP` 是資料 commit 的時間，`_COMMIT_HASH` 是資料 commit。唯音辭典資料夾裡另外那些配套檔（`phonic_table_Z.txt`、標點表、`template-*.txt` 等）是唯音自己的檔，這裡不附，用唯音內附的就好。

## 沒有提供的類別

和唯音原廠檔比，原廠有、這份檔沒有的類別如下；唯音 5.1.0 會由內建辭典逐表接手，所以這些表這份檔一筆都不放：

- CNS 條目（typeID 7）：不提供。
- 符號詞條目（typeID 9）：不提供。
- 注音文條目（typeID 10）：不提供。
- GBEX 條目（typeID 11）：不提供。
- 非漢字條目（typeID 8）：不提供。
- `_NORM`：唯音規範定義為詞頻基準 N；善解的分數來自語言模型機率，沒有對應的次數，所以不寫（規範允許省略）。
- `DEFAULT_PROB_7`、`DEFAULT_PROB_9`、`DEFAULT_PROB_10`、`DEFAULT_PROB_11`：對應的表不提供，照規範一併省略。

## 和規範不同的地方

- 繁體欄沒有依唯音的 `ChtOrthographySpec.md` 校對字形（規範 §七 #8）：繁體欄直接來自善解的詞庫，是臺灣用字、讀音照善解使用者的習慣，所以不再套一層校對；字形和唯音原廠辭典的用字規則可能不同。

## 從哪裡來

從善解 repo 的 commit `1a9899ac` 建出來：小麥注音基底與它推出的變調讀音、疊加層、名單層（含「善解」「唯音」等軟體名稱與車站、醫院、公司等專有名詞）、動漫、資訊、迷因、美妝四個詞包，以及通語 SJ-LM-7-9.7M 與承接 SJ-KN-2-397K 的詞頻。單字與詞的分數來自這些詞庫與通語的詞頻，另外收 40 萬條「前一個詞 → 下一個詞」的二元組，讓唯音在常見的前後文裡選對。簡體欄與簡體的二元組由繁體條目用 OpenCC 的繁轉簡表轉出。

重現方式：用 `tools/vanguard_textmap.py`，把 `--shanjie-root` 指向 commit `1a9899ac` 的乾淨 worktree、帶上定版旗標，會建出逐位元組相同的檔。步驟與旗標見 [`docs/spec/vanguard-textmap.md`](https://github.com/Nanako0129/shanjie/blob/main/docs/spec/vanguard-textmap.md) 第 10.1 節。

## 量到的效果

兩邊都用唯音 5.1.0 正式版的引擎、預設設定，逐鍵打大千注音，每句從空白打起（不帶前文），送出的整句和正解逐字相同才算對。「這份辭典」那一欄是這份檔當主辭典、唯音內建辭典在後面補沒提供的表：

| 集合與模式 | 唯音內建辭典 | 這份辭典 |
|---|---:|---:|
| 口語調參集，繁體模式（Common Voice 與 Tatoeba 的句子，2,992 句） | 65.61%（1,963 句） | 77.54%（2,320 句） |
| 口語調參集，簡體模式（同一份 2,992 句，正解整句轉成簡體） | 65.44%（1,958 句） | 78.71%（2,355 句） |

這份集合也是挑建置參數時看的集合，數字會偏樂觀。簡體模式的正解，是把繁體正解用建這份檔簡體欄的同一套 OpenCC 表（`TSPhrases.txt` 最長比對、沒對到的字用 `TSCharacters.txt`）整句轉成簡體；這份檔的簡體欄也是用這套表轉的，所以簡體那一列對這份檔偏有利。簡體模式另外核對過常見詞打出簡體字形（这个、后来、发现、头发、台湾）。

## 授權

整份檔以 CC BY-SA 4.0 散布（https://creativecommons.org/licenses/by-sa/4.0/ ）：轉載或改作都要署名，改作也要用同樣的授權釋出。簡體字形用 OpenCC 的表轉出（Apache-2.0，表本身不在檔裡）。署名與各來源的授權（含小麥注音的 MIT、name-suggestion-index 的 BSD-3、政府資料開放授權條款、OpenCC 的 Apache-2.0）寫在 `NOTICE.txt`；逐檔的說明在善解 repo 的 `LICENSES/data.md`。
