// 實機截圖探測：用指定的輸入法把一串按鍵打進「自己的」文字視窗，每一步截圖，看真正的候選窗長什麼樣子。
//
// 用法：swiftc -O tools/imeshot.swift -o <bin> && <bin> <輸出前綴> <輸入法 ID> <步驟>...
//   步驟：鍵碼（例如 49 是空白鍵、125 是 ↓）；後面加 s 表示按 Shift（43s 是 ⇧,）；
//         `-` 只截圖不按鍵；`N*a,b,c` 把鍵碼 a、b、c 依序快速連打 N 次（每鍵約 10 ms），打完截一張。
//   每一步截一張 `<輸出前綴>-<步驟序號>.png`，只截測試視窗和它下方候選窗的區域，不截全螢幕。
//   環境變數 IMESHOT_VIDEO=<檔名.mov>：從視窗拿到焦點開始錄影，長度剛好涵蓋所有步驟。錄影時 macOS 會讓
//   周圍變暗，這是刻意保留的，使用者看得到正在錄。
//   結束時印出按 Esc 前的組字範圍（markedRange）與最後的文字（含換行，用 debugDescription）。
// 安全：按鍵只用 CGEvent.postToPid 送給本程式自己的 pid（和 tools/imetype.swift 相同），就算焦點跑掉也不會
// 打進別的 App；視窗不在最前面就等，最多 60 秒，叫不回來就中止；結束時切回原本的輸入法。
// 使用者正在用電腦時，macOS 不讓背景程式搶焦點：要請使用者在「善解截圖探測」視窗跳出來時點一下，之後不要
// 碰鍵盤。佔用鍵盤的時間要盡量短（見 CLAUDE.md「實機測試」）。
// 剛重裝的輸入法第一次啟用時可能吃掉前幾個鍵，先打一個鍵再按 Esc 暖機。
// 需要「輔助使用」與「螢幕錄製」權限（給執行它的終端機 App）。
import AppKit
import Carbon

let args = CommandLine.arguments
func fail(_ m: String) -> Never { FileHandle.standardError.write((m + "\n").data(using: .utf8)!); exit(2) }
guard args.count >= 4 else { fail("usage: imeshot <out-prefix> <input-source-id> <step>...") }
guard CGPreflightPostEventAccess() else { fail("needs Accessibility permission") }
let prefix = args[1], sourceID = args[2], steps = Array(args[3...])

func source(_ id: String) -> TISInputSource? {
    (TISCreateInputSourceList([kTISPropertyInputSourceID as String: id] as CFDictionary, false)?
        .takeRetainedValue() as? [TISInputSource])?.first
}
guard let src = source(sourceID) else { fail("no input source \(sourceID)") }
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let window = NSWindow(contentRect: NSRect(x: 300, y: 500, width: 520, height: 90), styleMask: [.titled], backing: .buffered, defer: false)
window.title = "善解截圖探測（請不要碰鍵盤）"
let textView = NSTextView(frame: window.contentView!.bounds)
textView.font = NSFont.systemFont(ofSize: 22)
textView.isAutomaticQuoteSubstitutionEnabled = false
window.contentView!.addSubview(textView)
window.makeKeyAndOrderFront(nil)
window.makeFirstResponder(textView)
let pid = ProcessInfo.processInfo.processIdentifier
let original = TISCopyCurrentKeyboardInputSource().takeRetainedValue()

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
    onMain { _ = TISSelectInputSource(original) }
    fail("window is not frontmost; stopped")
}
func capture(_ n: Int) {
    let file = "\(prefix)-\(n).png"
    let f = onMain { window.frame }
    let screen = onMain { NSScreen.screens[0].frame }
    // Only the probe window and the area below it, where the candidate panel opens (top-left origin).
    let region = "\(Int(f.minX) - 20),\(Int(screen.height - f.maxY) - 20),900,\(Int(f.height) + 420)"
    let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    p.arguments = ["-x", "-R", region, file]
    try? p.run(); p.waitUntilExit()
    print("shot \(file) window \(Int(f.minX)),\(Int(f.minY)),\(Int(f.width)),\(Int(f.height)) screen \(Int(screen.width))x\(Int(screen.height))")
}

DispatchQueue.global().async {
    usleep(900_000)
    onMain { _ = TISSelectInputSource(src) }
    usleep(800_000)
    ensureFront()
    // IMESHOT_VIDEO=<file.mov>: record the probe region with screencapture (its dimmed overlay tells the
    // user a recording is running) from the moment the window has focus, just long enough for the steps.
    var recorder: Process?
    if let video = ProcessInfo.processInfo.environment["IMESHOT_VIDEO"] {
        let f = onMain { window.frame }, h = onMain { NSScreen.screens[0].frame.height }
        let seconds = Int((Double(steps.count) * 1.15).rounded(.up)) + 2
        let r = Process(); r.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        r.arguments = ["-x", "-V", String(seconds), "-R",
                       "\(Int(f.minX) - 20),\(Int(h - f.maxY) - 20),620,330", video]
        try? r.run()
        recorder = r
        usleep(1_500_000)  // screencapture's recording starts about a second after launch
    }
    for (i, step) in steps.enumerated() {
        // "N*a,b,c": type key codes a, b, c in order N times, quickly, then one capture.
        if let star = step.firstIndex(of: "*"), let n = Int(step[..<star]) {
            let codes = step[step.index(after: star)...].split(separator: ",").compactMap { CGKeyCode(String($0)) }
            // About 10 ms per key: the user cannot type while this runs (2026-10-05, "太慢了").
            for _ in 0..<n { for code in codes { press(code, shift: false, gap: 5_000) } }
        } else if step != "-" {
            let shift = step.hasSuffix("s")
            guard let code = CGKeyCode(shift ? String(step.dropLast()) : step) else { fail("bad step \(step)") }
            press(code, shift: shift)
        }
        usleep(900_000)
        capture(i)
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
    onMain { _ = TISSelectInputSource(original) }
    recorder?.waitUntilExit()
    print("text: \(onMain { textView.string }.debugDescription)")
    exit(0)
}
app.activate(ignoringOtherApps: true)
app.run()
