// 實機截圖探測：用指定的輸入法把一串按鍵打進「自己的」文字視窗，每一步截圖，看真正的候選窗長什麼樣子。
//
// 用法：swiftc -O tools/imeshot.swift -o <bin> && <bin> <輸出前綴> <輸入法 ID> <步驟>...
//   步驟：鍵碼（例如 49 是空白鍵、125 是 ↓）；後面加 s 表示按 Shift（43s 是 ⇧,）；
//         `-` 只截圖不按鍵；`N*a,b,c` 把鍵碼 a、b、c 依序快速連打 N 次（每鍵約 10 ms），打完截一張。
//   每一步截一張 `<輸出前綴>-<步驟序號>.png`，只截測試視窗和它下方候選窗的區域，不截全螢幕。
//   環境變數 IMESHOT_VIDEO=<檔名.mov>：從視窗拿到焦點開始錄影，長度依步驟估算（每步是按鍵約 0.2 秒加上
//   IMESHOT_STEP_WAIT_MS，截圖時再加約 0.3 秒；快速連打另加每鍵 IMESHOT_PACE_MS；加上 IMESHOT_PRE_WAIT_MS；再多留 3 秒）。錄影時 macOS 會讓周圍變暗，這是刻意保留的，使用者看得到正在錄。
//   環境變數 IMESHOT_SCREEN=builtin：視窗開在筆電內建螢幕的正中央（沒有內建螢幕就中止）。
//   環境變數 IMESHOT_DEMO=1：視窗加高，候選窗開在本程式自己的空白文字區上，截圖與錄影只截這個視窗，
//   不會拍到後面的任何畫面（給要分享的 demo 用）。
//   demo 的其他環境變數（做 demo 的完整流程在維護者本機的 skill `shanjie-demo`）：
//     IMESHOT_DEMO_SIZE=820x400 視窗大小；IMESHOT_FONT=34 字級；IMESHOT_TITLE 視窗標題；
//     IMESHOT_PACE_MS=50 快速連打時每鍵的間隔（預設 10）；IMESHOT_STEP_WAIT_MS=700 每步之後的等待（預設 900）；
//     IMESHOT_PRE_WAIT_MS=1000 開始錄影後、打字前的等待；IMESHOT_NOSHOT=1 不截圖；
//     IMESHOT_RESTORE=<輸入法 ID> 結束時切到這個輸入法（預設切回原本的）。
//   v0.5.0 的 demo 用 IMESHOT_DEMO=1 IMESHOT_SCREEN=builtin IMESHOT_NOSHOT=1 IMESHOT_PACE_MS=50 IMESHOT_STEP_WAIT_MS=700
//   IMESHOT_DEMO_SIZE=820x400 IMESHOT_FONT=34 IMESHOT_PRE_WAIT_MS=1000，錄出 1640×864；v0.4.0、v0.4.1 有紀錄的只有
//   820x400、字級 34、每鍵 50 ms。數值寫錯（解不開、負數、尺寸不是 寬x高）就中止，不會默默用預設值。
//   結束時印出按 Esc 前的組字範圍（markedRange）與最後的文字（含換行，用 debugDescription）。
// 安全：按鍵只用 CGEvent.postToPid 送給本程式自己的 pid（和 tools/imetype.swift 相同），就算焦點跑掉也不會
// 打進別的 App；每一步之前確認視窗在最前面，不在就等，最多 60 秒，叫不回來就中止；步驟參數在開始前全部
// 檢查過；中止或結束時都切回原本的輸入法（設了 IMESHOT_RESTORE 時切到它指定的輸入法）。
// 使用者正在用電腦時，macOS 不讓背景程式搶焦點：要請使用者在「善解截圖探測」視窗跳出來時點一下，之後不要
// 碰鍵盤。佔用鍵盤的時間要盡量短（見 CLAUDE.md「實機測試」）。
// 剛重裝的輸入法第一次啟用時可能吃掉前幾個鍵，先打一個鍵再按 Esc 暖機。
// 需要「輔助使用」與「螢幕錄製」權限（給執行它的終端機 App）。
import AppKit
import Carbon

let args = CommandLine.arguments
/// Set once the probe has switched input sources; `fail` switches back first.
var restore: (() -> Void)?
func fail(_ m: String) -> Never {
    restore?()
    FileHandle.standardError.write((m + "\n").data(using: .utf8)!)
    exit(2)
}
guard args.count >= 4 else { fail("usage: imeshot <out-prefix> <input-source-id> <step>...") }
guard CGPreflightPostEventAccess() else { fail("needs Accessibility permission") }
let prefix = args[1], sourceID = args[2], steps = Array(args[3...])

/// One parsed step: rapid keys (`N*a,b,c`), one key with or without Shift, or capture only (`-`).
enum Step { case rapid(Int, [CGKeyCode]), key(CGKeyCode, Bool), capture }
// Every step is checked before anything is typed, so a typo cannot stop the run halfway.
let plan: [Step] = steps.map { step in
    if let star = step.firstIndex(of: "*") {
        guard let n = Int(step[..<star]), n > 0 else { fail("bad step \(step)") }
        let parts = step[step.index(after: star)...].split(separator: ",")
        let codes = parts.compactMap { CGKeyCode(String($0)) }
        guard !codes.isEmpty, codes.count == parts.count else { fail("bad step \(step)") }
        return .rapid(n, codes)
    }
    if step == "-" { return .capture }
    let shift = step.hasSuffix("s")
    guard let code = CGKeyCode(shift ? String(step.dropLast()) : step) else { fail("bad step \(step)") }
    return .key(code, shift)
}

func source(_ id: String) -> TISInputSource? {
    (TISCreateInputSourceList([kTISPropertyInputSourceID as String: id] as CFDictionary, false)?
        .takeRetainedValue() as? [TISInputSource])?.first
}
guard let src = source(sourceID) else { fail("no input source \(sourceID)") }
let app = NSApplication.shared
app.setActivationPolicy(.regular)
/// IMESHOT_DEMO=1: a tall window, so the candidate panel opens over the probe's own empty text view and
/// screenshots and video show only this window, never what is behind it (for demo clips to share).
let env = ProcessInfo.processInfo.environment
let demo = env["IMESHOT_DEMO"] == "1"
/// IMESHOT_RESTORE: checked here, before any window, like the other parameters.
let restoreID: TISInputSource? = env["IMESHOT_RESTORE"].map { id in
    guard let s = source(id) else { fail("no input source \(id) for IMESHOT_RESTORE") }
    return s }
// Demo parameters (the header lists them); unset, every one keeps the probe's old behaviour.
/// A set variable that does not parse stops the run before anything is typed (a mistyped value would otherwise record a
/// whole clip with the defaults, as the first v0.4.1 take did).
func ms(_ k: String, _ d: Int) -> useconds_t {
    guard let v = env[k] else { return useconds_t(d * 1000) }
    guard let n = Int(v), n >= 0, n <= 600_000, let us = useconds_t(exactly: n * 1000) else { fail("bad \(k)=\(v): milliseconds 0-600000") }
    return us
}
let paceUS = ms("IMESHOT_PACE_MS", 10), stepWaitUS = ms("IMESHOT_STEP_WAIT_MS", 900), preWaitUS = ms("IMESHOT_PRE_WAIT_MS", 0)
let noShot = env["IMESHOT_NOSHOT"] == "1"
let demoSize: NSSize? = env["IMESHOT_DEMO_SIZE"].map { v in
    let p = v.split(separator: "x").compactMap { Double($0) }
    guard demo, p.count == 2, p[0] > 0, p[1] > 0 else { fail("bad IMESHOT_DEMO_SIZE=\(v): WIDTHxHEIGHT with a lowercase x, and only with IMESHOT_DEMO=1") }
    return NSSize(width: p[0], height: p[1]) }
let fontSize: CGFloat = env["IMESHOT_FONT"].map { v in
    guard let f = Double(v), f > 0 else { fail("bad IMESHOT_FONT=\(v)") }
    return CGFloat(f) } ?? 22
/// IMESHOT_SCREEN=builtin: open the window on the Mac's built-in display (when there is one), centred,
/// e.g. to record a Retina demo away from the screen the user is working on.
func builtinScreen() -> NSScreen? {
    NSScreen.screens.first { s in
        (s.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)
            .map { CGDisplayIsBuiltin(CGDirectDisplayID($0.uint32Value)) != 0 } ?? false
    }
}
let size = demoSize ?? (demo ? NSSize(width: 900, height: 520) : NSSize(width: 520, height: 90))
var origin = demo ? NSPoint(x: 300, y: 300) : NSPoint(x: 300, y: 500)
if env["IMESHOT_SCREEN"] == "builtin" {
    guard let b = builtinScreen() else { fail("no built-in display") }
    origin = NSPoint(x: b.visibleFrame.midX - size.width / 2, y: b.visibleFrame.midY - size.height / 2)
}
let window = NSWindow(contentRect: NSRect(origin: origin, size: size), styleMask: [.titled], backing: .buffered, defer: false)
// The initializer keeps a new window on the main screen (2026-10-05: a demo meant for the built-in
// display opened on the external one); setting the frame afterwards places it in global coordinates.
window.setFrame(window.frameRect(forContentRect: NSRect(origin: origin, size: size)), display: false)
window.title = env["IMESHOT_TITLE"] ?? "善解截圖探測（請不要碰鍵盤）"
let textView = NSTextView(frame: window.contentView!.bounds)
textView.font = NSFont.systemFont(ofSize: fontSize)
textView.isAutomaticQuoteSubstitutionEnabled = false
// Demo: the candidate panel aligns its first glyph under the text and sits about 15-18 pt left of it,
// so the text needs room on the left or the panel is cropped out of the captured window.
if demo { textView.textContainerInset = NSSize(width: 32, height: 16) }
window.contentView!.addSubview(textView)
window.makeKeyAndOrderFront(nil)
window.makeFirstResponder(textView)
let pid = ProcessInfo.processInfo.processIdentifier
let original = TISCopyCurrentKeyboardInputSource().takeRetainedValue()
let restoreTo = restoreID ?? original
restore = { DispatchQueue.main.sync { _ = TISSelectInputSource(restoreTo) } }

func onMain<T>(_ f: () -> T) -> T { DispatchQueue.main.sync(execute: f) }
func press(_ code: CGKeyCode, shift: Bool, gap: useconds_t = 80_000) {
    for down in [true, false] {
        let e = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down)!
        if shift { e.flags = .maskShift }
        e.postToPid(pid)
        usleep(gap)
    }
}
func ensureFront() {
    for _ in 0..<120 {
        if onMain({ NSWorkspace.shared.frontmostApplication?.processIdentifier == pid }) { return }
        onMain { app.activate(ignoringOtherApps: true); window.makeKeyAndOrderFront(nil); window.makeFirstResponder(textView) }
        usleep(500_000)
    }
    fail("window is not frontmost; stopped")
}
func capture(_ n: Int) {
    let file = "\(prefix)-\(n).png"
    let f = onMain { window.frame }
    let screen = onMain { NSScreen.screens[0].frame }
    // Only the probe window and the area below it, where the candidate panel opens (top-left origin).
    let region = demo ? "\(Int(f.minX)),\(Int(screen.height - f.maxY)),\(Int(f.width)),\(Int(f.height))"
                      : "\(Int(f.minX) - 20),\(Int(screen.height - f.maxY) - 20),900,\(Int(f.height) + 420)"
    let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    p.arguments = ["-x", "-R", region, file]
    try? p.run(); p.waitUntilExit()
    print("shot \(file) window \(Int(f.minX)),\(Int(f.minY)),\(Int(f.width)),\(Int(f.height)) screen \(Int(screen.width))x\(Int(screen.height))")
}

DispatchQueue.global().async {
    usleep(900_000)
    onMain { _ = TISSelectInputSource(src) }
    usleep(800_000)
    // The switch can silently not take (2026-10-05: a run typed into Apple Zhuyin instead); check it.
    let now = onMain { () -> String in
        let cur = TISCopyCurrentKeyboardInputSource().takeRetainedValue()
        return Unmanaged<CFString>.fromOpaque(TISGetInputSourceProperty(cur, kTISPropertyInputSourceID)).takeUnretainedValue() as String
    }
    guard now == sourceID else { fail("input source did not switch: wanted \(sourceID), got \(now)") }
    ensureFront()
    // IMESHOT_VIDEO=<file.mov>: record the probe region with screencapture (its dimmed overlay tells the
    // user a recording is running) from the moment the window has focus, just long enough for the steps.
    var recorder: Process?
    if let video = env["IMESHOT_VIDEO"] {
        let f = onMain { window.frame }, h = onMain { NSScreen.screens[0].frame.height }
        // Estimated, not measured exactly: a step is about 0.2 s of keys plus the step wait, plus about 0.3 s when it
        // takes a screenshot (with the defaults 1.4 s, the old value); rapid steps add the pace per key; then the
        // wait before typing; 3 s spare. With no demo variable set this equals the old formula for every length.
        // Whole microseconds, not Double: 0.2 + 0.9 + 0.3 is not exactly 1.4 and rounded some lengths up a second.
        let typingUS = plan.reduce(0) { sum, s in if case .rapid(let n, let c) = s { return sum + n * c.count * Int(paceUS) }; return sum }
        let perStepUS = 200_000 + Int(stepWaitUS) + (noShot ? 0 : 300_000)
        let totalUS = plan.count * perStepUS + typingUS + Int(preWaitUS)
        let seconds = (totalUS + 999_999) / 1_000_000 + 3
        let r = Process(); r.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        let area = demo ? "\(Int(f.minX)),\(Int(h - f.maxY)),\(Int(f.width)),\(Int(f.height))"
                        : "\(Int(f.minX) - 20),\(Int(h - f.maxY) - 20),620,330"
        r.arguments = ["-x", "-V", String(seconds), "-R", area, video]
        try? r.run()
        recorder = r
        usleep(1_500_000)  // screencapture's recording starts about a second after launch
    }
    usleep(preWaitUS)
    for (i, step) in plan.enumerated() {
        ensureFront()
        switch step {
        case .rapid(let n, let codes):
            // About 10 ms per key: the user cannot type while this runs (2026-10-05, "太慢了").
            for _ in 0..<n { for code in codes { press(code, shift: false, gap: paceUS / 2) } }
        case .key(let code, let shift):
            press(code, shift: shift)
        case .capture:
            break
        }
        usleep(stepWaitUS)
        if !noShot { capture(i) }
    }
    let state = onMain { () -> String in
        let r = textView.markedRange()
        let total = (textView.string as NSString).length
        return "before Esc: total \(total), marked \(r.location == NSNotFound ? "none" : "\(r.location)+\(r.length)")"
    }
    print(state)
    press(53, shift: false)   // Esc
    usleep(200_000)
    press(53, shift: false)
    restore?()
    recorder?.waitUntilExit()
    print("text: \(onMain { textView.string }.debugDescription)")
    exit(0)
}
app.activate(ignoringOtherApps: true)
app.run()
