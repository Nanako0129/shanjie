(function (root) {
  'use strict';

  var KEY_CHAR = 1;
  var KEY_SPACE = 2;
  var KEY_ENTER = 3;
  var KEY_BACKSPACE = 4;
  var KEY_DELETE = 5;
  var KEY_ESC = 6;
  var KEY_LEFT = 7;
  var KEY_RIGHT = 8;
  var KEY_UP = 9;
  var KEY_DOWN = 10;
  var KEY_HOME = 11;
  var KEY_END = 12;
  var KEY_TAB = 13;
  var KEY_PAGE_UP = 14;
  var KEY_PAGE_DOWN = 15;

  var MOD_SHIFT = 1;
  var MOD_CONTROL = 2;
  var MOD_OPTION = 4;
  var MOD_COMMAND = 8;

  var PUNCT_KEYS = {
    '，': [',', MOD_SHIFT],
    '。': ['.', MOD_SHIFT],
    '？': ['/', MOD_SHIFT],
    '！': ['1', MOD_SHIFT],
    '：': [';', MOD_SHIFT],
    '；': [';', MOD_OPTION],
    '、': ['\\', 0],
    '「': ['[', 0],
    '」': [']', 0],
    '『': ['[', MOD_SHIFT],
    '』': [']', MOD_SHIFT],
    '（': ['9', MOD_SHIFT],
    '）': ['0', MOD_SHIFT],
    '(': ['9', MOD_SHIFT],
    ')': ['0', MOD_SHIFT],
    '～': ['`', MOD_SHIFT],
    '｜': ['\\', MOD_SHIFT],
    '“': ['\'', MOD_SHIFT],
    '＋': ['=', MOD_SHIFT],
    '‘': ['\'', 0],
    '＝': ['=', 0],
    '·': ['`', 0]
  };

  var TONE_MARK_TO_KEY = { 'ˊ': '6', 'ˇ': '3', 'ˋ': '4', '˙': '7' };
  var SYMBOLS = 'ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ';
  var KEYS_STANDARD = '1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-';
  var SYM_TO_KEY = {};
  for (var i = 0; i < SYMBOLS.length; i++) {
    SYM_TO_KEY[SYMBOLS[i]] = KEYS_STANDARD[i];
  }

  var dec = new TextDecoder('utf-8');
  var enc = new TextEncoder();

  function ShanjieWasmCore(instance) {
    this.ex = instance.exports;
    this._lastOutput = null;
    this._colloquialPaired = false;
  }

  ShanjieWasmCore.instantiate = async function (wasmUrl) {
    var t0 = performance.now();
    var resp = await fetch(wasmUrl);
    if (!resp.ok) {
      throw new Error('WASM fetch failed: ' + resp.status + ' (' + wasmUrl + ')');
    }
    var contentType = (resp.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
    var compiled;
    if (WebAssembly.instantiateStreaming && contentType === 'application/wasm') {
      compiled = await WebAssembly.instantiateStreaming(resp, {});
    } else {
      compiled = await WebAssembly.instantiate(await resp.arrayBuffer(), {});
    }
    var compileMs = performance.now() - t0;
    return { core: new ShanjieWasmCore(compiled.instance), compileMs: compileMs };
  };

  ShanjieWasmCore.prototype._writeBytes = function (bytes) {
    var u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    var ptr = this.ex.shanjie_wasm_alloc(u8.length);
    if (u8.length > 0) {
      new Uint8Array(this.ex.memory.buffer, ptr, u8.length).set(u8);
    }
    return ptr;
  };

  ShanjieWasmCore.prototype._readOutput = function () {
    var ptr = this.ex.shanjie_wasm_output_ptr();
    var len = this.ex.shanjie_wasm_output_len();
    var json = dec.decode(new Uint8Array(this.ex.memory.buffer, ptr, len));
    var out = JSON.parse(json);
    this._lastOutput = out;
    return out;
  };

  ShanjieWasmCore.prototype.stage = function (name, bytes) {
    var u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    var nb = enc.encode(name);
    var np = this._writeBytes(nb);
    var dp = this._writeBytes(u8);
    var rc = this.ex.shanjie_wasm_stage(np, nb.length, dp, u8.length);
    this.ex.shanjie_wasm_free(np, nb.length);
    if (rc !== 0) {
      throw new Error('stage failed for ' + name + ': ' + rc);
    }
  };

  ShanjieWasmCore.prototype.clearStage = function () {
    if (typeof this.ex.shanjie_wasm_clear_stage === 'function') {
      this.ex.shanjie_wasm_clear_stage();
    }
  };

  ShanjieWasmCore.prototype.init = function (layoutCode, packsMask) {
    var rc = this.ex.shanjie_wasm_init(layoutCode || 0, packsMask == null ? 29 : packsMask);
    if (rc !== 0) {
      throw new Error('init failed: ' + rc);
    }
    this._lastOutput = null;
  };

  ShanjieWasmCore.prototype.loadTinylm = function (bytes) {
    var u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    var ptr = this._writeBytes(u8);
    var rc = this.ex.shanjie_wasm_load_tinylm(ptr, u8.length);
    this.ex.shanjie_wasm_free(ptr, u8.length);
    if (rc !== 0) {
      throw new Error('load_tinylm failed: ' + rc);
    }
  };

  ShanjieWasmCore.prototype.loadColloquialFromBytes = function (sjlmBytes, baseBytes, overlayBytes) {
    var sU8 = sjlmBytes instanceof Uint8Array ? sjlmBytes : new Uint8Array(sjlmBytes);
    var bU8 = baseBytes instanceof Uint8Array ? baseBytes : new Uint8Array(baseBytes);
    var oU8 = overlayBytes instanceof Uint8Array ? overlayBytes : new Uint8Array(overlayBytes);
    var sp = this._writeBytes(sU8);
    var bp = this._writeBytes(bU8);
    var op = this._writeBytes(oU8);
    var rc = this.ex.shanjie_wasm_load_colloquial(
      sp, sU8.length,
      bp, bU8.length,
      op, oU8.length
    );
    this.ex.shanjie_wasm_free(sp, sU8.length);
    this.ex.shanjie_wasm_free(bp, bU8.length);
    this.ex.shanjie_wasm_free(op, oU8.length);
    if (rc !== 0) {
      throw new Error('load_colloquial failed: ' + rc);
    }
    this._colloquialPaired = true;
  };

  ShanjieWasmCore.prototype.loadColloquial = async function (sjlmOrUrl, baseOrUrl, overlayOrUrl) {
    if (typeof sjlmOrUrl === 'string') {
      var parts = await Promise.all([sjlmOrUrl, baseOrUrl, overlayOrUrl].map(async function (url) {
        var resp = await fetch(url);
        if (!resp.ok) {
          throw new Error('fetch failed: ' + url + ' (' + resp.status + ')');
        }
        return new Uint8Array(await resp.arrayBuffer());
      }));
      this.loadColloquialFromBytes(parts[0], parts[1], parts[2]);
      return;
    }
    this.loadColloquialFromBytes(sjlmOrUrl, baseOrUrl, overlayOrUrl);
  };

  ShanjieWasmCore.prototype.setLeftContext = function (text) {
    var b = enc.encode(text || '');
    var ptr = this._writeBytes(b);
    var rc = this.ex.shanjie_wasm_set_left_context(ptr, b.length);
    this.ex.shanjie_wasm_free(ptr, b.length);
    if (rc !== 0) {
      throw new Error('setLeftContext failed: ' + rc);
    }
  };

  ShanjieWasmCore.prototype.setProfile = function (profile) {
    var rc = this.ex.shanjie_wasm_set_profile(profile === 'formal' ? 1 : 0);
    if (rc !== 0) {
      throw new Error('setProfile failed: ' + rc);
    }
    return this._readOutput();
  };

  ShanjieWasmCore.prototype.setRerank = function (on) {
    var rc = this.ex.shanjie_wasm_set_rerank(on ? 1 : 0);
    if (rc !== 0) {
      throw new Error('setRerank failed: ' + rc);
    }
    return this._readOutput();
  };

  ShanjieWasmCore.prototype.setRerankLevel = function (level) {
    var code = level === 'conservative' ? 0 : (level === 'aggressive' || level === 'eager') ? 2 : 1;
    var rc = this.ex.shanjie_wasm_set_rerank_level(code);
    if (rc !== 0) {
      throw new Error('setRerankLevel failed: ' + rc);
    }
    return this._readOutput();
  };

  ShanjieWasmCore.prototype.setEnglish = function (on) {
    var rc = this.ex.shanjie_wasm_set_english(on ? 1 : 0);
    if (rc !== 0) {
      throw new Error('setEnglish failed: ' + rc);
    }
    return this._readOutput();
  };

  ShanjieWasmCore.prototype.setFuzzy = function (flags) {
    var masked = (flags | 0) & 31;
    var rc = this.ex.shanjie_wasm_set_fuzzy(masked);
    if (rc !== 0) {
      throw new Error('setFuzzy failed: ' + rc);
    }
    return this._readOutput();
  };

  ShanjieWasmCore.prototype.setTone3 = function (on) {
    var rc = this.ex.shanjie_wasm_set_tone3(on ? 1 : 0);
    if (rc !== 0) {
      throw new Error('setTone3 failed: ' + rc);
    }
    return this._readOutput();
  };

  ShanjieWasmCore.prototype.setColloquialPaired = function (on) {
    this._colloquialPaired = Boolean(on);
    if (typeof this.ex.shanjie_wasm_set_colloquial_paired === 'function') {
      var rc = this.ex.shanjie_wasm_set_colloquial_paired(on ? 1 : 0);
      if (rc !== 0) {
        throw new Error('setColloquialPaired failed: ' + rc);
      }
    }
    return this._colloquialPaired;
  };

  ShanjieWasmCore.prototype.key = function (kind, ch, modifiers) {
    var cp = ch ? ch.codePointAt(0) : 0;
    var rc = this.ex.shanjie_wasm_key(kind, cp, modifiers || 0);
    if (rc !== 0) {
      throw new Error('key failed: ' + rc);
    }
    return this._readOutput();
  };

  ShanjieWasmCore.prototype.reset = function (mode) {
    var rc = this.ex.shanjie_wasm_reset(mode == null ? 1 : mode);
    if (rc !== 0) {
      throw new Error('reset failed: ' + rc);
    }
    return this._readOutput();
  };

  ShanjieWasmCore.prototype.isCandidateWindowOpen = function () {
    return Boolean(
      this._lastOutput &&
      this._lastOutput.selected != null &&
      this._lastOutput.selected >= 0 &&
      Array.isArray(this._lastOutput.candidates) &&
      this._lastOutput.candidates.length > 0 &&
      this._lastOutput.vertical !== 2
    );
  };

  ShanjieWasmCore.prototype.pick = function (index) {
    if (!this.isCandidateWindowOpen()) {
      return null;
    }
    if (index < 0 || index >= this._lastOutput.candidates.length) {
      return null;
    }
    var rc = this.ex.shanjie_wasm_pick(index);
    if (rc < 0) {
      throw new Error('pick failed: ' + rc);
    }
    return rc === 1 ? this._readOutput() : null;
  };

  ShanjieWasmCore.prototype.pickPrediction = function (index) {
    if (
      !this._lastOutput ||
      !Array.isArray(this._lastOutput.candidates) ||
      this._lastOutput.candidates.length === 0 ||
      this._lastOutput.vertical === 2
    ) {
      return null;
    }
    if (!this.isCandidateWindowOpen()) {
      this.key(KEY_TAB, '', 0);
    }
    return this.pick(index);
  };

  ShanjieWasmCore.prototype.rerank = function () {
    var rc = this.ex.shanjie_wasm_rerank();
    if (rc < 0) {
      throw new Error('rerank failed: ' + rc);
    }
    return rc === 1 ? this._readOutput() : null;
  };

  ShanjieWasmCore.prototype.decodeSyllables = function (syllables, opts) {
    opts = opts || {};
    var list = Array.isArray(syllables) ? syllables : [];
    this.reset(1);
    if (opts.profile) this.setProfile(opts.profile);
    if (opts.rerankLevel) this.setRerankLevel(opts.rerankLevel);
    if (typeof opts.rerank === 'boolean') this.setRerank(opts.rerank);
    if (typeof opts.english === 'boolean') this.setEnglish(opts.english);
    if (typeof opts.fuzzyFlags === 'number') this.setFuzzy(opts.fuzzyFlags & 31);
    this.setTone3(opts.toneSandhi !== false);
    this.setLeftContext(opts.context || '');

    var keyLatenciesMs = [];
    var out = null;
    var committed = '';

    var finalizeLast = opts.finalizeLast !== false;

    for (var s = 0; s < list.length; s++) {
      var syl = list[s];
      var isLast = s === list.length - 1;
      var ks = [];
      if (PUNCT_KEYS[syl]) {
        ks.push([KEY_CHAR, PUNCT_KEYS[syl][0], PUNCT_KEYS[syl][1]]);
      } else if (/^[A-Za-z0-9]+$/.test(syl)) {
        for (var a = 0; a < syl.length; a++) {
          var ac = syl[a];
          var isUpper = ac >= 'A' && ac <= 'Z';
          ks.push([KEY_CHAR, isUpper ? ac.toLowerCase() : ac, isUpper ? MOD_SHIFT : 0]);
        }
        // Only English-letter runs need KEY_SPACE to terminate; pure digit runs
        // stay numeric without an English run so KEY_SPACE would inject a literal space.
        if ((!isLast || finalizeLast) && /[A-Za-z]/.test(syl)) {
          ks.push([KEY_SPACE, '', 0]);
        }
      } else {
        var toneKey = null;
        for (var ch of syl) {
          if (TONE_MARK_TO_KEY[ch]) {
            toneKey = TONE_MARK_TO_KEY[ch];
          } else if (SYM_TO_KEY[ch]) {
            ks.push([KEY_CHAR, SYM_TO_KEY[ch], 0]);
          }
        }
        if (toneKey) {
          ks.push([KEY_CHAR, toneKey, 0]);
        } else if (ks.length > 0 && (!isLast || finalizeLast)) {
          ks.push([KEY_SPACE, '', 0]);
        }
      }
      for (var k = 0; k < ks.length; k++) {
        var tk0 = performance.now();
        out = this.key(ks[k][0], ks[k][1], ks[k][2]);
        if (out && out.commit) committed += out.commit;
        keyLatenciesMs.push(performance.now() - tk0);
      }
    }

    var rerankMs = null;
    var reranked = false;
    if (opts.runRerank !== false && out && out.hasRerank) {
      var tr0 = performance.now();
      var rout = this.rerank();
      rerankMs = performance.now() - tr0;
      if (rout) {
        out = rout;
        if (out.commit) committed += out.commit;
        reranked = true;
      }
    }

    return {
      text: committed + (out ? (out.preedit || '') : ''),
      committed: committed,
      output: out,
      keyLatenciesMs: keyLatenciesMs,
      rerankMs: rerankMs,
      hasRerank: Boolean(out && out.hasRerank),
      reranked: reranked
    };
  };

  var SHIFT_ASCII_PUNCT = {
    '!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=', '{': '[', '}': ']', '|': '\\', ':': ';', '"': '\'', '<': ',', '>': '.', '?': '/', '~': '`'
  };
  var OPTION_TOKEN_MAP = [
    ['Option+Shift+;', ';', MOD_OPTION | MOD_SHIFT],
    ['Option+;', ';', MOD_OPTION],
    ['Option+,', ',', MOD_OPTION],
    ['Option+[', '[', MOD_OPTION],
    ['Option+]', ']', MOD_OPTION],
    ['Option+\\', '\\', MOD_OPTION]
  ];

  /**
   * Replay raw user keystrokes directly into shanjie_wasm_key (matching macOS
   * ShanjieInputController) so real English-mixed tone-key splitting (§4.3),
   * unfinished syllables, formal capitalization (§4.4), and mixed words (§5.7)
   * work identically to native macOS without pre-tokenization mangling.
   */
  ShanjieWasmCore.prototype.decodeRawKeys = function (rawInput, opts) {
    opts = opts || {};
    var str = String(rawInput == null ? '' : rawInput);
    this.reset(1);
    if (opts.profile) this.setProfile(opts.profile);
    if (opts.rerankLevel) this.setRerankLevel(opts.rerankLevel);
    if (typeof opts.rerank === 'boolean') this.setRerank(opts.rerank);
    if (typeof opts.english === 'boolean') this.setEnglish(opts.english);
    if (typeof opts.fuzzyFlags === 'number') this.setFuzzy(opts.fuzzyFlags & 31);
    this.setTone3(opts.toneSandhi !== false);
    this.setLeftContext(opts.context || '');

    var events = [];
    var i = 0;
    var pendingNeutralDot = false;
    while (i < str.length) {
      var matchedOpt = false;
      for (var m = 0; m < OPTION_TOKEN_MAP.length; m++) {
        var ot = OPTION_TOKEN_MAP[m];
        if (str.indexOf(ot[0], i) === i) {
          events.push([KEY_CHAR, ot[1], ot[2], false]);
          i += ot[0].length;
          matchedOpt = true;
          break;
        }
      }
      if (matchedOpt) continue;
      var ch = str[i++];
      var code = ch.charCodeAt(0);
      if (ch === ' ' || ch === '␣') {
        events.push([KEY_SPACE, '', 0, ch === '␣']);
      } else if (ch >= 'A' && ch <= 'Z') {
        events.push([KEY_CHAR, ch.toLowerCase(), MOD_SHIFT, false]);
      } else if (SHIFT_ASCII_PUNCT[ch]) {
        events.push([KEY_CHAR, SHIFT_ASCII_PUNCT[ch], MOD_SHIFT, false]);
      } else if (code >= 0x21 && code <= 0x7e) {
        events.push([KEY_CHAR, ch, 0, false]);
      } else if (PUNCT_KEYS[ch]) {
        events.push([KEY_CHAR, PUNCT_KEYS[ch][0], PUNCT_KEYS[ch][1], false]);
      } else if (SYM_TO_KEY[ch]) {
        events.push([KEY_CHAR, SYM_TO_KEY[ch], 0, false]);
        if (pendingNeutralDot && (i >= str.length || !SYM_TO_KEY[str[i]])) {
          events.push([KEY_CHAR, '7', 0, false]);
          pendingNeutralDot = false;
        }
      } else if (ch === '˙' && i < str.length && SYM_TO_KEY[str[i]]) {
        // MOE/Taiwan notation places neutral-tone `˙` before the syllable (e.g. `ㄋㄟˋ ˙ㄍㄜ`);
        // defer key `'7'` until after that syllable's Bopomofo symbols so core applies the tone.
        pendingNeutralDot = true;
      } else if (TONE_MARK_TO_KEY[ch]) {
        events.push([KEY_CHAR, TONE_MARK_TO_KEY[ch], 0, false]);
      }
    }

    var keyLatenciesMs = [];
    var out = null;
    var committed = '';
    for (var e = 0; e < events.length; e++) {
      var ev = events[e];
      if (ev[0] === KEY_SPACE && !ev[3]) {
        var pre = out ? (out.preedit || '') : '';
        var enLen = out ? (out.englishRunUtf16Len || 0) : 0;
        var lastChar = pre ? Array.from(pre).slice(-1)[0] : '';
        var lastCp = lastChar ? lastChar.codePointAt(0) : 0;
        var hasPendingBopo = lastCp >= 0x3105 && lastCp <= 0x312f;
        var endsEnOrSpace = Boolean(lastChar && /[A-Za-z0-9 ]/.test(lastChar));
        if (pre && !hasPendingBopo && enLen === 0 && !endsEnOrSpace) {
          // Skip visual separator spaces pasted after completed Han/punct syllables
          // (e.g. "5k4 ek7") while preserving real 1st-tone, English-break, and post-English spaces.
          continue;
        }
      }
      var tk0 = performance.now();
      out = this.key(ev[0], ev[1], ev[2]);
      if (out && out.commit) committed += out.commit;
      keyLatenciesMs.push(performance.now() - tk0);
    }

    var rerankMs = null;
    var reranked = false;
    if (opts.runRerank !== false && out && out.hasRerank) {
      var tr0 = performance.now();
      var rout = this.rerank();
      rerankMs = performance.now() - tr0;
      if (rout) {
        out = rout;
        if (out.commit) committed += out.commit;
        reranked = true;
      }
    }

    return {
      text: committed + (out ? (out.preedit || '') : ''),
      committed: committed,
      output: out,
      keyLatenciesMs: keyLatenciesMs,
      rerankMs: rerankMs,
      hasRerank: Boolean(out && out.hasRerank),
      reranked: reranked
    };
  };

  ShanjieWasmCore.KEY = {
    CHAR: KEY_CHAR,
    SPACE: KEY_SPACE,
    ENTER: KEY_ENTER,
    BACKSPACE: KEY_BACKSPACE,
    DELETE: KEY_DELETE,
    ESC: KEY_ESC,
    LEFT: KEY_LEFT,
    RIGHT: KEY_RIGHT,
    UP: KEY_UP,
    DOWN: KEY_DOWN,
    HOME: KEY_HOME,
    END: KEY_END,
    TAB: KEY_TAB,
    PAGE_UP: KEY_PAGE_UP,
    PAGE_DOWN: KEY_PAGE_DOWN
  };

  ShanjieWasmCore.MOD = {
    SHIFT: MOD_SHIFT,
    CONTROL: MOD_CONTROL,
    OPTION: MOD_OPTION,
    COMMAND: MOD_COMMAND
  };

  root.ShanjieWasmCore = ShanjieWasmCore;
})(typeof self !== 'undefined' ? self : globalThis);
