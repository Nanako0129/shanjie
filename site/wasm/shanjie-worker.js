importScripts('./shanjie-wasm.js');

var core = null;
var cachedBase = null;
var cachedOverlay = null;

async function fetchBytes(url) {
  var resp = await fetch(url);
  if (!resp.ok) {
    throw new Error('fetch failed: ' + url + ' (' + resp.status + ')');
  }
  return new Uint8Array(await resp.arrayBuffer());
}

self.onmessage = async function (ev) {
  var msg = ev.data || {};
  var id = msg.id;
  try {
    if (msg.type === 'init') {
      var tStart = performance.now();
      cachedBase = null;
      cachedOverlay = null;

      var inst = await self.ShanjieWasmCore.instantiate(msg.wasmUrl);
      core = inst.core;

      var tFetch0 = performance.now();
      var entries = Object.entries(msg.files || {});
      var fetched = await Promise.all(entries.map(async function (pair) {
        return [pair[0], await fetchBytes(pair[1])];
      }));
      var fetchMs = performance.now() - tFetch0;

      var tInit0 = performance.now();
      for (var i = 0; i < fetched.length; i++) {
        var name = fetched[i][0];
        var bytes = fetched[i][1];
        if (name === 'mcbpmf-data.txt') cachedBase = bytes;
        else if (name === 'overlay-add.tsv') cachedOverlay = bytes;
        core.stage(name, bytes);
      }
      core.init(msg.layoutCode || 0, msg.packsMask == null ? 29 : msg.packsMask);
      var initMs = performance.now() - tInit0;
      var totalMs = performance.now() - tStart;

      self.postMessage({
        id: id,
        ok: true,
        ms: totalMs,
        compileMs: inst.compileMs,
        fetchMs: fetchMs,
        initMs: initMs,
        totalMs: totalMs,
        wasmMemoryBytes: core.ex.memory.buffer.byteLength
      });
    } else if (msg.type === 'loadStage2') {
      if (!core) {
        throw new Error('worker engine not initialized before loadStage2');
      }
      var t2Start = performance.now();
      var colloquialLoadMs = 0;
      var tinylmLoadMs = 0;
      try {
        var tasks = [
          fetchBytes(msg.colloquialUrl),
          fetchBytes(msg.tinylmUrl)
        ];
        if (!cachedBase && msg.baseUrl) tasks.push(fetchBytes(msg.baseUrl));
        if (!cachedOverlay && msg.overlayUrl) tasks.push(fetchBytes(msg.overlayUrl));

        var resolved = await Promise.all(tasks);
        var colloqBytes = resolved[0];
        var tinylmBytes = resolved[1];
        var idx = 2;
        var baseBytes = cachedBase || (msg.baseUrl ? resolved[idx++] : null);
        var overlayBytes = cachedOverlay || (msg.overlayUrl ? resolved[idx++] : null);

        if (!baseBytes || !overlayBytes) {
          throw new Error('Stage 1 base/overlay cache missing for loadStage2');
        }

        var lc0 = performance.now();
        core.loadColloquialFromBytes(colloqBytes, baseBytes, overlayBytes);
        colloquialLoadMs = performance.now() - lc0;

        var tl0 = performance.now();
        core.loadTinylm(tinylmBytes);
        tinylmLoadMs = performance.now() - tl0;

        // Do not call core.setRerank(true) here: Engine::set_rerank clears active
        // predictions and candidate windows. Each decode applies opts.rerank explicitly.
        core.setColloquialPaired(true);
      } finally {
        cachedBase = null;
        cachedOverlay = null;
      }

      var stage2Ms = performance.now() - t2Start;
      self.postMessage({
        id: id,
        ok: true,
        ms: stage2Ms,
        totalMs: stage2Ms,
        colloquialLoadMs: colloquialLoadMs,
        tinylmLoadMs: tinylmLoadMs,
        wasmMemoryBytes: core.ex.memory.buffer.byteLength
      });
    } else if (msg.type === 'loadTinylm') {
      if (!core) throw new Error('worker engine not initialized');
      var t0 = performance.now();
      var buf = await fetchBytes(msg.url);
      var tl1 = performance.now();
      core.loadTinylm(buf);
      var tlTotal = performance.now() - t0;
      self.postMessage({
        id: id,
        ok: true,
        ms: tlTotal,
        loadMs: performance.now() - tl1,
        totalMs: tlTotal,
        wasmMemoryBytes: core.ex.memory.buffer.byteLength
      });
    } else if (msg.type === 'loadColloquial') {
      if (!core) throw new Error('worker engine not initialized');
      var t0c = performance.now();
      var parts = await Promise.all([
        fetchBytes(msg.sjlmUrl),
        msg.baseUrl ? fetchBytes(msg.baseUrl) : Promise.resolve(cachedBase),
        msg.overlayUrl ? fetchBytes(msg.overlayUrl) : Promise.resolve(cachedOverlay)
      ]);
      if (!parts[1] || !parts[2]) {
        throw new Error('base/overlay bytes missing for loadColloquial');
      }
      var lc1 = performance.now();
      core.loadColloquialFromBytes(parts[0], parts[1], parts[2]);
      cachedBase = null;
      cachedOverlay = null;
      var colTotal = performance.now() - t0c;
      self.postMessage({
        id: id,
        ok: true,
        ms: colTotal,
        loadMs: performance.now() - lc1,
        totalMs: colTotal,
        wasmMemoryBytes: core.ex.memory.buffer.byteLength
      });
    } else if (msg.type === 'decodeSyllables') {
      if (!core) throw new Error('worker engine not initialized');
      var res = core.decodeSyllables(msg.syllables, msg.opts);
      self.postMessage({
        id: id,
        ok: true,
        result: res,
        wasmMemoryBytes: core.ex.memory.buffer.byteLength
      });
    } else if (msg.type === 'decodeRawKeys') {
      if (!core) throw new Error('worker engine not initialized');
      var rres = core.decodeRawKeys(msg.rawInput, msg.opts);
      self.postMessage({
        id: id,
        ok: true,
        result: rres,
        wasmMemoryBytes: core.ex.memory.buffer.byteLength
      });
    } else if (msg.type === 'key') {
      if (!core) throw new Error('worker engine not initialized');
      var tk0 = performance.now();
      var out = core.key(msg.kind, msg.ch, msg.modifiers);
      self.postMessage({ id: id, ok: true, output: out, keyMs: performance.now() - tk0 });
    } else if (msg.type === 'pick') {
      if (!core) throw new Error('worker engine not initialized');
      var tp0 = performance.now();
      var pout = core.pick(msg.index);
      self.postMessage({ id: id, ok: true, output: pout, picked: Boolean(pout), pickMs: performance.now() - tp0 });
    } else if (msg.type === 'pickPrediction') {
      if (!core) throw new Error('worker engine not initialized');
      var tpp0 = performance.now();
      var ppout = core.pickPrediction(msg.index);
      self.postMessage({ id: id, ok: true, output: ppout, picked: Boolean(ppout), pickMs: performance.now() - tpp0 });
    } else if (msg.type === 'rerank') {
      if (!core) throw new Error('worker engine not initialized');
      var tr0 = performance.now();
      var rout = core.rerank();
      self.postMessage({
        id: id,
        ok: true,
        output: rout,
        reranked: Boolean(rout),
        rerankMs: performance.now() - tr0,
        wasmMemoryBytes: core.ex.memory.buffer.byteLength
      });
    } else if (msg.type === 'reset') {
      if (!core) throw new Error('worker engine not initialized');
      self.postMessage({ id: id, ok: true, output: core.reset(msg.mode) });
    } else if (msg.type === 'setLeftContext') {
      if (!core) throw new Error('worker engine not initialized');
      core.setLeftContext(msg.text || '');
      self.postMessage({ id: id, ok: true });
    } else if (msg.type === 'setSettings') {
      if (!core) throw new Error('worker engine not initialized');
      var s = msg.settings || {};
      if (s.profile) core.setProfile(s.profile);
      if (s.rerankLevel) core.setRerankLevel(s.rerankLevel);
      if (typeof s.rerank === 'boolean') core.setRerank(s.rerank);
      if (typeof s.english === 'boolean') core.setEnglish(s.english);
      if (typeof s.fuzzyFlags === 'number') core.setFuzzy(s.fuzzyFlags & 31);
      if (typeof s.toneSandhi === 'boolean') core.setTone3(s.toneSandhi);
      self.postMessage({ id: id, ok: true });
    } else {
      throw new Error('unknown message type: ' + msg.type);
    }
  } catch (err) {
    self.postMessage({
      id: id,
      ok: false,
      error: String(err && err.message ? err.message : err)
    });
  }
};
