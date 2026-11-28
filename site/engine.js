// 善解輸入法 v0.9.1 網頁試打可抽換引擎層（預設查表對照，可透過 setBackend 抽換為真實 WASM / 伺服器引擎）
(function (root) {
  'use strict';

  var SYMBOLS = 'ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ';
  var KEYS_STANDARD = '1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-';
  var TONE_KEYS = { '6': 'ˊ', '3': 'ˇ', '4': 'ˋ', '7': '˙' };
  var KEY_TO_SYM = {};
  var SYM_COL = {};
  for (var idx = 0; idx < SYMBOLS.length; idx++) {
    KEY_TO_SYM[KEYS_STANDARD[idx]] = SYMBOLS[idx];
    SYM_COL[SYMBOLS[idx]] = idx < 21 ? 0 : idx < 24 ? 1 : 2;
  }

  var PUNCT_KEY_MAP = [
    ['Option+Shift+;', '：'],
    ['Option+;', '；'],
    ['Option+,', '，'],
    ['Option+[', '「'],
    ['Option+]', '」'],
    ['Option+\\', '、']
  ];

  var CATEGORY_ORDER = [
    ['colloquial', '口語'],
    ['long', '長句'],
    ['news', '新聞'],
    ['fiction', '小說'],
    ['mixed', '中英數混打'],
    ['feature', '功能示範']
  ];

  var FEATURE_ORDER = [
    ['pangu', '盤古之白（中英文自動加空格）'],
    ['context', '看前文選字（最多 64 字）'],
    ['rerank', '停頓時語意校正（三段）'],
    ['prediction', '即時預測（Tab 選取）'],
    ['mixed', '中英混打・正式大小寫・混合詞'],
    ['packs', '詞包（5 類）'],
    ['fuzzy', '模糊輸入與鄰鍵打錯'],
    ['memory', '選字記憶（依前文）'],
    ['profile', '聊天與書面切換'],
    ['tone', '變調與口語讀音'],
    ['punct', 'Option＋符號鍵全形標點']
  ];

  var customBackend = null;

  function getData() {
    if (root.SHANJIE_ENGINE_DATA && Array.isArray(root.SHANJIE_ENGINE_DATA.sentences)) {
      return root.SHANJIE_ENGINE_DATA;
    }
    if (typeof require === 'function') {
      try {
        var loaded = require('./engine-data.js');
        if (loaded && Array.isArray(loaded.sentences)) {
          root.SHANJIE_ENGINE_DATA = loaded;
          return loaded;
        }
      } catch (_err) {
        // ignore in browser-like environments
      }
    }
    return { version: '0.9.1-cli-2ff55dcc', notice: '', sentences: [] };
  }

  function isHanCodePoint(cp) {
    return (
      (cp >= 0x3400 && cp <= 0x4dbf) ||
      (cp >= 0x4e00 && cp <= 0x9fff) ||
      (cp >= 0xf900 && cp <= 0xfaff) ||
      (cp >= 0x20000 && cp <= 0x2a6df) ||
      (cp >= 0x2a700 && cp <= 0x2ebef) ||
      (cp >= 0x30000 && cp <= 0x3134f) ||
      (cp >= 0x2f800 && cp <= 0x2fa1f) ||
      cp === 0x3007
    );
  }

  function charKind(ch) {
    if (!ch) return 'other';
    if (/^[A-Za-z0-9]$/.test(ch)) return 'ascii';
    var cp = ch.codePointAt(0);
    if (cp !== undefined && isHanCodePoint(cp)) return 'han';
    return 'other';
  }

  function needsPanguSpace(a, b) {
    return (a === 'han' && b === 'ascii') || (a === 'ascii' && b === 'han');
  }

  /**
   * 精確對應 macOS 殼端 CJKSpacing.apply(previous:text:)：
   * 僅在漢字與半形英數 [A-Za-z0-9] 直接相鄰處補一個 U+0020（含開頭依 prevChar 判斷），結尾與標點不補。
   */
  function applyPanguSpacing(text, prevChar) {
    var src = String(text == null ? '' : text);
    var prevStr = String(prevChar == null ? '' : prevChar);
    var prevLast = prevStr ? Array.from(prevStr).slice(-1)[0] : '';
    var last = charKind(prevLast);
    var out = '';
    var chars = Array.from(src);
    for (var i = 0; i < chars.length; i++) {
      var c = chars[i];
      var k = charKind(c);
      if (needsPanguSpace(last, k)) {
        out += ' ';
      }
      out += c;
      last = k;
    }
    return out;
  }

  function isBopomofoChar(ch) {
    var cp = ch.codePointAt(0);
    return (cp >= 0x3105 && cp <= 0x312f) || ch === 'ˊ' || ch === 'ˇ' || ch === 'ˋ' || ch === '˙';
  }

  function isDaqianSymbolKey(ch) {
    return Object.prototype.hasOwnProperty.call(KEY_TO_SYM, ch);
  }

  function isDaqianToneKey(ch) {
    return Object.prototype.hasOwnProperty.call(TONE_KEYS, ch);
  }

  /**
   * 檢查一段連續按鍵（不含空白）是否可解成一或多組合法標準大千注音音節：
   * 同音節內必須符合 聲母(col 0) < 介音(col 1) < 韻母(col 2) 的遞增欄位順序，不可含大寫字母。
   */
  function tryDecodeDaqianChunk(chunk, hasTrailingFirstTone) {
    if (!chunk || /[A-Z]/.test(chunk)) return null;
    var isAllDigits = /^\d+$/.test(chunk);
    if (isAllDigits && chunk.length === 1) return null;

    var out = [];
    var syms = [];
    var lastCol = -1;

    for (var i = 0; i < chunk.length; i++) {
      var ch = chunk[i];
      if (isDaqianSymbolKey(ch)) {
        var sym = KEY_TO_SYM[ch];
        var col = SYM_COL[sym];
        if (col <= lastCol) {
          return null; // 同音節欄位順序不合法或重複（例如 push, bug, claude, review）
        }
        syms.push(sym);
        lastCol = col;
      } else if (isDaqianToneKey(ch)) {
        if (syms.length === 0) {
          return null; // 沒有注音符號卻先按聲調（例如純數字 3、6）
        }
        if (isAllDigits && syms.length === 1 && (syms[0] === 'ㄅ' || syms[0] === 'ㄉ')) {
          return null; // ㄅ、ㄉ 不可單獨成音節（避免純數字 13、14、24 被誤判為注音）
        }
        out.push(syms.join('') + TONE_KEYS[ch]);
        syms = [];
        lastCol = -1;
      } else if (ch === '␣') {
        if (syms.length === 0) return null;
        out.push(syms.join(''));
        syms = [];
        lastCol = -1;
      } else {
        return null;
      }
    }

    if (syms.length > 0) {
      if (isAllDigits) return null; // 純數字（無 ␣ 且無聲調鍵，如 10、28）保留為數字
      if (hasTrailingFirstTone) {
        out.push(syms.join('')); // 1st tone via space
      } else {
        // 尚在輸入中（未按聲調）
        out.push(syms.join(''));
      }
    }
    return out;
  }

  /**
   * 將標準大千按鍵字串轉成注音符號串（保留原本已輸入的注音、英文詞、數字與全形標點）。
   */
  function standardKeysToBopomofo(str) {
    var input = String(str == null ? '' : str);
    if (!input) return '';

    // 先處理 Option+符號 標記
    var working = input;
    for (var p = 0; p < PUNCT_KEY_MAP.length; p++) {
      working = working.split(PUNCT_KEY_MAP[p][0]).join(' ' + PUNCT_KEY_MAP[p][1] + ' ');
    }

    // 逐段掃描：保留注音與非 ASCII 字元，僅對連續 ASCII/␣ 段進行判斷
    var tokens = [];
    var i = 0;
    while (i < working.length) {
      var ch = working[i];
      if (ch === ' ' || ch === '\t' || ch === '\n') {
        i++;
        continue;
      }
      if (ch.charCodeAt(0) > 127 && ch !== '␣') {
        // 中文、注音或全形標點
        if (isBopomofoChar(ch)) {
          var j = i;
          while (j < working.length && isBopomofoChar(working[j])) {
            j++;
          }
          tokens.push(working.slice(i, j));
          i = j;
        } else {
          tokens.push(ch);
          i++;
        }
        continue;
      }

      // 連續可見 ASCII / ␣ 區塊
      var k = i;
      while (k < working.length && working[k] !== ' ' && working[k] !== '\t' && working[k] !== '\n' && (working[k].charCodeAt(0) <= 127 || working[k] === '␣')) {
        k++;
      }
      var chunk = working.slice(i, k);
      var followedBySpace = k < working.length && working[k] === ' ';
      var decoded = tryDecodeDaqianChunk(chunk, followedBySpace);
      if (decoded && decoded.length > 0) {
        for (var d = 0; d < decoded.length; d++) {
          tokens.push(decoded[d]);
        }
      } else {
        tokens.push(chunk);
      }
      i = k;
    }

    return tokens.join(' ');
  }

  function normalizeNeutralDot(tok) {
    if (tok.indexOf('˙') !== -1 && tok.length > 1) {
      return tok.replace(/˙/g, '') + '˙';
    }
    return tok;
  }

  function normalizeBopomofoTokens(str) {
    var converted = standardKeysToBopomofo(str);
    if (!converted) return [];
    return converted
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .map(normalizeNeutralDot);
  }

  function tokenizeExpected(text) {
    var units = [];
    var s = String(text || '');
    var i = 0;
    while (i < s.length) {
      var ch = s[i];
      if (ch === ' ') {
        i++;
        continue;
      }
      if (/^[A-Za-z0-9]$/.test(ch)) {
        var j = i;
        while (j < s.length && /^[A-Za-z0-9]$/.test(s[j])) {
          j++;
        }
        units.push(s.slice(i, j));
        i = j;
      } else {
        units.push(ch);
        i++;
      }
    }
    return units;
  }

  function joinExpectedPrefix(expected, count) {
    var units = tokenizeExpected(expected);
    if (count <= 0) return '';
    if (count >= units.length) return expected;
    // 在 expected 原字串裡找到第 count 個 unit 的結束位置，以精確保留原有空白
    var s = String(expected || '');
    var seen = 0;
    var i = 0;
    while (i < s.length && seen < count) {
      if (s[i] === ' ') {
        i++;
        continue;
      }
      if (/^[A-Za-z0-9]$/.test(s[i])) {
        while (i < s.length && /^[A-Za-z0-9]$/.test(s[i])) i++;
      } else {
        i++;
      }
      seen++;
    }
    return s.slice(0, i).trim();
  }

  function scoreContext(entry, ctx) {
    var eCtx = String(entry.context || '').trim();
    var uCtx = String(ctx || '').trim();
    if (uCtx && eCtx) {
      if (uCtx === eCtx) return 100;
      if (uCtx.endsWith(eCtx) || eCtx.endsWith(uCtx) || uCtx.indexOf(eCtx) !== -1) return 60;
      return -10;
    }
    if (!uCtx && !eCtx) return 20;
    return 0;
  }

  function checkFuzzyAllowed(entry, settings) {
    if (!entry.fuzzyRequired) return true;
    var fuzzySoundOn = Boolean(
      settings && typeof settings.fuzzy === 'object' ? settings.fuzzy && settings.fuzzy.sound : settings && settings.fuzzy
    );
    var adjacentOn = Boolean(
      (settings && settings.adjacentKey) ||
      (settings && typeof settings.fuzzy === 'object' && settings.fuzzy && settings.fuzzy.adjacent)
    );
    if (entry.fuzzyRequired === 'sound') return fuzzySoundOn;
    if (entry.fuzzyRequired === 'adjacent') return adjacentOn;
    return true;
  }

  var CATEGORY_ALIASES = {
    spoken: 'colloquial',
    novel: 'fiction'
  };

  function resolveCategory(cat) {
    return CATEGORY_ALIASES[cat] || cat;
  }

  function toCandidateObjects(words, activeTag) {
    var seen = {};
    var out = [];
    for (var i = 0; i < words.length; i++) {
      var w = typeof words[i] === 'string' ? words[i] : (words[i] && words[i].word);
      if (!w || seen[w]) continue;
      seen[w] = true;
      var existingTag = words[i] && typeof words[i] === 'object' ? words[i].tag : '';
      out.push({
        word: w,
        active: out.length === 0,
        tag: out.length === 0 ? (activeTag || existingTag || '') : existingTag
      });
    }
    return out;
  }

  function applySettingsToEntry(entry, context, rawInput, settings, trace) {
    var s = settings || {};
    var text = entry.expected;
    var predictions = Array.isArray(entry.predictions) ? entry.predictions.slice(0, 9) : [];
    var downCandidates = Array.isArray(entry.downCandidates) ? entry.downCandidates.slice(0, 9) : [];

    // 1. 聊天與書面設定檔
    var prof = s.profile || 'auto';
    if (entry.featureTag === 'profile' || prof !== 'auto') {
      trace.push(
        '設定檔：' +
          (prof === 'chat'
            ? '聊天（常談 SJ-CL-2-69K＋SJ-CX-2-950K）'
            : prof === 'formal'
              ? '書面（連文 SJ-CX-2-7.7M）'
              : '依 App 自動')
      );
    }

    // 2. 詞包開關
    if (entry.packId) {
      var packOn =
        s.packs && Object.prototype.hasOwnProperty.call(s.packs, entry.packId)
          ? Boolean(s.packs[entry.packId])
          : entry.packId !== 'quote';
      if (!packOn) {
        text = entry.packOffFallback || entry.expected;
        trace.push('詞包「' + entry.packId + '」為關閉狀態：退回一般詞庫結果');
      } else {
        trace.push('詞包「' + entry.packId + '」為開啟狀態：命中詞包詞條');
      }
    }

    // 3. 停頓時語意校正（保守 / 標準 / 積極 / 關閉）
    var rerankMode = s.rerank || 'standard';
    if (entry.rerankBefore || entry.rerankAfter) {
      if (rerankMode === 'off') {
        text = entry.rerankBefore || text;
        trace.push('語意校正（關閉）：保留整句解碼第 1 名「' + text + '」');
      } else {
        text = entry.rerankAfter || text;
        var label = rerankMode === 'conservative' ? '保守' : (rerankMode === 'eager' || rerankMode === 'aggressive') ? '積極' : '標準';
        trace.push('語意校正（會意 SJ-SR-10-31M・' + label + '）：於前 8 名原地重排為「' + text + '」');
      }
    }

    // 4. 盤古之白（中英文之間自動加空格，預設關）
    if (s.cjkSpacing) {
      var prev = context ? Array.from(String(context)).slice(-1)[0] : entry.context ? Array.from(String(entry.context)).slice(-1)[0] : '';
      var spaced = applyPanguSpacing(text, prev);
      if (spaced !== text) {
        text = spaced;
      }
      trace.push('盤古之白（開啟）：套用 CJKSpacing.apply');
    }

    return {
      text: text,
      barMode: predictions.length > 0 ? 'prediction' : 'none',
      barItems: predictions,
      barSelected: null,
      predictions: predictions,
      downCandidates: downCandidates
    };
  }

  function decode(rawInput, options) {
    var opts = options || {};
    var context = opts.context || '';
    var settings = opts.settings || {};

    if (typeof customBackend === 'function') {
      return customBackend(rawInput, { context: context, settings: settings });
    }

    var data = getData();
    var sentences = data.sentences;
    var input = String(rawInput == null ? '' : rawInput).trim();
    var converted = standardKeysToBopomofo(input);

    if (!input) {
      return {
        matched: false,
        partial: false,
        composition: '',
        bopomofo: '',
        candidates: [],
        prediction: null,
        notice: '請輸入注音按鍵、大千英文鍵或點選範例句試打。',
        trace: []
      };
    }

    var inputTokens = normalizeBopomofoTokens(input);
    var inputCompact = inputTokens.join('');
    var inputCompactLower = inputCompact.toLowerCase();
    var inputTextCompact = input.replace(/\s+/g, '');
    var inputDaqianCompact = input.replace(/[\s␣]+/g, '');

    // 1. 精確比對
    var exactMatches = [];
    for (var i = 0; i < sentences.length; i++) {
      var e = sentences[i];
      var eTokens = e.zhuyin.split(/\s+/).map(normalizeNeutralDot);
      var eCompact = eTokens.join('');
      var eDaqianCompact = e.daqianKeys.replace(/[\s␣]+/g, '');
      var eTextCompact = e.expected.replace(/\s+/g, '');

      var isExact =
        eCompact === inputCompact ||
        eCompact.toLowerCase() === inputCompactLower ||
        eDaqianCompact === inputDaqianCompact ||
        eDaqianCompact.toLowerCase() === inputDaqianCompact.toLowerCase() ||
        eTextCompact === inputTextCompact;

      if (isExact) {
        exactMatches.push({ entry: e, score: scoreContext(e, context) });
      }
    }

    if (exactMatches.length > 0) {
      exactMatches.sort(function (a, b) { return b.score - a.score; });
      var best = exactMatches[0].entry;

      if (!checkFuzzyAllowed(best, settings)) {
        return {
          matched: false,
          partial: false,
          entryId: best.id,
          composition: '',
          bopomofo: converted,
          candidates: [],
          prediction: null,
          notice: '目前『模糊輸入／鄰鍵打錯』為關閉（預設關），請在上方設定開啟後再試這組按鍵。',
          trace: ['模糊輸入／鄰鍵打錯為關閉（預設關）：未套用容錯']
        };
      }

      if (best.featureTag === 'tone' && settings && settings.toneSandhi === false) {
        return {
          matched: false,
          partial: false,
          entryId: best.id,
          composition: '',
          rawComposition: converted,
          bopomofo: best.zhuyin,
          candidates: [],
          prediction: null,
          notice: '目前「變調與口語讀音」為關閉，僅接受字典本調；請在上方設定開啟後再試變調、輕聲與合音讀法。',
          trace: ['變調與口語讀音（關閉）：僅接受字典本調']
        };
      }

      var trace = [
        '整句解碼命中「' + best.id + '」（' + best.categoryLabel + '）'
      ];
      var applied = applySettingsToEntry(best, context, input, settings, trace);
      var quotePackOff =
        best.packId === 'quote' &&
        !(settings && settings.packs && settings.packs.quote === true);

      return {
        matched: true,
        partial: false,
        entryId: best.id,
        entry: best,
        composition: applied.text,
        rawComposition: converted,
        bopomofo: best.zhuyin,
        daqianKeys: best.daqianKeys,
        barMode: applied.barMode,
        barItems: applied.barItems,
        barSelected: applied.barSelected,
        predictions: applied.predictions,
        downCandidates: applied.downCandidates,
        prediction: null,
        predictionInfo: best.prediction || null,
        appleText: best.appleText || null,
        cliStatus: best.cliStatus,
        source: best.source,
        notice:
          best.cliStatus === '已實機VM驗證(seg4)'
            ? '此句已於 macOS 實機 VM 雙軌驗證（seg4）。'
            : quotePackOff
              ? '「經典台詞」詞包目前為關閉（預設關），退回一般詞庫結果；在上方設定開啟詞包後直接命中經典台詞詞條（' + best.cliStatus + '）。'
              : '網頁試打對照表輸出（' + best.cliStatus + '）。',
        trace: trace
      };
    }

    // 2. 前綴比對（使用者逐音節輸入預先收錄句時）
    var prefixMatches = [];
    for (var m = 0; m < sentences.length; m++) {
      var cand = sentences[m];
      var cTokens = cand.zhuyin.split(/\s+/).map(normalizeNeutralDot);
      var cCompact = cTokens.join('');
      var cDaqianCompact = cand.daqianKeys.replace(/[\s␣]+/g, '');
      var cTextCompact = cand.expected.replace(/\s+/g, '');

      var byZhuyin = inputCompact && cCompact.toLowerCase().indexOf(inputCompactLower) === 0;
      var byDaqian = inputDaqianCompact && cDaqianCompact.toLowerCase().indexOf(inputDaqianCompact.toLowerCase()) === 0;
      var byText = inputTextCompact && cTextCompact.indexOf(inputTextCompact) === 0;

      if (byZhuyin || byDaqian || byText) {
        // 計算已完成的音節／詞元數
        var completedCount = 0;
        if (byText) {
          var units = tokenizeExpected(cand.expected);
          var acc = '';
          for (var u = 0; u < units.length; u++) {
            acc += units[u];
            if (acc.length <= inputTextCompact.length) completedCount = u + 1;
          }
        } else if (byZhuyin) {
          for (var t = 0; t < inputTokens.length && t < cTokens.length; t++) {
            if (inputTokens[t].toLowerCase() === cTokens[t].toLowerCase()) {
              completedCount++;
            } else {
              break;
            }
          }
        } else if (byDaqian) {
          var dToks = cand.daqianKeys.split(/\s+/);
          var accD = '';
          for (var dt = 0; dt < dToks.length; dt++) {
            accD += dToks[dt].replace(/␣/g, '');
            if (accD.toLowerCase().length <= inputDaqianCompact.length &&
                inputDaqianCompact.toLowerCase().indexOf(accD.toLowerCase()) === 0) {
              completedCount = dt + 1;
            } else {
              break;
            }
          }
        }

        prefixMatches.push({
          entry: cand,
          completedCount: completedCount,
          totalTokens: cTokens.length,
          ctxScore: scoreContext(cand, context)
        });
      }
    }

    if (prefixMatches.length > 0) {
      prefixMatches.sort(function (a, b) {
        if (b.ctxScore !== a.ctxScore) return b.ctxScore - a.ctxScore;
        if (b.completedCount !== a.completedCount) return b.completedCount - a.completedCount;
        return a.totalTokens - b.totalTokens;
      });
      var top = prefixMatches[0];
      var pEntry = top.entry;

      if (!checkFuzzyAllowed(pEntry, settings)) {
        return {
          matched: false,
          partial: false,
          entryId: pEntry.id,
          composition: '',
          bopomofo: converted,
          candidates: [],
          prediction: null,
          notice: '目前『模糊輸入／鄰鍵打錯』為關閉（預設關），請在上方設定開啟後再試這組按鍵。',
          trace: ['模糊輸入／鄰鍵打錯為關閉（預設關）：未套用容錯']
        };
      }

      if (pEntry.featureTag === 'tone' && settings && settings.toneSandhi === false) {
        return {
          matched: false,
          partial: false,
          entryId: pEntry.id,
          composition: '',
          rawComposition: converted,
          bopomofo: converted,
          candidates: [],
          prediction: null,
          notice: '目前「變調與口語讀音」為關閉，僅接受字典本調；請在上方設定開啟後再試變調、輕聲與合音讀法。',
          trace: ['變調與口語讀音（關閉）：僅接受字典本調']
        };
      }

      var baseText = pEntry.expected;
      if (pEntry.rerankAfter && settings.rerank !== 'off') baseText = pEntry.rerankAfter;
      if (pEntry.rerankBefore && settings.rerank === 'off') baseText = pEntry.rerankBefore;

      var partialComp = joinExpectedPrefix(baseText, top.completedCount);
      var fullTarget = baseText;
      if (settings.cjkSpacing) {
        var prevC = context ? Array.from(String(context)).slice(-1)[0] : '';
        if (partialComp) partialComp = applyPanguSpacing(partialComp, prevC);
        fullTarget = applyPanguSpacing(baseText, prevC);
      }

      var predTail = '';
      if (partialComp && fullTarget.indexOf(partialComp) === 0) {
        predTail = fullTarget.slice(partialComp.length);
      } else {
        predTail = fullTarget;
      }

      var predItems = [];
      for (var pm = 0; pm < Math.min(prefixMatches.length, 5); pm++) {
        predItems.push(prefixMatches[pm].entry.expected);
      }
      var predObj = pEntry.prediction || {
        trigger: (inputTokens[0] || converted.slice(0, 1)),
        items: predItems,
        hint: '按 Tab 可採納預測，或繼續輸入完整按鍵'
      };

      return {
        matched: false,
        partial: true,
        entryId: pEntry.id,
        entry: pEntry,
        composition: partialComp,
        rawComposition: converted,
        bopomofo: converted,
        barMode: 'none',
        barItems: [],
        barSelected: null,
        predictions: [],
        downCandidates: [],
        prediction: predTail || fullTarget,
        predictionInfo: predObj,
        notice: '正在比對預先收錄句…（繼續打或按 Tab 採納預測）',
        trace: [
          '前綴比對命中「' + pEntry.id + '」（已完成 ' + top.completedCount + '/' + top.totalTokens + ' 音節／詞元）'
        ]
      };
    }

    // 3. 查表未命中：誠實回報，絕不編造輸出
    return {
      matched: false,
      partial: false,
      composition: '',
      bopomofo: converted,
      barMode: 'none',
      barItems: [],
      barSelected: null,
      predictions: [],
      downCandidates: [],
      prediction: null,
      notice:
        '這句還沒收錄在網頁 54 句對照表裡；點上方「啟用瀏覽器真引擎（WASM）」即可直接自由試打，或安裝善解 v0.9.1。',
      trace: ['查表未命中：不編造輸出']
    };
  }

  function getAll() {
    return getData().sentences.slice();
  }

  function getById(id) {
    var list = getData().sentences;
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return list[i];
    }
    return null;
  }

  function getByCategory(cat) {
    var resolved = resolveCategory(cat);
    return getData().sentences.filter(function (s) {
      return s.category === resolved;
    });
  }

  function getByFeature(tag) {
    return getData().sentences.filter(function (s) {
      return s.featureTag === tag;
    });
  }

  function getCategories() {
    var list = getData().sentences;
    return CATEGORY_ORDER.map(function (pair) {
      var id = pair[0];
      var label = pair[1];
      var count = list.filter(function (s) { return s.category === id; }).length;
      return { id: id, label: label, count: count };
    });
  }

  function getFeatures() {
    var list = getData().sentences;
    return FEATURE_ORDER.map(function (pair) {
      var id = pair[0];
      var label = pair[1];
      var count = list.filter(function (s) { return s.featureTag === id; }).length;
      return { id: id, label: label, count: count };
    });
  }

  function setBackend(fn) {
    customBackend = typeof fn === 'function' ? fn : null;
  }

  var api = {
    decode: decode,
    applyPanguSpacing: applyPanguSpacing,
    standardKeysToBopomofo: standardKeysToBopomofo,
    getCategories: getCategories,
    getFeatures: getFeatures,
    getByCategory: getByCategory,
    getByFeature: getByFeature,
    getAll: getAll,
    getById: getById,
    setBackend: setBackend
  };

  root.ShanjieEngine = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
