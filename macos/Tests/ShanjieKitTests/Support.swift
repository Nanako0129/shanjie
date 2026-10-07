import AppKit
import CShanjie
import XCTest
@testable import ShanjieKit

/// The repository's data, laid out like the app's Resources (docs/contracts/s3b.md section 10.4):
/// a temporary directory of symlinks to data/lexicon/* and data/lm/bigram.sjlm. Missing files fail
/// the test with instructions; nothing is ever skipped.
enum TestData {
    static let repo = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent()

    static let files: [(name: String, source: String)] = [
        ("mcbpmf-data.txt", "data/lexicon/mcbpmf-data.txt"),
        ("overlay-add.tsv", "data/lexicon/overlay-add.tsv"),
        ("sandhi-add.tsv", "data/lexicon/sandhi-add.tsv"),
        ("demote.tsv", "data/lexicon/demote.tsv"),
        ("bigram.sjlm", "data/lm/bigram.sjlm"),
    ]

    /// Builds a Resources-like directory with the given files; nil (after failing the test) when a
    /// source file is missing.
    static func resources(only names: Set<String>? = nil, file: StaticString = #filePath, line: UInt = #line) -> URL? {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("shanjie-tests-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        for f in files where names?.contains(f.name) ?? true {
            let src = repo.appendingPathComponent(f.source)
            guard FileManager.default.fileExists(atPath: src.path) else {
                XCTFail("""
                    \(f.source) is missing. The lexicon is in git; the model is the model-v3 release asset: \
                    gh release download model-v3 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
                    """, file: file, line: line)
                return nil
            }
            try? FileManager.default.createSymbolicLink(at: dir.appendingPathComponent(f.name), withDestinationURL: src)
        }
        return dir
    }
}

/// An IMKTextInput stand-in that behaves like a text view: insertText replaces the marked text.
@MainActor
final class FakeClient: TextClient {
    enum Call: Equatable {
        case insert(String)
        case mark(String, cursor: Int, underlined: Bool)
    }

    let bundleIdentifier: String?
    private(set) var calls: [Call] = []
    private(set) var text = ""   // committed text
    private(set) var marked = ""
    /// S4: the document's text before anything this client received; the insertion point is
    /// after `before + text`.
    var before = ""
    /// Overrides for the left-context tests: no insertion point, or marked text left over.
    var selectedOverride: NSRange?
    var markedOverride: NSRange?
    /// s3b2: the line rectangle this client reports.
    var line: NSRect? = NSRect(x: 100, y: 200, width: 8, height: 18)
    private(set) var lineCursor: Int?
    private(set) var lineAsks = 0
    func lineRect(cursor: Int) -> NSRect? { lineCursor = cursor; lineAsks += 1; return line }

    /// Every call of the three reading methods, and each range requested.
    private(set) var reads = 0
    private(set) var requested: [NSRange] = []

    init(bundle: String? = "com.apple.TextEdit") { bundleIdentifier = bundle }

    private var document: NSString { (before + text + marked) as NSString }

    func selectedRange() -> NSRange {
        reads += 1
        return selectedOverride ?? NSRange(location: document.length, length: 0)
    }

    /// NSTextInputClient: {NSNotFound, 0} without marked text.
    func markedRange() -> NSRange {
        reads += 1
        if let markedOverride { return markedOverride }
        let m = (marked as NSString).length
        return m == 0 ? NSRange(location: NSNotFound, length: 0) : NSRange(location: document.length - m, length: m)
    }

    func attributedSubstring(from range: NSRange) -> NSAttributedString? {
        reads += 1
        requested.append(range)
        guard range.location != NSNotFound, NSMaxRange(range) <= document.length else { return nil }
        return NSAttributedString(string: document.substring(with: range))
    }

    func insertText(_ text: String, replacementRange: NSRange) {
        XCTAssertEqual(replacementRange.location, NSNotFound)
        calls.append(.insert(text))
        self.text += text
        marked = ""
    }

    func setMarkedText(_ text: NSAttributedString, selectionRange: NSRange) {
        let underline = text.length == 0
            ? false
            : (text.attribute(.underlineStyle, at: 0, effectiveRange: nil) as? Int) == NSUnderlineStyle.single.rawValue
        calls.append(.mark(text.string, cursor: selectionRange.location, underlined: underline))
        marked = text.string
    }
}

/// Answers the clear's windows for the user (S4 section 4) and counts them.
@MainActor
final class FakeDialogs: LearningDialogs {
    var answer = true
    private(set) var asked = 0
    private(set) var failures = 0
    func confirmClear(_ reply: @escaping @MainActor (Bool) -> Void) { asked += 1; reply(answer) }
    func clearFailed() { failures += 1 }
}

@MainActor
final class FakePanel: CandidatePanel {
    private(set) var visible = false
    private(set) var items: [String] = []
    private(set) var notes: [String?] = []
    private(set) var selected = -1
    private(set) var lineRect: NSRect?
    private(set) var columns = 0
    private(set) var first = 0
    private(set) var total = 0
    var onSelect: ((Int) -> Void)?

    func show(_ candidates: [String], notes: [String?], selected: Int, columns: Int, first: Int, total: Int,
              lineRect: NSRect?) {
        self.lineRect = lineRect
        self.columns = columns
        self.first = first
        self.total = total
        visible = true
        items = candidates
        self.notes = notes
        self.selected = selected
    }

    /// A mouse click on the cell at `i`, as the app's panel reports it.
    func click(_ i: Int) { onSelect?(i) }

    func hide() {
        visible = false
        items = []
        selected = -1
    }
}

/// One fake IMK controller: a session with its own client and panel. Dropping it models IMK
/// releasing the controller (the session's deinit runs on the main actor, synchronously).
@MainActor
final class Controller {
    let client: FakeClient
    let session: Session
    /// The shell's one panel, shared by every controller.
    var panel: FakePanel { session.shell.panel as! FakePanel }

    init(_ shell: Shell, bundle: String? = "com.apple.TextEdit") {
        client = FakeClient(bundle: bundle)
        session = Session(shell: shell, client: client)
    }

    /// Presses each ASCII key of `keys` (a space is the space bar); returns the handled flags.
    @discardableResult
    func type(_ keys: String, flags: NSEvent.ModifierFlags = []) -> [Bool] {
        keys.map { session.handle(Keys.event(Keys.code(for: $0), flags: flags)) }
    }

    @discardableResult
    func press(_ code: UInt16, flags: NSEvent.ModifierFlags = []) -> Bool {
        session.handle(Keys.event(code, flags: flags))
    }
}

enum Keys {
    static let enter: UInt16 = 36, space: UInt16 = 49, esc: UInt16 = 53, keypad0: UInt16 = 82

    static func code(for c: Character) -> UInt16 {
        if c == " " { return space }
        guard let code = KeyMap.ansi.first(where: { $0.value == c })?.key else {
            fatalError("no ANSI key for a test character")
        }
        return code
    }

    static func event(_ code: UInt16, flags: NSEvent.ModifierFlags = [], type: NSEvent.EventType = .keyDown) -> NSEvent {
        if type == .flagsChanged {
            return NSEvent.keyEvent(with: type, location: .zero, modifierFlags: flags, timestamp: 0, windowNumber: 0,
                                    context: nil, characters: "", charactersIgnoringModifiers: "", isARepeat: false,
                                    keyCode: code) ?? event(code, flags: flags)
        }
        return NSEvent.keyEvent(with: type, location: .zero, modifierFlags: flags, timestamp: 0, windowNumber: 0,
                                context: nil, characters: "", charactersIgnoringModifiers: "", isARepeat: false,
                                keyCode: code)!
    }
}

/// s3a section 1, transcribed independently of the core: symbol to (standard, ETen) key.
enum Layouts {
    static let symbols: [(String, Character, Character)] = [
        ("ㄅ", "1", "b"), ("ㄆ", "q", "p"), ("ㄇ", "a", "m"), ("ㄈ", "z", "f"), ("ㄉ", "2", "d"),
        ("ㄊ", "w", "t"), ("ㄋ", "s", "n"), ("ㄌ", "x", "l"), ("ㄍ", "e", "v"), ("ㄎ", "d", "k"),
        ("ㄏ", "c", "h"), ("ㄐ", "r", "g"), ("ㄑ", "f", "7"), ("ㄒ", "v", "c"), ("ㄓ", "5", ","),
        ("ㄔ", "t", "."), ("ㄕ", "g", "/"), ("ㄖ", "b", "j"), ("ㄗ", "y", ";"), ("ㄘ", "h", "'"),
        ("ㄙ", "n", "s"), ("ㄧ", "u", "e"), ("ㄨ", "j", "x"), ("ㄩ", "m", "u"), ("ㄚ", "8", "a"),
        ("ㄛ", "i", "o"), ("ㄜ", "k", "r"), ("ㄝ", ",", "w"), ("ㄞ", "9", "i"), ("ㄟ", "o", "q"),
        ("ㄠ", "l", "z"), ("ㄡ", ".", "y"), ("ㄢ", "0", "8"), ("ㄣ", "p", "9"), ("ㄤ", ";", "0"),
        ("ㄥ", "/", "-"), ("ㄦ", "-", "="),
    ]
    /// Tones 2-5; the first tone is the space bar on both layouts.
    static let tones: [(String, Character, Character)] = [
        ("ˊ", "6", "2"), ("ˇ", "3", "3"), ("ˋ", "4", "4"), ("˙", "7", "1"),
    ]

    /// Keys for space-separated syllables; a syllable without a tone mark ends with a space.
    static func keys(_ zhuyin: String, eten: Bool) -> String {
        let table = Dictionary(uniqueKeysWithValues: (symbols + tones).map { ($0.0, eten ? $0.2 : $0.1) })
        return zhuyin.split(separator: " ").map { syl -> String in
            let ks = String(syl.map { table[String($0)]! })
            return tones.contains { syl.hasSuffix($0.0) } ? ks : ks + " "
        }.joined()
    }
}

/// dev302 row 10 (docs/contracts/s3b.md section 9).
enum Row10 {
    static let zhuyin = "ㄑㄧˊ ㄓㄨㄥ ㄅㄠˋ ㄍㄠˋ ㄇㄧㄥˊ ㄊㄧㄢ ㄧㄠˋ ㄐㄧㄠ"
    static let standardKeys = Layouts.keys(zhuyin, eten: false)
    static let chat = "其中報告明天要交"
    static let formal = "期中報告明天要交"
}

/// A temporary learning directory (S4 section 4); never the real Application Support.
enum TestLearning {
    static func directory(create: Bool = true) -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("shanjie-learning-\(UUID().uuidString)", isDirectory: true)
        if create { try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true) }
        return dir
    }

    /// The record lines of learning.tsv (header excluded); empty when there is no file.
    static func records(in dir: URL) -> [String] {
        guard let text = try? String(contentsOf: dir.appendingPathComponent("learning.tsv"), encoding: .utf8) else { return [] }
        return text.split(separator: "\n").dropFirst().map(String.init)
    }
}
