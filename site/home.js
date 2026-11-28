(function () {
  "use strict";

  var THEME_KEY = "theme";
  var root = document.documentElement;
  var prefersReduced = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : { matches: false };
  var systemDark = window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : { matches: false };

  function isReducedMotion() {
    return Boolean(prefersReduced && prefersReduced.matches);
  }

  /* =========================================================
   * 1. 3-State Theme Switch (跟隨系統 / 淺色 / 深色)
   * ========================================================= */
  var themeButtons = Array.prototype.slice.call(
    document.querySelectorAll("[data-theme-choice]")
  );

  function getSavedThemeChoice() {
    try {
      var val = localStorage.getItem(THEME_KEY);
      if (val === "light" || val === "dark") return val;
    } catch (e) {}
    return "system";
  }

  function applyThemeChoice(choice) {
    var valid = choice === "light" || choice === "dark" ? choice : "system";
    if (valid === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", valid);
    }
    themeButtons.forEach(function (btn) {
      var active = btn.getAttribute("data-theme-choice") === valid;
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    try {
      if (valid === "system") {
        localStorage.removeItem(THEME_KEY);
      } else {
        localStorage.setItem(THEME_KEY, valid);
      }
    } catch (e) {}
  }

  applyThemeChoice(getSavedThemeChoice());
  themeButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      applyThemeChoice(btn.getAttribute("data-theme-choice"));
    });
  });

  /* =========================================================
   * 2. Copy Buttons (Works on file:// and HTTPS)
   * ========================================================= */
  function fallbackCopy(text) {
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try {
        var ok = document.execCommand("copy");
        document.body.removeChild(ta);
        if (ok) resolve();
        else reject(new Error("copy failed"));
      } catch (err) {
        document.body.removeChild(ta);
        reject(err);
      }
    });
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function () {
        return fallbackCopy(text);
      });
    }
    return fallbackCopy(text);
  }

  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    var labelEl = btn.querySelector(".copy-label");
    var origLabel = labelEl ? labelEl.textContent : "複製";
    var timer = null;
    btn.addEventListener("click", function () {
      var val = btn.getAttribute("data-copy");
      if (!val) return;
      copyText(val).then(function () {
        btn.classList.add("is-copied");
        if (labelEl) labelEl.textContent = "已複製";
        clearTimeout(timer);
        timer = setTimeout(function () {
          btn.classList.remove("is-copied");
          if (labelEl) labelEl.textContent = origLabel;
        }, 2000);
      }).catch(function () {});
    });
  });

  /* =========================================================
   * 3. Shared Engine Settings State (Workbench <-> Feature Lab)
   * ========================================================= */
  function createDefaultSettings() {
    return {
      cjkSpacing: false,
      rerank: "standard",
      profile: "auto",
      packs: {
        acg: true,
        it: true,
        beauty: true,
        meme: true,
        quote: false
      },
      fuzzy: false,
      adjacentKey: false,
      toneSandhi: true,
      memoryOverrides: {}
    };
  }

  var settings = createDefaultSettings();
  var wbSettingsToggle = document.getElementById("wb-settings-toggle");
  var wbSettingsDrawer = document.getElementById("wb-settings-drawer");
  var wbSettingsBadge = document.getElementById("wb-settings-badge");
  var wbSettingsReset = document.getElementById("wb-settings-reset");

  if (wbSettingsToggle && wbSettingsDrawer) {
    wbSettingsToggle.addEventListener("click", function () {
      var open = wbSettingsDrawer.hidden;
      wbSettingsDrawer.hidden = !open;
      wbSettingsToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  function updateSettingsBadge() {
    if (!wbSettingsBadge) return;
    var diffs = [];
    if (settings.cjkSpacing) diffs.push("盤古");
    if (settings.rerank !== "standard") {
      var rMap = { off: "校正關", conservative: "保守", aggressive: "積極" };
      diffs.push(rMap[settings.rerank] || settings.rerank);
    }
    if (settings.profile !== "auto") {
      diffs.push(settings.profile === "chat" ? "聊天" : "書面");
    }
    if (settings.fuzzy) diffs.push("模糊");
    if (settings.adjacentKey) diffs.push("鄰鍵");
    if (!settings.toneSandhi) diffs.push("閉變調");
    if (settings.packs.quote) diffs.push("台詞包");
    wbSettingsBadge.textContent = diffs.length ? diffs.slice(0, 2).join("・") + (diffs.length > 2 ? "…" : "") : "預設";
  }

  function syncSettingsUI() {
    document.querySelectorAll("[data-setting-rerank]").forEach(function (btn) {
      var active = btn.getAttribute("data-setting-rerank") === settings.rerank;
      btn.classList.toggle("is-active", active);
    });
    document.querySelectorAll("[data-setting-profile]").forEach(function (btn) {
      var active = btn.getAttribute("data-setting-profile") === settings.profile;
      btn.classList.toggle("is-active", active);
    });
    document.querySelectorAll("[data-setting-bool]").forEach(function (cb) {
      var key = cb.getAttribute("data-setting-bool");
      cb.checked = Boolean(settings[key]);
    });
    document.querySelectorAll("[data-setting-pack]").forEach(function (cb) {
      var pack = cb.getAttribute("data-setting-pack");
      cb.checked = Boolean(settings.packs[pack]);
    });
    updateSettingsBadge();
    updateLabDynamicViews();
  }

  document.querySelectorAll("[data-setting-rerank]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      settings.rerank = btn.getAttribute("data-setting-rerank") || "standard";
      onSettingsChanged();
    });
  });

  document.querySelectorAll("[data-setting-profile]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      settings.profile = btn.getAttribute("data-setting-profile") || "auto";
      onSettingsChanged();
    });
  });

  document.querySelectorAll("[data-setting-bool]").forEach(function (cb) {
    cb.addEventListener("change", function () {
      var key = cb.getAttribute("data-setting-bool");
      settings[key] = Boolean(cb.checked);
      onSettingsChanged();
    });
  });

  document.querySelectorAll("[data-setting-pack]").forEach(function (cb) {
    cb.addEventListener("change", function () {
      var pack = cb.getAttribute("data-setting-pack");
      settings.packs[pack] = Boolean(cb.checked);
      onSettingsChanged();
    });
  });

  if (wbSettingsReset) {
    wbSettingsReset.addEventListener("click", function () {
      settings = createDefaultSettings();
      onSettingsChanged();
    });
  }

  function onSettingsChanged() {
    syncSettingsUI();
    evaluateFreeInput();
    renderCorpusList(activeCorpusCat);
  }

  /* =========================================================
   * 4. Workbench 3-Mode Tabs (Desktop 3-Mode / Mobile <=768px Compact 2-Mode)
   * ========================================================= */
  var mobileWorkbenchQuery = window.matchMedia
    ? window.matchMedia("(max-width: 768px)")
    : { matches: false };

  function isCompactMobile() {
    return Boolean(mobileWorkbenchQuery && mobileWorkbenchQuery.matches);
  }

  var modeTabs = Array.prototype.slice.call(document.querySelectorAll(".mode-tab"));
  var panelFree = document.getElementById("panel-free");
  var panelCorpus = document.getElementById("panel-corpus");
  var panelDuel = document.getElementById("panel-duel");
  var activeMode = "free";

  function switchMode(mode, opts) {
    if (isCompactMobile() && mode === "free") {
      mode = "duel";
    }
    activeMode = mode;
    modeTabs.forEach(function (tab) {
      var sel = tab.getAttribute("data-mode") === mode;
      tab.classList.toggle("is-active", sel);
      tab.setAttribute("aria-selected", sel ? "true" : "false");
    });
    if (panelFree) panelFree.hidden = mode !== "free";
    if (panelCorpus) panelCorpus.hidden = mode !== "corpus";
    if (panelDuel) panelDuel.hidden = mode !== "duel";

    if (mode === "duel") {
      clearDuelTimers();
      setScenarioChipActive(currentScenarioIdx);
      if (duelPaused || isReducedMotion()) {
        renderScenarioFinal(SCENARIOS[currentScenarioIdx]);
      } else if (opts && opts.deferAnimateMs) {
        renderScenarioFinal(SCENARIOS[currentScenarioIdx]);
        nextTimer = setTimeout(function () {
          if (activeMode === "duel" && !duelPaused) {
            playScenario(currentScenarioIdx);
          }
        }, opts.deferAnimateMs);
      } else {
        playScenario(currentScenarioIdx);
      }
    } else {
      clearDuelTimers();
    }
  }

  function handleMobileViewportChange() {
    if (isCompactMobile() && activeMode === "free") {
      switchMode("duel", { deferAnimateMs: 900 });
    }
  }

  if (mobileWorkbenchQuery) {
    if (typeof mobileWorkbenchQuery.addEventListener === "function") {
      mobileWorkbenchQuery.addEventListener("change", handleMobileViewportChange);
    } else if (typeof mobileWorkbenchQuery.addListener === "function") {
      mobileWorkbenchQuery.addListener(handleMobileViewportChange);
    }
  }

  modeTabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      switchMode(tab.getAttribute("data-mode") || "free");
    });
  });

  var jumpDuelBtn = document.getElementById("jump-duel-btn");
  if (jumpDuelBtn) {
    jumpDuelBtn.addEventListener("click", function () {
      switchMode("duel");
    });
  }

  /* =========================================================
   * 5. Mode 1: Free Typing Fake-Engine Workbench
   * ========================================================= */
  var freeCtxInput = document.getElementById("free-context");
  var freeMainInput = document.getElementById("free-input");
  var freeClearBtn = document.getElementById("free-clear-btn");
  var freeImeWarn = document.getElementById("free-ime-warn");
  var freeImeClearBtn = document.getElementById("free-ime-clear-btn");
  var freeSampleStrip = document.getElementById("free-sample-strip");
  var freeBopomofoEl = document.getElementById("free-bopomofo");
  var freeStageCtxEl = document.getElementById("free-stage-context");
  var freeStageOutEl = document.getElementById("free-stage-output");
  var freePillEl = document.getElementById("free-pill");
  var freeCandidatesEl = document.getElementById("free-candidates");
  var freeDownBtn = document.getElementById("free-down-btn");
  var freePredictionBtn = document.getElementById("free-prediction-btn");
  var freePredictionText = document.getElementById("free-prediction-text");
  var freeForgetBtn = document.getElementById("free-forget-btn");
  var freeAppleRow = document.getElementById("free-apple-row");
  var freeAppleOut = document.getElementById("free-apple-output");
  var freeSourceNote = document.getElementById("free-source-note");
  var freeTracePills = document.getElementById("free-trace-pills");
  var freeHonestNotice = document.getElementById("free-honest-notice");
  var workbenchEl = document.querySelector(".workbench");
  var wasmEnableBtn = document.getElementById("wasm-enable-btn");
  var wasmStatusBadge = document.getElementById("wasm-status-badge");
  var wbPacksWasmNote = document.getElementById("wb-packs-wasm-note");
  var packTogglesEl = document.querySelector(".pack-toggles");
  var simModeTagEl = document.querySelector(".sim-mode-tag");

  var activeFreeCat = "mixed";
  var activeSampleId = "mix_3";
  var isImeComposing = false;
  var lastDecodedResult = null;
  var freeCandOpen = false;
  var freeBarMode = "none";
  var freeBarItems = [];
  var freeCandSelected = 0;
  var freeBarOffset = 0;
  var engineMode = "table";
  var wasmSession = null;
  var wasmStartPromise = null;
  var wasmStage = "idle";
  var wasmStage2Error = false;
  var userManualTableMode = false;
  var decodeSeq = 0;
  var wasmRerankTimer = null;

  var DEFAULT_NOTICE = "網頁預設對照表介面（54 句與即時預測／游標候選均經實機 VM 或真實核心實測，無編造候選；完整自由打字請安裝善解輸入法 v0.9.1）。";
  var WASM_DEFAULT_NOTICE = "瀏覽器 WASM 真引擎即時解碼（按鍵不出本機；按 ↓ 可開啟真實游標候選窗並點選）";
  var WASM_UNSUPPORTED_FILE_NOTICE = "本機檔案（file://）無法載入 Web Worker，目前使用 54 句 CLI 實測對照模式；透過本機伺服器或官網 HTTPS 開啟即可啟用瀏覽器 WASM 真引擎。";
  var WASM_UNSUPPORTED_ENV_NOTICE = "此瀏覽器或預覽環境不支援 WebAssembly Web Worker，目前維持 54 句實測對照模式；完整體驗請安裝善解輸入法 v0.9.1。";
  var WASM_FETCH_FAIL_NOTICE = "此預覽環境未掛載 WASM 模型檔，已維持 54 句實測對照模式；完整體驗請安裝善解輸入法 v0.9.1。";

  function syncWasmUI() {
    var isWasm = engineMode === "wasm";
    var isLoadingStage1 = wasmStage === "loading-stage1";

    if (workbenchEl) {
      workbenchEl.setAttribute("data-engine-mode", engineMode);
      workbenchEl.setAttribute("data-wasm-stage", wasmStage);
    }

    if (wasmEnableBtn) {
      if (isWasm) {
        wasmEnableBtn.textContent = "切回範例對照模式";
        wasmEnableBtn.disabled = false;
        wasmEnableBtn.classList.add("is-active");
        wasmEnableBtn.setAttribute("aria-pressed", "true");
        wasmEnableBtn.removeAttribute("aria-busy");
      } else if (isLoadingStage1) {
        wasmEnableBtn.textContent = "載入 WASM 引擎中…";
        wasmEnableBtn.disabled = true;
        wasmEnableBtn.classList.remove("is-active");
        wasmEnableBtn.setAttribute("aria-pressed", "false");
        wasmEnableBtn.setAttribute("aria-busy", "true");
      } else {
        wasmEnableBtn.textContent = "啟用瀏覽器真引擎（WASM）";
        wasmEnableBtn.disabled = false;
        wasmEnableBtn.classList.remove("is-active");
        wasmEnableBtn.setAttribute("aria-pressed", "false");
        wasmEnableBtn.removeAttribute("aria-busy");
      }
    }

    if (wasmStatusBadge) {
      if (isWasm) {
        if (wasmStage === "stage2-ready") {
          wasmStatusBadge.textContent = "WASM 真引擎（含會意重排）";
        } else if (wasmStage === "stage1-ready") {
          wasmStatusBadge.textContent = wasmStage2Error
            ? "WASM 就緒（語意校正未載入）"
            : "WASM 就緒（背景載入會意…）";
        } else if (isLoadingStage1) {
          wasmStatusBadge.textContent = "載入 Stage 1…";
        } else {
          wasmStatusBadge.textContent = "WASM 真引擎";
        }
      } else if (isLoadingStage1) {
        wasmStatusBadge.textContent = "載入 Stage 1…";
      } else {
        wasmStatusBadge.textContent = "目前：54 句實測對照";
      }
    }

    document.querySelectorAll("#wb-settings-drawer [data-setting-pack]").forEach(function (cb) {
      cb.disabled = isWasm;
    });
    if (packTogglesEl) {
      packTogglesEl.classList.toggle("is-wasm-locked", isWasm);
    }
    if (wbPacksWasmNote) {
      wbPacksWasmNote.hidden = !isWasm;
    }
    if (simModeTagEl) {
      simModeTagEl.textContent = isWasm ? "善解 WASM 真引擎（即時解碼）" : "善解 v0.9.1 組字區";
    }
  }

  function applySafePanguFallback(text) {
    if (window.ShanjieEngine && typeof window.ShanjieEngine.applyPanguSpacing === "function") {
      return window.ShanjieEngine.applyPanguSpacing(text, "");
    }
    return String(text || "")
      .replace(/([\u4e00-\u9fff])([A-Za-z0-9])/g, "$1 $2")
      .replace(/([A-Za-z0-9])([\u4e00-\u9fff])/g, "$1 $2");
  }

  function makeMemoryKey(ctx, raw) {
    return (ctx || "").trim() + "||" + (raw || "").trim();
  }

  function loadIntoWorkbench(keys, ctx, sampleId, scrollToHero) {
    if (isCompactMobile()) return;
    if (freeCtxInput) freeCtxInput.value = ctx || "";
    if (freeMainInput) freeMainInput.value = keys || "";
    activeSampleId = sampleId || null;
    highlightSamplePills();
    switchMode("free");
    evaluateFreeInput();
    if (scrollToHero) {
      var hero = document.querySelector(".workbench");
      if (hero && hero.scrollIntoView) {
        hero.scrollIntoView({ behavior: isReducedMotion() ? "auto" : "smooth", block: "nearest" });
      }
    }
  }

  function highlightSamplePills() {
    if (!freeSampleStrip) return;
    freeSampleStrip.querySelectorAll(".sample-pill").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-sample-id") === activeSampleId);
    });
  }

  function renderFreeSampleStrip(catId) {
    activeFreeCat = catId;
    document.querySelectorAll("[data-free-cat]").forEach(function (btn) {
      btn.classList.toggle("is-active", btn.getAttribute("data-free-cat") === catId);
    });
    if (!freeSampleStrip) return;
    freeSampleStrip.innerHTML = "";
    if (!window.ShanjieEngine || typeof window.ShanjieEngine.getByCategory !== "function") {
      return;
    }
    var list = window.ShanjieEngine.getByCategory(catId) || [];
    list.slice(0, 6).forEach(function (item) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "sample-pill" + (item.id === activeSampleId ? " is-active" : "");
      btn.setAttribute("data-sample-id", item.id);
      btn.textContent = item.expected;
      btn.title = (item.context ? "前文：" + item.context + "｜" : "") + item.zhuyin;
      btn.addEventListener("click", function () {
        loadIntoWorkbench(item.daqianKeys || item.zhuyin, item.context || "", item.id, false);
      });
      freeSampleStrip.appendChild(btn);
    });
  }

  document.querySelectorAll("[data-free-cat]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      renderFreeSampleStrip(btn.getAttribute("data-free-cat") || "colloquial");
    });
  });

  function renderTracePills(traceList) {
    if (!freeTracePills) return;
    freeTracePills.innerHTML = "";
    (traceList || []).forEach(function (txt) {
      var span = document.createElement("span");
      span.className = "trace-chip";
      span.textContent = txt;
      freeTracePills.appendChild(span);
    });
  }

  /**
   * Render an authentic macOS 26 single-row horizontal Liquid Glass bar
   * matching docs/spec/macos-shell.md §8.2, CandidateCells.swift, c1-c.png & mpa-5-cand-tilde.png:
   * - mode "prediction": passive ⇥ on first item, no numbers, no blue highlight
   * - mode "candidates": numbered 1..9 with macOS system blue capsule on selected item
   * - mode "english-hint": compact '空白：英文斷開' pill (no chevron)
   * - mode "none" / empty: hidden
   */
  function renderGlassBar(pillEl, containerEl, mode, items, selectedIdx, onPick) {
    if (!pillEl || !containerEl) return;
    var list = Array.isArray(items) ? items : [];
    containerEl.innerHTML = "";
    var isFreeBar = pillEl === freePillEl;

    if (!mode || mode === "none" || list.length === 0) {
      pillEl.setAttribute("data-bar-mode", "none");
      pillEl.hidden = true;
      if (isFreeBar) {
        freeBarMode = "none";
        freeBarItems = [];
        freeCandSelected = 0;
      }
      return;
    }

    pillEl.setAttribute("data-bar-mode", mode);
    pillEl.hidden = false;

    if (mode === "english-hint") {
      if (isFreeBar) {
        freeBarMode = "english-hint";
        freeBarItems = [list[0] || "空白：英文斷開"];
        freeCandSelected = 0;
      }
      var hint = document.createElement("span");
      hint.className = "cand-hint";
      hint.textContent = list[0] || "空白：英文斷開";
      containerEl.appendChild(hint);
      return;
    }

    var isCand = mode === "candidates";
    var sel = isCand ? (typeof selectedIdx === "number" && selectedIdx >= 0 ? selectedIdx : 0) : -1;
    if (isFreeBar) {
      freeBarMode = mode;
      freeBarItems = list.slice(0, 9);
      freeCandSelected = sel >= 0 ? sel : 0;
    }

    list.slice(0, 9).forEach(function (word, idx) {
      var el = document.createElement(typeof onPick === "function" ? "button" : "span");
      if (typeof onPick === "function") el.type = "button";
      el.className = "cand" + (isCand && idx === sel ? " is-selected" : "");
      if (isCand) {
        var num = document.createElement("b");
        num.textContent = String(idx + 1);
        el.appendChild(num);
        el.appendChild(document.createTextNode(word));
      } else {
        if (idx === 0) {
          var tabMark = document.createElement("b");
          tabMark.textContent = "⇥";
          el.appendChild(tabMark);
        }
        el.appendChild(document.createTextNode(word));
      }
      if (typeof onPick === "function") {
        el.addEventListener("click", function () {
          onPick(idx, word, mode);
        });
      }
      containerEl.appendChild(el);
    });
  }

  function renderCompositionWithEnRun(el, text, enStart, enLen, baseClass) {
    if (!el) return;
    el.className = baseClass;
    el.innerHTML = "";
    if (!text) return;
    if (enLen > 0 && enStart >= 0 && enStart < text.length) {
      var end = Math.min(text.length, enStart + enLen);
      if (enStart > 0) el.appendChild(document.createTextNode(text.slice(0, enStart)));
      var enSpan = document.createElement("span");
      enSpan.className = "editor-en-run";
      enSpan.textContent = text.slice(enStart, end);
      el.appendChild(enSpan);
      if (end < text.length) el.appendChild(document.createTextNode(text.slice(end)));
    } else {
      el.textContent = text;
    }
  }

  function updateFreeDownBtnState(enabled, open) {
    if (!freeDownBtn) return;
    freeDownBtn.disabled = !enabled;
    freeDownBtn.setAttribute("aria-pressed", open ? "true" : "false");
    freeDownBtn.textContent = open ? "Esc 收起候選" : "↓ 游標候選";
  }

  function hasActiveFreeInput() {
    var raw = freeMainInput ? freeMainInput.value || "" : "";
    return Boolean(raw.trim() && !isImeComposing && !hasSystemImeHan(raw));
  }

  function applyWasmBarResult(res, noticeText, expectedSeq) {
    if (!res || engineMode !== "wasm") return;
    if (expectedSeq != null && expectedSeq !== decodeSeq) return;
    if (!hasActiveFreeInput()) return;
    freeCandOpen = res.barMode === "candidates";
    freeBarOffset = typeof res.barOffset === "number" ? res.barOffset : 0;
    if (res.composition) {
      renderCompositionWithEnRun(
        freeStageOutEl,
        res.composition,
        res.englishRunStart || 0,
        res.englishRunLen || 0,
        "editor-output is-right"
      );
    }
    renderGlassBar(freePillEl, freeCandidatesEl, res.barMode, res.barItems, res.barSelected, handleFreeBarPick);
    updateFreeDownBtnState(Boolean(res.composition || (lastDecodedResult && lastDecodedResult.composition)), freeCandOpen);
    if (freeHonestNotice && noticeText) {
      freeHonestNotice.textContent = noticeText;
    }
  }

  function handleFreeBarPick(idx, word, mode) {
    var ctx = freeCtxInput ? freeCtxInput.value || "" : "";
    if (engineMode === "wasm" && wasmSession) {
      if (!hasActiveFreeInput()) return;
      var seq = decodeSeq;
      if (mode === "candidates" && typeof wasmSession.pickCandidate === "function") {
        clearTimeout(wasmRerankTimer);
        wasmRerankTimer = null;
        wasmSession.pickCandidate(freeBarOffset + idx, { context: ctx, settings: settings }).then(function (picked) {
          applyWasmBarResult(picked, "已於 WASM 游標候選窗選取「" + word + "」（shanjie_wasm_pick 實時套用）。", seq);
        }).catch(function () {});
        return;
      }
      if (mode === "prediction" && typeof wasmSession.pickPrediction === "function") {
        clearTimeout(wasmRerankTimer);
        wasmRerankTimer = null;
        wasmSession.pickPrediction(idx, { context: ctx, settings: settings }).then(function (picked) {
          applyWasmBarResult(picked, "已採納即時預測「" + word + "」（shanjie_wasm_pick 實時套用）。", seq);
        }).catch(function () {});
        return;
      }
      return;
    }

    if (mode === "candidates") {
      renderGlassBar(
        freePillEl,
        freeCandidatesEl,
        "candidates",
        (lastDecodedResult && lastDecodedResult.downCandidates) || [],
        idx,
        handleFreeBarPick
      );
      if (freeHonestNotice) {
        freeHonestNotice.textContent = "游標候選「" + word + "」（54 句對照表實測資料；啟用上方 WASM 引擎或安裝 macOS 版可即時套用選字）。";
      }
    } else if (freeHonestNotice) {
      freeHonestNotice.textContent = "即時預測「" + word + "」（真實核心實測接續詞；按 ↓ 或點「游標候選」看游標處單排候選窗）。";
    }
  }

  function toggleFreeCandidatesWindow() {
    var ctx = freeCtxInput ? freeCtxInput.value || "" : "";
    if (engineMode === "wasm" && wasmSession && typeof wasmSession.openCandidates === "function") {
      if (!hasActiveFreeInput() || (!freeCandOpen && !(lastDecodedResult && lastDecodedResult.composition))) {
        return;
      }
      var seq = decodeSeq;
      clearTimeout(wasmRerankTimer);
      wasmRerankTimer = null;
      if (freeCandOpen) {
        if (typeof wasmSession.sendKey === "function") {
          wasmSession.sendKey(6, "", 0, { context: ctx, settings: settings }).then(function (closed) {
            applyWasmBarResult(closed, WASM_DEFAULT_NOTICE, seq);
          }).catch(function () {});
        } else {
          freeCandOpen = false;
          evaluateFreeInput();
        }
        return;
      }
      wasmSession.openCandidates({ context: ctx, settings: settings }).then(function (opened) {
        applyWasmBarResult(
          opened,
          opened && opened.barMode === "candidates"
            ? "已送出 ↓ 開啟真實單排候選窗（按 1–9、←／→＋Enter 或點擊任一候選即時套用）。"
            : "游標位置目前無可展開之候選詞。",
          seq
        );
      }).catch(function () {});
      return;
    }

    if (!lastDecodedResult || !lastDecodedResult.matched) return;
    var downList = lastDecodedResult.downCandidates || [];
    var predList = lastDecodedResult.predictions || [];
    if (!freeCandOpen && downList.length > 0) {
      freeCandOpen = true;
      freeBarOffset = 0;
      renderGlassBar(freePillEl, freeCandidatesEl, "candidates", downList, 0, handleFreeBarPick);
      updateFreeDownBtnState(true, true);
      if (freeHonestNotice) {
        freeHonestNotice.textContent = "游標處單排候選窗（按 ↓ 開啟，真實核心實測候選；再按一次收起回預測列）。";
      }
    } else {
      freeCandOpen = false;
      freeBarOffset = 0;
      renderGlassBar(
        freePillEl,
        freeCandidatesEl,
        predList.length ? "prediction" : "none",
        predList,
        null,
        handleFreeBarPick
      );
      updateFreeDownBtnState(downList.length > 0, false);
      if (freeHonestNotice && lastDecodedResult.notice) {
        freeHonestNotice.textContent = lastDecodedResult.notice;
      }
    }
  }

  if (freeDownBtn) {
    freeDownBtn.addEventListener("click", toggleFreeCandidatesWindow);
  }

  var HAN_CHAR_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/;

  function hasSystemImeHan(str) {
    return HAN_CHAR_RE.test(String(str || ""));
  }

  function updateImeWarnUI(show) {
    if (!freeImeWarn) return;
    freeImeWarn.hidden = !show;
  }

  function evaluateFreeInput(opts) {
    if (!freeMainInput) return;
    var seq = ++decodeSeq;
    freeCandOpen = false;
    freeBarOffset = 0;
    clearTimeout(wasmRerankTimer);
    wasmRerankTimer = null;

    var raw = freeMainInput.value || "";
    var ctx = freeCtxInput ? freeCtxInput.value || "" : "";
    var rawHasHan = hasSystemImeHan(raw);
    updateImeWarnUI(isImeComposing || rawHasHan);

    if (engineMode === "wasm") {
      if (!raw.trim()) {
        lastDecodedResult = null;
        if (wasmSession && typeof wasmSession.reset === "function") {
          wasmSession.reset().catch(function () {});
        }
        if (freeBopomofoEl) freeBopomofoEl.textContent = "等待輸入注音或英文…";
        if (freeStageCtxEl) freeStageCtxEl.textContent = ctx;
        if (freeStageOutEl) {
          freeStageOutEl.textContent = "請先切英文輸入法，直接敲大千鍵或點上方例句";
          freeStageOutEl.className = "editor-output is-unmatched";
        }
        if (freeForgetBtn) freeForgetBtn.hidden = true;
        if (freePredictionBtn) freePredictionBtn.hidden = true;
        if (freeAppleRow) freeAppleRow.hidden = true;
        renderGlassBar(freePillEl, freeCandidatesEl, "none", [], null, null);
        updateFreeDownBtnState(false, false);
        renderTracePills([
          "WASM 真引擎（p35）",
          wasmStage === "stage2-ready" ? "會意 C-M 就緒" : (wasmStage2Error ? "語意校正未載入" : "背景載入會意…")
        ]);
        if (freeHonestNotice) {
          freeHonestNotice.textContent = WASM_DEFAULT_NOTICE;
        }
        return;
      }

      if (rawHasHan) {
        lastDecodedResult = null;
        if (wasmSession && typeof wasmSession.reset === "function") {
          wasmSession.reset().catch(function () {});
        }
        if (freeBopomofoEl) freeBopomofoEl.textContent = "偵測到系統輸入法送出的漢字";
        if (freeStageCtxEl) freeStageCtxEl.textContent = ctx;
        if (freeStageOutEl) {
          freeStageOutEl.textContent = "請先切換為英文輸入法（ABC），再直接敲標準大千鍵試打";
          freeStageOutEl.className = "editor-output is-unmatched";
        }
        if (freeForgetBtn) freeForgetBtn.hidden = true;
        if (freePredictionBtn) freePredictionBtn.hidden = true;
        if (freeAppleRow) freeAppleRow.hidden = true;
        renderGlassBar(freePillEl, freeCandidatesEl, "none", [], null, null);
        updateFreeDownBtnState(false, false);
        renderTracePills(["需切換英文輸入法（ABC）"]);
        if (freeHonestNotice) {
          freeHonestNotice.textContent = "目前系統中文輸入法已先將按鍵轉成漢字，善解網頁試打無法收到原始鍵位；請切回英文輸入法後重打，或展開下方虛擬大千鍵盤。";
        }
        return;
      }

      if (!wasmSession || typeof wasmSession.decode !== "function") {
        engineMode = "table";
        syncWasmUI();
      } else {
        var rerankDelay = opts && opts.immediateRerank ? 0 : 260;
        var isSampleLoad = Boolean(activeSampleId);
        var finalizeLast = isSampleLoad || Boolean(opts && opts.finalizeLast);
        var useRawKeys = !isSampleLoad;
        wasmSession.decode(raw, {
          context: ctx,
          settings: settings,
          finalizeLast: finalizeLast,
          useRawKeys: useRawKeys,
          runRerank: false
        }).then(function (result) {
          if (seq !== decodeSeq || engineMode !== "wasm" || (result && result.superseded)) return;
          result = result || {};
          lastDecodedResult = result;

          if (freeBopomofoEl) {
            var bpm = result.bopomofo || result.rawComposition || "";
            if (!bpm && raw && window.ShanjieEngine && typeof window.ShanjieEngine.standardKeysToBopomofo === "function") {
              bpm = window.ShanjieEngine.standardKeysToBopomofo(raw);
            }
            freeBopomofoEl.textContent = bpm || "等待輸入注音或英文…";
          }

          if (freeStageCtxEl) {
            freeStageCtxEl.textContent = ctx;
          }

          if (freeStageOutEl) {
            var outText = result.composition || result.rawComposition || result.bopomofo || raw;
            var outClass = result.matched
              ? "editor-output is-right"
              : (result.partial ? "editor-output is-composing" : "editor-output is-unmatched");
            renderCompositionWithEnRun(
              freeStageOutEl,
              outText,
              result.englishRunStart || 0,
              result.englishRunLen || 0,
              outClass
            );
          }

          if (freeForgetBtn) {
            freeForgetBtn.hidden = true;
          }

          if (freePredictionBtn && freePredictionText) {
            freePredictionBtn.hidden = true;
          }

          freeCandOpen = result.barMode === "candidates";
          freeBarOffset = typeof result.barOffset === "number" ? result.barOffset : 0;
          renderGlassBar(
            freePillEl,
            freeCandidatesEl,
            result.barMode || "none",
            result.barItems || [],
            result.barSelected,
            handleFreeBarPick
          );
          updateFreeDownBtnState(Boolean(result.composition), freeCandOpen);

          if (freeAppleRow) {
            freeAppleRow.hidden = true;
          }

          renderTracePills(result.trace && result.trace.length ? result.trace : ["WASM 真引擎（p35）"]);

          if (freeHonestNotice) {
            freeHonestNotice.textContent = result.notice || WASM_DEFAULT_NOTICE;
          }

          var stageNow = typeof wasmSession.getStage === "function" ? wasmSession.getStage() : wasmStage;
          if (result.hasRerank && settings.rerank !== "off" && stageNow === "stage2-ready") {
            wasmRerankTimer = setTimeout(function () {
              if (seq !== decodeSeq || engineMode !== "wasm" || !wasmSession || freeCandOpen) return;
              wasmSession.rerank({ context: ctx, settings: settings }).then(function (rr) {
                if (seq !== decodeSeq || engineMode !== "wasm" || !rr || freeCandOpen) return;
                if (rr.changed) {
                  freeBarOffset = typeof rr.barOffset === "number" ? rr.barOffset : 0;
                  if (freeStageOutEl && rr.composition) {
                    renderCompositionWithEnRun(
                      freeStageOutEl,
                      rr.composition,
                      rr.englishRunStart || 0,
                      rr.englishRunLen || 0,
                      "editor-output is-right"
                    );
                  }
                  renderGlassBar(
                    freePillEl,
                    freeCandidatesEl,
                    rr.barMode || "none",
                    rr.barItems || [],
                    rr.barSelected,
                    handleFreeBarPick
                  );
                  var nextTrace = Array.isArray(rr.trace) && rr.trace.length
                    ? rr.trace.slice()
                    : (Array.isArray(result.trace) ? result.trace.slice() : ["WASM 真引擎（p35）"]);
                  if (nextTrace.indexOf("會意 C-M 原地重排") === -1) {
                    nextTrace.push("會意 C-M 原地重排");
                  }
                  renderTracePills(nextTrace);
                }
              }).catch(function () {});
            }, rerankDelay);
          }
        }).catch(function () {
          if (seq !== decodeSeq || engineMode !== "wasm") return;
          if (freeHonestNotice) {
            freeHonestNotice.textContent = "瀏覽器 WASM 解碼暫時無法處理此輸入，請調整輸入或切回範例對照模式。";
          }
        });
        return;
      }
    }

    if (!window.ShanjieEngine || typeof window.ShanjieEngine.decode !== "function") {
      if (freeStageCtxEl) freeStageCtxEl.textContent = ctx;
      if (freeStageOutEl) {
        freeStageOutEl.textContent = raw || "這個 bug 明天再修";
        freeStageOutEl.className = "editor-output is-right";
      }
      if (freeHonestNotice) {
        freeHonestNotice.textContent = DEFAULT_NOTICE;
      }
      return;
    }

    // Swappable engine interface (ShanjieEngine.decode + setBackend(fn)),
    // paired with opt-in browser WASM worker above.
    var result = window.ShanjieEngine.decode(raw, { context: ctx, settings: settings });
    lastDecodedResult = result;

    if (freeBopomofoEl) {
      var bpm = result.bopomofo || result.rawComposition || "";
      if (!bpm && raw && typeof window.ShanjieEngine.standardKeysToBopomofo === "function") {
        bpm = window.ShanjieEngine.standardKeysToBopomofo(raw);
      }
      freeBopomofoEl.textContent = bpm || "等待輸入注音或英文…";
    }

    if (freeStageCtxEl) {
      freeStageCtxEl.textContent = ctx;
    }

    if (freeStageOutEl) {
      if (!raw.trim()) {
        freeStageOutEl.textContent = "請先切英文輸入法，直接敲大千鍵或點上方例句";
        freeStageOutEl.className = "editor-output is-unmatched";
      } else if (rawHasHan && !result.matched && !result.partial) {
        freeStageOutEl.textContent = "請先切換為英文輸入法（ABC），再直接敲標準大千鍵試打";
        freeStageOutEl.className = "editor-output is-unmatched";
      } else if (result.matched || result.partial) {
        freeStageOutEl.textContent = result.composition || result.rawComposition || raw;
        freeStageOutEl.className = result.matched ? "editor-output is-right" : "editor-output is-composing";
      } else {
        freeStageOutEl.textContent = result.rawComposition || result.bopomofo || raw;
        freeStageOutEl.className = "editor-output is-unmatched";
      }
    }

    if (freeForgetBtn) {
      freeForgetBtn.hidden = true;
    }

    renderGlassBar(
      freePillEl,
      freeCandidatesEl,
      result.barMode || "none",
      result.barItems || [],
      result.barSelected,
      handleFreeBarPick
    );
    updateFreeDownBtnState(Boolean(result.matched && result.downCandidates && result.downCandidates.length), false);

    // Prediction pill (only during partial prefix typing)
    if (freePredictionBtn && freePredictionText) {
      if (result.prediction) {
        freePredictionText.textContent = result.prediction;
        freePredictionBtn.hidden = false;
      } else {
        freePredictionBtn.hidden = true;
      }
    }

    // Apple comparison & source
    if (freeAppleRow && freeAppleOut && freeSourceNote) {
      if (result.appleText || result.source || result.cliStatus) {
        freeAppleRow.hidden = false;
        freeAppleOut.textContent = result.appleText || "（此句未附蘋果注音對照）";
        var notes = [];
        if (result.source) notes.push("出處：" + result.source);
        if (result.cliStatus) notes.push(result.cliStatus);
        freeSourceNote.textContent = notes.join("・");
      } else {
        freeAppleRow.hidden = true;
      }
    }

    renderTracePills(result.trace && result.trace.length ? result.trace : ["通語 SJ-LM-7-9.7M"]);

    if (freeHonestNotice) {
      freeHonestNotice.textContent = result.notice || DEFAULT_NOTICE;
    }

    // When a desktop user actively types their own sentence that misses the 54-sentence
    // lookup table, auto-start the browser WASM engine instead of stranding them on a dead end.
    // Skip auto-start if the input is Han characters already committed by a system IME.
    if (
      opts &&
      opts.userTyped &&
      raw.trim() &&
      !rawHasHan &&
      !result.matched &&
      !result.partial &&
      !isCompactMobile() &&
      !userManualTableMode &&
      engineMode === "table" &&
      wasmStage !== "error" &&
      !wasmStartPromise
    ) {
      startWasmEngine(true);
    }
  }

  if (freeMainInput) {
    freeMainInput.addEventListener("compositionstart", function () {
      isImeComposing = true;
      updateImeWarnUI(true);
    });
    freeMainInput.addEventListener("compositionend", function () {
      isImeComposing = false;
      activeSampleId = null;
      highlightSamplePills();
      evaluateFreeInput({ userTyped: true });
    });
    freeMainInput.addEventListener("input", function () {
      if (isImeComposing) {
        updateImeWarnUI(true);
        return;
      }
      activeSampleId = null;
      highlightSamplePills();
      evaluateFreeInput({ userTyped: true });
    });
    freeMainInput.addEventListener("keydown", function (e) {
      var noMods = !e.ctrlKey && !e.metaKey && !e.altKey;
      var ctx = freeCtxInput ? freeCtxInput.value || "" : "";

      if (e.key === "Tab" && noMods) {
        if (engineMode === "table" && lastDecodedResult && lastDecodedResult.prediction) {
          e.preventDefault();
          acceptFreePrediction();
          return;
        }
        if (engineMode === "wasm" && wasmSession && typeof wasmSession.sendKey === "function" && hasActiveFreeInput()) {
          if (freeBarMode === "prediction" && !e.shiftKey) {
            e.preventDefault();
            var tabSeq = decodeSeq;
            clearTimeout(wasmRerankTimer);
            wasmRerankTimer = null;
            wasmSession.sendKey(13, "", 0, { context: ctx, settings: settings }).then(function (entered) {
              applyWasmBarResult(entered, "已按 Tab 進入即時預測列（按 1–9、←／→＋Enter 選取，或按 Esc 退出）。", tabSeq);
            }).catch(function () {});
            return;
          }
          if (freeCandOpen) {
            e.preventDefault();
            var tab2Seq = decodeSeq;
            clearTimeout(wasmRerankTimer);
            wasmRerankTimer = null;
            wasmSession.sendKey(13, "", e.shiftKey ? 1 : 0, { context: ctx, settings: settings }).then(function (next) {
              applyWasmBarResult(next, e.shiftKey ? WASM_DEFAULT_NOTICE : null, tab2Seq);
            }).catch(function () {});
            return;
          }
        }
      }

      if (e.key === "ArrowDown" && noMods) {
        if (!hasActiveFreeInput()) return;
        if (engineMode === "wasm" && !freeCandOpen && !(lastDecodedResult && lastDecodedResult.composition)) return;
        e.preventDefault();
        if (!freeCandOpen) {
          toggleFreeCandidatesWindow();
        } else if (engineMode === "wasm" && wasmSession && typeof wasmSession.sendKey === "function") {
          var downSeq = decodeSeq;
          wasmSession.sendKey(10, "", 0, { context: ctx, settings: settings }).then(function (next) {
            applyWasmBarResult(next, null, downSeq);
          }).catch(function () {});
        }
        return;
      }

      if (freeCandOpen && noMods) {
        if (e.key === "Escape") {
          e.preventDefault();
          toggleFreeCandidatesWindow();
          return;
        }
        if (e.key === "ArrowUp" && engineMode === "wasm" && wasmSession && typeof wasmSession.sendKey === "function") {
          e.preventDefault();
          var upSeq = decodeSeq;
          wasmSession.sendKey(9, "", 0, { context: ctx, settings: settings }).then(function (prev) {
            applyWasmBarResult(prev, null, upSeq);
          }).catch(function () {});
          return;
        }
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          e.preventDefault();
          var dir = e.key === "ArrowRight" ? 1 : -1;
          if (engineMode === "wasm" && wasmSession && typeof wasmSession.sendKey === "function") {
            var moveSeq = decodeSeq;
            wasmSession.sendKey(dir > 0 ? 8 : 7, "", 0, { context: ctx, settings: settings }).then(function (moved) {
              applyWasmBarResult(moved, null, moveSeq);
            }).catch(function () {});
          } else if (freeBarItems.length > 0) {
            var nextIdx = (freeCandSelected + dir + freeBarItems.length) % freeBarItems.length;
            renderGlassBar(freePillEl, freeCandidatesEl, "candidates", freeBarItems, nextIdx, handleFreeBarPick);
          }
          return;
        }
        if (e.key >= "1" && e.key <= "9" && !e.shiftKey) {
          e.preventDefault();
          var pickIdx = Number(e.key) - 1;
          if (pickIdx >= 0 && pickIdx < freeBarItems.length) {
            handleFreeBarPick(pickIdx, freeBarItems[pickIdx], "candidates");
          }
          return;
        }
        if (e.key === "Enter" && !e.shiftKey && freeBarItems.length > 0) {
          e.preventDefault();
          var selIdx = Math.max(0, Math.min(freeCandSelected, freeBarItems.length - 1));
          handleFreeBarPick(selIdx, freeBarItems[selIdx], "candidates");
          return;
        }
      }
    });
  }

  if (freeCtxInput) {
    freeCtxInput.addEventListener("input", function () {
      evaluateFreeInput();
    });
  }

  function acceptFreePrediction() {
    if (!lastDecodedResult || !lastDecodedResult.prediction || !freeStageOutEl) return;
    var pred = lastDecodedResult.prediction;
    freeStageOutEl.textContent = (lastDecodedResult.composition || "") + pred;
    if (freeHonestNotice) {
      freeHonestNotice.textContent = "已按 Tab 採納預測接續詞「" + pred + "」（網頁試打介面示範）。";
    }
    if (freePredictionBtn) freePredictionBtn.hidden = true;
  }

  if (freePredictionBtn) {
    freePredictionBtn.addEventListener("click", acceptFreePrediction);
  }

  if (freeForgetBtn) {
    freeForgetBtn.addEventListener("click", function () {
      var raw = freeMainInput ? freeMainInput.value || "" : "";
      var ctx = freeCtxInput ? freeCtxInput.value || "" : "";
      var key = makeMemoryKey(ctx, raw);
      if (settings.memoryOverrides && settings.memoryOverrides[key]) {
        delete settings.memoryOverrides[key];
      }
      evaluateFreeInput();
    });
  }

  function clearFreeInputAndFocus() {
    if (freeMainInput) freeMainInput.value = "";
    if (freeCtxInput) freeCtxInput.value = "";
    activeSampleId = null;
    isImeComposing = false;
    updateImeWarnUI(false);
    highlightSamplePills();
    evaluateFreeInput();
    if (freeMainInput) freeMainInput.focus();
  }

  if (freeClearBtn) {
    freeClearBtn.addEventListener("click", clearFreeInputAndFocus);
  }
  if (freeImeClearBtn) {
    freeImeClearBtn.addEventListener("click", clearFreeInputAndFocus);
  }

  function startWasmEngine(isAutoTrigger) {
    if (wasmStartPromise) return;

    var ctrl = window.ShanjieWasmController;
    var sup = ctrl && typeof ctrl.isSupported === "function"
      ? ctrl.isSupported()
      : {
        ok: false,
        reason: window.location && window.location.protocol === "file:" ? "file-protocol" : "no-wasm-worker"
      };

    if (!sup || !sup.ok) {
      engineMode = "table";
      wasmStage = "idle";
      syncWasmUI();
      if (!isAutoTrigger && freeHonestNotice) {
        freeHonestNotice.textContent = sup && sup.reason === "file-protocol"
          ? WASM_UNSUPPORTED_FILE_NOTICE
          : WASM_UNSUPPORTED_ENV_NOTICE;
      }
      return;
    }

    var curStage = wasmSession && typeof wasmSession.getStage === "function" ? wasmSession.getStage() : "idle";
    if (wasmSession && (curStage === "stage1-ready" || curStage === "stage2-ready")) {
      wasmStage = curStage;
      engineMode = "wasm";
      syncWasmUI();
      evaluateFreeInput();
      return;
    }

    if (wasmSession && typeof wasmSession.terminate === "function") {
      try {
        wasmSession.terminate();
      } catch (_e) {}
    }
    wasmSession = null;
    wasmStage = "loading-stage1";
    wasmStage2Error = false;
    syncWasmUI();
    if (isAutoTrigger && freeHonestNotice) {
      freeHonestNotice.textContent = "偵測到自由輸入，正自動載入瀏覽器真引擎（WASM，約 19 MiB）… 載入完成後立即解碼。";
    }

    var assetBase = (workbenchEl && workbenchEl.getAttribute("data-wasm-base")) || "/wasm-assets/v1-p35-2ff55dcc";
    var workerUrl = (workbenchEl && workbenchEl.getAttribute("data-wasm-worker")) || "/wasm/shanjie-worker.js";

    wasmSession = ctrl.createSession({
      workerUrl: workerUrl,
      assetBase: assetBase,
      onProgress: function (info) {
        if (!info || !info.stage) return;
        wasmStage = info.stage;
        wasmStage2Error = Boolean(info.stage2Error);
        syncWasmUI();
        if (info.stage === "stage2-ready" && engineMode === "wasm") {
          if (freeMainInput && freeMainInput.value && freeMainInput.value.trim()) {
            evaluateFreeInput({ immediateRerank: true });
          }
        }
      }
    });

    wasmStartPromise = Promise.resolve(wasmSession.start()).then(function () {
      wasmStartPromise = null;
      if (!wasmSession) return;
      wasmStage = typeof wasmSession.getStage === "function" ? wasmSession.getStage() : "stage1-ready";
      engineMode = "wasm";
      syncWasmUI();
      evaluateFreeInput();
    }).catch(function () {
      wasmStartPromise = null;
      if (wasmSession && typeof wasmSession.terminate === "function") {
        try {
          wasmSession.terminate();
        } catch (_e) {}
      }
      wasmSession = null;
      engineMode = "table";
      wasmStage = "error";
      syncWasmUI();
      if (freeHonestNotice) {
        freeHonestNotice.textContent = WASM_FETCH_FAIL_NOTICE;
      }
    });
  }

  if (wasmEnableBtn) {
    wasmEnableBtn.addEventListener("click", function () {
      if (engineMode === "wasm") {
        userManualTableMode = true;
        engineMode = "table";
        clearTimeout(wasmRerankTimer);
        wasmRerankTimer = null;
        decodeSeq += 1;
        syncWasmUI();
        evaluateFreeInput();
        return;
      }
      userManualTableMode = false;
      startWasmEngine(false);
    });
  }

  /* Virtual Daqian Keypad (Standard Daqian layout) */
  var DAQIAN_ROWS = [
    [
      ["1", "ㄅ"], ["2", "ㄉ"], ["3", "ˇ"], ["4", "ˋ"], ["5", "ㄓ"],
      ["6", "ˊ"], ["7", "˙"], ["8", "ㄚ"], ["9", "ㄞ"], ["0", "ㄢ"], ["-", "ㄦ"]
    ],
    [
      ["q", "ㄆ"], ["w", "ㄊ"], ["e", "ㄍ"], ["r", "ㄐ"], ["t", "ㄔ"],
      ["y", "ㄗ"], ["u", "ㄧ"], ["i", "ㄛ"], ["o", "ㄟ"], ["p", "ㄣ"]
    ],
    [
      ["a", "ㄇ"], ["s", "ㄋ"], ["d", "ㄎ"], ["f", "ㄑ"], ["g", "ㄕ"],
      ["h", "ㄘ"], ["j", "ㄨ"], ["k", "ㄜ"], ["l", "ㄠ"], [";", "ㄤ"]
    ],
    [
      ["z", "ㄈ"], ["x", "ㄌ"], ["c", "ㄏ"], ["v", "ㄒ"], ["b", "ㄖ"],
      ["n", "ㄙ"], ["m", "ㄩ"], [",", "ㄝ"], [".", "ㄡ"], ["/", "ㄥ"]
    ]
  ];

  var toggleKeypadBtn = document.getElementById("toggle-keypad-btn");
  var virtualKeypadEl = document.getElementById("virtual-keypad");

  function buildVirtualKeypad() {
    if (!virtualKeypadEl) return;
    virtualKeypadEl.innerHTML = "";
    DAQIAN_ROWS.forEach(function (row) {
      var rowDiv = document.createElement("div");
      rowDiv.className = "vk-row";
      row.forEach(function (pair) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "vk-key";
        btn.innerHTML = "<span>" + pair[1] + "</span><small>" + pair[0] + "</small>";
        btn.addEventListener("click", function () {
          if (!freeMainInput) return;
          freeMainInput.value += pair[0];
          activeSampleId = null;
          highlightSamplePills();
          evaluateFreeInput({ userTyped: true });
          freeMainInput.focus();
        });
        rowDiv.appendChild(btn);
      });
      virtualKeypadEl.appendChild(rowDiv);
    });

    var actionRow = document.createElement("div");
    actionRow.className = "vk-row";
    var spaceBtn = document.createElement("button");
    spaceBtn.type = "button";
    spaceBtn.className = "vk-key";
    spaceBtn.innerHTML = "<span>一聲／空白</span><small>Space</small>";
    spaceBtn.addEventListener("click", function () {
      if (!freeMainInput) return;
      freeMainInput.value += " ";
      activeSampleId = null;
      highlightSamplePills();
      evaluateFreeInput({ userTyped: true });
      freeMainInput.focus();
    });
    var bkBtn = document.createElement("button");
    bkBtn.type = "button";
    bkBtn.className = "vk-key";
    bkBtn.innerHTML = "<span>退格</span><small>⌫</small>";
    bkBtn.addEventListener("click", function () {
      if (!freeMainInput) return;
      freeMainInput.value = freeMainInput.value.slice(0, -1);
      activeSampleId = null;
      highlightSamplePills();
      evaluateFreeInput();
      freeMainInput.focus();
    });
    actionRow.appendChild(spaceBtn);
    actionRow.appendChild(bkBtn);
    virtualKeypadEl.appendChild(actionRow);
  }

  if (toggleKeypadBtn && virtualKeypadEl) {
    buildVirtualKeypad();
    toggleKeypadBtn.addEventListener("click", function () {
      var open = virtualKeypadEl.hidden;
      virtualKeypadEl.hidden = !open;
      toggleKeypadBtn.setAttribute("aria-expanded", open ? "true" : "false");
      toggleKeypadBtn.textContent = open ? "收合大千鍵盤" : "展開大千鍵盤";
    });
  }

  /* =========================================================
   * 6. Mode 2: Categorized Sentence Corpus Browser
   * ========================================================= */
  var corpusCatTabsEl = document.getElementById("corpus-cat-tabs");
  var corpusListEl = document.getElementById("corpus-list");
  var corpusCountEl = document.getElementById("corpus-count");
  var activeCorpusCat = "colloquial";

  function renderCorpusTabs() {
    if (!corpusCatTabsEl || !window.ShanjieEngine || typeof window.ShanjieEngine.getCategories !== "function") {
      return;
    }
    var cats = window.ShanjieEngine.getCategories() || [];
    if (!cats.length) return;
    corpusCatTabsEl.innerHTML = "";
    cats.forEach(function (cat, idx) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip" + (cat.id === activeCorpusCat || (idx === 0 && !activeCorpusCat) ? " is-active" : "");
      btn.setAttribute("data-corpus-cat", cat.id);
      btn.textContent = cat.label + "（" + cat.count + "）";
      btn.addEventListener("click", function () {
        activeCorpusCat = cat.id;
        renderCorpusTabs();
        renderCorpusList(cat.id);
      });
      corpusCatTabsEl.appendChild(btn);
    });
  }

  document.querySelectorAll("[data-corpus-cat]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      activeCorpusCat = btn.getAttribute("data-corpus-cat") || "colloquial";
      document.querySelectorAll("[data-corpus-cat]").forEach(function (b) {
        b.classList.toggle("is-active", b.getAttribute("data-corpus-cat") === activeCorpusCat);
      });
      renderCorpusList(activeCorpusCat);
    });
  });

  function renderCorpusList(catId) {
    if (!corpusListEl) return;
    if (!window.ShanjieEngine || typeof window.ShanjieEngine.getByCategory !== "function") {
      corpusListEl.innerHTML = "<p class=\"honest-notice\">對照表資料尚未載入。</p>";
      return;
    }
    var items = window.ShanjieEngine.getByCategory(catId) || [];
    if (corpusCountEl) {
      corpusCountEl.textContent = "共 " + items.length + " 句";
    }
    corpusListEl.innerHTML = "";
    items.forEach(function (item) {
      var card = document.createElement("article");
      card.className = "corpus-card";

      var top = document.createElement("div");
      top.className = "corpus-card-top";

      var tags = document.createElement("div");
      tags.className = "corpus-tags";
      if (item.featureLabel) {
        var ft = document.createElement("span");
        ft.className = "corpus-tag";
        ft.textContent = item.featureLabel;
        tags.appendChild(ft);
      }
      if (item.cliStatus) {
        var cs = document.createElement("span");
        cs.className = "corpus-tag";
        cs.textContent = item.cliStatus;
        tags.appendChild(cs);
      }
      top.appendChild(tags);

      var loadBtn = document.createElement("button");
      loadBtn.type = "button";
      loadBtn.className = "ctrl-btn corpus-load-btn";
      loadBtn.textContent = "載入試打 →";
      loadBtn.addEventListener("click", function () {
        loadIntoWorkbench(item.daqianKeys || item.zhuyin, item.context || "", item.id, true);
      });
      top.appendChild(loadBtn);
      card.appendChild(top);

      var zy = document.createElement("div");
      zy.className = "corpus-zy";
      zy.textContent = item.zhuyin + (item.daqianKeys ? "  [" + item.daqianKeys + "]" : "");
      card.appendChild(zy);

      var decoded = window.ShanjieEngine.decode(item.daqianKeys || item.zhuyin, {
        context: item.context || "",
        settings: settings
      });
      var out = document.createElement("div");
      out.className = "corpus-out";
      if (item.context) {
        var ctxSpan = document.createElement("span");
        ctxSpan.className = "corpus-ctx";
        ctxSpan.textContent = item.context;
        out.appendChild(ctxSpan);
      }
      var corpusDisplay = decoded.composition || (decoded.matched === false && decoded.notice ? decoded.notice : item.expected);
      out.appendChild(document.createTextNode(corpusDisplay));
      card.appendChild(out);

      var meta = document.createElement("div");
      meta.className = "corpus-meta";
      if (item.appleText) {
        var appSpan = document.createElement("span");
        appSpan.textContent = "macOS 內建：" + item.appleText;
        meta.appendChild(appSpan);
      }
      if (item.source) {
        var srcSpan = document.createElement("span");
        srcSpan.textContent = "出處：" + item.source;
        meta.appendChild(srcSpan);
      }
      card.appendChild(meta);

      corpusListEl.appendChild(card);
    });
  }

  /* =========================================================
   * 7. Mode 3: Auto-Playing Scenario Duel (Authentic macOS 26 UI)
   * Every bar item comes directly from real shanjie_wasm_simd128.wasm + p35
   * or real VM seg4 recordings; zero fabricated whole-sentence candidates.
   * ========================================================= */
  var SCENARIOS = [
    {
      id: "github",
      topTrackName: "macOS 內建注音",
      context: "",
      keys: ["ㄨㄛˇ", "p", "u", "s", "h", "ㄉㄠˋ", "G", "i", "t", "H", "u", "b", "ㄌㄜ˙"],
      appleSteps: [
        "我", "我", "我", "我", "我",
        "我調", "我調Ｇ", "我調Ｇ", "我調Ｇ", "我調Ｇ", "我調Ｇ", "我調Ｇ",
        "我調Ｇㄌㄧㄜ˙"
      ],
      shanjieSteps: [
        "我", "我 p", "我 pu", "我 pus", "我 push",
        "我 push 到", "我 push 到 G", "我 push 到 Gi", "我 push 到 Git", "我 push 到 GitH", "我 push 到 GitHu", "我 push 到 GitHub",
        "我 push 到 GitHub 了"
      ],
      // Active English runs show thick underline + real macOS hint/prediction bar
      stepBars: {
        1: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [2, 1] },
        2: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [2, 2] },
        3: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [2, 3] },
        4: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [2, 4] },
        6: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [9, 1] },
        7: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [9, 2] },
        8: { mode: "prediction", items: ["GitHub", "Gitzo", "GitLab", "Gittens"], enRange: [9, 3] },
        9: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [9, 4] },
        10: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [9, 5] },
        11: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [9, 6] }
      },
      // At sentence completion, Shanjie commits cleanly without opening a whole-sentence window
      finalBar: { mode: "none", items: [] },
      appleBadge: "同音／英文誤判",
      shanjieBadge: "通語整句解碼",
      readout: "中英混打自動判讀＋英文正式大小寫（GitHub），打完直接送出、全程不必開選字窗。"
    },
    {
      id: "bug",
      topTrackName: "macOS 內建注音",
      context: "",
      keys: ["ㄓㄜˋ", "ㄍㄜ˙", "b", "u", "g", "ㄇㄧㄥˊ", "ㄊㄧㄢ", "ㄗㄞˋ", "ㄒㄧㄡ"],
      appleSteps: [
        "這", "這個", "這個", "這個", "這個咪",
        "這個咪", "這個咪贏", "這個咪贏天", "這個咪贏天在修"
      ],
      shanjieSteps: [
        "這", "這個", "這個 b", "這個 bu", "這個 bug",
        "這個 bug 明", "這個 bug 明天", "這個 bug 明天再", "這個 bug 明天再修"
      ],
      stepBars: {
        2: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [3, 1] },
        3: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [3, 2] },
        4: { mode: "english-hint", items: ["空白：英文斷開"], enRange: [3, 3] }
      },
      finalBar: { mode: "prediction", items: ["修正", "休", "修改", "休息", "修法", "修煉"] },
      appleBadge: "同音／英文誤判",
      shanjieBadge: "1.9 ms 整句解碼",
      readout: "通語 SJ-LM-7-9.7M 整句解碼（每鍵 p95 1.9 ms）＋英文片段保留為 bug，游標下方僅顯示單排即時預測。"
    },
    {
      id: "chang",
      topTrackName: "macOS 內建注音",
      context: "這家店我以前",
      keys: ["ㄔㄤˊ", "ㄔㄤˊ"],
      appleSteps: ["嚐", "嚐嚐"],
      shanjieSteps: ["長", "常常"],
      stepBars: {},
      finalBar: { mode: "prediction", items: ["長長", "長長的", "嚐嚐", "長長久久", "嘗嘗", "長長短短"] },
      appleBadge: "改選一次後被搶走",
      shanjieBadge: "看前文選字（64 字）",
      readout: "讀游標前 64 字前文，選字記憶依前文分開記，不污染「常常」。"
    },
    {
      id: "rerank",
      topTrackName: "通語初解（未開語意校正）",
      context: "",
      keys: ["ㄨㄛˇ", "ㄕ", "ㄇㄧㄢˊ", "ㄌㄜ˙", "ㄧ", "ㄧㄝˋ"],
      appleSteps: ["我", "我師", "我失眠", "我失眠了", "我失眠了一", "我失眠了一頁"],
      shanjieSteps: ["我", "我師", "我失眠", "我失眠了", "我失眠了一", "我失眠了一頁"],
      rerankOutput: "我失眠了一夜",
      stepBars: {},
      finalBar: { mode: "prediction", items: ["一頁", "一夜情", "一葉", "一夜致富", "一頁書", "一夜之間"] },
      appleBadge: "初解同音第 1 名",
      shanjieBadge: "停頓時會意重排",
      readout: "停頓時會意模型在背景重排內部前 8 名，直接原地把組字區「一頁」改為「一夜」，不彈出整句選單。"
    },
    {
      id: "anime",
      topTrackName: "macOS 內建注音",
      context: "",
      keys: ["ㄙㄢ", "ㄐㄧㄠˇ", "ㄔㄨ", "ㄏㄨㄚˊ", "ㄏㄠˇ", "ㄨㄣ", "ㄖㄡˊ"],
      appleSteps: ["三", "三角", "三角出", "三角出華", "三角出華好", "三角出華好溫", "三角出華好溫柔"],
      shanjieSteps: ["三", "三角", "三角初", "三角初華", "三角初華好", "三角初華好溫", "三角初華好溫柔"],
      stepBars: {},
      finalBar: { mode: "prediction", items: ["溫柔敦厚", "溫柔鄉", "溫柔交響曲舞衣", "柔霧妝", "柔霧唇", "柔軟"] },
      appleBadge: "未收角色名誤選",
      shanjieBadge: "動漫與遊戲詞包",
      readout: "內建「動漫與遊戲」詞包（44,552 詞）直接命中角色名「三角初華」。"
    }
  ];

  var scenarioChips = document.querySelectorAll(".scenario-chips .chip");
  var duelKeysEl = document.getElementById("duel-keys");
  var appleTrackNameEl = document.querySelector(".track-apple .track-name");
  var appleCtxEl = document.getElementById("apple-context");
  var appleOutEl = document.getElementById("apple-output");
  var appleBadgeEl = document.getElementById("apple-badge");
  var shanjieCtxEl = document.getElementById("shanjie-context");
  var shanjieOutEl = document.getElementById("shanjie-output");
  var shanjieBadgeEl = document.getElementById("shanjie-badge");
  var duelPillEl = document.getElementById("duel-pill");
  var duelPillItemsEl = document.getElementById("duel-pill-items");
  var duelRerankTagEl = document.getElementById("duel-rerank-tag");
  var duelReadoutTextEl = document.getElementById("duel-readout-text");
  var pauseBtn = document.getElementById("duel-pause");
  var pauseLabel = document.getElementById("duel-pause-label");
  var replayBtn = document.getElementById("duel-replay");

  var currentScenarioIdx = 0;
  var duelPaused = isReducedMotion();
  var stepTimer = null;
  var nextTimer = null;
  var rerankTimer = null;

  function clearDuelTimers() {
    clearTimeout(stepTimer);
    clearTimeout(nextTimer);
    clearTimeout(rerankTimer);
  }

  function setScenarioChipActive(idx) {
    scenarioChips.forEach(function (chip, i) {
      var active = i === idx;
      chip.classList.toggle("is-active", active);
      chip.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function renderScenarioFinal(scenario) {
    if (!duelKeysEl) return;
    if (appleTrackNameEl) appleTrackNameEl.textContent = scenario.topTrackName || "macOS 內建注音";
    duelKeysEl.textContent = scenario.keys.join(" ");
    appleCtxEl.textContent = scenario.context;
    shanjieCtxEl.textContent = scenario.context;
    appleOutEl.textContent = scenario.appleSteps[scenario.appleSteps.length - 1];
    appleBadgeEl.textContent = scenario.appleBadge;
    shanjieBadgeEl.textContent = scenario.shanjieBadge;
    var finalText = scenario.rerankOutput || scenario.shanjieSteps[scenario.shanjieSteps.length - 1];
    renderCompositionWithEnRun(shanjieOutEl, finalText, 0, 0, "editor-output is-right");
    var fb = scenario.finalBar || { mode: "none", items: [] };
    renderGlassBar(duelPillEl, duelPillItemsEl, fb.mode, fb.items, fb.selected, null);
    if (duelRerankTagEl) duelRerankTagEl.hidden = !scenario.rerankOutput;
    if (duelReadoutTextEl) duelReadoutTextEl.textContent = scenario.readout;
  }

  function playScenario(idx, forceAnimate) {
    clearDuelTimers();
    currentScenarioIdx = idx;
    var scenario = SCENARIOS[idx];
    setScenarioChipActive(idx);

    if (isReducedMotion() && !forceAnimate) {
      renderScenarioFinal(scenario);
      return;
    }

    if (appleTrackNameEl) appleTrackNameEl.textContent = scenario.topTrackName || "macOS 內建注音";
    appleCtxEl.textContent = scenario.context;
    shanjieCtxEl.textContent = scenario.context;
    appleBadgeEl.textContent = scenario.appleBadge;
    shanjieBadgeEl.textContent = scenario.shanjieBadge;
    if (duelReadoutTextEl) duelReadoutTextEl.textContent = scenario.readout;
    if (duelRerankTagEl) duelRerankTagEl.hidden = true;

    var step = 0;
    var total = scenario.keys.length;

    function tick() {
      if (activeMode !== "duel") return;
      duelKeysEl.textContent = scenario.keys.slice(0, step + 1).join(" ");
      appleOutEl.textContent = scenario.appleSteps[step];
      var stepBar = scenario.stepBars && scenario.stepBars[step] ? scenario.stepBars[step] : null;
      var enStart = stepBar && stepBar.enRange ? stepBar.enRange[0] : 0;
      var enLen = stepBar && stepBar.enRange ? stepBar.enRange[1] : 0;
      var cls = step === total - 1 && !scenario.rerankOutput
        ? "editor-output is-right"
        : "editor-output is-composing";
      renderCompositionWithEnRun(shanjieOutEl, scenario.shanjieSteps[step], enStart, enLen, cls);

      if (step < total - 1) {
        if (stepBar) {
          renderGlassBar(duelPillEl, duelPillItemsEl, stepBar.mode, stepBar.items, stepBar.selected, null);
        } else {
          renderGlassBar(duelPillEl, duelPillItemsEl, "none", [], null, null);
        }
        step += 1;
        stepTimer = setTimeout(tick, 220);
      } else {
        var fb = scenario.finalBar || { mode: "none", items: [] };
        if (scenario.rerankOutput) {
          renderGlassBar(duelPillEl, duelPillItemsEl, "none", [], null, null);
          rerankTimer = setTimeout(function () {
            renderCompositionWithEnRun(shanjieOutEl, scenario.rerankOutput, 0, 0, "editor-output is-right");
            renderGlassBar(duelPillEl, duelPillItemsEl, fb.mode, fb.items, fb.selected, null);
            if (duelRerankTagEl) duelRerankTagEl.hidden = false;
            scheduleNext();
          }, 520);
        } else {
          renderGlassBar(duelPillEl, duelPillItemsEl, fb.mode, fb.items, fb.selected, null);
          scheduleNext();
        }
      }
    }

    function scheduleNext() {
      if (duelPaused) return;
      nextTimer = setTimeout(function () {
        if (activeMode === "duel" && !duelPaused) {
          playScenario((currentScenarioIdx + 1) % SCENARIOS.length);
        }
      }, 2800);
    }

    tick();
  }

  scenarioChips.forEach(function (chip, idx) {
    chip.addEventListener("click", function () {
      playScenario(idx, true);
    });
  });

  if (pauseBtn && pauseLabel) {
    pauseBtn.addEventListener("click", function () {
      duelPaused = !duelPaused;
      pauseBtn.setAttribute("aria-pressed", duelPaused ? "true" : "false");
      pauseLabel.textContent = duelPaused ? "繼續" : "暫停";
      if (duelPaused) {
        clearDuelTimers();
        renderScenarioFinal(SCENARIOS[currentScenarioIdx]);
      } else if (activeMode === "duel") {
        playScenario(currentScenarioIdx, true);
      }
    });
  }

  if (replayBtn) {
    replayBtn.addEventListener("click", function () {
      playScenario(currentScenarioIdx, true);
    });
  }

  /* =========================================================
   * 8. Section 2: Real-VM Theatre & Homophone Traps
   * Media budgets: WebM <=350 KB, MP4 <=500 KB, WebP poster <=35 KB.
   * Direct <video> + WebP poster playback with reduced-motion fallback.
   * ========================================================= */
  var ledgerButtons = Array.prototype.slice.call(document.querySelectorAll(".ledger-item"));
  var theatreFrame = document.getElementById("theatre-frame");
  var theatreVideo = document.getElementById("theatre-video");
  var theatreSrcWebm = document.getElementById("theatre-src-webm");
  var theatreSrcMp4 = document.getElementById("theatre-src-mp4");
  var theatrePosterImg = document.getElementById("theatre-poster-img");
  var theatreBadge = document.getElementById("theatre-badge");
  var theatreKeys = document.getElementById("theatre-keys-text");
  var theatreApple = document.getElementById("theatre-apple-text");
  var theatreShanjie = document.getElementById("theatre-shanjie-text");
  var theatreMotionToggle = document.getElementById("theatre-motion-toggle");
  var theatrePlaying = !isReducedMotion();

  function getClipFromButton(btn) {
    if (!btn) return null;
    return {
      webm: btn.getAttribute("data-video-webm") || "",
      mp4: btn.getAttribute("data-video-mp4") || "",
      poster: btn.getAttribute("data-poster-webp") || "",
      alt: btn.getAttribute("data-alt") || ""
    };
  }

  var initialLedger = ledgerButtons.filter(function (b) {
    return b.classList.contains("is-active");
  })[0] || ledgerButtons[0];

  var activeClip = getClipFromButton(initialLedger) || {
    webm: theatreSrcWebm ? theatreSrcWebm.getAttribute("src") || "" : "",
    mp4: theatreSrcMp4 ? theatreSrcMp4.getAttribute("src") || "" : "",
    poster: theatrePosterImg ? theatrePosterImg.getAttribute("src") || "" : "",
    alt: theatreVideo ? theatreVideo.getAttribute("aria-label") || "" : ""
  };

  function updateTheatreMedia() {
    if (theatrePlaying) {
      if (theatreFrame) theatreFrame.setAttribute("data-motion-override", "play");
      if (theatrePosterImg) theatrePosterImg.hidden = true;
      if (theatreVideo) {
        theatreVideo.hidden = false;
        var curWebm = theatreSrcWebm ? theatreSrcWebm.getAttribute("src") || "" : "";
        var curMp4 = theatreSrcMp4 ? theatreSrcMp4.getAttribute("src") || "" : "";
        var srcChanged = curWebm !== activeClip.webm || curMp4 !== activeClip.mp4;
        if (activeClip.poster && theatreVideo.getAttribute("poster") !== activeClip.poster) {
          theatreVideo.setAttribute("poster", activeClip.poster);
        }
        if (activeClip.alt) {
          theatreVideo.setAttribute("aria-label", activeClip.alt);
        }
        if (srcChanged) {
          if (theatreSrcWebm) theatreSrcWebm.setAttribute("src", activeClip.webm);
          if (theatreSrcMp4) theatreSrcMp4.setAttribute("src", activeClip.mp4);
          theatreVideo.load();
        }
        var playPromise = theatreVideo.play();
        if (playPromise && typeof playPromise.catch === "function") {
          playPromise.catch(function () {});
        }
      }
      if (theatreMotionToggle) {
        theatreMotionToggle.setAttribute("aria-pressed", "true");
        theatreMotionToggle.textContent = "暫停動畫";
      }
    } else {
      if (theatreFrame) theatreFrame.setAttribute("data-motion-override", "pause");
      if (theatreVideo) {
        theatreVideo.pause();
        theatreVideo.hidden = true;
      }
      if (theatrePosterImg) {
        if (activeClip.poster && theatrePosterImg.getAttribute("src") !== activeClip.poster) {
          theatrePosterImg.setAttribute("src", activeClip.poster);
        }
        if (activeClip.alt) {
          theatrePosterImg.setAttribute(
            "alt",
            activeClip.alt.replace(/^macOS 實機錄影：/, "macOS 實機錄影靜態畫面：")
          );
        }
        theatrePosterImg.hidden = false;
      }
      if (theatreMotionToggle) {
        theatreMotionToggle.setAttribute("aria-pressed", "false");
        theatreMotionToggle.textContent = "播放動畫";
      }
    }
  }

  ledgerButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      ledgerButtons.forEach(function (b) {
        var active = b === btn;
        b.classList.toggle("is-active", active);
        b.setAttribute("aria-selected", active ? "true" : "false");
      });
      activeClip = getClipFromButton(btn) || activeClip;
      if (theatreBadge) theatreBadge.textContent = btn.getAttribute("data-code") || "";
      if (theatreKeys) theatreKeys.textContent = btn.getAttribute("data-keys") || "";
      if (theatreApple) theatreApple.textContent = btn.getAttribute("data-apple") || "";
      if (theatreShanjie) theatreShanjie.textContent = btn.getAttribute("data-shanjie") || "";
      updateTheatreMedia();
    });
  });

  if (theatreFrame && (theatreVideo || theatrePosterImg)) {
    updateTheatreMedia();
  }

  if (theatreMotionToggle) {
    theatreMotionToggle.addEventListener("click", function () {
      theatrePlaying = !theatrePlaying;
      updateTheatreMedia();
    });
  }

  // Homophone Traps Cycler
  var trapTabs = Array.prototype.slice.call(document.querySelectorAll("[data-trap]"));
  var trapItems = Array.prototype.slice.call(document.querySelectorAll("[data-trap-panel]"));
  var trapPanel = document.querySelector(".trap-panel");
  var trapIdx = 0;
  var trapTimer = null;
  var trapPinned = false;

  function selectTrap(idx) {
    if (!trapTabs.length) return;
    trapIdx = idx % trapTabs.length;
    trapTabs.forEach(function (t, i) {
      var active = i === trapIdx;
      t.classList.toggle("is-active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
    trapItems.forEach(function (item, i) {
      item.classList.toggle("is-active", i === trapIdx);
    });
  }

  function startTrapCycle() {
    clearInterval(trapTimer);
    if (isReducedMotion() || trapPinned || !trapTabs.length) return;
    trapTimer = setInterval(function () {
      selectTrap(trapIdx + 1);
    }, 4500);
  }

  trapTabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      trapPinned = true;
      clearInterval(trapTimer);
      selectTrap(Number(tab.getAttribute("data-trap")) || 0);
    });
  });

  if (trapPanel) {
    trapPanel.addEventListener("mouseenter", function () { clearInterval(trapTimer); });
    trapPanel.addEventListener("mouseleave", startTrapCycle);
  }
  startTrapCycle();

  document.querySelectorAll(".trap-load-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      loadIntoWorkbench(
        btn.getAttribute("data-load-keys") || "",
        btn.getAttribute("data-load-ctx") || "",
        null,
        true
      );
    });
  });

  /* =========================================================
   * 9. Section 3: All 9 Interactive Feature Labs
   * ========================================================= */
  var labTabs = Array.prototype.slice.call(document.querySelectorAll("[data-lab-tab]"));
  var labPanes = Array.prototype.slice.call(document.querySelectorAll("[data-lab-pane]"));

  labTabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      var id = tab.getAttribute("data-lab-tab");
      labTabs.forEach(function (t) {
        var active = t.getAttribute("data-lab-tab") === id;
        t.classList.toggle("is-active", active);
        t.setAttribute("aria-selected", active ? "true" : "false");
      });
      labPanes.forEach(function (p) {
        p.classList.toggle("is-active", p.getAttribute("data-lab-pane") === id);
      });
    });
  });

  // Lab 1: Pangu live input
  var labPanguInput = document.getElementById("lab-pangu-input");
  var labPanguLive = document.getElementById("lab-pangu-live");
  var labPanguSamples = document.getElementById("lab-pangu-samples");
  var labPanguState = document.getElementById("lab-pangu-state");

  if (labPanguInput) {
    labPanguInput.addEventListener("input", updateLabDynamicViews);
  }

  // Lab 2: Context
  var LAB_CTX_DATA = {
    myth: {
      prev: "神話傳說裡的",
      zhuyin: "ㄒㄧㄢ ㄖㄣˊ",
      out: "仙人",
      note: "依「神話傳說」前文語境，第一名直接選出「仙人」。"
    },
    taste: {
      prev: "這間店的蛋糕很好吃",
      zhuyin: "ㄋㄧˇ ㄧㄠˋ ㄅㄨˊ ㄧㄠˋ ㄔㄤˊ ㄔㄤˊ ㄎㄢˋ",
      out: "你要不要嚐嚐看",
      note: "游標前出現「這間店的蛋糕很好吃」時，通語初解「常常看」經會意看前文重排為「你要不要嚐嚐看」（官方 --selftest 測項）。"
    },
    wiki: {
      prev: "剛才打開維基百科",
      zhuyin: "ㄨㄟˊ ㄐㄧ",
      out: "維基",
      note: "前文重現機制：同一行游標前 64 字出現過的詞，再打同音詞優先維持同一個寫法（不選成「危機」）。"
    }
  };

  document.querySelectorAll("[data-lab-ctx]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var key = btn.getAttribute("data-lab-ctx");
      document.querySelectorAll("[data-lab-ctx]").forEach(function (b) {
        b.classList.toggle("is-active", b === btn);
      });
      var d = LAB_CTX_DATA[key];
      if (!d) return;
      var prevEl = document.getElementById("lab-ctx-prev");
      var zyEl = document.getElementById("lab-ctx-zhuyin");
      var outEl = document.getElementById("lab-ctx-out");
      var noteEl = document.getElementById("lab-ctx-note");
      if (prevEl) prevEl.textContent = d.prev;
      if (zyEl) zyEl.textContent = d.zhuyin;
      if (outEl) outEl.textContent = d.out;
      if (noteEl) noteEl.textContent = d.note;
    });
  });

  // Lab 4: Real-Time Prediction (100% real engine outputs matching lw-lw-kpredict-row.png & WASM)
  var LAB_PRED_DATA = [
    {
      input: "K（粗底線英文段）",
      items: ["K線", "K歌", "K書", "Kd", "Kt", "Kerr"]
    },
    {
      input: "今天會議 ㄧˇ",
      items: ["以及", "已經", "已", "以後", "以前", "以上"]
    },
    {
      input: "這個 bug 明天再修",
      items: ["修正", "休", "修改", "休息", "修法", "修煉"]
    }
  ];
  var labPredIdx = 0;
  var labPredEntered = false;
  var labPredTabBtn = document.getElementById("lab-pred-tab-btn");

  function renderLabPrediction(idx, entered) {
    labPredIdx = idx;
    if (typeof entered === "boolean") labPredEntered = entered;
    var d = LAB_PRED_DATA[labPredIdx] || LAB_PRED_DATA[0];
    var inpEl = document.getElementById("lab-pred-input");
    var listEl = document.getElementById("lab-pred-list");
    var resEl = document.getElementById("lab-pred-result");
    if (inpEl) inpEl.textContent = d.input;
    if (labPredTabBtn) {
      labPredTabBtn.setAttribute("aria-pressed", labPredEntered ? "true" : "false");
      labPredTabBtn.textContent = labPredEntered ? "模擬按 Shift+Tab 退出預測列" : "模擬按 Tab 進入預測列";
    }
    if (resEl) {
      resEl.textContent = labPredEntered
        ? "已按 Tab 進入預測列：第一格 ⇥ 變為藍底 1，按數字鍵或 Enter 即可採納"
        : "未進入時第一格顯示 ⇥ 且無藍底；按 Tab 進入後才顯示 1–9 與藍底膠囊";
    }
    if (!listEl) return;
    listEl.setAttribute("data-entered", labPredEntered ? "true" : "false");
    listEl.innerHTML = "";
    d.items.forEach(function (word, i) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cand" + (labPredEntered && i === 0 ? " is-selected" : "");
      if (labPredEntered) {
        var n = document.createElement("b");
        n.textContent = String(i + 1);
        btn.appendChild(n);
      } else if (i === 0) {
        var t = document.createElement("b");
        t.textContent = "⇥";
        btn.appendChild(t);
      }
      btn.appendChild(document.createTextNode(word));
      btn.addEventListener("click", function () {
        if (resEl) resEl.textContent = "已採納「" + word + "」（接在游標處，不擅自改動前面已打好的字）";
      });
      listEl.appendChild(btn);
    });
    var chev = document.createElement("span");
    chev.className = "pill-chevron";
    chev.setAttribute("aria-hidden", "true");
    chev.innerHTML = "<svg viewBox=\"0 0 12 12\" width=\"10\" height=\"10\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M2.5 4.25L6 7.75l3.5-3.5\"/></svg>";
    listEl.appendChild(chev);
  }

  if (labPredTabBtn) {
    labPredTabBtn.addEventListener("click", function () {
      renderLabPrediction(labPredIdx, !labPredEntered);
    });
  }

  document.querySelectorAll("[data-lab-pred]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll("[data-lab-pred]").forEach(function (b) {
        b.classList.toggle("is-active", b === btn);
      });
      renderLabPrediction(Number(btn.getAttribute("data-lab-pred")) || 0, false);
    });
  });

  renderLabPrediction(0, false);

  // Lab 5: English chips
  var labEnOutput = document.getElementById("lab-en-output");
  document.querySelectorAll(".lab-en-chip").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var i = btn.getAttribute("data-en-in");
      var o = btn.getAttribute("data-en-out");
      if (labEnOutput) {
        labEnOutput.textContent = "輸入「" + i + "」→ 善解直接輸出「" + o + "」（免切輸入法）";
      }
    });
  });

  // Lab 8: Selection memory demo
  var memPickedTaste = false;
  var memAOut = document.getElementById("lab-mem-a-out");
  var memPickBtn = document.getElementById("lab-mem-pick-taste");
  var memResetBtn = document.getElementById("lab-mem-reset");

  if (memPickBtn && memAOut) {
    memPickBtn.addEventListener("click", function () {
      memPickedTaste = true;
      memAOut.textContent = "嚐嚐（已記住：只在前文「來一口」後面優先出「嚐嚐」）";
    });
  }

  if (memResetBtn && memAOut) {
    memResetBtn.addEventListener("click", function () {
      memPickedTaste = false;
      memAOut.textContent = "已清除示範記憶（恢復模型原本排序）";
    });
  }

  function updateLabDynamicViews() {
    // Lab 1 Pangu
    if (labPanguState) {
      labPanguState.textContent = settings.cjkSpacing ? "設定開啟時自動加空格" : "設定關閉時保持原樣";
    }
    if (labPanguSamples) {
      var base = "我用claude寫／2026年1月15日開會";
      labPanguSamples.textContent = settings.cjkSpacing ? applySafePanguFallback(base) : base;
    }
    if (labPanguLive && labPanguInput) {
      var rawPangu = labPanguInput.value || "";
      labPanguLive.textContent = settings.cjkSpacing ? applySafePanguFallback(rawPangu) : rawPangu;
    }

    // Lab 3 Rerank
    var rerankLabels = {
      off: "關閉",
      conservative: "保守",
      standard: "標準（預設）",
      aggressive: "積極"
    };
    var modeLabelEl = document.getElementById("lab-rerank-mode-label");
    var afterEl = document.getElementById("lab-rerank-after");
    var explainEl = document.getElementById("lab-rerank-explain");
    if (modeLabelEl) modeLabelEl.textContent = rerankLabels[settings.rerank] || "標準";
    if (afterEl) {
      afterEl.textContent = settings.rerank === "off" ? "我失眠了一頁（保留初解第 1 名）" : "我失眠了一夜";
    }
    if (explainEl) {
      explainEl.textContent = settings.rerank === "off"
        ? "關閉語意校正時，只依「通語」初解排序，停頓時不重排。"
        : "讀入游標前最後 62 個字元後，會意小模型只在既有的內部前 8 名候選之間挑最通順的一句，直接原地更新組字區底線文字。";
    }

    // Lab 6 Word Packs
    var packCopy = {
      acg: ["開啟 → 三角初華／若葉睦／葬送的芙莉蓮", "關閉 → 三角出華（退回一般詞庫）"],
      it: ["開啟 → 圖像生成／訓練集／螢幕保護器", "關閉 → 不套用資訊與 AI 詞包"],
      beauty: ["開啟 → 眉彩膏／細閃／界面活性劑", "關閉 → 不套用美妝與保養詞包"],
      meme: ["開啟 → 可是瑞凡／計畫通／好耶", "關閉 → 不套用迷因與實況用語詞包"],
      quote: ["開啟 → 原句與常打說法整句收錄（208 列）", "關閉時不介入（預設關）"]
    };
    Object.keys(packCopy).forEach(function (k) {
      var el = document.querySelector("[data-pack-preview=\"" + k + "\"]");
      if (el) el.textContent = settings.packs[k] ? packCopy[k][0] : packCopy[k][1];
    });

    // Lab 7 Fuzzy & Adjacent
    var fuzzyRes = document.getElementById("lab-fuzzy-result");
    var adjRes = document.getElementById("lab-adj-result");
    if (fuzzyRes) {
      fuzzyRes.textContent = settings.fuzzy
        ? "已開啟：ㄗ ㄉㄠˋ 也能打出「知道」、ㄧㄥ ㄨㄟˋ 也能打出「因為」。"
        : "預設關閉：嚴格區分 ㄣ/ㄥ 與捲舌平舌音。";
    }
    if (adjRes) {
      adjRes.textContent = settings.adjacentKey
        ? "已開啟：標準排列同一列鄰鍵按偏（如 ㄋㄞˇ ㄐㄚˊ → 奶茶）可納入候選。"
        : "預設關閉：只在開啟時容許標準排列同一列鄰鍵容錯。";
    }

    // Lab 9 Sandhi
    var sandhiEl = document.getElementById("lab-sandhi-out");
    if (sandhiEl) {
      sandhiEl.innerHTML = settings.toneSandhi
        ? "<code>ㄨㄛˊ ㄒㄧㄤˇ</code> → 我想・<code>ㄌㄧˋ ˙ㄏㄞ</code> → 厲害・<code>ㄖㄜˋ ㄊㄥ ㄊㄥ</code> → 熱騰騰・<code>ㄋㄟˋ ˙ㄍㄜ</code> → 那個"
        : "已關閉變調與口語讀音：僅接受字典本調（照本調打照舊）。";
    }
  }

  /* =========================================================
   * 10. Section 4: Sticky 3-Layer Engine Story Choreography
   * ========================================================= */
  var stageTabs = Array.prototype.slice.call(document.querySelectorAll("[data-stage-tab]"));
  var stageViews = Array.prototype.slice.call(document.querySelectorAll("[data-stage-view]"));
  var storySteps = Array.prototype.slice.call(document.querySelectorAll("[data-step]"));
  var chromeTitle = document.querySelector("[data-stage-chrome-title]");
  var stepTitles = {
    "1": "步驟 1・每一鍵 1.9 ms 整句解碼",
    "2": "步驟 2・停頓時會意語意校正",
    "3": "步驟 3・改選記憶只存在這台 Mac"
  };

  function activateStoryStep(stepNum) {
    var key = String(stepNum);
    stageTabs.forEach(function (tab) {
      var active = tab.getAttribute("data-stage-tab") === key;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    stageViews.forEach(function (v) {
      v.classList.toggle("is-active", v.getAttribute("data-stage-view") === key);
    });
    storySteps.forEach(function (s) {
      s.classList.toggle("is-active", s.getAttribute("data-step") === key);
    });
    if (chromeTitle && stepTitles[key]) {
      chromeTitle.textContent = stepTitles[key];
    }
  }

  stageTabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      activateStoryStep(tab.getAttribute("data-stage-tab"));
    });
  });

  storySteps.forEach(function (step) {
    step.addEventListener("click", function () {
      activateStoryStep(step.getAttribute("data-step"));
    });
  });

  if ("IntersectionObserver" in window && storySteps.length) {
    var observer = new IntersectionObserver(
      function (entries) {
        var best = null;
        entries.forEach(function (entry) {
          if (entry.isIntersecting && (!best || entry.intersectionRatio > best.intersectionRatio)) {
            best = entry;
          }
        });
        if (best) {
          activateStoryStep(best.target.getAttribute("data-step"));
        }
      },
      { root: null, rootMargin: "-32% 0px -44% 0px", threshold: [0.05, 0.25, 0.5] }
    );
    storySteps.forEach(function (s) { observer.observe(s); });
  }

  /* =========================================================
   * 11. Initial Boot
   * ========================================================= */
  window.__shanjieWorkbench = Object.freeze({
    getMode: function () { return engineMode; },
    getStage: function () { return wasmStage; },
    getWasmSession: function () { return wasmSession; }
  });
  syncWasmUI();
  syncSettingsUI();
  renderFreeSampleStrip("mixed");
  renderCorpusTabs();
  renderCorpusList("colloquial");
  evaluateFreeInput();
  if (isCompactMobile()) {
    switchMode("duel", { deferAnimateMs: 1200 });
  }
})();
