@echo off
rem model-v5 契約 §2.1 / §2.5：計數，加 --extra-lexicon（acg-add.tsv）。範本，不是現成可跑：先把下面三個路徑換成 188 上的絕對路徑，再讀 README.md。
rem 慣例同 S2n／S2f：絕對路徑、start /b /wait /affinity FFF（P-core）、PYTHONUTF8=1。
set PYTHONUTF8=1
set REPO=C:\ABSOLUTE\PATH\TO\shanjie-modelv5
set M3=%USERPROFILE%\.cache\shanjie\work\s2f4
set SYNTH=C:\ABSOLUTE\PATH\TO\synth.txt
set S2_WORK=%USERPROFILE%\.cache\shanjie\work\modelv5
rem modelv5 是新目錄；M3（model-v3 的計數）只讀、不會被寫。已存在就停，免得混到舊檔。
if exist "%S2_WORK%\counts-200000.pkl" ( echo %S2_WORK% already has counts & exit /b 1 )
if not exist "%S2_WORK%" mkdir "%S2_WORK%"
rem 輸入雜湊先記到 %S2_WORK%\inputs.sha256（README 的表），再往下跑。
for %%F in ("%USERPROFILE%\.cache\shanjie\sources\zhwiki-20261001-pages-articles.xml.bz2" "%M3%\tune\colloquial-train.txt" "%SYNTH%" "%REPO%\data\lexicon\mcbpmf-data.txt" "%REPO%\data\lexicon\overlay-add.tsv" "%USERPROFILE%\.cache\shanjie\sources\opencc\TWVariants.txt" "%USERPROFILE%\.cache\shanjie\sources\opencc\STCharacters.txt" "%REPO%\data\packs\acg-add.tsv") do certutil -hashfile %%F SHA256 >> "%S2_WORK%\inputs.sha256"
cd /d "%REPO%"
rem acg-add.tsv 的 SHA-256 必須等於 main 上的（不同就停；README 的表）
set ACG=%REPO%\data\packs\acg-add.tsv
rem 1. 口語計數（單一程序，×5 的那份；輸入同 model-v3：colloquial-train.txt 加 synth.txt）
start "" /b /wait /affinity FFF python -u experiments\s2\build_counts_text.py --expected --extra-lexicon "%ACG%" "%S2_WORK%\counts-colloquial3.pkl" "%M3%\tune\colloquial-train.txt" "%SYNTH%"
if errorlevel 1 exit /b 1
rem 2. 維基計數（imap_unordered，位元組不可重現；比內容用 compare_counts.py）
start "" /b /wait /affinity FFF python -u experiments\s2\build_counts.py --articles 200000 --procs 10 --expected --extra-lexicon "%ACG%" 
if errorlevel 1 exit /b 1
rem 3. 建模型（PRUNE=2、D=0.75 在 build_lm.py 裡，不變）；輸出在 S2_WORK，不覆蓋任何現成檔
start "" /b /wait /affinity FFF python -u tools\build_lm.py --out "%S2_WORK%\bigram-v5-nofp.sjlm"
if errorlevel 1 exit /b 1
certutil -hashfile "%S2_WORK%\counts-colloquial3.pkl" SHA256
certutil -hashfile "%S2_WORK%\bigram-v5-nofp.sjlm" SHA256
