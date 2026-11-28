(function (root) {
  'use strict';

  var DEFAULT_ASSET_BASE = '/wasm-assets/v1-p35-2ff55dcc';
  var DEFAULT_WORKER_URL = '/wasm/shanjie-worker.js';

  var STAGE1_DATA_FILES = [
    'mcbpmf-data.txt',
    'overlay-add.tsv',
    'sandhi-add.tsv',
    'names-add.tsv',
    'variant-order.tsv',
    'rerank-protect.tsv',
    'demote.tsv',
    'std-exempt.tsv',
    'digits.tsv',
    'english.tsv',
    'mixed-words.tsv',
    'acg-add.tsv',
    'it-add.tsv',
    'meme-add.tsv',
    'beauty-add.tsv'
  ];

  var STAGE1_MODEL_FILES = [
    'bigram.sjlm',
    'classes.sjc',
    'kn.sjkn',
    'trigram-chat.sjt3'
  ];

  var COALESCED_CMDS = {
    decodeSyllables: true,
    decodeRawKeys: true,
    rerank: true,
    key: true,
    pick: true,
    pickPrediction: true
  };

  function ShanjieWorkerClient(workerUrl, onError) {
    this.worker = new Worker(workerUrl);
    this._seq = 1;
    this._pending = new Map();
    this._cmdQueue = [];
    this._cmdActiveId = null;
    this._onError = typeof onError === 'function' ? onError : null;
    var self = this;
    this.worker.onmessage = function (ev) {
      var msg = ev.data || {};
      var p = self._pending.get(msg.id);
      if (!p) return;
      self._pending.delete(msg.id);
      if (self._cmdActiveId === msg.id) {
        self._cmdActiveId = null;
        self._pumpCmdQueue();
      }
      if (msg.ok) {
        p.resolve(msg);
      } else {
        p.reject(new Error(msg.error || 'worker error'));
      }
    };
    this.worker.onerror = function (err) {
      var e = err instanceof Error ? err : new Error((err && err.message) || 'worker runtime error');
      self._cmdActiveId = null;
      var queued = self._cmdQueue.splice(0);
      for (var i = 0; i < queued.length; i++) {
        queued[i].reject(e);
      }
      for (var entry of self._pending.values()) {
        entry.reject(e);
      }
      self._pending.clear();
      if (self._onError) {
        try {
          self._onError(e);
        } catch (_cbErr) {
          // ignore error observer failure
        }
      }
    };
  }

  ShanjieWorkerClient.prototype._call = function (payload) {
    var id = this._seq++;
    var self = this;
    return new Promise(function (resolve, reject) {
      self._pending.set(id, { resolve: resolve, reject: reject });
      payload.id = id;
      try {
        self.worker.postMessage(payload);
      } catch (err) {
        self._pending.delete(id);
        reject(err);
      }
    });
  };

  ShanjieWorkerClient.prototype._pumpCmdQueue = function () {
    while (this._cmdActiveId === null && this._cmdQueue.length > 0) {
      var next = this._cmdQueue.shift();
      this._cmdActiveId = next.id;
      this._pending.set(next.id, { resolve: next.resolve, reject: next.reject });
      next.payload.id = next.id;
      try {
        this.worker.postMessage(next.payload);
        return;
      } catch (err) {
        this._pending.delete(next.id);
        this._cmdActiveId = null;
        next.reject(err);
      }
    }
  };

  ShanjieWorkerClient.prototype._callCmd = function (payload) {
    var id = this._seq++;
    var self = this;
    return new Promise(function (resolve, reject) {
      // Full decodes and resets overwrite engine preedit state, so any queued
      // (not-yet-dispatched) decode/rerank/key/pick ahead of them is stale.
      if (
        payload.type === 'decodeRawKeys' ||
        payload.type === 'decodeSyllables' ||
        payload.type === 'reset'
      ) {
        var kept = [];
        for (var i = 0; i < self._cmdQueue.length; i++) {
          var item = self._cmdQueue[i];
          if (COALESCED_CMDS[item.payload.type]) {
            item.resolve({ id: item.id, ok: true, superseded: true, result: null, output: null });
          } else {
            kept.push(item);
          }
        }
        self._cmdQueue = kept;
      }
      self._cmdQueue.push({ id: id, payload: payload, resolve: resolve, reject: reject });
      self._pumpCmdQueue();
    });
  };

  ShanjieWorkerClient.prototype.init = function (wasmUrl, files, opts) {
    opts = opts || {};
    return this._call({
      type: 'init',
      wasmUrl: wasmUrl,
      files: files,
      layoutCode: opts.layoutCode || 0,
      packsMask: opts.packsMask == null ? 29 : opts.packsMask
    });
  };

  ShanjieWorkerClient.prototype.loadStage2 = function (colloquialUrl, tinylmUrl, opts) {
    opts = opts || {};
    return this._call({
      type: 'loadStage2',
      colloquialUrl: colloquialUrl,
      tinylmUrl: tinylmUrl,
      baseUrl: opts.baseUrl || null,
      overlayUrl: opts.overlayUrl || null
    });
  };

  ShanjieWorkerClient.prototype.loadTinylm = function (url) {
    return this._call({ type: 'loadTinylm', url: url });
  };

  ShanjieWorkerClient.prototype.loadColloquial = function (sjlmUrl, baseUrl, overlayUrl) {
    return this._call({
      type: 'loadColloquial',
      sjlmUrl: sjlmUrl,
      baseUrl: baseUrl,
      overlayUrl: overlayUrl
    });
  };

  ShanjieWorkerClient.prototype.decodeSyllables = function (syllables, opts) {
    return this._callCmd({ type: 'decodeSyllables', syllables: syllables, opts: opts });
  };

  ShanjieWorkerClient.prototype.decodeRawKeys = function (rawInput, opts) {
    return this._callCmd({ type: 'decodeRawKeys', rawInput: rawInput, opts: opts });
  };

  ShanjieWorkerClient.prototype.key = function (kind, ch, modifiers) {
    return this._callCmd({ type: 'key', kind: kind, ch: ch, modifiers: modifiers });
  };

  ShanjieWorkerClient.prototype.pick = function (index) {
    return this._callCmd({ type: 'pick', index: index });
  };

  ShanjieWorkerClient.prototype.pickPrediction = function (index) {
    return this._callCmd({ type: 'pickPrediction', index: index });
  };

  ShanjieWorkerClient.prototype.rerank = function () {
    return this._callCmd({ type: 'rerank' });
  };

  ShanjieWorkerClient.prototype.reset = function (mode) {
    return this._callCmd({ type: 'reset', mode: mode });
  };

  ShanjieWorkerClient.prototype.setLeftContext = function (text) {
    return this._callCmd({ type: 'setLeftContext', text: text });
  };

  ShanjieWorkerClient.prototype.setSettings = function (settings) {
    return this._callCmd({ type: 'setSettings', settings: settings });
  };

  ShanjieWorkerClient.prototype.terminate = function () {
    var err = new Error('worker terminated');
    this._cmdActiveId = null;
    var queued = this._cmdQueue.splice(0);
    for (var i = 0; i < queued.length; i++) {
      queued[i].reject(err);
    }
    for (var entry of this._pending.values()) {
      entry.reject(err);
    }
    this._pending.clear();
    this.worker.terminate();
  };

  function buildAssetUrls(assetBase) {
    var base = String(assetBase || DEFAULT_ASSET_BASE).replace(/\/+$/, '');
    var stage1Files = {};
    for (var i = 0; i < STAGE1_DATA_FILES.length; i++) {
      var dName = STAGE1_DATA_FILES[i];
      stage1Files[dName] = base + '/data/' + dName;
    }
    for (var j = 0; j < STAGE1_MODEL_FILES.length; j++) {
      var mName = STAGE1_MODEL_FILES[j];
      stage1Files[mName] = base + '/p35/' + mName;
    }
    return {
      wasmUrl: base + '/wasm/shanjie_wasm_simd128.wasm',
      stage1Files: stage1Files,
      colloquialUrl: base + '/p35/colloquial.sjlm',
      tinylmUrl: base + '/p35/tinylm-cm.sjtl'
    };
  }

  function computeFuzzyFlags(settings) {
    var s = settings || {};
    var soundOn = typeof s.fuzzy === 'object' && s.fuzzy !== null
      ? Boolean(s.fuzzy.sound)
      : Boolean(s.fuzzy);
    var adjOn = Boolean(s.adjacentKey) ||
      (typeof s.fuzzy === 'object' && s.fuzzy !== null && Boolean(s.fuzzy.adjacent));
    return (soundOn ? 15 : 0) | (adjOn ? 16 : 0);
  }

  function normalizeRerankLevel(rerankSetting) {
    if (rerankSetting === 'conservative') return 'conservative';
    if (rerankSetting === 'aggressive' || rerankSetting === 'eager') return 'aggressive';
    return 'standard';
  }

  function isCandidateWindowOpen(out) {
    return Boolean(
      out &&
      out.selected !== null &&
      out.selected !== undefined &&
      out.selected >= 0 &&
      out.vertical !== 2 &&
      Array.isArray(out.candidates) &&
      out.candidates.length > 0
    );
  }

  function extractBarState(out) {
    // Mirror docs/spec/macos-shell.md §8.2 & docs/spec/english-mixed.md §4.6 faithfully:
    // - Open candidate window (selected >= 0, vertical !== 2): horizontal numbered bar (1..9)
    // - Passive prediction row (selected === null, candidates non-empty): horizontal ⇥ bar
    // - Active English run with no predictions (englishRunUtf16Len > 0): '空白：英文斷開' hint
    // - Otherwise: no bar shown. Never fabricate whole-sentence candidates.
    if (!out) {
      return {
        barMode: 'none',
        barItems: [],
        barSelected: null,
        barOffset: 0,
        englishRunStart: 0,
        englishRunLen: 0
      };
    }
    var allList = Array.isArray(out.candidates) ? out.candidates : [];
    var enStart = typeof out.englishRunUtf16Start === 'number' ? out.englishRunUtf16Start : 0;
    var enLen = typeof out.englishRunUtf16Len === 'number' ? out.englishRunUtf16Len : 0;

    if (isCandidateWindowOpen(out)) {
      var pageOffset = Math.floor(out.selected / 9) * 9;
      return {
        barMode: 'candidates',
        barItems: allList.slice(pageOffset, pageOffset + 9),
        barSelected: out.selected - pageOffset,
        barOffset: pageOffset,
        englishRunStart: enStart,
        englishRunLen: enLen
      };
    }
    if (allList.length > 0) {
      return {
        barMode: 'prediction',
        barItems: allList.slice(0, 9),
        barSelected: null,
        barOffset: 0,
        englishRunStart: enStart,
        englishRunLen: enLen
      };
    }
    if (enLen > 0) {
      return {
        barMode: 'english-hint',
        barItems: ['空白：英文斷開'],
        barSelected: null,
        barOffset: 0,
        englishRunStart: enStart,
        englishRunLen: enLen
      };
    }
    return {
      barMode: 'none',
      barItems: [],
      barSelected: null,
      barOffset: 0,
      englishRunStart: enStart,
      englishRunLen: enLen
    };
  }

  function remapEnglishRunStart(rawText, rawStart, rawLen, prevChar, cjkSpacing, engine) {
    if (!rawLen || rawStart < 0 || rawStart >= rawText.length) return 0;
    if (!cjkSpacing) return rawStart;
    return engine.applyPanguSpacing(rawText.slice(0, rawStart + 1), prevChar).length - 1;
  }

  function getEngine() {
    if (root.ShanjieEngine) return root.ShanjieEngine;
    if (typeof window !== 'undefined' && window.ShanjieEngine) return window.ShanjieEngine;
    throw new Error('ShanjieEngine is not loaded');
  }

  var ShanjieWasmController = {
    isSupported: function () {
      if (typeof window !== 'undefined' && window.location && window.location.protocol === 'file:') {
        return { ok: false, reason: 'file-protocol' };
      }
      if (typeof window === 'undefined' || !window.location || typeof Worker === 'undefined' || typeof WebAssembly === 'undefined') {
        return { ok: false, reason: 'no-wasm-worker' };
      }
      return { ok: true };
    },

    createSession: function (options) {
      var opts = options || {};
      var workerUrl = opts.workerUrl || DEFAULT_WORKER_URL;
      var urls = buildAssetUrls(opts.assetBase || DEFAULT_ASSET_BASE);
      var onProgress = typeof opts.onProgress === 'function' ? opts.onProgress : null;

      var stage = 'idle';
      var terminated = false;
      var lastPreRerankComposition = '';
      var lastCommittedPrefix = '';
      var sessionSeq = 0;
      var client = null;
      var stage2Promise = null;

      function emitProgress(info) {
        if (!onProgress) return;
        try {
          onProgress(info);
        } catch (_err) {
          // ignore observer errors
        }
      }

      async function start() {
        if (terminated) {
          throw new Error('Session already terminated');
        }
        var support = ShanjieWasmController.isSupported();
        if (!support.ok) {
          throw new Error('WASM session unsupported: ' + support.reason);
        }

        stage = 'loading-stage1';
        emitProgress({
          stage: 'loading-stage1',
          label: '載入瀏覽器引擎中（Stage 1：約 19 MiB）…',
          ready: false,
          rerankReady: false
        });

        function handleFatalWorkerError(err) {
          if (terminated || stage === 'error') return;
          stage = 'error';
          var dead = client;
          client = null;
          if (dead) {
            try {
              dead.terminate();
            } catch (_tErr) {}
          }
          emitProgress({
            stage: 'error',
            label: 'WASM 引擎發生錯誤',
            ready: false,
            rerankReady: false,
            error: String(err && err.message ? err.message : err)
          });
        }

        try {
          client = new ShanjieWorkerClient(workerUrl, handleFatalWorkerError);
          var initRes = await client.init(urls.wasmUrl, urls.stage1Files, {
            layoutCode: 0,
            packsMask: 29
          });
          if (terminated || stage === 'error') return initRes;

          stage = 'stage1-ready';
          emitProgress({
            stage: 'stage1-ready',
            label: 'WASM 真引擎就緒（背景載入語意校正中…）',
            ready: true,
            rerankReady: false
          });

          stage2Promise = client.loadStage2(urls.colloquialUrl, urls.tinylmUrl).then(function (s2Res) {
            if (terminated || stage === 'error') return s2Res;
            stage = 'stage2-ready';
            emitProgress({
              stage: 'stage2-ready',
              label: 'WASM 真引擎就緒（含會意語意校正）',
              ready: true,
              rerankReady: true
            });
            return s2Res;
          }).catch(function (err) {
            if (!terminated && stage !== 'error') {
              emitProgress({
                stage: 'stage1-ready',
                label: 'WASM 真引擎就緒（語意校正載入失敗，維持 n-gram 解碼）',
                ready: true,
                rerankReady: false,
                stage2Error: String(err && err.message ? err.message : err)
              });
            }
            return null;
          });

          return initRes;
        } catch (err) {
          var alreadyErrored = stage === 'error';
          stage = 'error';
          if (!alreadyErrored) {
            emitProgress({
              stage: 'error',
              label: 'WASM 引擎載入失敗',
              ready: false,
              rerankReady: false,
              error: String(err && err.message ? err.message : err)
            });
          }
          if (client) {
            client.terminate();
            client = null;
          }
          throw err;
        }
      }

      async function decode(rawInput, options) {
        if (terminated || !client || (stage !== 'stage1-ready' && stage !== 'stage2-ready')) {
          throw new Error('WASM engine is not ready (stage=' + stage + ')');
        }
        var engine = getEngine();
        var decOpts = options || {};
        var context = decOpts.context || '';
        var settings = decOpts.settings || {};
        var rawStr = String(rawInput == null ? '' : rawInput);
        var bopo = engine.standardKeysToBopomofo(rawStr);
        var useRawKeys = decOpts.useRawKeys === true;
        var tokens = bopo ? bopo.trim().split(/\s+/).filter(Boolean) : [];
        if ((!useRawKeys && tokens.length === 0) || !rawStr.trim()) {
          return {
            matched: false,
            partial: false,
            composition: '',
            rawComposition: '',
            bopomofo: '',
            barMode: 'none',
            barItems: [],
            barSelected: null,
            englishRunStart: 0,
            englishRunLen: 0,
            prediction: null,
            hasRerank: false,
            reranked: false,
            engineMode: 'wasm',
            notice: '',
            trace: []
          };
        }

        var profile = settings.profile === 'formal' ? 'formal' : 'chat';
        var rerankOn = settings.rerank !== 'off';
        var rerankLevel = normalizeRerankLevel(settings.rerank);
        var fuzzyFlags = computeFuzzyFlags(settings);
        var toneSandhi = settings.toneSandhi !== false;
        var hasTrailingSpace = /[\s␣]$/.test(rawStr);
        var finalizeLast = decOpts.finalizeLast === true ? true : hasTrailingSpace;
        var mySeq = ++sessionSeq;

        var workerRes = useRawKeys
          ? await client.decodeRawKeys(rawStr, {
            context: context,
            profile: profile,
            rerank: rerankOn,
            rerankLevel: rerankLevel,
            english: true,
            fuzzyFlags: fuzzyFlags,
            toneSandhi: toneSandhi,
            runRerank: Boolean(decOpts.runRerank)
          })
          : await client.decodeSyllables(tokens, {
            context: context,
            profile: profile,
            rerank: rerankOn,
            rerankLevel: rerankLevel,
            english: true,
            fuzzyFlags: fuzzyFlags,
            toneSandhi: toneSandhi,
            finalizeLast: finalizeLast,
            runRerank: Boolean(decOpts.runRerank)
          });
        if (!workerRes || workerRes.superseded || mySeq !== sessionSeq) {
          return { superseded: true };
        }
        var res = workerRes.result || {};
        lastCommittedPrefix = res.committed || '';
        var prevChar = context ? Array.from(String(context)).slice(-1)[0] : '';
        var rawText = res.text || '';
        var composition = rawText;
        if (settings.cjkSpacing && composition) {
          composition = engine.applyPanguSpacing(composition, prevChar);
        }
        if (!res.reranked) {
          lastPreRerankComposition = composition;
        }

        var bar = extractBarState(res.output);
        if (bar.englishRunLen > 0) {
          bar.englishRunStart = remapEnglishRunStart(
            rawText,
            lastCommittedPrefix.length + bar.englishRunStart,
            bar.englishRunLen,
            prevChar,
            Boolean(settings.cjkSpacing),
            engine
          );
        }

        var notice = res.reranked
          ? 'WASM 真實引擎解碼（會意已原地重排組字區）'
          : (stage === 'stage2-ready' ? 'WASM 真實引擎解碼（按 ↓ 可開啟游標候選列）' : 'WASM 真實引擎解碼（Stage 1，按 ↓ 可開啟游標候選列）');
        var trace = [
          'WASM 瀏覽器實時解碼（' + (profile === 'formal' ? '書面' : '聊天') + (res.reranked ? '＋語意校正' : '') + '）'
        ];

        return {
          matched: true,
          partial: false,
          composition: composition,
          rawComposition: bopo,
          bopomofo: bopo,
          barMode: bar.barMode,
          barItems: bar.barItems,
          barSelected: bar.barSelected,
          barOffset: bar.barOffset,
          englishRunStart: bar.englishRunStart,
          englishRunLen: bar.englishRunLen,
          prediction: null,
          hasRerank: Boolean(res.hasRerank),
          reranked: Boolean(res.reranked),
          engineMode: 'wasm',
          notice: notice,
          trace: trace
        };
      }

      function formatCoreOutput(out, context, settings) {
        if (!out) return null;
        var engine = getEngine();
        if (out.commit) {
          lastCommittedPrefix += out.commit;
        }
        var rawText = lastCommittedPrefix + (out.preedit || '');
        var prevChar = context ? Array.from(String(context)).slice(-1)[0] : '';
        var composition = settings.cjkSpacing && rawText ? engine.applyPanguSpacing(rawText, prevChar) : rawText;
        var bar = extractBarState(out);
        if (bar.englishRunLen > 0) {
          bar.englishRunStart = remapEnglishRunStart(
            rawText,
            lastCommittedPrefix.length + bar.englishRunStart,
            bar.englishRunLen,
            prevChar,
            Boolean(settings.cjkSpacing),
            engine
          );
        }
        return {
          composition: composition,
          barMode: bar.barMode,
          barItems: bar.barItems,
          barSelected: bar.barSelected,
          barOffset: bar.barOffset,
          englishRunStart: bar.englishRunStart,
          englishRunLen: bar.englishRunLen
        };
      }

      async function rerank(options) {
        var rOpts = options || {};
        var settings = rOpts.settings || {};
        var context = rOpts.context || '';
        if (terminated || !client || stage !== 'stage2-ready' || settings.rerank === 'off') {
          return { changed: false };
        }
        var mySeq = sessionSeq;
        var workerRes = await client.rerank();
        if (!workerRes || workerRes.superseded || mySeq !== sessionSeq || !workerRes.output) {
          return { changed: false };
        }
        var formatted = formatCoreOutput(workerRes.output, context, settings);
        if (!formatted || !formatted.composition || formatted.composition === lastPreRerankComposition) {
          return { changed: false };
        }
        return {
          changed: true,
          composition: formatted.composition,
          barMode: formatted.barMode,
          barItems: formatted.barItems,
          barSelected: formatted.barSelected,
          barOffset: formatted.barOffset,
          englishRunStart: formatted.englishRunStart,
          englishRunLen: formatted.englishRunLen,
          reranked: true,
          trace: ['語意校正（會意模型）：停頓原地重排完成']
        };
      }

      async function reset() {
        sessionSeq += 1;
        lastCommittedPrefix = '';
        lastPreRerankComposition = '';
        if (terminated || !client || (stage !== 'stage1-ready' && stage !== 'stage2-ready')) {
          return null;
        }
        return client.reset(1);
      }

      async function sendKey(kind, ch, modifiers, options) {
        var kOpts = options || {};
        var settings = kOpts.settings || {};
        var context = kOpts.context || '';
        if (terminated || !client || (stage !== 'stage1-ready' && stage !== 'stage2-ready')) {
          return null;
        }
        var mySeq = sessionSeq;
        var workerRes = await client.key(kind, ch || '', modifiers || 0);
        if (!workerRes || workerRes.superseded || mySeq !== sessionSeq) return null;
        var out = workerRes.output || null;
        return formatCoreOutput(out, context, settings);
      }

      async function openCandidates(options) {
        return sendKey(10, '', 0, options); // KEY_DOWN
      }

      async function pickCandidate(index, options) {
        var pOpts = options || {};
        var settings = pOpts.settings || {};
        var context = pOpts.context || '';
        if (terminated || !client || (stage !== 'stage1-ready' && stage !== 'stage2-ready')) {
          return null;
        }
        var mySeq = sessionSeq;
        var workerRes = await client.pick(index);
        if (!workerRes || workerRes.superseded || mySeq !== sessionSeq) return null;
        var out = workerRes.output || null;
        return formatCoreOutput(out, context, settings);
      }

      async function pickPrediction(index, options) {
        var pOpts = options || {};
        var settings = pOpts.settings || {};
        var context = pOpts.context || '';
        if (terminated || !client || (stage !== 'stage1-ready' && stage !== 'stage2-ready')) {
          return null;
        }
        var mySeq = sessionSeq;
        var workerRes = await client.pickPrediction(index);
        if (!workerRes || workerRes.superseded || mySeq !== sessionSeq) return null;
        var out = workerRes.output || null;
        return formatCoreOutput(out, context, settings);
      }

      function whenStage2Ready() {
        return stage2Promise || Promise.resolve(null);
      }

      function getStage() {
        return stage;
      }

      function terminate() {
        terminated = true;
        if (client) {
          client.terminate();
          client = null;
        }
      }

      return {
        start: start,
        decode: decode,
        rerank: rerank,
        reset: reset,
        sendKey: sendKey,
        openCandidates: openCandidates,
        pickCandidate: pickCandidate,
        pickPrediction: pickPrediction,
        whenStage2Ready: whenStage2Ready,
        getStage: getStage,
        terminate: terminate
      };
    }
  };

  root.ShanjieWorkerClient = ShanjieWorkerClient;
  root.ShanjieWasmController = ShanjieWasmController;
  if (typeof window !== 'undefined') {
    window.ShanjieWorkerClient = ShanjieWorkerClient;
    window.ShanjieWasmController = ShanjieWasmController;
  }
})(typeof window !== 'undefined' ? window : globalThis);
