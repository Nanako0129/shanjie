// 打字測驗自動化：用標準注音鍵盤的按鍵，把評測句依序打進「自己的」文字視窗，讀回各輸入法選出的字。
//
// 用法：swiftc -O tools/imetype.swift -o <bin> && <bin> <rows.txt> <out.tsv> <輸入法 ID>...
//   rows.txt：`前文|句子|讀音`（讀音以空白分隔音節，只含注音，不含英文）
//   out.tsv ：`輸入法 ID\t句子\t輸出`
// 按鍵只用 CGEvent.postToPid 送給本程式自己的 pid，就算焦點跑掉也不會打進別的 App；
// 每句之前確認本程式在最前面，叫不回來就中止。結束時切回原本的輸入法。
// 需要「輔助使用」權限（給執行它的終端機 App）。不選字：每句打完按 Return 送出組字區。
import AppKit
import Carbon

let keyCode: [Character: CGKeyCode] = [
    "ㄅ": 18, "ㄉ": 19, "ˇ": 20, "ˋ": 21, "ㄓ": 23, "ˊ": 22, "˙": 26, "ㄚ": 28, "ㄞ": 25, "ㄢ": 29, "ㄦ": 27,
    "ㄆ": 12, "ㄊ": 13, "ㄍ": 14, "ㄐ": 15, "ㄔ": 17, "ㄗ": 16, "ㄧ": 32, "ㄛ": 34, "ㄟ": 31, "ㄣ": 35,
    "ㄇ": 0, "ㄋ": 1, "ㄎ": 2, "ㄑ": 3, "ㄕ": 5, "ㄘ": 4, "ㄨ": 38, "ㄜ": 40, "ㄠ": 37, "ㄤ": 41,
    "ㄈ": 6, "ㄌ": 7, "ㄏ": 8, "ㄒ": 9, "ㄖ": 11, "ㄙ": 45, "ㄩ": 46, "ㄝ": 43, "ㄡ": 47, "ㄥ": 44,
]
let space: CGKeyCode = 49, ret: CGKeyCode = 36
// 每個按鍵之間；太快輸入法會掉鍵。環境變數 IMETYPE_KEY_MS 可調（預設 25 毫秒）
let keyDelay = useconds_t((Int(ProcessInfo.processInfo.environment["IMETYPE_KEY_MS"] ?? "") ?? 25) * 1000)
let commitDelay: useconds_t = 400_000   // 送出後等輸入法把字交給文字視窗

func fail(_ msg: String) -> Never { FileHandle.standardError.write((msg + "\n").data(using: .utf8)!); exit(2) }

let args = CommandLine.arguments
guard args.count >= 4 else { fail("usage: imetype <rows.txt> <out.tsv> <input-source-id>...") }
guard CGPreflightPostEventAccess() else {
    _ = CGRequestPostEventAccess()
    fail("需要「輔助使用」權限：系統設定 → 隱私權與安全性 → 輔助使用，打開執行這支程式的終端機 App，再重跑。")
}
let rows: [(String, [String])] = try! String(contentsOfFile: args[1], encoding: .utf8)
    .split(separator: "\n").map { line in
        let p = line.split(separator: "|", omittingEmptySubsequences: false)
        return (String(p[1]), p[2].split(separator: " ").map(String.init))
    }
for (_, syls) in rows { for s in syls { for c in s where keyCode[c] == nil { fail("讀音含無法對應按鍵的字元（長度 \(s.count)）") } } }

func source(_ id: String) -> TISInputSource? {
    let list = TISCreateInputSourceList([kTISPropertyInputSourceID as String: id] as CFDictionary, false)?.takeRetainedValue() as? [TISInputSource]
    return list?.first
}
let sources = args[3...].map { id -> (String, TISInputSource) in
    guard let s = source(id) else { fail("找不到輸入法 \(id)") }
    return (id, s)
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let window = NSWindow(contentRect: NSRect(x: 200, y: 200, width: 720, height: 160), styleMask: [.titled], backing: .buffered, defer: false)
window.title = "善解打字測驗（自動打字中，請不要碰鍵盤）"
let textView = NSTextView(frame: window.contentView!.bounds)
textView.font = NSFont.systemFont(ofSize: 22)
window.contentView!.addSubview(textView)
window.makeKeyAndOrderFront(nil)
window.makeFirstResponder(textView)
let pid = ProcessInfo.processInfo.processIdentifier
let original = TISCopyCurrentKeyboardInputSource().takeRetainedValue()

func press(_ code: CGKeyCode) {
    for down in [true, false] {
        CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down)!.postToPid(pid)
        usleep(keyDelay)
    }
}
func onMain<T>(_ f: () -> T) -> T { DispatchQueue.main.sync(execute: f) }
func ensureFront() {
    for _ in 0..<5 {
        if onMain({ NSWorkspace.shared.frontmostApplication?.processIdentifier == pid }) { return }
        onMain { app.activate(ignoringOtherApps: true); window.makeKeyAndOrderFront(nil); window.makeFirstResponder(textView) }
        usleep(500_000)
    }
    onMain { _ = TISSelectInputSource(original) }
    fail("測驗視窗不在最前面，已中止（沒有按鍵送到其他 App）。")
}

DispatchQueue.global().async {
    usleep(800_000)
    var out = ""
    for (id, src) in sources {
        onMain { _ = TISSelectInputSource(src) }
        usleep(600_000)
        // 確認真的切過去了；切不過去就中止，避免整輪默默用前一套輸入法打
        let now = onMain { () -> String in
            let cur = TISCopyCurrentKeyboardInputSource().takeRetainedValue()
            return Unmanaged<CFString>.fromOpaque(TISGetInputSourceProperty(cur, kTISPropertyInputSourceID)).takeUnretainedValue() as String
        }
        guard now == id else { onMain { _ = TISSelectInputSource(original) }; fail("切換輸入法失敗：要的是 \(id)，目前是 \(now)") }
        for (sent, syls) in rows {
            ensureFront()
            onMain { textView.string = "" }
            for s in syls {
                for c in s where !"ˊˇˋ˙".contains(c) { press(keyCode[c]!) }
                if let tone = s.last, "ˊˇˋ˙".contains(tone) { press(keyCode[tone]!) } else { press(space) }
            }
            press(ret)
            usleep(commitDelay)
            let got = onMain { textView.string.trimmingCharacters(in: .whitespacesAndNewlines) }
            out += "\(id)\t\(sent)\t\(got)\n"
            FileHandle.standardError.write("\(id.split(separator: ".").last!) \(got == sent ? "✓" : "✗") \(got)\n".data(using: .utf8)!)
        }
    }
    onMain { _ = TISSelectInputSource(original) }
    try! out.write(toFile: args[2], atomically: true, encoding: .utf8)
    exit(0)
}
app.activate(ignoringOtherApps: true)
app.run()
