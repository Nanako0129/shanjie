// 基準測試頁與 KPI 頁：讀 /data/bench.json、/data/kpi.json，把數字畫成表格。
// 格子的寫法和 tools/bench.py、tools/kpi.py 產生的完整表逐字相同（數字的捨入照 Python），所以兩邊可以逐格核對。
// 不載入外部資源、不寫 cookie 或 localStorage，只讀同一個網站的那一個 JSON。
"use strict";
(function () {
  const root = document.getElementById("numbers");
  if (!root) return;

  // ---------- 數字格式：照 Python 的 format（對浮點數的精確二進位值做 round-half-even） ----------
  // toFixed(100) 給出精確的十進位展開（這裡的數字都在它能精確表示的範圍內），再自己捨入；
  // 不直接用 toFixed(d)／toPrecision：它們遇到剛好一半時進位（0.03125 → 0.0313，Python 是 0.0312）。
  function digitsOf(x) {
    const [i, f] = x.toFixed(100).split(".");
    return { i, f };
  }
  function halfEvenUp(kept, rest) {
    return rest[0] > "5" || (rest[0] === "5" && (/[1-9]/.test(rest.slice(1)) || Number(kept.slice(-1)) % 2 === 1));
  }
  function fixed(x, dec) { // Python f"{x:.{dec}f}" for 0 <= x < 1e21 (toFixed switches to exponent form from 1e21)
    const { i, f } = digitsOf(x);
    const kept = i + f.slice(0, dec);
    const s = (BigInt(kept) + (halfEvenUp(kept, f.slice(dec)) ? 1n : 0n)).toString().padStart(dec + 1, "0");
    return dec ? s.slice(0, -dec) + "." + s.slice(-dec) : s;
  }
  function g3(x) { // Python f"{x:.3g}" for 0 <= x < 1e21; the site's numbers (p, %, ms, MB) stay far below
    if (x === 0) return "0";
    // 1e-4 以下 Python 用指數寫法。這個範圍的浮點數不可能剛好落在 3 位有效數字的一半（要 5^8 整除一個四位數），
    // 所以 toExponential 的捨入就是 Python 的結果；toFixed(100) 只留給 1e-4 以上，100 位小數足夠判斷是不是剛好一半
    if (x < 1e-4) {
      const s = x.toExponential(2);
      if (Number(s.split("e")[1]) < -4) return s.replace(/\.?0+e/, "e").replace(/e([+-])(\d)$/, "e$10$2");
    }
    const { i, f } = digitsOf(x);
    const all = i + f, lead = all.match(/^0*/)[0].length;
    let e = i.length - 1 - lead; // 第一個有效位數的 10 的次方
    let sig = all.slice(lead, lead + 3);
    if (halfEvenUp(sig, all.slice(lead + 3))) {
      sig = String(Number(sig) + 1);
      if (sig.length > 3) { sig = sig.slice(0, 3); e += 1; }
    }
    if (e >= -4 && e < 3) {
      const s = e >= 0 ? sig.slice(0, e + 1) + "." + sig.slice(e + 1) : "0." + "0".repeat(-e - 1) + sig;
      return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
    }
    const m = (sig[0] + "." + sig.slice(1)).replace(/0+$/, "").replace(/\.$/, "");
    return m + "e" + (e < 0 ? "-" : "+") + String(Math.abs(e)).padStart(2, "0");
  }
  const pct = (top1, n) => fixed(100 * top1 / n, 2) + "%";

  // ---------- 畫表 ----------
  // 一格是幾行（md 裡用 <br> 分開）；一行裡反引號包住的部分畫成 <code>。
  function el(tag, attrs, kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    for (const c of kids || []) e.append(c);
    return e;
  }
  function lines(cell) {
    const out = [];
    String(cell).split("<br>").forEach((line, j) => {
      if (j) out.push(el("br"));
      line.split("`").forEach((part, k) => out.push(k % 2 ? el("code", {}, [part]) : part));
    });
    return out;
  }
  function table(path, head, rows) {
    const t = el("table", { "data-md": path }, [
      el("thead", {}, [el("tr", {}, head.map((h) => el("th", { scope: "col" }, lines(h))))]),
      el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c, j) => el(j ? "td" : "th", j ? {} : { scope: "row" }, lines(c)))))),
    ]);
    return el("div", { class: "table" }, [t]);
  }
  function fill(name, nodes) {
    const slot = root.querySelector(`[data-fill="${name}"]`);
    if (slot) slot.replaceChildren(...nodes);
  }
  const heading = (tag, text) => el(tag, {}, [text]);

  // ---------- 基準測試（tools/bench.py 的 render_table、render_v2、render_external） ----------
  const V2 = "套件 v2（每一版用自己出貨的設定）";
  const MODEL_ORDER = ["bigram.sjlm", "classes.sjc", "kn.sjkn", "trigram.sjt3", "trigram-chat.sjt3", "tinylm.sjtl", "colloquial.sjlm"];
  const SETS_V1 = [["dev302", ["chat", "formal"]], ["typing76", ["chat", "formal"]], ["probe", ["chat"]]];
  const SETS_V2 = ["dev302", "typing76", "probe", "user-reported", "fw"];
  const REF_NOTE = "善解的 unigram 解碼器加小麥的基底詞庫，近似小麥的資料；不是小麥本身的解碼器（小麥現在有沒有用 bigram 沒有核對）";

  function accText(r) {
    if (!r || r.missing) return "—（沒有私有資料）";
    if (r.failed) return "失敗";
    return pct(r.top1, r.n);
  }
  function cellText(rec, cell) {
    const r = (rec.accuracy || {})[cell];
    let t = accText(r);
    if (!r || r.missing || r.failed) return t;
    t += `<br>${r.top1}/${r.n}`;
    const d = (rec.vs_prev || {})[cell];
    if (d) t += d.failed ? "<br>失敗" : `<br>+${d.fixed} / −${d.broken}，p=${g3(d.p)}`;
    return t;
  }
  function holdoutCols(recs) { // 依第一次出現的順序；同一份（rows_sha256，沒有就用 n）一欄
    const cols = [];
    for (const r of recs) {
      const h = r.holdout_chat;
      if (h && !cols.some((c) => c.key === (h.rows_sha256 || h.n))) cols.push({ key: h.rows_sha256 || h.n, h });
    }
    return cols.length ? cols : [{ key: null, h: null }]; // 還沒量過任何一版：一欄空的 chat，同完整表
  }
  const holdoutHead = (c) => (c.h ? `chat（n ${c.h.n}，` + (c.h.rows_sha256 ? `#${c.h.rows_sha256.slice(0, 8)}` : "沒記 rows_sha256") + "）" : "chat");
  function holdoutCell(rec, c, oracle) {
    const h = rec.holdout_chat;
    if (!h || (h.rows_sha256 || h.n) !== c.key) return "—（尚未量測）";
    const t = `${pct(h.top1, h.n)}<br>${h.top1}/${h.n}`;
    if (oracle) return t + `，oracle@64 ${h.oracle64}`;
    return t + ("fixed" in h ? `<br>+${h.fixed} / −${h.broken}，p=${g3(h.p)}` : "");
  }
  const envText = (r) => `${r.machine} / ${r.macos} / ${fixed(r.load_avg_start, 1)}→${fixed(r.load_avg_end, 1)}`;
  const speedCells = (s) => [`${fixed(s.p95_us / 1000, 3)} ms`, `${fixed(s.max_us / 1000, 2)} ms`, `${fixed(s.load_ms, 0)} ms`, `${fixed(s.rss_mb, 0)} MB`, String(s.keys), s.rows_match];

  function bench(d) {
    const v2 = d.suite_v2, v1 = d.suite_v1;
    // 套件 v2：出貨設定
    fill("v2-models", [table(`${V2}/出貨設定`, ["版本", "被量的 commit", "模型（SHA-256 前 8 碼）"], v2.map((r) => {
      const names = Object.keys(r.models || {}).sort((a, b) => {
        const ia = MODEL_ORDER.indexOf(a), ib = MODEL_ORDER.indexOf(b);
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
      });
      return [r.version, "`" + r.sha.slice(0, 7) + "`", names.map((n) => `${n} \`${r.models[n].slice(0, 8)}\``).join("、")];
    }))]);
    // 套件 v2：每個結果檔一張準確率表
    const files = [];
    for (const r of v2) {
      if (!files.length || files[files.length - 1].label !== r.label) files.push({ label: r.label, date: r.date, recs: [] });
      files[files.length - 1].recs.push(r);
    }
    fill("v2-accuracy", files.reverse().flatMap((f) => {
      const title = `${f.label}（${f.date}）`;
      const rows = SETS_V2.flatMap((s) => ["chat", "formal"].map((p) => {
        const c = `${s}/${p}`;
        const n = f.recs.map((r) => ((r.accuracy || {})[c] || {}).n).find((x) => x !== undefined);
        return [c, n === undefined ? "—" : String(n), ...f.recs.map((r) => (r.accuracy ? cellText(r, c) : "失敗"))];
      }));
      return [heading("h4", title), table(`${V2}/準確率（top1，寬鬆對照）/${title}`, ["集合／設定", "列數", ...f.recs.map((r) => r.version)], rows)];
    }));
    // 套件 v2：保留集（只放 chat 那幾欄，JSON 只有這一半）
    const hc2 = holdoutCols(v2);
    fill("v2-holdout", [table(`${V2}/保留集`, ["版本", ...hc2.map(holdoutHead)],
      v2.map((r) => [`${r.version}<br>\`${((r.holdout_chat && r.holdout_chat.commit) || r.sha).slice(0, 7)}\``, ...hc2.map((c) => holdoutCell(r, c, false))]))]);
    // 套件 v2：速度與記憶體
    fill("v2-speed", [table(`${V2}/速度與記憶體（同場）`, ["版本", "日期", "機型／macOS／load avg", "p95", "max", "載入", "RSS", "按鍵數", "重播＝CLI", "重排 p50／p95"],
      v2.flatMap((r) => {
        const s = r.speed || { failed: true };
        const rr = "rerank_p50_ms" in s ? `${fixed(s.rerank_p50_ms, 2)}／${fixed(s.rerank_p95_ms, 2)} ms` : "—（沒有語意校正）";
        const chat = [r.version, r.date, envText(r), ...(s.failed ? ["失敗", "", "", "", "", "", ""] : [...speedCells(s), rr])];
        const f = s.formal;
        const formal = [`${r.version} 書面`, r.date, "同上", ...(f ? (f.failed ? ["失敗", "", "", "", "", "", ""] : [...speedCells(f), "—（重播不含語意校正）"]) : ["—", "—", "—", "—", "—", "—", "—"])];
        return [chat, formal];
      }))]);
    // 外部模型對照（公開集合的列）
    fill("external", d.external.flatMap((x) => {
      const title = `${x.name}（${x.date}）`;
      const f = (r, k) => `${pct(r[k], r.n)}<br>${r[k]}/${r.n}`;
      return [heading("h3", title), table(`外部模型對照（不是善解的版本）/${title}`,
        ["設定／集合", "n", "n-gram 第一名", x.ours_label, x.label, `${x.label} 對 ${x.ours_label}：改對／改壞，p`],
        x.rows.map((r) => [`${r.setting}／${r.set}`, String(r.n), f(r, "ngram"), f(r, "ours"), f(r, "external"),
          `+${r.fixed} / −${r.broken}，` + (r.p < 1e-4 ? "p<0.0001" : `p=${g3(r.p)}`)]))];
    }));
    // 實打對照（只放本機的組不在 JSON 裡）
    const st = d.static;
    const stTitle = `實打對照（靜態，${st.date}）`;
    const stHead = root.querySelector('[data-fill-title="static"]');
    if (stHead) stHead.textContent = stTitle;
    fill("static", st.groups.flatMap((g) => {
      const title = g.name + (g.n !== undefined ? `（${g.n} 句）` : "");
      return [heading("h3", title), table(`${stTitle}/${title}`, ["輸入法", "錯的句數", "錯字數"],
        Object.entries(g.ime).map(([ime, r]) => [ime, String(r.sentences), String(r.chars)]))];
    }));
    // 套件 v1（早期版本的歷史表）
    const cells = SETS_V1.flatMap(([s, ps]) => ps.map((p) => `${s}/${p}`));
    const refRow = d.reference ? [[`小麥資料基準<br>\`${d.reference.commit.slice(0, 7)}\`<br>${REF_NOTE}`,
      ...cells.map((c) => cellText({ accuracy: { [c]: d.reference.accuracy[c.split("/")[0]] || { missing: true } } }, c))]] : [];
    fill("v1-accuracy", [table("準確率（top1，寬鬆對照）", ["版本", ...cells], [...refRow,
      ...v1.map((r) => [`${r.ref}<br>\`${r.sha.slice(0, 7)}\``, ...(r.failed ? cells.map(() => "失敗") : cells.map((c) => cellText(r, c)))])])]);
    const hc1 = holdoutCols(v1);
    fill("v1-holdout", [table("保留集", ["版本", ...hc1.map(holdoutHead)],
      v1.map((r) => [`${r.ref}<br>\`${r.sha.slice(0, 7)}\``, ...hc1.map((c) => holdoutCell(r, c, true))]))]);
    fill("v1-speed", [table("速度與記憶體（同場）", ["版本", "日期", "機型／macOS／load avg", "p95", "max", "載入", "RSS", "按鍵數", "重播＝CLI"],
      v1.map((r) => [r.ref, r.date, envText(r), ...(!r.speed || r.speed.failed ? ["失敗", "", "", "", "", ""] : speedCells(r.speed))]))]);
  }

  // ---------- KPI（tools/kpi.py 的 COLUMNS 裡公開的七欄） ----------
  const kpct = (top1, n) => `${fixed(100 * top1 / n, 2)}%（${top1}/${n}）`;
  const replayCol = (title, set, text) => [title, (v) => {
    const b = (v.replay || {})[set];
    const t = b ? text(b) : null;
    return t === null ? null : [b.rows_sha256, b.n, t];
  }];
  const KPI_COLUMNS = [
    replayCol("錯字回報重播 top1（學習關）", "user-reported", (b) => (b.off && b.off.top1 !== undefined && b.n !== undefined ? kpct(b.off.top1, b.n) : null)),
    replayCol("錯字回報重播 學習開改壞", "user-reported", (b) => ("learn_broken" in b ? String(b.learn_broken) : null)),
    replayCol("按鍵分數 KySS（dev302 聊天）", "dev302", (b) => (b.off && b.off.kyss !== undefined ? fixed(b.off.kyss, 4) : null)),
    replayCol("按鍵分數 KySS（錯字回報聊天）", "user-reported", (b) => (b.off && b.off.kyss !== undefined ? fixed(b.off.kyss, 4) : null)),
    replayCol("顯示後被改掉（dev302 聊天，每百列次數）", "dev302", (b) => (b.off && b.off.rewrites !== undefined && b.n
      ? `${fixed(100 * b.off.rewrites / b.n, 1)} 次／百列（${b.off.rewrites}/${b.n}）` : null)),
    ["保留集口語半邊（聊天）", (v) => {
      const h = v.holdout_talk_chat;
      return h && h.top1 !== undefined && h.n !== undefined ? [h.rows_sha256, h.n, kpct(h.top1, h.n)] : null;
    }],
    ["速度與記憶體（閒置窗）", (v) => {
      const s = v.speed;
      if (!s) return null;
      // 兩個規則（tools/kpi.py 的 speed_verdict）：有 rule: "window" 的列看速度量測前後的備份狀態與量完時的負載，其他的看開跑時的負載
      const win = s.rule === "window";
      if (win && (s.speed_tm_before === true || s.speed_tm_after === true)) return [s.machine, null, "—（備份中）"];
      if (!s.idle) return [s.machine, null, win ? `—（速度段後負載 ${fixed(s.speed_load_after, 2)}）` : `—（負載 ${fixed(s.load_avg_start, 2)}）`];
      let t = `按鍵 p95 ${fixed(s.p95_us / 1000, 2)} ms`;
      if ("rerank_p95_ms" in s) t += `／重排 p95 ${fixed(s.rerank_p95_ms, 1)} ms`;
      const note = win && (s.speed_tm_before == null || s.speed_tm_after == null) ? "（備份狀態未知）" : "";
      return [s.machine, null, t + `／RSS ${fixed(s.rss_mb, 0)} MB` + note];
    }],
  ];
  function kpi(d) {
    const vs = d.versions, cols = [];
    for (const [title, f] of KPI_COLUMNS) { // 換了列檔（或機器）就分成另一欄，不同的集合不放同一欄
      const groups = new Map();
      for (const v of vs) {
        const r = f(v);
        if (!r) continue;
        const key = r[0] === undefined ? null : r[0];
        if (!groups.has(key)) groups.set(key, { n: r[1], cells: {} });
        groups.get(key).cells[v.version] = r[2];
      }
      if (!groups.size) groups.set(null, { n: null, cells: {} });
      for (const [key, g] of groups) {
        const head = [...(g.n !== null && g.n !== undefined ? [`n=${g.n}`] : []),
          ...(key ? [/^[0-9a-f]{64}$/.test(key) ? "#" + key.slice(0, 8) : key] : [])];
        cols.push([title + (head.length ? "<br>" + head.join("，") : ""), g.cells]);
      }
    }
    fill("kpi", [table("", ["版本", ...cols.map((c) => c[0])], vs.map((v) => [v.version, ...cols.map((c) => c[1][v.version] || "—")]))]);
  }

  const kinds = { bench, kpi };
  fetch(root.dataset.src)
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((d) => { kinds[root.dataset.kind](d); root.classList.add("ready"); })
    .catch(() => {
      const p = el("p", { class: "note" }, ["數字載入失敗。原始資料在 ", el("a", { href: root.dataset.src }, [root.dataset.src]), " 。"]);
      root.prepend(p);
    });
})();
