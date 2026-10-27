import AppKit
import CShanjie
import XCTest
@testable import ShanjieKit

/// The repository's data, laid out like the app's Resources (docs/contracts/s3b.md section 10.4):
/// a temporary directory of symlinks to data/lexicon/*, data/lm/bigram.sjlm, classes.sjc, kn.sjkn, trigram.sjt3, tinylm.sjtl and colloquial.sjlm. Missing files fail
/// the test with instructions; nothing is skipped for a missing file (the one opt-in benchmark, CandidateCellTests.testBenchmark, is skipped without SHANJIE_BENCH=1).
enum TestData {
    static let repo = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent()

    static let files: [(name: String, source: String)] = [
        ("mcbpmf-data.txt", "data/lexicon/mcbpmf-data.txt"),
        ("overlay-add.tsv", "data/lexicon/overlay-add.tsv"),
        ("sandhi-add.tsv", "data/lexicon/sandhi-add.tsv"),
        ("names-add.tsv", "data/lexicon/names-add.tsv"),  // model-v6 section 2.5: the always-on names layer, required by the core
        ("demote.tsv", "data/lexicon/demote.tsv"),
        ("digits.tsv", "data/lexicon/digits.tsv"),  // digits.md A.3.2: optional for the core, but the app bundles it
        ("bigram.sjlm", "data/lm/bigram.sjlm"),
        ("classes.sjc", "data/lm/classes.sjc"),
        ("kn.sjkn", "data/lm/kn.sjkn"),  // model-v6: the Kneser-Ney side file the shipped tinylm.sjtl (SJTL0004) records
        ("trigram.sjt3", "data/lm/trigram.sjt3"),  // trigram-core: the formal profile's table, loaded from beside the model
        ("tinylm.sjtl", "data/lm/tinylm.sjtl"),
        ("colloquial.sjlm", "data/lm/colloquial.sjlm"),  // colloquial-term.md: the colloquial table the selftest requires
    ]

    static let itPackRow = "ㄊㄨˊ-ㄒㄧㄤˋ-ㄕㄥ-ㄔㄥˊ\t圖像生成\t-6.60980192\tit\n"

    /// Adds Resources/packs/acg-add.tsv (a symlink to the repo's pack) like the app bundle has it. Opt-in: tests
    /// that write their own pack file into `packs` must not get a link through which they would overwrite the repo's.
    static func addBundledPack(to dir: URL, itPack: Bool = true) throws {
        let packs = dir.appendingPathComponent("packs", isDirectory: true)
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        // The IT+AI pack's one row (the selftest word, core/tests/engine_pack.rs): a real file, so the test does not depend on the generated data/packs/it-add.tsv.
        if itPack { try itPackRow.write(to: packs.appendingPathComponent("it-add.tsv"), atomically: true, encoding: .utf8) }
        try FileManager.default.createSymbolicLink(at: packs.appendingPathComponent("acg-add.tsv"),
                                                   withDestinationURL: repo.appendingPathComponent("data/packs/acg-add.tsv"))
    }

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
                    \(f.source) is missing. The lexicon is in git; the model is the model-v6 release asset: \
                    gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
                    classes.sjc: gh release download classes-v5 -R Nanako0129/shanjie -p classes.sjc -D data/lm
                    kn.sjkn: gh release download kn-v1 -R Nanako0129/shanjie -p kn.sjkn -D data/lm
                    trigram.sjt3: gh release download sj-cx-1-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm
                    tinylm.sjtl: gh release download sj-sr-6-14m -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm
                    colloquial.sjlm: gh release download sj-cl-1-68k -R Nanako0129/shanjie -p colloquial.sjlm -D data/lm
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

    /// One stretch of the last marked text with the same underline style (`NSUnderlineStyle` raw value, 0 for none).
    struct UnderlineRun: Equatable {
        let range: NSRange
        let style: Int
    }
    private(set) var underlineRuns: [UnderlineRun] = []

    private let bundle: String?
    /// Every read of `bundleIdentifier` (a synchronous IPC in the app).
    private(set) var bundleReads = 0
    var bundleIdentifier: String? { bundleReads += 1; return bundle }
    private(set) var calls: [Call] = []
    private(set) var text = ""   // committed text
    private(set) var marked = ""
    /// S4: the document's text before anything this client received; the insertion point is
    /// after `before + text`.
    var before = ""
    /// Overrides for the left-context tests: no insertion point, or marked text left over.
    var selectedOverride: NSRange?
    var markedOverride: NSRange?
    /// context-return section 1 (P3-2): an app that answers attributedSubstring with this, whatever range was asked for.
    var replyOverride: String?
    /// s3b2: the line rectangle this client reports.
    var line: NSRect? = NSRect(x: 100, y: 200, width: 8, height: 18)
    private(set) var lineCursor: Int?
    private(set) var lineAsks = 0
    var appearance: NSAppearance?

    func lineRect(cursor: Int) -> NSRect? { lineCursor = cursor; lineAsks += 1; return line }
    /// shift-toggle section 3: the insertion point's rectangle when nothing is marked.
    var caret: NSRect? = NSRect(x: 300, y: 400, width: 2, height: 18)
    func caretRect() -> NSRect? { caret }

    /// Every call of the three reading methods, and each range requested.
    private(set) var reads = 0
    private(set) var requested: [NSRange] = []

    init(bundle: String? = "com.apple.TextEdit") { self.bundle = bundle }

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
        if let replyOverride { return NSAttributedString(string: replyOverride) }
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
        var runs: [UnderlineRun] = []
        text.enumerateAttribute(.underlineStyle, in: NSRange(location: 0, length: text.length)) { value, range, _ in
            runs.append(UnderlineRun(range: range, style: value as? Int ?? 0))
        }
        underlineRuns = runs
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
    /// The text of the one-line hint on screen (zhen-inline section 1.3f); `nil` when none. Any `show` or `hide` clears it.
    private(set) var hint: String?
    private(set) var notes: [String?] = []
    private(set) var selected = -1
    private(set) var lineRect: NSRect?
    private(set) var appearance: NSAppearance?
    private(set) var columns = 0
    private(set) var first = 0
    private(set) var total = 0
    /// The `vertical` of the last show: 0 horizontal, 1 vertical candidate window, 2 vertical prediction row.
    private(set) var vertical = 0
    /// The glassTint of every show, in order.
    private(set) var glassTints: [Double] = []
    var onSelect: ((Int) -> Void)?
    /// The values `setGlassTint` was called with, in order.
    private(set) var retints: [Double] = []

    func setGlassTint(_ glassTint: Double) { retints.append(glassTint) }

    func show(_ candidates: [String], notes: [String?], selected: Int, columns: Int, first: Int, total: Int, vertical: Int,
              lineRect: NSRect?, appearance: NSAppearance?, glassTint: Double) {
        hint = nil
        glassTints.append(glassTint)
        self.lineRect = lineRect
        self.appearance = appearance
        self.columns = columns
        self.first = first
        self.total = total
        self.vertical = vertical
        visible = true
        items = candidates
        self.notes = notes
        self.selected = selected
    }

    /// A mouse click on the cell at `i`, as the app's panel reports it.
    func click(_ i: Int) { onSelect?(i) }

    func showHint(_ text: String, vertical: Int, lineRect: NSRect?, appearance: NSAppearance?, glassTint: Double) {
        show([], notes: [], selected: -1, columns: 0, first: 0, total: 0, vertical: vertical, lineRect: lineRect, appearance: appearance,
             glassTint: glassTint)
        hint = text
    }

    func hide() {
        visible = false
        hint = nil
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

    /// The session's left-context read with this client's bundle ID, as `send` passes it.
    func readLeft() -> (text: String?, last: Character?) {
        session.leftContext(bundle: client.bundleIdentifier)
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

    static func event(_ code: UInt16, flags: NSEvent.ModifierFlags = [], type: NSEvent.EventType = .keyDown, time: TimeInterval = 0,
                      characters: String = "") -> NSEvent {
        if type == .flagsChanged {
            return NSEvent.keyEvent(with: type, location: .zero, modifierFlags: flags, timestamp: time, windowNumber: 0,
                                    context: nil, characters: "", charactersIgnoringModifiers: "", isARepeat: false,
                                    keyCode: code) ?? event(code, flags: flags)
        }
        return NSEvent.keyEvent(with: type, location: .zero, modifierFlags: flags, timestamp: time, windowNumber: 0,
                                context: nil, characters: characters, charactersIgnoringModifiers: characters, isARepeat: false,
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

/// dev302 row 284 (docs/contracts/s3b.md section 9; row 10 before S2k, row 226 before dec-recombine, see Selftest.swift).
enum Row284 {
    static let zhuyin = "ㄔㄨˊ ㄈㄤˊ ㄒㄧㄣ ㄓㄨㄤ ㄌㄜ˙ ㄧ ㄍㄜ˙ ㄌㄩˋ ㄕㄨㄟˇ ㄏㄨˊ"
    static let standardKeys = Layouts.keys(zhuyin, eten: false)
    static let chat = "廚房新裝了一個率水壺"
    // With trigram.sjt3 beside the model the formal profile gives 律水壺; without the table it gives 濾水壺, so a test
    // (and the App selftest) that expects 律水壺 also catches a missing table. Checked with the CLI `--profile formal --context`.
    static let formal = "廚房新裝了一個律水壺"
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

/// s7c2b section 2.1: a rerank runner that never completes on its own. It holds each job until the test completes it, so a
/// result never lands at a time the test did not choose.
@MainActor
final class HeldRerankRunner: RerankRunner {
    private(set) var held: [(job: RerankJob, done: @MainActor @Sendable (Int32) -> Void)] = []
    /// Jobs handed over so far.
    private(set) var received = 0

    func run(_ job: RerankJob, done: @escaping @MainActor @Sendable (Int32) -> Void) {
        received += 1
        held.append((job, done))
    }

    /// Runs the oldest held job on this thread and hands back its code, as the app's runner does from its queue. `code`
    /// instead skips the run and hands back that code (run's 4, a caught panic, keeps no result).
    func complete(code: Int32? = nil, file: StaticString = #filePath, line: UInt = #line) {
        guard !held.isEmpty else { return XCTFail("no job is with the runner", file: file, line: line) }
        let (job, done) = held.removeFirst()
        let c = code ?? job.run()
        done(c)
    }

    /// Completes until nothing is held (a completion may start the job that waited).
    func drain() {
        while !held.isEmpty { complete() }
    }
}

/// Counts rerank frees at the shell's injectable free function (s7c2b section 3 item 1). The jobs are not freed until
/// `release()`, so no address is reused while a test runs: a job freed twice is counted as such, never undefined behaviour.
/// Lock-protected: the app's runner may drop the last reference on its queue.
final class FreeCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var freedJobs: [UInt] = []
    private var seen: Set<UInt> = []
    private var twice = 0

    var free: @Sendable (OpaquePointer) -> Void { { [self] in record($0) } }

    private func record(_ job: OpaquePointer) {
        let bits = UInt(bitPattern: Int(bitPattern: UnsafeRawPointer(job)))
        lock.lock()
        defer { lock.unlock() }
        if seen.insert(bits).inserted { freedJobs.append(bits) } else { twice += 1 }
    }

    var freed: Int { lock.lock(); defer { lock.unlock() }; return freedJobs.count }
    var doubleFrees: Int { lock.lock(); defer { lock.unlock() }; return twice }

    /// Frees every recorded job once, for real.
    func release() {
        lock.lock()
        let jobs = freedJobs
        freedJobs = []
        lock.unlock()
        for bits in jobs { shanjie_rerank_free(OpaquePointer(bitPattern: bits)) }
    }
}

/// dev302 row 27 since classes-v5 (2026-10-16; the selftest's sentence): in the chat profile the n-gram's first is 回復 and tinylm-v5
/// picks 回覆, with and without the pack (CLI rowstats). Before: dev302 row 65 (s7c2b section 2.5, revised 2026-10-12): in the chat profile the n-gram's first is 在,
/// the small model picks 再, under tinylm-v2, tinylm-v3 and tinylm-v4 alike (RerankTests, 2026-10-13; v4: CLI and these tests, 2026-10-14); under model-v6 + kn-v1 +
/// tinylm-v5 (chat standard 0.3/2/0.25) the RerankTests passed on 2026-10-15 (`make test-swift`). Row 25 (執行這個城市 -> 程式) was this
/// sentence under tinylm-v1; v2's strict chat values did not change it, v3's do again (`DeviceRow` below).
/// dev302 row 65: under classes-v5 the chat n-gram already gives 我等一下再過去 and the small model keeps it (CLI rowstats: right with
/// and without `--tinylm`), so a finished job changes nothing (RerankTests.testNoChangeMakesNoCall).
enum NoChangeRow {
    static let standardKeys = Layouts.keys("ㄨㄛˇ ㄉㄥˇ ㄧ ㄒㄧㄚˋ ㄗㄞˋ ㄍㄨㄛˋ ㄑㄩˋ", eten: false)
    static let text = "我等一下再過去"
}

enum RerankRow {
    static let zhuyin = "ㄨㄛˇ ㄧˇ ㄐㄧㄥ ㄏㄨㄟˊ ㄈㄨˋ ㄋㄧˇ ㄉㄜ˙ ㄧㄡˊ ㄐㄧㄢˋ ㄌㄜ˙"
    static let standardKeys = Layouts.keys(zhuyin, eten: false)
    static let etenKeys = Layouts.keys(zhuyin, eten: true)
    static let ngram = "我已經回復你的郵件了"
    static let reranked = "我已經回覆你的郵件了"
}

/// dev302 row 25, the device sentence of docs/contracts/s7h-tinylm-v3.md section 1: in the chat profile with no left context and
/// the bundled pack (RerankTests adds it for this sentence), the n-gram's first is 城市; tinylm-v3's chat values pick 程式
/// (tinylm-v2's did not).
enum DeviceRow {
    static let zhuyin = "ㄓˊ ㄒㄧㄥˊ ㄓㄜˋ ㄍㄜ˙ ㄔㄥˊ ㄕˋ"
    static let standardKeys = Layouts.keys(zhuyin, eten: false)
    static let ngram = "執行這個城市"
    static let reranked = "執行這個程式"
}
