import AppKit
import CShanjie
import Synchronization
import os

/// R2 (docs/spec/macos-shell.md §6): messages are static strings and C ABI return codes only.
/// No other value is ever interpolated, `.public` is never used, and nothing else logs.
enum Log {
    static let subsystem = "com.nyanako.inputmethod.shanjie"
    static let shell = Logger(subsystem: subsystem, category: "shell")
}

/// The text client of one IMK controller (IMKTextInput in the app, a fake in the tests).
@MainActor
public protocol TextClient: AnyObject {
    var bundleIdentifier: String? { get }
    func insertText(_ text: String, replacementRange: NSRange)
    func setMarkedText(_ text: NSAttributedString, selectionRange: NSRange)
    // S4 left context (docs/spec/learning.md §2). NSTextInputClient documents
    // markedRange as {NSNotFound, 0} without marked text; real clients also report {insertion, 0}.
    func selectedRange() -> NSRange
    func markedRange() -> NSRange
    func attributedSubstring(from range: NSRange) -> NSAttributedString?
    /// docs/spec/macos-shell.md §8.3: the line rectangle of the composition (screen coordinates), `nil` if the
    /// client reports none.
    /// `cursor`: the composition cursor in UTF-16 units within the marked text.
    func lineRect(cursor: Int) -> NSRect?
    /// docs/spec/keys-and-composition.md §9.4: the insertion point's line rectangle when nothing is marked (`attributes(forCharacterIndex: 0,
    /// lineHeightRectangle:)` in the app), `nil` if the client reports none.
    func caretRect() -> NSRect?
    /// docs/spec/macos-shell.md §2.3, §8.1: the light or dark appearance of the view being typed into, `nil` if unknown.
    var appearance: NSAppearance? { get }
}

extension TextClient {
    /// A client that cannot tell shows no hint.
    public func caretRect() -> NSRect? { nil }
}

/// The 英/中 hint of the Shift toggle (docs/spec/keys-and-composition.md §9.4): its own small window, display only, never key.
@MainActor
public protocol ShiftHintPanel: AnyObject {
    /// Shows `text` above `lineRect` (screen coordinates), left edges aligned, for `SHIFT_HINT_SECONDS`, then fades it away.
    func show(_ text: String, lineRect: NSRect, appearance: NSAppearance?, glassTint: Double)
}

/// Where the 單按 Shift 切換中英 switch is kept (docs/spec/keys-and-composition.md §9.1): `nil` means never chosen, which is off
/// (docs/spec/english-mixed.md §10.1; it was on earlier).
@MainActor
public protocol ShiftToggleStore: AnyObject {
    var shiftToggle: Bool? { get set }
}

/// The in-memory ShiftToggleStore.
@MainActor
public final class MemoryShiftToggleStore: ShiftToggleStore {
    public var shiftToggle: Bool?
    public init(_ shiftToggle: Bool? = nil) { self.shiftToggle = shiftToggle }
}

/// The explanation window of the English defaults change (docs/spec/english-mixed.md §10.3): its own small
/// panel that never takes the typing app's keyboard focus but whose buttons can be clicked. The app target draws it.
@MainActor
public protocol EnglishNoticePanel: AnyObject {
    /// Whether the window is on screen.
    var isShown: Bool { get }
    /// Shows the window away from `caret` (screen coordinates, `nil` when unknown). `done(true)` is 打開設定, `done(false)` is 知道了
    /// or the close button; the window does not hide itself, the shell does.
    func show(caret: NSRect?, done: @escaping @MainActor (_ openSettings: Bool) -> Void)
    func hide()
}

/// Whether the explanation window is still owed (docs/spec/english-mixed.md §10.2, key `englishDefaultsNoticePending`): written by
/// the one-time reset in main.swift for a person who updated from an earlier version, cleared by a button or the close button.
@MainActor
public protocol EnglishNoticeStore: AnyObject {
    var pending: Bool { get set }
}

/// The in-memory EnglishNoticeStore.
@MainActor
public final class MemoryEnglishNoticeStore: EnglishNoticeStore {
    public var pending: Bool
    public init(_ pending: Bool = false) { self.pending = pending }
}

/// SHIFT_HINT_SECONDS (docs/spec/keys-and-composition.md §9.4): how long the 英/中 hint stays before it fades.
public let SHIFT_HINT_SECONDS: TimeInterval = 1.0

/// A display-only candidate list (an NSPanel in the app). It never receives keys.
@MainActor
public protocol CandidatePanel: AnyObject {
    /// `notes[i]`: the name shown beside `candidates[i]` (s3f), `nil` for none.
    /// `columns`: 0 for the collapsed single row, else the expanded grid's columns (docs/spec/macos-shell.md §8.1).
    /// `first`, `total`: the position of `candidates[0]` in the whole list and the list's length
    /// (the scroll indicator).
    /// `lineRect`: where the composition's line is (docs/spec/macos-shell.md §8.3), `nil` if unknown.
    /// `appearance`: the client's (docs/spec/macos-shell.md §2.3, §8.1), `nil` for the system's.
    /// `glassTint`: the 0...1 slider value (docs/spec/macos-shell.md §8.7), applied on every show, 0 included.
    /// `vertical`: the core's `candidate_vertical` of this output (docs/spec/macos-shell.md §8.6.2, §8.6.4): 0
    /// horizontal, 1 the vertical candidate window (a single column of up to nine rows), 2 the vertical prediction row;
    /// `columns` is 0 for both. The panel draws what it is told and reads no setting.
    func show(_ candidates: [String], notes: [String?], selected: Int, columns: Int, first: Int, total: Int, vertical: Int,
              lineRect: NSRect?, appearance: NSAppearance?, glassTint: Double)
    /// docs/spec/english-mixed.md §4.6: a one-line hint in the candidate window's place, drawn like the prediction row (`vertical` 2 when
    /// the vertical orientation is chosen, else 0) but without a number's room, an expand mark or a highlight. It is not a
    /// candidate: the session ignores a click on it. Replaces whatever was on screen without an animation; hidden by `hide()`
    /// like any other content.
    func showHint(_ text: String, vertical: Int, lineRect: NSRect?, appearance: NSAppearance?, glassTint: Double)
    func hide()
    /// Re-applies the tint to what is on screen now, without a new `show` (the slider moved while the panel is up).
    func setGlassTint(_ glassTint: Double)
    /// A mouse click on a cell, by position in what `show` last received.
    var onSelect: ((Int) -> Void)? { get set }
}

/// The keyboard layout. Since docs/spec/macos-shell.md §7 it is chosen in the input
/// method's menu and kept in a `LayoutStore` under its raw value; Info.plist has a single input
/// mode, `<bundle ID>.zhuyin`, which maps to no layout.
public enum InputMode: String, Sendable {
    case standard
    case eten

    var layout: UInt32 { self == .standard ? 0 : 1 }
}

/// Where the chosen layout is kept (docs/spec/macos-shell.md §7): the raw value of an `InputMode`. The app keeps
/// it in its own UserDefaults (the Shanjie target); ShanjieKit and the tests only have the
/// in-memory store, so a test can never write the real preference.
@MainActor
public protocol LayoutStore: AnyObject {
    var layout: String? { get set }
}

/// The in-memory LayoutStore.
@MainActor
public final class MemoryLayoutStore: LayoutStore {
    public var layout: String?
    public init(_ layout: String? = nil) { self.layout = layout }
}

/// Where the "avoid ranking sensitive words first" switch is kept (docs/spec/decoder.md §2.4):
/// `nil` means never chosen, which is on. The app keeps it in its own UserDefaults; ShanjieKit
/// and the tests only have the in-memory store, so a test can never write the real preference.
@MainActor
public protocol DemoteStore: AnyObject {
    var demote: Bool? { get set }
}

/// The in-memory DemoteStore.
@MainActor
public final class MemoryDemoteStore: DemoteStore {
    public var demote: Bool?
    public init(_ demote: Bool? = nil) { self.demote = demote }
}

/// Where the "即時預測" switch is kept (docs/spec/prediction.md §7): `nil` means never chosen, which is on.
/// Same arrangement as `DemoteStore`.
@MainActor
public protocol PredictionStore: AnyObject {
    var prediction: Bool? { get set }
    /// docs/spec/prediction.md §9.4: whether browsers hide the row for the first syllable; `nil` is on.
    var browserFirstSyllable: Bool? { get set }
}

/// The in-memory PredictionStore.
@MainActor
public final class MemoryPredictionStore: PredictionStore {
    public var prediction: Bool?
    public var browserFirstSyllable: Bool?
    public init(_ prediction: Bool? = nil, browserFirstSyllable: Bool? = nil) {
        self.prediction = prediction
        self.browserFirstSyllable = browserFirstSyllable
    }
}

/// Where the "可省略韻母" switch is kept (docs/spec/prediction.md §6): `nil` means never chosen, which is off.
/// Same arrangement as `PredictionStore`.
@MainActor
public protocol AbbreviationStore: AnyObject {
    var abbreviation: Bool? { get set }
}

/// The in-memory AbbreviationStore.
@MainActor
public final class MemoryAbbreviationStore: AbbreviationStore {
    public var abbreviation: Bool?
    public init(_ abbreviation: Bool? = nil) { self.abbreviation = abbreviation }
}

/// Where the "中英文之間自動加空格" switch is kept (docs/spec/english-mixed.md §9, §11): `nil` means never chosen, which is off.
@MainActor
public protocol SpacingStore: AnyObject {
    var spacing: Bool? { get set }
}

/// The in-memory SpacingStore.
@MainActor
public final class MemorySpacingStore: SpacingStore {
    public var spacing: Bool?
    public init(_ spacing: Bool? = nil) { self.spacing = spacing }
}

/// Where the "中英混打（實驗）" switch is kept (docs/spec/english-mixed.md §1.1, §11): `nil` means never chosen, which is on (docs/spec/english-mixed.md §1.1, §11;
/// it was off before). A saved value, either way, wins.
/// Same arrangement as `AbbreviationStore`.
@MainActor
public protocol EnglishStore: AnyObject {
    var english: Bool? { get set }
    /// The choice under it (docs/spec/english-mixed.md §11): a key typed over the symbol already in its column is English
    /// (`ok`, `rust`, `gta6`). `nil` means never chosen, which is false (docs/spec/english-mixed.md §10.1; true earlier).
    var englishOverwrite: Bool? { get set }
    /// The choice next to it (docs/spec/english-mixed.md §11, default changed by zhen-revert): keys out of Zhuyin's column order,
    /// and a tone key that makes a syllable the lexicon lacks, are English. `nil` means never chosen, which is true.
    var englishKeyOrder: Bool? { get set }
    /// The choice before it (docs/spec/english-mixed.md §1.1, §3.2): a key in a column before a filled one is never English, so a
    /// hurried reversal stays Zhuyin. `nil` means never chosen, which is true.
    var englishReversalGuard: Bool? { get set }
}

/// The in-memory EnglishStore.
@MainActor
public final class MemoryEnglishStore: EnglishStore {
    public var english: Bool?
    public var englishOverwrite: Bool?
    public var englishKeyOrder: Bool?
    public var englishReversalGuard: Bool?
    public init(_ english: Bool? = nil, overwrite: Bool? = nil, keyOrder: Bool? = nil, reversalGuard: Bool? = nil) {
        self.english = english
        self.englishOverwrite = overwrite
        self.englishKeyOrder = keyOrder
        self.englishReversalGuard = reversalGuard
    }
}

/// Where the 模糊輸入 flags are kept (docs/spec/decoder.md §6.1): bits 0-3 the four sound pairs, bit 4 the adjacent-key
/// slips, as `shanjie_engine_set_fuzzy`; `nil` means never chosen, which is 0 (all off). Same arrangement as `PredictionStore`.
@MainActor
public protocol FuzzyStore: AnyObject {
    var fuzzyFlags: UInt32? { get set }
}

/// The in-memory FuzzyStore.
@MainActor
public final class MemoryFuzzyStore: FuzzyStore {
    public var fuzzyFlags: UInt32?
    public init(_ fuzzyFlags: UInt32? = nil) { self.fuzzyFlags = fuzzyFlags }
}

/// Where the "動漫與遊戲詞" switch is kept (docs/spec/lexicon-and-packs.md §4.1, §4.2): `nil` means never chosen, which is on. Same arrangement as `PredictionStore`.
@MainActor
public protocol AcgPackStore: AnyObject {
    var acgPack: Bool? { get set }
}

/// The in-memory AcgPackStore.
@MainActor
public final class MemoryAcgPackStore: AcgPackStore {
    public var acgPack: Bool?
    public init(_ acgPack: Bool? = nil) { self.acgPack = acgPack }
}

/// Where the "經典台詞" switch is kept (docs/spec/lexicon-and-packs.md §7.2, §4.1, §4.2): `nil` means never chosen, which is **off**
/// (the opposite of `AcgPackStore`: the quote pack ships off).
@MainActor
public protocol QuotePackStore: AnyObject {
    var quotePack: Bool? { get set }
}

/// The in-memory QuotePackStore.
@MainActor
public final class MemoryQuotePackStore: QuotePackStore {
    public var quotePack: Bool?
    public init(_ quotePack: Bool? = nil) { self.quotePack = quotePack }
}

/// Where the "資訊與 AI 詞" switch is kept (docs/spec/lexicon-and-packs.md §4.2, §4.3): `nil` means never chosen, which is on. Same arrangement as `AcgPackStore`.
@MainActor
public protocol ItPackStore: AnyObject {
    var itPack: Bool? { get set }
}

/// The in-memory ItPackStore.
@MainActor
public final class MemoryItPackStore: ItPackStore {
    public var itPack: Bool?
    public init(_ itPack: Bool? = nil) { self.itPack = itPack }
}

/// Where the "美妝與保養詞" switch is kept (docs/spec/lexicon-and-packs.md §4.2, §8.3): `nil` means never chosen, which is on. Same arrangement as `ItPackStore`.
@MainActor
public protocol BeautyPackStore: AnyObject {
    var beautyPack: Bool? { get set }
}

/// The in-memory BeautyPackStore.
@MainActor
public final class MemoryBeautyPackStore: BeautyPackStore {
    public var beautyPack: Bool?
    public init(_ beautyPack: Bool? = nil) { self.beautyPack = beautyPack }
}

/// Where the candidate glass tint slider is kept (docs/spec/macos-shell.md §8.7): 0...1,
/// `nil` means never set, which is 0 (no tint). Same arrangement as `DemoteStore`.
@MainActor
public protocol GlassTintStore: AnyObject {
    var glassTint: Double? { get set }
}

/// The in-memory GlassTintStore.
@MainActor
public final class MemoryGlassTintStore: GlassTintStore {
    public var glassTint: Double?
    public init(_ glassTint: Double? = nil) { self.glassTint = glassTint }
}

/// Where the "候選窗方向" choice is kept (docs/spec/macos-shell.md §8.6.1): `true` is the vertical window, `nil`
/// means never set, which is horizontal. Same arrangement as `GlassTintStore`.
@MainActor
public protocol CandidateOrientationStore: AnyObject {
    var candidateVertical: Bool? { get set }
}

/// The in-memory CandidateOrientationStore.
@MainActor
public final class MemoryCandidateOrientationStore: CandidateOrientationStore {
    public var candidateVertical: Bool?
    public init(_ candidateVertical: Bool? = nil) { self.candidateVertical = candidateVertical }
}

/// Where the "語意校正" switch is kept (docs/spec/semantic-rerank.md §8.2): `nil` means never chosen, which is on
/// (docs/spec/semantic-rerank.md §1.2).
/// Same arrangement as `PredictionStore`. `rerankLevel` is the strength (docs/spec/semantic-rerank.md §8.2): the raw
/// value of a `RerankLevel`; `nil` or any other string is standard, like `ProfileModeStore`.
@MainActor
public protocol RerankStore: AnyObject {
    var rerank: Bool? { get set }
    var rerankLevel: String? { get set }
}

/// The in-memory RerankStore.
@MainActor
public final class MemoryRerankStore: RerankStore {
    public var rerank: Bool?
    public var rerankLevel: String?
    public init(_ rerank: Bool? = nil, level: String? = nil) {
        self.rerank = rerank
        rerankLevel = level
    }
}

/// 語意校正的強度 (docs/spec/semantic-rerank.md §8.2): stored by raw value; `code` is the C ABI's level, fixed per case.
public enum RerankLevel: String, CaseIterable, Sendable {
    case conservative
    case standard
    case aggressive

    var code: UInt32 {
        switch self {
        case .conservative: return 0
        case .standard: return 1
        case .aggressive: return 2
        }
    }
}

/// One job's token, returned by `RerankRunner.run` (docs/spec/keys-and-composition.md §7.3). The shell keeps it as its running state
/// and hands it back to `wait`; a runner subclasses it to find the job again. Compared with `===`, never by address.
@MainActor
open class RerankHandle {
    public init() {}
}

/// Runs a rerank job off the main thread (docs/spec/semantic-rerank.md §8.1): `job.run()` on another thread, then `done` with its code on the
/// main thread. The app passes `QueueRerankRunner`; the tests pass runners they complete by hand.
@MainActor
public protocol RerankRunner: AnyObject {
    func run(_ job: RerankJob, done: @escaping @MainActor @Sendable (Int32) -> Void) -> RerankHandle

    /// Enter's bounded wait (docs/spec/keys-and-composition.md §7.3): blocks the calling (main) thread until `deadline` for the
    /// job `handle` names, and returns its code only after that job's `run()` returned; `nil` when the deadline came first. The
    /// job's `done` is still called afterwards (the shell makes that late call a no-op).
    func wait(_ handle: RerankHandle, until deadline: DispatchTime) -> Int32?
}

/// The wait object of one job of `QueueRerankRunner`: the queue's block signals it once, after the code is in the box (so a
/// woken waiter always finds the code). One per job, never shared, so a signal left by a job nobody waited for cannot end the
/// next wait early. Holds no pointer, only the semaphore and the code.
private final class RerankWaiter: Sendable {
    let signal = DispatchSemaphore(value: 0)
    let code = Mutex<Int32?>(nil)
}

private final class QueueRerankHandle: RerankHandle {
    let waiter: RerankWaiter
    init(_ waiter: RerankWaiter) { self.waiter = waiter }
}

/// The app's runner: one serial `.utility` queue with a fixed label. The closure holds the job wrapper (never its
/// pointer) until run returns. Two orderings put the run before the main-thread apply (RerankJob invariant 2): the code comes
/// back through `DispatchQueue.main.async`, or the main thread is woken by this job's semaphore, which is signalled only after
/// `run()` returned (Enter's bounded wait, `wait(_:until:)`, docs/spec/keys-and-composition.md §7.3). The main thread otherwise never waits for the queue.
public final class QueueRerankRunner: RerankRunner {
    private let queue = DispatchQueue(label: "com.nyanako.inputmethod.shanjie.rerank", qos: .utility)

    public init() {}

    public func run(_ job: RerankJob, done: @escaping @MainActor @Sendable (Int32) -> Void) -> RerankHandle {
        let waiter = RerankWaiter()
        queue.async {
            let code = job.run()
            waiter.code.withLock { $0 = code }  // before the signal
            waiter.signal.signal()
            DispatchQueue.main.async { done(code) }
        }
        return QueueRerankHandle(waiter)
    }

    public func wait(_ handle: RerankHandle, until deadline: DispatchTime) -> Int32? {
        guard let waiter = (handle as? QueueRerankHandle)?.waiter, waiter.signal.wait(timeout: deadline) == .success else { return nil }
        return waiter.code.withLock { $0 }
    }
}

/// How the chat or formal profile is chosen (docs/spec/macos-shell.md §5.1): by the app (`chatApps` and the
/// user's per-app choices), or always one of the two.
public enum ProfileMode: String, Sendable {
    case auto
    case chat
    case formal
}

/// Where the profile mode is kept (docs/spec/macos-shell.md §5.2): the raw value of a `ProfileMode`; `nil` or any other string is
/// `auto`. Same arrangement as `LayoutStore`.
@MainActor
public protocol ProfileModeStore: AnyObject {
    var profileMode: String? { get set }
}

/// The in-memory ProfileModeStore.
@MainActor
public final class MemoryProfileModeStore: ProfileModeStore {
    public var profileMode: String?
    public init(_ profileMode: String? = nil) { self.profileMode = profileMode }
}

/// Where the per-app choices are kept (docs/spec/macos-shell.md §5.2): bundle ID to `chat` or `formal`, only for apps the user
/// switched from the menu. Read back raw, exactly as stored, and filtered entry by entry by `AppProfiles.parse`; written as a
/// whole, an empty dictionary removing the value. Same arrangement as `LayoutStore` otherwise.
@MainActor
public protocol AppProfileStore: AnyObject {
    var appProfiles: Any? { get }
    func setAppProfiles(_ profiles: [String: String])
}

/// The in-memory AppProfileStore. `appProfiles` is whatever the test put there, so a malformed value can be tried.
@MainActor
public final class MemoryAppProfileStore: AppProfileStore {
    public private(set) var appProfiles: Any?
    public init(_ appProfiles: Any? = nil) { self.appProfiles = appProfiles }
    public func setAppProfiles(_ profiles: [String: String]) { appProfiles = profiles.isEmpty ? nil : profiles }
}

/// The stored per-app choices, converted both ways (docs/spec/macos-shell.md §5.2).
@MainActor
public enum AppProfiles {
    /// Entry by entry: a root that is not a dictionary is empty; only a non-empty string key with the string value `chat` or
    /// `formal` (`ProfileMode`'s raw values, the one string table) is kept, and any other entry is skipped without affecting the
    /// valid ones (a whole-dictionary cast would drop them all, and the next write would erase them).
    public static func parse(_ raw: Any?) -> [String: UInt32] {
        guard let dict = raw as? [AnyHashable: Any] else { return [:] }
        var out: [String: UInt32] = [:]
        for (key, value) in dict {
            guard let bundle = key.base as? String, !bundle.isEmpty,
                  let mode = (value as? String).flatMap(ProfileMode.init(rawValue:)) else { continue }
            switch mode {
            case .chat: out[bundle] = Shell.chat
            case .formal: out[bundle] = Shell.formal
            case .auto: continue
            }
        }
        return out
    }

    /// What `parse` reads back.
    static func stored(_ profiles: [String: UInt32]) -> [String: String] {
        profiles.mapValues { ($0 == Shell.chat ? ProfileMode.chat : ProfileMode.formal).rawValue }
    }
}

/// Process-wide state: the single engine (about 240 MB each, so never two at once), the single
/// candidate panel and the page it shows, and which session owns the composition (docs/spec/macos-shell.md §2.2).
/// All calls happen on the main thread.
@MainActor
public final class Shell {
    /// Built-in chat apps (docs/spec/macos-shell.md §6); every other app gets the formal profile, unless the user switched it
    /// (docs/spec/macos-shell.md §5.4). A bundle ID is never passed to the core or logged; it is stored only for an
    /// app the user switched from the menu (`appProfiles`).
    static let chatApps: Set<String> = [
        "com.hnc.Discord", "jp.naver.line.mac", "com.apple.MobileSMS", "com.tinyspeck.slackmacgap",
        "ru.keepcoder.Telegram", "com.tdesktop.Telegram", "net.whatsapp.WhatsApp", "com.facebook.archon",
    ]
    static let chat: UInt32 = 0, formal: UInt32 = 1

    /// Chromium browsers (docs/spec/prediction.md §9.3): Google Docs answers an input method's first
    /// rectangle query with the view's origin, so the prediction row is not shown for the first syllable there. Crest and
    /// Aside were added with bundle IDs read off a machine.
    static let browserApps: Set<String> = [
        "com.google.Chrome", "com.google.Chrome.beta", "com.google.Chrome.dev", "com.google.Chrome.canary",
        "org.chromium.Chromium",
        "com.microsoft.edgemac", "com.microsoft.edgemac.Beta", "com.microsoft.edgemac.Dev", "com.microsoft.edgemac.Canary",
        "com.brave.Browser", "com.brave.Browser.beta", "com.brave.Browser.nightly",
        "company.thebrowser.Browser", "com.vivaldi.Vivaldi", "com.operasoftware.Opera", "com.operasoftware.OperaGX",
        "com.pauldavis.crest", "at.studio.AsideBrowser",
    ]

    /// docs/spec/prediction.md §9.2: at most one syllable on screen, a run of zhuyin symbols and tones counting as one and
    /// every other character as one ("ㄘㄜ", "測" and "" are; "測ㄕ" is not).
    static func singleSyllable(_ preedit: String) -> Bool {
        var count = 0
        var inZhuyin = false
        for u in preedit.unicodeScalars {
            let zhuyin: Bool
            switch u.value {
            case 0x3105...0x3129, 0x02CA, 0x02C7, 0x02CB, 0x02D9: zhuyin = true
            default: zhuyin = false
            }
            if !zhuyin || !inZhuyin { count += 1 }
            if count > 1 { return false }
            inZhuyin = zhuyin
        }
        return true
    }

    /// The one place the profile is decided (docs/spec/macos-shell.md §5.1). `auto`: no bundle ID is formal, as before; then the
    /// user's choice for the app; then `chatApps`.
    static func profile(bundle: String?, mode: ProfileMode, overrides: [String: UInt32]) -> UInt32 {
        switch mode {
        case .chat: return chat
        case .formal: return formal
        case .auto:
            guard let bundle, !bundle.isEmpty else { return formal }
            return overrides[bundle] ?? (chatApps.contains(bundle) ? chat : formal)
        }
    }

    private let resources: URL
    /// s3e: the converted system punctuation table, `nil` when unavailable (the core keeps its own).
    private let punctuation: String?
    /// s3f: Apple's punctuation names; empty when unavailable (candidates show without names).
    private let names: [String: String]
    private let layoutStore: LayoutStore
    private let demoteStore: DemoteStore
    /// sw: whether the core's demotion table applies (default on); sent to every engine `build()` makes.
    private(set) var demoteOn = true
    private let predictionStore: PredictionStore
    /// V3: whether the prediction row is computed (default on); sent to every engine `build()` makes.
    private(set) var predictionOn = true
    /// docs/spec/prediction.md §9.4: hide the passive prediction row for the first syllable in `browserApps` (default on).
    private(set) var browserFirstSyllableOn = true
    private let abbreviationStore: AbbreviationStore
    /// docs/spec/prediction.md §6: the abbreviation composer (default off); sent to every engine `build()` makes, next to the prediction switch.
    private(set) var abbreviationOn = false
    /// Whether the abbreviation switch is offered at all (menu item, settings toggle). The app passes false: the feature does not
    /// ship in v0.4.0 (after a device check: a whole sentence cannot be abbreviated, and keys seemed stuck; the
    /// stuck keys are read against docs/spec/prediction.md §6, not reproduced: Space and tone keys are eaten while two or more units are
    /// pending). Hidden means always off; the stored value is neither read nor written.
    public let showsAbbreviation: Bool
    private let spacingStore: SpacingStore
    /// cjk-spacing: space between Han and ASCII letters or digits in the text the shell commits (default off).
    private(set) var spacingOn = false
    private let englishStore: EnglishStore
    /// zhen-mixed: the experimental English window (default on since docs/spec/english-mixed.md §1.1, §11; absent in the store is on, a saved off stays off); sent to every engine `build()` makes. Unlike the abbreviation
    /// switch it ships (the app shows it).
    private(set) var englishOn = false
    /// docs/spec/english-mixed.md §11: whether a key typed over the symbol already in its column is English (default false since
    /// docs/spec/english-mixed.md §10.1: `UserDefaults` key `englishOverwrite`, absent is false); sent to every
    /// engine `build()` makes. Only matters while `englishOn`.
    private(set) var englishOverwriteOn = false
    /// docs/spec/english-mixed.md §11 with zhen-revert's default: whether keys out of Zhuyin's column order, and a tone key that makes a
    /// syllable the lexicon lacks, are English (default true: `UserDefaults` key `englishKeyOrder`, absent is true, a saved false stays
    /// false); sent to every engine `build()` makes. Only matters while `englishOn`.
    private(set) var englishKeyOrderOn = true
    /// docs/spec/english-mixed.md §1.1, §3.2: whether a key in a column before a filled one is never English (default true: `UserDefaults` key
    /// `englishReversalGuard`, absent is true); sent to every engine `build()` makes. Only matters while `englishOn`.
    private(set) var englishReversalGuardOn = true
    private let fuzzyStore: FuzzyStore
    /// docs/spec/decoder.md §6.1: the 模糊輸入 flags (default 0, all off; bits as `shanjie_engine_set_fuzzy`); sent to every engine
    /// `build()` makes. Only the bits the core knows are kept.
    private(set) var fuzzyFlags: UInt32 = 0
    /// What `build()` sent to each engine it made, in order (test hook, like `rerankSent`).
    private(set) var fuzzySent: [UInt32] = []
    private let acgPackStore: AcgPackStore
    /// The ACG word pack (default on): parsed into the lexicon, so a change rebuilds the engine like a layout change.
    private(set) var acgPackOn = true
    /// The pack's source date for the grey menu line (docs/spec/lexicon-and-packs.md §4.5): `latest_source_revision` of `Resources/packs/acg.json`
    /// as a UTC date, read once at init. `nil` (file or field missing or unreadable) means no line.
    let acgDataDate: String?
    private let quotePackStore: QuotePackStore
    /// The classic quote pack (default off, docs/spec/lexicon-and-packs.md §7.2, §4.1, §4.2): a bit of the same packs mask as the ACG pack, so a change rebuilds the engine too.
    private(set) var quotePackOn = false
    private let itPackStore: ItPackStore
    /// The IT+AI word pack (default on, docs/spec/lexicon-and-packs.md §4.2, §4.3): parsed into the lexicon like the ACG pack, so a change rebuilds the engine.
    private(set) var itPackOn = true
    private let beautyPackStore: BeautyPackStore
    /// The beauty and skincare word pack (default on, docs/spec/lexicon-and-packs.md §4.2, §8.3): parsed into the lexicon like the IT pack, so a change rebuilds the engine.
    private(set) var beautyPackOn = true
    /// The packs in the settings order: mask bit, file in the packs directory, menu title. `packMask`, the missing-file check and the menu line all read this.
    private static let packTable: [(bit: UInt32, file: String, title: String)] = [
        (CoreEngine.packACG, "acg-add.tsv", MenuEntry.Text.acgPack),
        (CoreEngine.packQuotes, "quotes-add.tsv", MenuEntry.Text.quotePack),
        (CoreEngine.packIT, "it-add.tsv", MenuEntry.Text.itPack),
        (CoreEngine.packBeauty, "beauty-add.tsv", MenuEntry.Text.beautyPack),
    ]
    /// The mask `build()` passes to `shanjie_engine_new_packs`: one bit per switch that is on. The quote switch never chosen is off and the
    /// IT and beauty switches never chosen are on, so an app that never touched any builds with the ACG, IT and beauty bits.
    var packMask: UInt32 { (acgPackOn ? CoreEngine.packACG : 0) | (quotePackOn ? CoreEngine.packQuotes : 0) | (itPackOn ? CoreEngine.packIT : 0) | (beautyPackOn ? CoreEngine.packBeauty : 0) }
    /// The pack bits the current engine was really built with (docs/spec/lexicon-and-packs.md §4.2, §4.3): `packMask` when that build worked, less the bits
    /// whose file is missing (the core loads nothing for them); 0 after the pack-less retry and 0 with no engine. The menu's 已開啟的詞包 section reads this, never the stored choices.
    private(set) var loadedPackMask: UInt32 = 0
    /// The names of the loaded packs in the settings order, one line each in the menu's 已開啟的詞包 section.
    var loadedPackTitles: [String] {
        Shell.packTable.compactMap { loadedPackMask & $0.bit != 0 ? $0.title : nil }
    }
    private let shiftToggleStore: ShiftToggleStore
    /// docs/spec/keys-and-composition.md §9.1: a lone Shift tap switches between Chinese and English (default off since
    /// docs/spec/english-mixed.md §10.1). Off: `flagsChanged` is not handled, the states below are cleared and the
    /// core is back to `set_ascii(0)`.
    private(set) var shiftToggleOn = false
    let shiftHint: ShiftHintPanel?
    let englishNoticeStore: EnglishNoticeStore
    let englishNotice: EnglishNoticePanel?
    /// docs/spec/english-mixed.md §10.3: the next keyDown of any session shows the window. Process-wide, so the order of one app's
    /// deactivate and the next app's activate does not matter. Set by `Session.activate` while the store says pending; cleared when the
    /// window is shown; the window's buttons clear the store itself.
    var englishNoticeArmed = false
    /// Set by the app: opens the settings window on the 選字 page (the notice's 打開設定 button).
    public var onOpenCandidateSettings: (() -> Void)?
    var shiftTap = ShiftTap()
    /// docs/spec/macos-shell.md §13: the in-memory diagnostic log of the Shift toggle.
    public let diag: DiagLog
    /// Seconds since the latest mouse-button press (left, right, other) in this login session; a test replaces it. No permission needed.
    var mouseAgo: () -> TimeInterval = {
        [CGEventType.leftMouseDown, .rightMouseDown, .otherMouseDown]
            .map { CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0) }.min() ?? .infinity
    }
    /// The system uptime, the clock `NSEvent.timestamp` uses; a test replaces it.
    var uptime: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }
    /// docs/spec/english-mixed.md §11: CLOCK_MONOTONIC, which `man clock_gettime` says "will continue to increment while the system is
    /// asleep" (`uptime` does not), so a lid closed for an hour counts as a long pause; not observed across a real sleep. A test replaces it.
    var idleClock: () -> TimeInterval = { Double(clock_gettime_nsec_np(CLOCK_MONOTONIC)) / 1_000_000_000 }
    /// docs/spec/english-mixed.md §11: when the last typing key (or a Shift-tap hint) came, on `idleClock`. `nil` means the next typing
    /// key shows the 英/中 hint: set by activation and by an owner change, and before any typing.
    var lastTypingAt: TimeInterval?
    /// docs/spec/keys-and-composition.md §9.4: a pause at least this long before a typing key shows the 英/中 hint again. Picked, not measured; tune it on a device.
    static let modeHintIdleSeconds: TimeInterval = 30
    /// What the latest `build()` sent its engine as `set_ascii` (test hook); `nil` before the first build.
    private(set) var lastBuiltAscii: Bool?
    /// The composition cursor (UTF-16) of the last snapshot applied: where the Shift hint anchors while composing.
    var composingCursor = 0
    /// docs/spec/keys-and-composition.md §9.2: the apps (bundle ID, `""` for a client with none) whose state is English. In memory only: a restart
    /// of the input method puts every app back to Chinese.
    var asciiApps: Set<String> = []
    /// What the current engine was last told by `set_ascii` (the engine is the owner's; `build()` sends it again to a new one).
    var engineAscii = false
    private let glassTintStore: GlassTintStore
    private let candidateOrientationStore: CandidateOrientationStore
    /// The orientation setting (default horizontal); sent to every engine `build()` makes and on every change (candidate-vertical
    /// docs/spec/macos-shell.md §8.6.1). A candidate window already open keeps the orientation it opened with, so a change reaches the
    /// next window. The core also re-renders a prediction row that is on screen in the new orientation (docs/spec/macos-shell.md §8.6.4); with the
    /// setting only in the settings window, where clicking is inferred (not measured) to commit the composition and hide the
    /// panel, that is reachable through the API and the tests but not observable from the window as shipped.
    public private(set) var candidateVertical = false
    private let rerankStore: RerankStore
    /// docs/spec/semantic-rerank.md §8.2: whether the core makes rerank jobs (default on since S7h: on, with a
    /// break rate <= 0.5%); sent to every engine `build()` makes and on every change.
    private(set) var rerankOn: Bool
    /// The small model's file in Resources (docs/spec/semantic-rerank.md §8.3). The only path it is loaded from: `build()` and the selftest join it to
    /// `resources`; no init parameter, defaults key or environment variable names another.
    static let tinyLMFile = "tinylm.sjtl"
    /// docs/spec/semantic-rerank.md §4.6: the colloquial table (常談 SJ-CL), in Resources next to the other model files.
    static let colloquialFile = "colloquial.sjlm"
    private let rerankRunner: RerankRunner
    /// The job with the runner (docs/spec/semantic-rerank.md §8.1: one at a time), nil when none. Its completion is handled once: by the runner's
    /// `done` or by Enter's bounded wait, whichever comes first; the other finds it no longer here (`rerankFinished`).
    private var rerankRunning: RunningRerank?
    /// docs/spec/keys-and-composition.md §7.3: the longest Enter waits for the rerank (ms), in total. 33 to 84 ms was the last job's
    /// time in PUNCT-0's VM measurement, so the limit is above the largest; on the Mac dev302's per-row p95 is
    /// 12.6 ms. The exception §7.3 makes: only Enter waits, and only while a job of this session runs or is queued.
    static let ENTER_RERANK_WAIT_MS = 100
    /// The limit `Session.handle` uses on Enter; the tests replace it (the real-queue test gives it 10 s, the shipping value is asserted apart).
    var enterRerankWaitMS = Shell.ENTER_RERANK_WAIT_MS
    /// The latest job taken while another ran. A newer one replaces it, and the replaced job is freed as it is dropped here.
    private var rerankPending: PendingRerank?
    /// Jobs taken from the core; the tests compare it with the frees.
    private(set) var rerankTaken = 0
    /// Frees a job; every wrapper calls it once from its deinit. The tests count here.
    var rerankFree: @Sendable (OpaquePointer) -> Void = { shanjie_rerank_free($0) }
    /// `CoreEngine.rerankTake`; the tests replace it to see a non-zero take handled.
    var rerankTake: (CoreEngine, @escaping @Sendable (OpaquePointer) -> Void) -> (RerankJob?, Int32) = { $0.rerankTake(free: $1) }
    /// `RerankJob.apply(on:)`; the tests replace it to see apply's non-zero codes other than 5 handled.
    var rerankApply: (RerankJob, CoreEngine) -> CoreResult = { $0.apply(on: $1) }
    /// Every value `build()` passed to `setRerank`, in order (the tests read it: a nil store must send true, not nothing).
    private(set) var rerankSent: [Bool] = []
    /// How many engines `build()` has tried to make (test hook, like `rerankSent`): the fall-back order is asserted by counting them.
    private(set) var engineBuilds = 0
    private func makeEngine(packsDir: String?, packs: UInt32) -> (CoreEngine?, Int32) {
        engineBuilds += 1
        return CoreEngine.make(dataDir: resources.path, layout: mode.layout, packsDir: packsDir, packs: packs)
    }
    /// Rerank strength (docs/spec/semantic-rerank.md §8.2): the stored level, sent to every engine `build()` makes and on every change.
    private(set) var rerankLevel: RerankLevel
    /// Every level code sent to an engine, by `build()` and by `setRerankLevel`, in order (the tests read it, as `rerankSent`).
    private(set) var rerankLevelSent: [UInt32] = []
    /// `shanjie_engine_rerank_levels` of the current engine after `build()` loaded the small model (0 without one).
    private(set) var rerankLevels: UInt32 = 0
    /// Whether the current engine has the small model (set by every `build()`): false when there is no engine, when load_lm
    /// failed (load_tinylm is then skipped) or when load_tinylm returned non-zero. Without it no job is ever made.
    private(set) var rerankAvailable = false
    /// The 語意校正 menu item and settings toggle are enabled only when the switch can do something: the demotion table on (the
    /// core makes no job without it, F11) and the small model loaded. The checkmark still shows the stored choice.
    var rerankEnabled: Bool { demoteOn && rerankAvailable }
    /// The settings window's 強度 control (docs/spec/semantic-rerank.md §8.2) is enabled only when the switch is on and can do something, and the loaded
    /// model offers the conservative and the aggressive set (a version 3 file); otherwise it shows greyed with the stored choice.
    var rerankLevelEnabled: Bool { rerankOn && rerankEnabled && rerankLevels & 0b101 == 0b101 }
    /// The 中英混打（實驗） menu item and settings toggle, likewise: the standard layout (the core records no
    /// shadow in the Eten layout, docs/spec/english-mixed.md §1.2, so the switch would do nothing there). The checkmark still shows the choice.
    var englishEnabled: Bool { mode == .standard }
    /// The adjacent-key checkbox of 模糊輸入: the core's neighbour table is the standard layout's keyboard, so bit 4 does nothing
    /// in the Eten layout and the checkbox is greyed there (the value is kept; docs/spec/decoder.md §6.1).
    var fuzzyAdjacentEnabled: Bool { mode == .standard }
    /// The 重打同一個位置算英文 choice under it is enabled on the same condition and while the switch itself is on.
    var englishOverwriteEnabled: Bool { englishEnabled && englishOn }
    /// The 依按鍵順序判斷英文 choice is greyed on the same condition as the overwrite choice.
    var englishKeyOrderEnabled: Bool { englishOverwriteEnabled }
    /// The 快打時鍵序顛倒不當英文 choice only matters for trigger (b), which needs the key-order choice on: greyed unless that one is
    /// enabled and on (the setting is kept).
    var englishReversalGuardEnabled: Bool { englishKeyOrderEnabled && englishKeyOrderOn }

    /// A job waiting for the runner, with the session and engine it was taken for. Weak: a waiting job keeps neither alive,
    /// and an identity is never compared by address.
    private struct PendingRerank {
        let job: RerankJob
        weak var session: Session?
        weak var engine: CoreEngine?
    }
    /// The job with the runner: the same three, and the runner's handle. Compared with ===, so a new job on an old one's address is
    /// still another job.
    @MainActor
    private final class RunningRerank {
        let job: RerankJob
        weak var session: Session?
        weak var engine: CoreEngine?
        var handle: RerankHandle?
        init(_ p: PendingRerank) {
            job = p.job
            session = p.session
            engine = p.engine
        }
    }
    private let profileModeStore: ProfileModeStore
    private let appProfileStore: AppProfileStore
    /// docs/spec/macos-shell.md §5.2: both read once at init, then changed only through the setters below.
    public private(set) var profileMode = ProfileMode.auto
    /// Bundle ID to profile, only where it differs from `chatApps` (docs/spec/macos-shell.md §5.3).
    public private(set) var appProfiles: [String: UInt32] = [:]
    /// The `vertical` value (0, 1 or 2) of what the panel shows now; 0 once it hid. Page Up / Down go to the core only while
    /// it is 1, the vertical candidate window (docs/spec/macos-shell.md §8.6.2); the vertical prediction row, 2, does not count.
    private(set) var shownVertical = 0
    /// The `selected` of what the panel shows now (-1: no candidate window and no entered prediction row, also after it hid).
    /// Enter does not wait for a rerank while it is >= 0: the core applies none then (docs/spec/keys-and-composition.md §7.3).
    private(set) var candidateSelected = -1
    var verticalOpen: Bool { shownVertical == VerticalLayout.candidates }
    /// Posted (object: this shell) after any setting changes, from the menu or the settings window;
    /// the window's model re-reads on it (docs/spec/macos-shell.md §9.3).
    public static let didChangeSettings = Notification.Name("ShanjieShellDidChangeSettings")
    let panel: CandidatePanel
    let isSecureInput: () -> Bool
    private(set) var engine: CoreEngine?
    private(set) var mode: InputMode = .standard
    private(set) var profile: UInt32 = Shell.chat
    /// S4: the learning directory (docs/spec/learning.md §10), `nil` for none (no file is opened or written).
    let learningDirectory: URL?
    /// The last `shanjie_engine_learning_open` of the current engine failed.
    var learningOpenFailed = false
    /// The clear's confirmation and failure windows (docs/spec/learning.md §10).
    let dialogs: LearningDialogs

    /// The owning session. "Still valid" means this weak reference is not nil; an address
    /// (ObjectIdentifier) is never used, as a freed controller's address can be reused.
    weak var owner: Session?
    /// Whether the composition (as last applied to a client) is non-empty.
    var composing = false
    /// The last `lineRect` answer and the preedit and cursor it was asked for. Asking the client is a
    /// synchronous IPC on the key path, so a pure selection move (same preedit and cursor) reuses it.
    /// Cleared whenever the panel hides: commit, reset, owner change.
    var lineCache: (preedit: String, cursor: Int, rect: NSRect?)?
    /// docs/spec/english-mixed.md §4.6: the hint is anchored at the start of the open English run, and the `lineRect` answer for that
    /// start is kept for the run's lifetime, so only a run's first key asks the client. Cleared when the hint goes.
    var hintAnchor: (start: Int, rect: NSRect?)?
    /// Whether the hint is what the panel shows now. A click on it is not a candidate pick (`Session.candidateSelected`).
    private(set) var hintShown = false

    /// `latest_source_revision` ("2026-10-06T00:08:14Z") of the manifest at `url` as a UTC "yyyy-MM-dd"; `nil` for anything else.
    static func readAcgDataDate(_ url: URL) -> String? {
        guard let data = try? Data(contentsOf: url),
              let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let stamp = json["latest_source_revision"] as? String,
              let date = ISO8601DateFormatter().date(from: stamp) else { return nil }
        let utc = DateFormatter()
        utc.locale = Locale(identifier: "en_US_POSIX")
        utc.timeZone = TimeZone(identifier: "UTC")
        utc.dateFormat = "yyyy-MM-dd"
        return utc.string(from: date)
    }

    /// `resources`: the absolute Resources directory holding the lexicon files and bigram.sjlm.
    /// `panel`: the one candidate panel (an NSPanel in the app).
    /// `isSecureInput`: IsSecureEventInputEnabled in the app (Carbon lives in the executable only);
    /// tests pass a fake.
    /// `layoutStore`: the chosen layout. Required, with no default, so the app cannot silently
    /// start without its preference (docs/spec/macos-shell.md §7); a missing or unknown value is the standard
    /// layout.
    /// `learningDirectory`: where the core keeps learning.tsv (docs/spec/learning.md §10), `Shell.learningURL()`
    /// in the app; tests pass a temporary directory or nil. Required, with no default, so no caller
    /// can reach the real Application Support by omission.
    /// `dialogs`: the clear's windows (`AlertDialogs` in the app; tests answer for the user). Required,
    /// so a build cannot ship a clear that silently does nothing.
    /// `demoteStore`: the demotion switch (sw). Required, with no default, like `layoutStore`: the app passes
    /// its UserDefaults-backed store, tests the in-memory one.
    /// `predictionStore`: the prediction switch (docs/spec/prediction.md §7). Required, like `demoteStore`.
    /// `abbreviationStore`: the abbreviation switch (docs/spec/prediction.md §6). Required, like `predictionStore`.
    /// `englishStore`: the 中英混打 switch (docs/spec/english-mixed.md §1.1, §11). Required, like `abbreviationStore`.
    /// `acgPackStore`: the ACG word pack switch (docs/spec/lexicon-and-packs.md §4.1). Required, like `demoteStore`.
    /// `quotePackStore`: the classic quote pack switch (docs/spec/lexicon-and-packs.md §7.2, §4.1, §4.2). Required, like `acgPackStore`.
    /// `itPackStore`: the IT+AI word pack switch (docs/spec/lexicon-and-packs.md §4.2, §4.3). Defaults to an in-memory store so the many test rigs need not name it; the app passes its own.
    /// `beautyPackStore`: the beauty and skincare word pack switch (docs/spec/lexicon-and-packs.md §4.2, §8.3). Defaults to an in-memory store like `itPackStore`; the app passes its own.
    /// `glassTintStore`: the candidate glass tint (docs/spec/macos-shell.md §8.7). Required, like `demoteStore`.
    /// `candidateOrientationStore`: the candidate window's orientation (docs/spec/macos-shell.md §8.6.1). Required, like `glassTintStore`.
    /// `rerankStore`: the 語意校正 switch (docs/spec/semantic-rerank.md §8.2). Required, like `predictionStore`.
    /// `rerankRunner`: runs rerank jobs off the main thread (docs/spec/semantic-rerank.md §8.1). Required, with no default: the app passes
    /// `QueueRerankRunner`, the tests a runner they complete by hand.
    /// `profileModeStore`, `appProfileStore`: how chat or formal is chosen and the per-app choices (docs/spec/macos-shell.md §5.2).
    /// Required, like `glassTintStore`.
    /// `fuzzyStore`: the 模糊輸入 flags (docs/spec/decoder.md §6.1). The default (in memory, all off) is for the tests; the app passes its
    /// UserDefaults-backed store.
    /// `punctuationTable`: Apple's punctuation candidate table (s3e); tests pass another path.
    /// `punctuationNames`: Apple's punctuation names (s3f); tests pass another path.
    /// `spacingStore`: the 中英文之間自動加空格 switch (docs/spec/english-mixed.md §9, §11). Required, with no default, like the other stores.
    /// `shiftToggleStore`, `shiftHint`: the 單按 Shift 切換中英 switch and its 英/中 hint window (docs/spec/keys-and-composition.md §9.1, §9.4). The defaults (in
    /// memory, no window) are for the tests; the app passes its own.
    /// `diagLogStore`: the 記錄偶發問題的診斷資料 switch (docs/spec/macos-shell.md §13); the default (in memory, on) is for the tests.
    /// `englishNoticeStore`, `englishNotice`: the one-time explanation window of the English defaults change and whether it is still owed
    /// (docs/spec/english-mixed.md §10.3). The defaults (in memory, not owed, no window) are for the tests; the app passes its own.
    /// `showsAbbreviation`: whether the abbreviation switch is offered. The default `true` is for the tests; the app passes `false`
    /// (v0.4.0). No test sees the app's value: the release device check does (no 可省略韻母 in the menu).
    public init(resources: URL, panel: CandidatePanel, isSecureInput: @escaping () -> Bool,
                layoutStore: LayoutStore, learningDirectory: URL?, dialogs: LearningDialogs,
                demoteStore: DemoteStore, predictionStore: PredictionStore, abbreviationStore: AbbreviationStore, englishStore: EnglishStore, acgPackStore: AcgPackStore, quotePackStore: QuotePackStore,
                glassTintStore: GlassTintStore, candidateOrientationStore: CandidateOrientationStore,
                rerankStore: RerankStore, rerankRunner: RerankRunner,
                profileModeStore: ProfileModeStore, appProfileStore: AppProfileStore,
                fuzzyStore: FuzzyStore = MemoryFuzzyStore(),
                punctuationTable: URL = PunctuationTable.systemURL,
                punctuationNames: URL = PunctuationNames.systemURL,
                showsAbbreviation: Bool = true,
                spacingStore: SpacingStore,
                itPackStore: ItPackStore = MemoryItPackStore(),
                beautyPackStore: BeautyPackStore = MemoryBeautyPackStore(),
                shiftToggleStore: ShiftToggleStore = MemoryShiftToggleStore(),
                shiftHint: ShiftHintPanel? = nil,
                englishNoticeStore: EnglishNoticeStore = MemoryEnglishNoticeStore(),
                englishNotice: EnglishNoticePanel? = nil,
                diagLogStore: DiagLogStore = MemoryDiagLogStore()) {
        self.resources = resources
        diag = DiagLog(store: diagLogStore)
        self.shiftToggleStore = shiftToggleStore
        shiftToggleOn = shiftToggleStore.shiftToggle ?? false // docs/spec/english-mixed.md §10.1: was true
        self.shiftHint = shiftHint
        self.englishNoticeStore = englishNoticeStore
        self.englishNotice = englishNotice
        self.learningDirectory = learningDirectory
        self.dialogs = dialogs
        // Read once: the converted table never changes while the process runs, and the layout
        // switch that rebuilds the engine already blocks.
        punctuation = PunctuationTable.load(from: punctuationTable)
        if punctuation == nil {
            Log.shell.notice("punctuation candidates: system table unavailable, using the built-in list")
        }
        names = PunctuationNames.load(from: punctuationNames)
        if names.isEmpty {
            Log.shell.notice("punctuation names: system table unavailable, candidates show without names")
        }
        self.panel = panel
        self.isSecureInput = isSecureInput
        self.layoutStore = layoutStore
        self.demoteStore = demoteStore
        demoteOn = demoteStore.demote ?? true
        self.predictionStore = predictionStore
        predictionOn = predictionStore.prediction ?? true
        browserFirstSyllableOn = predictionStore.browserFirstSyllable ?? true
        self.abbreviationStore = abbreviationStore
        self.showsAbbreviation = showsAbbreviation
        abbreviationOn = showsAbbreviation && abbreviationStore.abbreviation == true
        self.spacingStore = spacingStore
        spacingOn = spacingStore.spacing == true
        self.englishStore = englishStore
        englishOn = englishStore.english ?? true // docs/spec/english-mixed.md §1.1, §11: on unless the person turned it off (a saved value wins)
        englishOverwriteOn = englishStore.englishOverwrite ?? false // docs/spec/english-mixed.md §10.1: was true
        englishKeyOrderOn = englishStore.englishKeyOrder ?? true // zhen-revert: was false; a saved false stays false
        englishReversalGuardOn = englishStore.englishReversalGuard ?? true
        self.fuzzyStore = fuzzyStore
        fuzzyFlags = (fuzzyStore.fuzzyFlags ?? 0) & 0b11111
        self.acgPackStore = acgPackStore
        acgDataDate = Shell.readAcgDataDate(resources.appendingPathComponent("packs/acg.json"))
        self.quotePackStore = quotePackStore
        self.glassTintStore = glassTintStore
        glassTint = glassTintStore.glassTint ?? 0
        self.candidateOrientationStore = candidateOrientationStore
        candidateVertical = candidateOrientationStore.candidateVertical ?? false
        self.rerankStore = rerankStore
        rerankOn = rerankStore.rerank ?? true
        rerankLevel = rerankStore.rerankLevel.flatMap(RerankLevel.init(rawValue:)) ?? .standard
        self.rerankRunner = rerankRunner
        self.profileModeStore = profileModeStore
        profileMode = profileModeStore.profileMode.flatMap(ProfileMode.init(rawValue:)) ?? .auto
        self.appProfileStore = appProfileStore
        appProfiles = AppProfiles.parse(appProfileStore.appProfiles)
        acgPackOn = acgPackStore.acgPack ?? true
        quotePackOn = quotePackStore.quotePack ?? false
        self.itPackStore = itPackStore
        itPackOn = itPackStore.itPack ?? true
        self.beautyPackStore = beautyPackStore
        beautyPackOn = beautyPackStore.beautyPack ?? true
        // The preference is read before the one engine is built (about 240 MB): building first
        // and switching after would build twice.
        mode = layoutStore.layout.flatMap(InputMode.init(rawValue:)) ?? .standard
        build()
        panel.onSelect = { [weak self] i in self?.owner?.candidateSelected(at: i) }
    }

    /// The current layout (the menu's checkmark).
    public var layout: InputMode { mode }

    /// The menu's choice (docs/spec/macos-shell.md §7): the existing switch (commit, rebuild), then the choice is
    /// stored.
    public func selectLayout(_ newMode: InputMode) {
        switchMode(to: newMode)
        layoutStore.layout = newMode.rawValue
        changed()
    }

    /// Set by the app to show the settings window; the menu's 善解設定… calls it.
    public var onOpenSettings: (() -> Void)?
    /// Set by the app (docs/spec/install-and-update.md): the update state the menu and the 關於 page read; `nil` in the tests
    /// that do not set it.
    public var updates: Updates?
    func openSettings() { onOpenSettings?() }

    /// docs/spec/english-mixed.md §10.3: any session's activation, or a keyDown that changes the owner with no activation (right
    /// after the process starts), arms the window while it is still owed. A Shift tap or menu item that changes the owner does not.
    func armEnglishNotice() {
        if englishNotice != nil && englishNoticeStore.pending { englishNoticeArmed = true }
    }

    /// Shows the window if armed and not already on screen. A button or the close button clears the owed flag, hides the window and
    /// (打開設定) opens the settings on the 選字 page.
    func showEnglishNoticeIfArmed(caret: @autoclosure () -> NSRect?) {
        guard englishNoticeArmed, let panel = englishNotice, !panel.isShown else { return }
        englishNoticeArmed = false
        panel.show(caret: caret()) { [weak self] openSettings in
            guard let self else { return }
            self.englishNoticeStore.pending = false
            self.englishNoticeArmed = false   // an activate while it was on screen re-armed it; after a button nothing shows again
            self.englishNotice?.hide()
            if openSettings { self.onOpenCandidateSettings?() }
        }
    }

    /// Any session's deactivation only hides the window; the owed flag and `englishNoticeArmed` stay.
    func hideEnglishNotice() { englishNotice?.hide() }

    /// The menu's switches, for a caller with no session (the settings window): the owner of the
    /// composition, if any, shows the re-ranked snapshot exactly as for the menu; otherwise just the setter.
    func applyDemote(_ on: Bool) {
        if let o = owner { o.applyDemote(on) } else { dropOnFailure(setDemote(on)) }
    }

    func applyPrediction(_ on: Bool) {
        if let o = owner { o.applyPrediction(on) } else { dropOnFailure(setPrediction(on)) }
    }

    func applyAbbreviation(_ on: Bool) {
        if let o = owner { o.applyAbbreviation(on) } else { dropOnFailure(setAbbreviation(on)) }
    }

    func applyEnglish(_ on: Bool) {
        if let o = owner { o.applyEnglish(on) } else { dropOnFailure(setEnglish(on)) }
    }

    func applyFuzzy(_ flags: UInt32) {
        if let o = owner { o.applyFuzzy(flags) } else { dropOnFailure(setFuzzy(flags)) }
    }

    func applyCandidateVertical(_ on: Bool) {
        if let o = owner { o.applyCandidateVertical(on) } else { dropOnFailure(setCandidateVertical(on)) }
    }

    func applyEnglishOverwrite(_ on: Bool) {
        if let o = owner { o.applyEnglishOverwrite(on) } else { dropOnFailure(setEnglishOverwrite(on)) }
    }

    func applyEnglishKeyOrder(_ on: Bool) {
        if let o = owner { o.applyEnglishKeyOrder(on) } else { dropOnFailure(setEnglishKeyOrder(on)) }
    }

    func applyEnglishReversalGuard(_ on: Bool) {
        if let o = owner { o.applyEnglishReversalGuard(on) } else { dropOnFailure(setEnglishReversalGuard(on)) }
    }

    func applyRerank(_ on: Bool) {
        if let o = owner { o.applyRerank(on) } else { dropOnFailure(setRerank(on)) }
    }

    func applyRerankLevel(_ level: RerankLevel) {
        if let o = owner { o.applyRerankLevel(level) } else { dropOnFailure(setRerankLevel(level)) }
    }

    /// No session to ask: a failed core call is logged and the composition dropped, as `Session.fail` does.
    private func dropOnFailure(_ r: CoreResult?) {
        guard case .failed(let c)? = r else { return }
        Log.shell.error("core call failed, code \(c)")
        discardOrphan()
    }

    /// The clear with its confirmation window (docs/spec/learning.md §10), for the menu and the settings window.
    /// `done` runs after the answer, cleared or not.
    func confirmAndClear(done: (@MainActor () -> Void)? = nil) {
        dialogs.confirmClear { [weak self] clear in
            if clear { self?.clearLearning() }
            done?()
        }
    }

    func changed() { NotificationCenter.default.post(name: Shell.didChangeSettings, object: self) }

    /// The slider value 0...1 (docs/spec/macos-shell.md §8.7); unset is 0. Cached like the other settings.
    public private(set) var glassTint: Double = 0

    public func setGlassTint(_ value: Double) {
        glassTint = value
        glassTintStore.glassTint = value
        panel.setGlassTint(value)  // docs/spec/macos-shell.md §8.7: a panel that is up changes at once
        changed()
    }

    /// The settings window's 切換方式 (docs/spec/macos-shell.md §5.1): stored, posted, then re-applied to the owner of the
    /// composition, if any, so what Enter commits matches the menu's checkmark at once.
    public func setProfileMode(_ m: ProfileMode) {
        profileMode = m
        profileModeStore.profileMode = m.rawValue
        changed()
        reapplyProfileToOwner()
    }

    /// The settings window's remove button: the app goes back to `chatApps`; re-applied like `setProfileMode`.
    public func removeAppProfile(_ bundle: String) {
        appProfiles[bundle] = nil
        appProfileStore.setAppProfiles(AppProfiles.stored(appProfiles))
        changed()
        reapplyProfileToOwner()
    }

    /// docs/spec/macos-shell.md §5.3: no claim, the owner already owns the composition (as the window's other switches go through the owner).
    private func reapplyProfileToOwner() {
        guard let o = owner else { return }
        o.applyProfile(bundle: o.client.bundleIdentifier)
    }

    /// The menu's choice for one app (docs/spec/macos-shell.md §5.3): a value equal to `chatApps`' removes the entry, so the list keeps only the
    /// apps that differ. The caller re-applies the profile and posts the change.
    func setAppProfile(_ bundle: String, _ p: UInt32) {
        appProfiles[bundle] = p == (Shell.chatApps.contains(bundle) ? Shell.chat : Shell.formal) ? nil : p
        appProfileStore.setAppProfiles(AppProfiles.stored(appProfiles))
    }

    /// The menu's choice (sw): stored, and sent to the engine, which recomputes the composition; the
    /// snapshot is for the caller to show (like `setProfile`).
    func setDemote(_ on: Bool) -> CoreResult? {
        demoteOn = on
        demoteStore.demote = on
        defer { changed() }
        return engine?.setDemote(on)
    }

    /// The menu's choice (docs/spec/prediction.md §7): stored, and sent to the engine; the snapshot is for the caller to show.
    func setPrediction(_ on: Bool) -> CoreResult? {
        predictionOn = on
        predictionStore.prediction = on
        defer { changed() }
        return engine?.setPrediction(on)
    }

    /// The settings window's browser switch (docs/spec/prediction.md §9.4): the shell's own switch, nothing goes to the core.
    func setBrowserFirstSyllable(_ on: Bool) {
        browserFirstSyllableOn = on
        predictionStore.browserFirstSyllable = on
        changed()
    }

    /// The settings window's 中英文之間自動加空格 (docs/spec/english-mixed.md §9, §11): the shell's own switch, nothing goes to the core.
    func setSpacing(_ on: Bool) {
        spacingOn = on
        spacingStore.spacing = on
        changed()
    }

    /// The menu's choice (docs/spec/prediction.md §6): stored, and sent to the engine; the snapshot is for the caller to show.
    func setAbbreviation(_ on: Bool) -> CoreResult? {
        guard showsAbbreviation else { return nil }
        abbreviationOn = on
        abbreviationStore.abbreviation = on
        defer { changed() }
        return engine?.setAbbreviation(on)
    }

    /// The menu's choice (docs/spec/english-mixed.md §1.1, §11): stored, and sent to the engine; the snapshot is for the caller to show (turning
    /// it off closes an open English window).
    func setEnglish(_ on: Bool) -> CoreResult? {
        englishOn = on
        englishStore.english = on
        defer { changed() }
        return engine?.setEnglish(on)
    }

    /// The settings window's 模糊輸入 flags (docs/spec/decoder.md §6.1): stored, and sent to the engine, which recomputes the composition
    /// (an accepted syllable keeps decoding whatever the flags are); the snapshot is for the caller to show.
    func setFuzzy(_ flags: UInt32) -> CoreResult? {
        fuzzyFlags = flags & 0b11111
        fuzzyStore.fuzzyFlags = fuzzyFlags
        defer { changed() }
        return engine?.setFuzzy(fuzzyFlags)
    }

    /// The settings window's 重打同一個位置算英文 choice (docs/spec/english-mixed.md §11): stored, and sent to the engine; the snapshot is for the
    /// caller to show (nothing on screen changes: an English run is text).
    func setEnglishOverwrite(_ on: Bool) -> CoreResult? {
        englishOverwriteOn = on
        englishStore.englishOverwrite = on
        defer { changed() }
        return engine?.setEnglishOverwrite(on)
    }

    /// The settings window's 依按鍵順序判斷英文 choice (docs/spec/english-mixed.md §11): stored, and sent to the engine; the snapshot is for the
    /// caller to show (nothing on screen changes).
    func setEnglishKeyOrder(_ on: Bool) -> CoreResult? {
        englishKeyOrderOn = on
        englishStore.englishKeyOrder = on
        defer { changed() }
        return engine?.setEnglishKeyOrder(on)
    }

    /// The settings window's 快打時鍵序顛倒不當英文 choice (docs/spec/english-mixed.md §1.1, §3.2): stored, and sent to the engine; the snapshot is for
    /// the caller to show (nothing on screen changes).
    func setEnglishReversalGuard(_ on: Bool) -> CoreResult? {
        englishReversalGuardOn = on
        englishStore.englishReversalGuard = on
        defer { changed() }
        return engine?.setEnglishReversalGuard(on)
    }

    /// The settings window's choice (docs/spec/macos-shell.md §8.6.1): stored, and sent to the engine, which applies it
    /// to the next window that opens. An open candidate window keeps its orientation; a prediction row on screen is re-rendered
    /// in the snapshot, for the caller to show. In the app only the settings window calls this, and a click there is inferred
    /// (not measured) to commit the composition and hide the panel, so the row case is reachable through the API and the tests
    /// but not observable from the window as shipped.
    func setCandidateVertical(_ on: Bool) -> CoreResult? {
        candidateVertical = on
        candidateOrientationStore.candidateVertical = on
        defer { changed() }
        return engine?.setCandidateVertical(on)
    }

    /// The menu's choice (docs/spec/semantic-rerank.md §8.2): stored, and sent to the engine, which recomputes the composition (off: the n-gram
    /// result at once); the snapshot is for the caller to show. A job waiting for the runner is dropped; one running is not applied.
    func setRerank(_ on: Bool) -> CoreResult? {
        rerankOn = on
        rerankStore.rerank = on
        if !on { rerankPending = nil }
        defer { changed() }
        return engine?.setRerank(on)
    }

    /// The settings window's 強度 (docs/spec/semantic-rerank.md §8.2): stored, and sent to the engine, which recomputes the composition;
    /// the snapshot is for the caller to show (its job uses the new level; one already taken is stale in the core).
    func setRerankLevel(_ level: RerankLevel) -> CoreResult? {
        rerankLevel = level
        rerankStore.rerankLevel = level.rawValue
        defer { changed() }
        guard let engine else { return nil }
        rerankLevelSent.append(level.code)
        return engine.setRerankLevel(level.code)
    }

    /// docs/spec/semantic-rerank.md §8.1: a job taken for `session` from `engine` runs now, or waits if another is running (only the latest waits).
    func enqueueRerank(_ job: RerankJob, session: Session, engine: CoreEngine) {
        rerankTaken += 1
        let pending = PendingRerank(job: job, session: session, engine: engine)
        if rerankRunning != nil { rerankPending = pending } else { startRerank(pending) }
    }

    private func startRerank(_ p: PendingRerank) {
        let slot = RunningRerank(p)
        rerankRunning = slot
        slot.handle = rerankRunner.run(slot.job) { [weak self, slot] code in
            self?.rerankFinished(slot, code: code)
        }
    }

    /// On the main thread once run returned: from the runner's `done`, or from Enter's bounded wait. Applied only while the switch
    /// is on, the session still owns the composition and the engine is still the current one (weak references compared with ===);
    /// the core's generation check is the second line.
    private func rerankFinished(_ slot: RunningRerank, code: Int32) {
        // A job is finished once. The second call (the main.async one after Enter's wait got the code, or one from an engine
        // rebuilt meanwhile) finds another job or none here and does nothing: no apply, no flag clear, no second start.
        guard rerankRunning === slot else { return }
        // whatever is returned below, the running state is cleared and a waiting job starts.
        defer {
            rerankRunning = nil
            if let next = rerankPending {
                rerankPending = nil
                startRerank(next)
            }
        }
        guard code == 0 else {
            Log.shell.error("shanjie_rerank_run failed, code \(code)")
            return
        }
        guard rerankOn, let session = slot.session, session === owner, let engine = slot.engine, engine === self.engine else { return }
        session.applyRerankJob(slot.job, on: engine)
    }

    /// Enter's bounded wait (docs/spec/keys-and-composition.md §7.3): while a job of `session` for the current engine runs or waits, block
    /// the main thread on the runner until it is done and apply it (`rerankFinished`, the same path as the async completion), all
    /// within one deadline of `enterRerankWaitMS` taken now. No nested run loop and no unbounded wait: after the deadline the jobs
    /// are left as they are and Enter commits what is shown. Not when the core would not apply (a selection is open).
    func waitForRerank(session: Session) {
        func mine(_ s: Session?, _ e: CoreEngine?) -> Bool { s === session && e != nil && e === engine }
        func relevant() -> Bool {
            rerankOn && composing && candidateSelected < 0
                && (rerankRunning.map { mine($0.session, $0.engine) } ?? false || rerankPending.map { mine($0.session, $0.engine) } ?? false)
        }
        guard relevant() else { return }
        let deadline = DispatchTime.now() + .milliseconds(enterRerankWaitMS)
        while relevant(), let slot = rerankRunning, let handle = slot.handle, let code = rerankRunner.wait(handle, until: deadline) {
            rerankFinished(slot, code: code)
        }
    }

    /// The settings switch (docs/spec/lexicon-and-packs.md §4.2; it is not in the menu): the pack is part of the lexicon, so like a layout change the
    /// composition is committed and the engine rebuilt; the choice is stored. Same value: nothing happens.
    func setAcgPack(_ on: Bool) {
        guard on != acgPackOn else { return }
        commitThenRebuild {
            acgPackOn = on
            acgPackStore.acgPack = on
        }
        changed()
        Log.shell.debug("word pack switched")
    }

    /// The 經典台詞 switch (docs/spec/lexicon-and-packs.md §7.2, §4.1, §4.2): like `setAcgPack`, the pack is part of the lexicon, so the composition
    /// is committed and the engine rebuilt; the choice is stored. Same value: nothing happens.
    func setQuotePack(_ on: Bool) {
        guard on != quotePackOn else { return }
        commitThenRebuild {
            quotePackOn = on
            quotePackStore.quotePack = on
        }
        changed()
        Log.shell.debug("quote pack switched")
    }

    /// The settings switch (docs/spec/lexicon-and-packs.md §4.2, §4.3): the same as `setAcgPack` for the IT+AI pack.
    func setItPack(_ on: Bool) {
        guard on != itPackOn else { return }
        commitThenRebuild {
            itPackOn = on
            itPackStore.itPack = on
        }
        changed()
        Log.shell.debug("it pack switched")
    }

    /// The settings switch (docs/spec/lexicon-and-packs.md §4.2, §8.3): the same as `setItPack` for the beauty and skincare pack.
    func setBeautyPack(_ on: Bool) {
        guard on != beautyPackOn else { return }
        commitThenRebuild {
            beautyPackOn = on
            beautyPackStore.beautyPack = on
        }
        changed()
        Log.shell.debug("beauty pack switched")
    }

    /// Whether the core records the shadow and has an English run to keep a composition in (its `shadow_on()`): English mixing on, the
    /// standard layout and the abbreviation composer off. Without it a switch to English first sends the composition (docs/spec/keys-and-composition.md §9.2).
    var shadowOn: Bool { englishOn && mode == .standard && !abbreviationOn }

    /// The settings window's 單按 Shift 切換中英: stored; off clears every app's state and the core's (docs/spec/keys-and-composition.md §9.1). The snapshot is shown by the owner.
    func applyShiftToggle(_ on: Bool) {
        shiftToggleOn = on
        shiftToggleStore.shiftToggle = on
        if !on {
            asciiApps.removeAll()
            shiftTap = ShiftTap()
            if let o = owner { o.pushAscii(false) } else if engineAscii {
                engineAscii = false
                diag.record(.ascii(bundle: "-", english: false))
                dropOnFailure(engine?.setAscii(false))
            }
        }
        changed()
    }

    /// Creates the engine for the current mode, then loads the LM and the current profile. A
    /// failure leaves an engine without the LM (still usable) or no engine (every key passes).
    private func build() {
        engine = nil  // free the old engine first: only one exists at a time
        rerankAvailable = false
        rerankLevels = 0
        rerankPending = nil  // the old engine's; one still running is not applied (its engine is gone)
        loadedPackMask = 0
        let packsDir = resources.appendingPathComponent("packs").path
        let mask = packMask
        // A missing pack file contributes nothing (the core builds without it), so only bits whose file is there count as loaded.
        let present = Shell.packTable.reduce(UInt32(0)) { FileManager.default.fileExists(atPath: packsDir + "/" + $1.file) ? $0 | $1.bit : $0 }
        var built = mask & present
        var (made, code) = makeEngine(packsDir: mask != 0 ? packsDir : nil, packs: mask)
        if made == nil && mask != 0 {
            // The combined build failed, so the first code alone does not say whose fault it is. (a) Build with no packs, only to learn whether
            // it works, and free it at once (build() keeps one engine at a time): if it fails too the cause is not a pack, both codes are logged
            // and these two builds are all there are. (b) Otherwise a pack is broken: for each enabled pack in the settings order build with just
            // that pack dropped (mask & ~bit); the first success wins, so the healthy packs stay on. (c) If no single drop works (two broken
            // files), build the no-pack engine again. The stored choices are untouched and nothing is shown; the next rebuild tries the packs again.
            let packCode = code
            built = 0
            var noPackWorks = false
            do {
                let (probe, probeCode) = makeEngine(packsDir: nil, packs: 0)
                noPackWorks = probe != nil
                code = probeCode
            }   // the probe engine is freed here
            if noPackWorks {
                var kept: UInt32 = 0
                for bit in Shell.packTable.map(\.bit) where mask & bit != 0 && mask & ~bit != 0 {
                    let (dropped, _) = makeEngine(packsDir: packsDir, packs: mask & ~bit)
                    if let dropped {
                        made = dropped
                        built = mask & ~bit & present
                        kept = mask & ~bit
                        break
                    }
                }
                if made == nil { (made, code) = makeEngine(packsDir: nil, packs: 0) }
                Log.shell.error("shanjie_engine_new_packs failed, code \(packCode); built with packs mask \(kept)")
            } else {
                Log.shell.error("shanjie_engine_new_packs failed, code \(packCode); the build without packs failed too")
            }
        }
        guard let e = made else {
            Log.shell.error("shanjie_engine_new failed, code \(code)")
            return
        }
        loadedPackMask = built
        let lm = e.loadLM(path: resources.appendingPathComponent("bigram.sjlm").path)
        if lm != 0 { Log.shell.error("shanjie_engine_load_lm failed, code \(lm)") }
        // docs/spec/semantic-rerank.md §8.2, §8.3: after load_lm (which drops a small model). Missing or failed: the n-gram alone, no rerank.
        if lm == 0 {
            let tiny = e.loadTinyLM(path: resources.appendingPathComponent(Shell.tinyLMFile).path)
            if tiny != 0 { Log.shell.error("shanjie_engine_load_tinylm failed, code \(tiny); no rerank") }
            rerankAvailable = tiny == 0
            // docs/spec/semantic-rerank.md §4.6: after load_tinylm, and only when it loaded (the term applies to the small model's rerank, so there is
            // nothing to load it for otherwise; "no rerank" is already logged above). A failure is logged (the code only) and the rerank runs without the term.
            if tiny == 0 {
                let colloquial = e.loadColloquial(path: resources.appendingPathComponent(Shell.colloquialFile).path)
                if colloquial != 0 { Log.shell.error("shanjie_engine_load_colloquial failed, code \(colloquial); rerank without the colloquial term") }
            }
        }
        if case .failed(let c) = e.setDemote(demoteOn) {
            Log.shell.error("shanjie_engine_set_demote failed, code \(c)")
        }
        if case .failed(let c) = e.setPrediction(predictionOn) {
            Log.shell.error("shanjie_engine_set_prediction failed, code \(c)")
        }
        // A rebuilt engine (layout, word pack) starts with the core's default, so this is sent every time (docs/spec/prediction.md §6).
        if case .failed(let c) = e.setAbbreviation(abbreviationOn) {
            Log.shell.error("shanjie_engine_set_abbreviation failed, code \(c)")
        }
        // And the English mixing switch, every time: a rebuilt engine (layout, word pack) starts with the core's default, off.
        if case .failed(let c) = e.setEnglish(englishOn) {
            Log.shell.error("shanjie_engine_set_english failed, code \(c)")
        }
        // And the overwrite choice (docs/spec/english-mixed.md §11), every time for the same reason.
        if case .failed(let c) = e.setEnglishOverwrite(englishOverwriteOn) {
            Log.shell.error("shanjie_engine_set_english_overwrite failed, code \(c)")
        }
        // And the ASCII state of the app being served (docs/spec/keys-and-composition.md §9.2): a rebuilt engine starts with it off.
        lastBuiltAscii = engineAscii
        if case .failed(let c) = e.setAscii(engineAscii) {
            Log.shell.error("shanjie_engine_set_ascii failed, code \(c)")
        }
        // And the key-order choice (docs/spec/english-mixed.md §11), every time.
        if case .failed(let c) = e.setEnglishKeyOrder(englishKeyOrderOn) {
            Log.shell.error("shanjie_engine_set_english_key_order failed, code \(c)")
        }
        // And the reversal guard (docs/spec/english-mixed.md §1.1, §3.2), every time.
        if case .failed(let c) = e.setEnglishReversalGuard(englishReversalGuardOn) {
            Log.shell.error("shanjie_engine_set_english_reversal_guard failed, code \(c)")
        }
        // And the 模糊輸入 flags (docs/spec/decoder.md §6.1), every time: the core's default is 0.
        fuzzySent.append(fuzzyFlags)
        if case .failed(let c) = e.setFuzzy(fuzzyFlags) {
            Log.shell.error("shanjie_engine_set_fuzzy failed, code \(c)")
        }
        // Same for the candidate window's orientation (docs/spec/macos-shell.md §8.6.2).
        if case .failed(let c) = e.setCandidateVertical(candidateVertical) {
            Log.shell.error("shanjie_engine_set_candidate_vertical failed, code \(c)")
        }
        // A rebuilt engine starts with the core's default (off since S7c-2c), and load_tinylm leaves the switch alone: the shell's
        // own value is sent every time, so the core's default never decides.
        rerankSent.append(rerankOn)
        if case .failed(let c) = e.setRerank(rerankOn) {
            Log.shell.error("shanjie_engine_set_rerank failed, code \(c)")
        }
        // Rerank strength (docs/spec/semantic-rerank.md §8.2): after load_tinylm and before `engine = e`, the stored level, every build (the core
        // starts at standard); then what the loaded model offers, for the settings window.
        rerankLevelSent.append(rerankLevel.code)
        if case .failed(let c) = e.setRerankLevel(rerankLevel.code) {
            Log.shell.error("shanjie_engine_set_rerank_level failed, code \(c)")
        }
        rerankLevels = e.rerankLevels()
        if case .failed(let c) = e.setProfile(profile) {
            Log.shell.error("shanjie_engine_set_profile failed, code \(c)")
        }
        // s3e: the system's punctuation candidates; without them the core's built-in list stays.
        if let table = punctuation {
            let code = e.setPunctuation(table)
            if code != 0 { Log.shell.error("punctuation candidates: core rejected the system table, code \(code)") }
        }
        // S4: learning stays off (the core's default) until send() or finish() samples the gate.
        if let dir = learningDirectory {
            let code = e.learningOpen(dir: dir.path)
            learningOpenFailed = code != 0
            if code != 0 { Log.shell.error("shanjie_engine_learning_open failed, code \(code)") }
        }
        engine = e
    }

    /// docs/spec/macos-shell.md §2.2: commit the composition to its owner, free the engine, rebuild with the new
    /// layout, reload the LM and the current profile.
    func switchMode(to newMode: InputMode) {
        guard newMode != mode else { return }
        commitThenRebuild { mode = newMode }
        Log.shell.debug("input mode switched")
    }

    /// What a setting that lives in the engine's construction (layout, word pack) does: commit the
    /// composition to its owner (or drop an orphan), apply the change, build a new engine.
    private func commitThenRebuild(_ apply: () -> Void) {
        if composing {
            if let o = owner { o.finish(mode: 0) } else { discardOrphan() }
        }
        apply()
        build()
    }

    func setProfile(_ p: UInt32) -> CoreResult? {
        profile = p
        return engine?.setProfile(p)
    }

    func showCandidates(_ list: [String], selected: Int, columns: Int, first: Int, total: Int, vertical: Int, lineRect: NSRect?,
                        appearance: NSAppearance?) {
        // Only an exact punctuation mark has a name: a word candidate is never a key of the table.
        let notes = list.map { names[$0] }
        shownVertical = vertical
        candidateSelected = selected
        hintShown = false
        panel.show(list, notes: notes, selected: selected, columns: columns, first: first, total: total, vertical: vertical,
                   lineRect: lineRect, appearance: appearance, glassTint: glassTint)
    }

    /// The hint shown while an English run is open (docs/spec/english-mixed.md §4.6).
    static let englishRunHint = "空白：英文斷開"

    /// The hint in the candidate window's place, in the prediction row's orientation. It is not a candidate list, so Page Up /
    /// Down see no vertical window (`verticalOpen` is false for the prediction row).
    func showHint(lineRect: NSRect?, appearance: NSAppearance?) {
        shownVertical = candidateVertical ? VerticalLayout.predictions : 0
        candidateSelected = -1
        hintShown = true
        panel.showHint(Self.englishRunHint, vertical: shownVertical, lineRect: lineRect, appearance: appearance, glassTint: glassTint)
    }

    func hideCandidates() {
        lineCache = nil
        hintAnchor = nil
        hintShown = false
        shownVertical = 0
        candidateSelected = -1
        panel.hide()
    }

    /// Discards a composition whose owner is gone; there is no client left to clear.
    func discardOrphan() {
        if case .failed(let c) = engine?.reset(mode: 1) {
            Log.shell.error("shanjie_engine_reset failed, code \(c)")
        }
        composing = false
        hideCandidates()
    }
}

/// One IMK controller's view of the shell: its client. The app's IMKInputController forwards
/// every callback here; tests drive it with fake clients, and releasing a Session is IMK
/// releasing the controller.
@MainActor
public final class Session {
    let shell: Shell
    let client: TextClient
    /// docs/spec/english-mixed.md §9: the character before the cursor as far as this session knows it; `nil` when unknown. Cleared when
    /// a composition may start, read from the client by the key that may start it, set to the last character of every commit,
    /// cleared by a pass-through key, a deactivation and an owner change. Never logged.
    private var spacingPrev: Character?
    /// docs/spec/prediction.md §9.4: this client's bundle ID as last read by `send` or passed to `applyProfile`; never read
    /// for this rule alone.
    private var bundle: String?
    /// docs/spec/keys-and-composition.md §9.2: the last non-empty bundle ID `activate`, an owner change in `handle` or `toggleAscii` read. Only the key of
    /// the English state falls back to it when a read gives nil; nothing else (profile, learning, menu) uses it.
    private var knownBundle: String?

    public init(shell: Shell, client: TextClient) {
        self.shell = shell
        self.client = client
    }

    /// docs/spec/macos-shell.md §2.2: weak references already read nil here, so an owner that reads nil while the
    /// composition is non-empty means this session (or an earlier owner) is gone: discard.
    isolated deinit {
        if shell.owner == nil && shell.composing { shell.discardOrphan() }
    }

    // MARK: IMK callbacks

    /// Returns whether the key was consumed. `event` is nil when IMK passes nil.
    public func handle(_ event: NSEvent?) -> Bool {
        guard shell.engine != nil else { return false }
        if let event, event.type == .flagsChanged { return handleFlags(event) }
        guard event == nil || event?.type == .keyDown else { return false }
        // docs/spec/english-mixed.md §10.3: the key is handled as usual first; the window (when armed) shows after it.
        defer { shell.showEnglishNoticeIfArmed(caret: currentLineRect()) }
        shell.shiftTap.keyDown(shiftHeld: event?.modifierFlags.contains(.shift) ?? false)  // docs/spec/keys-and-composition.md §9.1: a key between Shift's press and release spoils the tap
        if claim() {
            let bundle = client.bundleIdentifier  // a synchronous IPC: read once for both
            shell.diag.record(.owner(bundle: DiagLog.clean(bundle)))
            applyProfile(bundle: bundle)
            syncAscii(bundle: bundle)
            shell.lastTypingAt = nil  // docs/spec/english-mixed.md §11: an owner change without activateServer counts too
            // docs/spec/english-mixed.md §10.3: for the notice too. Measured in a tart VM (2026-11-05): right after the process
            // starts, the app being typed in gets its keys with no activateServer, so arming only on activate never showed it.
            shell.armEnglishNotice()
        }
        // docs/spec/english-mixed.md §11: the first typing key after the input method was activated shows which mode is on, after the key
        // is handled (so a key that starts a composition anchors the hint to the marked text), only while the toggle is still on.
        // Due after an activation or owner change (nil) or a pause of at least `modeHintIdleSeconds` (docs/spec/keys-and-composition.md §9.4), and only when
        // typing starts (nothing composed: a candidate pick after a long look is not the start of typing).
        var showModeHint = false
        if let event, Self.isTypingKey(event) {
            let now = shell.idleClock()
            let due = shell.lastTypingAt.map { now - $0 >= Shell.modeHintIdleSeconds } ?? true
            shell.lastTypingAt = now
            showModeHint = due && !shell.composing && shell.shiftToggleOn
        }
        defer { if showModeHint { showShiftHint(shell.engineAscii ? "英" : "中") } }
        // docs/spec/keys-and-composition.md §9.2: English state with nothing composed: every key goes to the app untouched, not through the core.
        if shell.engineAscii && !shell.composing { return false }
        // docs/spec/macos-shell.md §8.6.2: Page Up / Down reach the core only while the vertical window is on screen
        // and no modifier (Shift, Control, Option, Command, Caps Lock) is held; otherwise they are keys outside the tables.
        if let event, shell.verticalOpen, KeyMap.modifiers(event.modifierFlags) == 0, let kind = KeyMap.pageKeys[event.keyCode] {
            return send(ShanjieKey(kind: kind, ch: 0, modifiers: 0))
        }
        guard let event,
              let key = (shell.engineAscii && shell.shadowOn ? Self.asciiKey(event) : nil) ?? KeyMap.translate(keyCode: event.keyCode, flags: event.modifierFlags)
        else {
            // nil, or a key outside the tables: never sent to the core.
            if shell.composing { finish(mode: 0) }
            return false
        }
        // docs/spec/keys-and-composition.md §7.3: after claim/applyProfile/syncAscii (a profile change would stale the job), right before
        // the Enter reaches the core, the shown composition catches up with the rerank that is running for it, within the limit.
        if key.kind == KeyMap.enter { shell.waitForRerank(session: self) }
        return send(key)
    }

    /// docs/spec/keys-and-composition.md §9.2: with a composition open in the English state, a printable key is sent as the character the keyboard
    /// layout made of it (`event.characters`: Shift+1 is `!`), with no modifier bits. `nil` for anything else (Control, Option,
    /// Command and Caps Lock keys, a non-ASCII or multi-character result, the space bar): those take the usual table.
    static func asciiKey(_ event: NSEvent) -> ShanjieKey? {
        let flags = event.modifierFlags
        guard flags.isDisjoint(with: [.control, .option, .command, .capsLock]),
              let chars = event.characters, chars.unicodeScalars.count == 1, let c = chars.unicodeScalars.first,
              (0x21...0x7E).contains(c.value) else { return nil }
        return ShanjieKey(kind: KeyMap.char, ch: c.value, modifiers: 0)
    }

    /// docs/spec/keys-and-composition.md §9.1: a `flagsChanged` event is never consumed (the app sees Shift as always); a lone tap switches.
    private func handleFlags(_ event: NSEvent) -> Bool {
        guard shell.shiftToggleOn else { return false }
        let flags = event.modifierFlags
        let others = !flags.isDisjoint(with: [.control, .option, .command, .function])
        // The state of this Shift key from the flags: the device bits (NX_DEVICELSHIFTKEYMASK 0x2, NX_DEVICERSHIFTKEYMASK 0x4), or the
        // generic bit for an event that carries none.
        let raw = flags.rawValue
        let generic = flags.contains(.shift)
        let hasDevice = raw & 0x6 != 0
        let leftDown = hasDevice ? raw & 0x2 != 0 : generic && event.keyCode == 56
        let rightDown = hasDevice ? raw & 0x4 != 0 : generic && event.keyCode == 60
        let outcome = shell.shiftTap.event(keyCode: event.keyCode, leftDown: leftDown, rightDown: rightDown, others: others,
                                           capsLock: flags.contains(.capsLock), now: event.timestamp, processedAt: shell.uptime(),
                                           mouseAgo: shell.mouseAgo)
        // docs/spec/macos-shell.md §13.1: never during secure input (each Shift would tell how many characters of a password needed it).
        if let outcome, shell.diag.enabled, !shell.isSecureInput() {
            shell.diag.record(.shift(right: event.keyCode == 60, outcome: outcome))
        }
        if outcome == .toggled { toggleAscii() }
        return false
    }

    /// docs/spec/keys-and-composition.md §9.2: this app goes to English or back to Chinese. A composition the core cannot keep as an English run (`shadowOn` false)
    /// is sent first. The hint shows which one (docs/spec/keys-and-composition.md §9.4).
    private func toggleAscii() {
        let bundle = client.bundleIdentifier  // a synchronous IPC: read once
        if claim() {
            shell.diag.record(.owner(bundle: DiagLog.clean(bundle)))
            applyProfile(bundle: bundle)
        }
        let app = asciiApp(bundle)
        let on = !shell.asciiApps.contains(app)
        if on && shell.composing && !shell.shadowOn { finish(mode: 0) }
        if on { shell.asciiApps.insert(app) } else { shell.asciiApps.remove(app) }
        pushAscii(on)
        shell.lastTypingAt = shell.idleClock()  // docs/spec/english-mixed.md §11: this hint already shows the mode
        let shown = showShiftHint(on ? "英" : "中")
        shell.diag.record(.toggle(bundle: DiagLog.clean(bundle), english: on, hintShown: shown))
    }

    /// docs/spec/english-mixed.md §11: a key that types: what `asciiKey` takes (no Control, Option, Command or Caps Lock, one character
    /// `!` to `~`; arrow and function keys carry private-use characters, so they do not count), or a plain space.
    static func isTypingKey(_ event: NSEvent) -> Bool {
        asciiKey(event) != nil || (event.characters == " " && event.modifierFlags.isDisjoint(with: [.control, .option, .command]))
    }

    /// Hands the state of the app being typed into to the core (activate, owner change, switch, setting off).
    func syncAscii(bundle: String?) {
        // The key first: `&&` would skip it while the toggle is off, and a session activated then would never learn its `knownBundle`.
        let app = asciiApp(bundle)
        pushAscii(shell.shiftToggleOn && shell.asciiApps.contains(app))
    }

    /// docs/spec/keys-and-composition.md §9.2: the `asciiApps` key for a bundle ID as the caller read it. A nil or empty read (a controller whose client
    /// cannot answer just now) takes this session's `knownBundle`, so a toggle does not land on the shared `""` entry; `""` only for a session
    /// that never read one (docs/spec/keys-and-composition.md §9.2).
    private func asciiApp(_ bundle: String?) -> String {
        if let bundle, !bundle.isEmpty {
            knownBundle = bundle
            return bundle
        }
        return knownBundle ?? ""
    }

    func pushAscii(_ on: Bool) {
        guard on != shell.engineAscii, let engine = shell.engine else { return }
        shell.engineAscii = on
        shell.diag.record(.ascii(bundle: DiagLog.clean(knownBundle), english: on))
        switch engine.setAscii(on) {
        case .ok(let o): apply(o)
        case .failed(let c): _ = fail(c)
        }
    }

    /// docs/spec/keys-and-composition.md §9.4: the hint above the cursor line: the marked text's rectangle while composing, the insertion point's otherwise. No
    /// rectangle, no hint (not the screen corner, not the last place). Returns whether it was shown (docs/spec/macos-shell.md §13.1).
    @discardableResult
    private func showShiftHint(_ text: String) -> Bool {
        guard let panel = shell.shiftHint else { return false }
        guard let rect = currentLineRect() else { return false }
        panel.show(text, lineRect: rect, appearance: client.appearance, glassTint: shell.glassTint)
        return true
    }

    /// The marked text's rectangle while composing, the insertion point's otherwise; `nil` when the client gives none.
    private func currentLineRect() -> NSRect? {
        shell.composing ? client.lineRect(cursor: shell.composingCursor) : client.caretRect()
    }

    /// docs/spec/macos-shell.md §2.2 order: ownership first, then the profile for this client's app.
    public func activate() {
        let claimed = claim()
        let bundle = client.bundleIdentifier
        if claimed { shell.diag.record(.owner(bundle: DiagLog.clean(bundle))) }
        applyProfile(bundle: bundle)
        syncAscii(bundle: bundle)
        shell.diag.record(.activate(bundle: DiagLog.clean(bundle), owner: shell.owner === self, english: shell.engineAscii))
        shell.lastTypingAt = nil  // docs/spec/english-mixed.md §11: the next typing key shows the mode (only while the toggle is on)
        shell.armEnglishNotice()  // docs/spec/english-mixed.md §10.3
    }

    /// Only the owner acts; a late call from any other controller does nothing at all.
    public func deactivate() {
        spacingPrev = nil
        shell.hideEnglishNotice()  // docs/spec/english-mixed.md §10.3: any deactivate, owner or not, only hides the window
        // docs/spec/macos-shell.md §13.1: no bundle read here; the one this session last read.
        shell.diag.record(.deactivate(bundle: DiagLog.clean(bundle), owner: shell.owner === self, english: shell.engineAscii))
        guard shell.owner === self else { return }
        // Never send the composition into a field that has just turned secure.
        if shell.composing { finish(mode: shell.isSecureInput() ? 1 : 0) }
        shell.hideCandidates()
    }

    public func commitComposition() {
        guard shell.owner === self else { return }
        if shell.composing { finish(mode: 0) }
        shell.hideCandidates()
    }

    /// A mouse click on a candidate (docs/spec/macos-shell.md §8.1): `i` is its position in the output the panel
    /// shows. The core picks it through the same path as Enter and checks the range (code 2 leaves
    /// everything as it was); the learning gate is sampled first, as for a key.
    /// Returns whether a pick was sent to the core (false: not the owner, or the hint is what the panel shows).
    @discardableResult
    public func candidateSelected(at i: Int) -> Bool {
        // The hint is on the panel, not a list: a click on it picks nothing and never reaches the core.
        guard shell.owner === self, let engine = shell.engine, !shell.hintShown else { return false }
        applyLearningGate(engine)
        switch engine.pick(UInt32(truncatingIfNeeded: i)) {
        case .ok(let o): apply(o)
        case .failed(2): Log.shell.error("core call failed, code 2")
        case .failed(let c): _ = fail(c)
        }
        return true
    }

    /// The input method menu (docs/spec/macos-shell.md §7).
    public var layout: InputMode { shell.layout }
    public func selectLayout(_ m: InputMode) { shell.selectLayout(m) }

    // MARK: internals

    /// docs/spec/macos-shell.md §2.2: a composition owned by another session goes back to that session's client if it
    /// is still alive, else it is discarded; then this session becomes the owner. Returns whether
    /// the owner changed.
    @discardableResult
    func claim() -> Bool {
        guard shell.owner !== self else { return false }
        if shell.composing {
            if let previous = shell.owner { previous.finish(mode: 0) } else { shell.discardOrphan() }
        }
        shell.owner?.spacingPrev = nil
        spacingPrev = nil
        shell.owner = self
        return true
    }

    /// The menu's demotion switch: the core recomputes the composition and the snapshot is shown, if this
    /// session owns the composition (a toggle mid-composition visibly re-ranks).
    func applyDemote(_ on: Bool) {
        switch shell.setDemote(on) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The menu's prediction switch: the snapshot is shown, if this session owns the composition.
    func applyPrediction(_ on: Bool) {
        switch shell.setPrediction(on) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The menu's 中英混打 switch: the snapshot is shown, if this session owns the composition.
    func applyEnglish(_ on: Bool) {
        switch shell.setEnglish(on) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The settings window's 重打同一個位置算英文 choice: the snapshot is shown, if this session owns the composition.
    func applyEnglishOverwrite(_ on: Bool) {
        switch shell.setEnglishOverwrite(on) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The settings window's 依按鍵順序判斷英文 choice: the snapshot is shown, if this session owns the composition (it is what was on screen).
    func applyEnglishKeyOrder(_ on: Bool) {
        switch shell.setEnglishKeyOrder(on) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The settings window's 快打時鍵序顛倒不當英文 choice: the snapshot is shown, if this session owns the composition.
    func applyEnglishReversalGuard(_ on: Bool) {
        switch shell.setEnglishReversalGuard(on) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The menu's abbreviation switch: the snapshot is shown, if this session owns the composition.
    func applyAbbreviation(_ on: Bool) {
        switch shell.setAbbreviation(on) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The settings window's 模糊輸入 flags: the snapshot is shown, if this session owns the composition.
    func applyFuzzy(_ flags: UInt32) {
        switch shell.setFuzzy(flags) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The orientation setting: the snapshot is shown, if this session owns the composition, but only when its `vertical` value
    /// differs from what the panel shows, i.e. a prediction row that turns (docs/spec/macos-shell.md §8.6.4). An open
    /// candidate window keeps its orientation and nothing is on screen otherwise, so those outputs are not applied again: a
    /// horizontal composition sees no client or panel call at all. The row case is reachable only through the API and the
    /// tests: the setting is only in the settings window, where a click is inferred (not measured) to commit the composition
    /// and hide the panel first.
    func applyCandidateVertical(_ on: Bool) {
        switch shell.setCandidateVertical(on) {
        case .ok(let o)? where shell.owner === self && o.vertical != shell.shownVertical: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The menu's 語意校正 switch: the snapshot is shown, if this session owns the composition.
    func applyRerank(_ on: Bool) {
        switch shell.setRerank(on) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The settings window's 強度: the snapshot is shown, if this session owns the composition.
    func applyRerankLevel(_ level: RerankLevel) {
        switch shell.setRerankLevel(level) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// docs/spec/semantic-rerank.md §8.1: a finished job, for the engine it was taken from (the shell checked that it is current). 0 shows the
    /// snapshot like any output; 5 changes nothing; anything else (4: the engine has reset itself) is handled like a failed key.
    func applyRerankJob(_ job: RerankJob, on engine: CoreEngine) {
        switch shell.rerankApply(job, engine) {
        case .ok(let o): apply(o)
        case .failed(let c) where c == SHANJIE_NO_CHANGE: break
        case .failed(let c): _ = fail(c)
        }
    }

    /// docs/spec/semantic-rerank.md §8.1: the job the output just applied left, if any, goes to the shell's runner. A non-zero take is handled like
    /// apply's 4: composition and panel cleared, only the code logged.
    private func takeRerank() {
        guard let engine = shell.engine else { return }
        let (job, code) = shell.rerankTake(engine, shell.rerankFree)
        if code != 0 {
            _ = fail(code)
        } else if let job {
            shell.enqueueRerank(job, session: self, engine: engine)
        }
    }

    /// The menu's 這個 App 用聊天設定 (docs/spec/macos-shell.md §5.3): the opposite of this client's profile is stored for its
    /// app, then it is re-applied in `activate()`'s order: `claim()` first sends another session's composition back to its
    /// own client, so `applyProfile` recomputes only this session's profile. The bundle ID is read once (a
    /// synchronous IPC) and passed on. Nothing to store without a bundle ID, and the item is greyed outside `auto`.
    func toggleAppChat() {
        guard let bundle = client.bundleIdentifier, !bundle.isEmpty, shell.profileMode == .auto else { return }
        shell.setAppProfile(bundle, profile(for: bundle) == Shell.chat ? Shell.formal : Shell.chat)
        if claim() { shell.diag.record(.owner(bundle: DiagLog.clean(bundle))) }
        applyProfile(bundle: bundle)
        shell.changed()
    }

    /// The profile `bundle` (this client's, read by the caller) gets now (docs/spec/macos-shell.md §5.1).
    func profile(for bundle: String?) -> UInt32 {
        Shell.profile(bundle: bundle, mode: shell.profileMode, overrides: shell.appProfiles)
    }

    /// The profile for this client's app, `bundle` being its bundle ID as the caller read it. No owner check: callers claim
    /// first (`activate`, `handle`, `toggleAppChat`) or are the owner (`Shell.reapplyProfileToOwner`).
    fileprivate func applyProfile(bundle: String?) {
        self.bundle = bundle
        switch shell.setProfile(profile(for: bundle)) {
        case .ok(let o)?: apply(o)
        case .failed(let c)?: _ = fail(c)
        case nil: break
        }
    }

    /// The single place a key reaches the core.
    func send(_ key: ShanjieKey) -> Bool {
        guard let engine = shell.engine else { return false }
        let bundle = client.bundleIdentifier  // a synchronous IPC: read once, for the gate and the left context's terminal check
        self.bundle = bundle
        let paused = applyLearningGate(engine, bundle: bundle)
        // docs/spec/keys-and-composition.md §9.2: once per composition, before the key that may start it and so before its
        // first setMarkedText. Only a character key without Control, Option, Command or Caps Lock
        // can start one, so pass-through keys outside a composition never read the client.
        // docs/spec/english-mixed.md §9: p starts as unknown for every key that may start a composition, modifiers or not, and is
        // taken from the same read.
        if !shell.composing {
            spacingPrev = nil
            if key.kind == KeyMap.char && key.modifiers & ~1 == 0 {
                let read = paused ? nil : leftContext(bundle: bundle)
                spacingPrev = read?.last
                let code = engine.setLeftContext(read?.text)
                if code != 0 { Log.shell.error("shanjie_engine_set_left_context failed, code \(code)") }
            }
        }
        switch engine.key(key) {
        case .ok(let o):
            // handled 0 with nothing to commit is a pure pass-through (docs/spec/keys-and-composition.md §4.1 rules 1 and 22): the
            // core's state is unchanged, so the client is not touched either. The app inserts or moves
            // the cursor by itself then, so p is unknown (docs/spec/english-mixed.md §9).
            if o.handled || !o.commit.isEmpty { apply(o) }
            if !o.handled { spacingPrev = nil }
            return o.handled
        case .failed(let c):
            return fail(c)
        }
    }

    /// reset mode 0 commits to this session's client, 1 discards; either way the client's marked
    /// text and the panel are cleared.
    func finish(mode: UInt32) {
        guard let engine = shell.engine else { return }
        applyLearningGate(engine)
        switch engine.reset(mode: mode) {
        case .ok(let o): apply(o)
        case .failed(let c): _ = fail(c)
        }
    }

    /// docs/spec/macos-shell.md §4: commit first, then the preedit (single underline with a thick one over the open English run, caret at
    /// cursor_utf16), then the candidates.
    private func apply(_ o: CoreOutput) {
        shell.composingCursor = Int(o.cursorUTF16)
        if !o.commit.isEmpty {
            var text = o.commit
            if shell.spacingOn {
                text = CJKSpacing.apply(previous: spacingPrev, text: text)
            }
            spacingPrev = text.last
            client.insertText(text, replacementRange: NSRange(location: NSNotFound, length: 0))
        }
        if !o.preedit.isEmpty {
            // docs/spec/macos-shell.md §4: an unchanged composition (Tab into the prediction row) is not sent again. Chromium hands the key
            // to the page before the composition, so a page that moves focus on the key would get the text a second time.
            let marked = Marked(text: o.preedit, cursor: o.cursorUTF16, runStart: o.englishRunStart, runLength: o.englishRunLength)
            if marked != sentMarked || !o.commit.isEmpty {
                client.setMarkedText(Self.marked(o), selectionRange: NSRange(location: o.cursorUTF16, length: 0))
                sentMarked = marked
            }
        } else if shell.composing {
            clearMarkedText()
        }
        shell.composing = !o.preedit.isEmpty
        // docs/spec/english-mixed.md §4.6: an open English run has no candidates, so the hint takes the window's place.
        let hint = o.candidates.isEmpty && o.englishRunLength > 0
        if o.candidates.isEmpty && !hint || hidesFirstSyllableRow(o) {
            shell.hideCandidates()
        } else {
            let cursor = Int(o.cursorUTF16)
            if hint {
                // Anchored at the run's start, asked once per run (the cursor moves a letter per key; the start does not).
                let start = o.englishRunStart
                let rect: NSRect?
                if let a = shell.hintAnchor, a.start == start {
                    rect = a.rect
                } else {
                    rect = client.lineRect(cursor: start)
                    shell.hintAnchor = (start, rect)
                }
                shell.showHint(lineRect: rect, appearance: client.appearance)
            } else {
                shell.hintAnchor = nil
                let rect: NSRect?
                if let c = shell.lineCache, c.preedit == o.preedit, c.cursor == cursor {
                    rect = c.rect
                } else {
                    rect = client.lineRect(cursor: cursor)
                    shell.lineCache = (o.preedit, cursor, rect)
                }
                shell.showCandidates(o.candidates, selected: o.selected, columns: o.columns, first: o.first, total: o.total,
                                     vertical: o.vertical, lineRect: rect, appearance: client.appearance)
            }
        }
        takeRerank()
    }

    /// docs/spec/prediction.md §9.2, §9.4: a passive prediction row for the first syllable, in a browser, is not shown.
    private func hidesFirstSyllableRow(_ o: CoreOutput) -> Bool {
        o.selected == -1 && !o.candidates.isEmpty && shell.browserFirstSyllableOn && !shell.abbreviationOn
            && bundle.map(Shell.browserApps.contains) == true && Shell.singleSyllable(o.preedit)
    }

    /// docs/spec/macos-shell.md §4 and docs/spec/english-mixed.md §4.6: the preedit with a single underline, and a thick one over the open English run
    /// (clamped to the text, so a wrong range from the core can never throw).
    static func marked(_ o: CoreOutput) -> NSAttributedString {
        let text = NSMutableAttributedString(
            string: o.preedit, attributes: [.underlineStyle: NSUnderlineStyle.single.rawValue])
        let run = NSRange(location: o.englishRunStart, length: o.englishRunLength)
        let range = NSIntersectionRange(run, NSRange(location: 0, length: text.length))
        if range.length > 0 {
            text.addAttribute(.underlineStyle, value: NSUnderlineStyle.thick.rawValue, range: range)
        }
        return text
    }

    /// docs/spec/macos-shell.md §4, non-zero return code: the core discards its composition too (reset mode 1; it
    /// does so by itself only on code 4), then the marked text and the panel are cleared and the
    /// key passes. Nothing but the code is recorded.
    private func fail(_ code: Int32) -> Bool {
        Log.shell.error("core call failed, code \(code)")
        if case .failed(let c)? = shell.engine?.reset(mode: 1) {
            Log.shell.error("shanjie_engine_reset failed, code \(c)")
        }
        clearMarkedText()
        shell.composing = false
        shell.hideCandidates()
        return false
    }

    private func clearMarkedText() {
        client.setMarkedText(NSAttributedString(string: ""), selectionRange: NSRange(location: 0, length: 0))
        sentMarked = nil
    }

    /// docs/spec/macos-shell.md §4: the marked text this session last sent to its client (nil after clearing it).
    private struct Marked: Equatable { let text: String; let cursor: Int; let runStart: Int; let runLength: Int }
    private var sentMarked: Marked?
}
