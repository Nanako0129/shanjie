import AppKit
import CShanjie
import os

/// R2 (docs/contracts/s3b.md section 9): messages are static strings and C ABI return codes only.
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
    // S4 left context (docs/contracts/s4-learning.md section 2). NSTextInputClient documents
    // markedRange as {NSNotFound, 0} without marked text; real clients also report {insertion, 0}.
    func selectedRange() -> NSRange
    func markedRange() -> NSRange
    func attributedSubstring(from range: NSRange) -> NSAttributedString?
    /// s3b2 section 2.2: the line rectangle of the composition (screen coordinates), `nil` if the
    /// client reports none.
    /// `cursor`: the composition cursor in UTF-16 units within the marked text.
    func lineRect(cursor: Int) -> NSRect?
    /// s3b2 section 10: the light or dark appearance of the view being typed into, `nil` if unknown.
    var appearance: NSAppearance? { get }
}

/// A display-only candidate list (an NSPanel in the app). It never receives keys.
@MainActor
public protocol CandidatePanel: AnyObject {
    /// `notes[i]`: the name shown beside `candidates[i]` (s3f), `nil` for none.
    /// `columns`: 0 for the collapsed single row, else the expanded grid's columns (s3b2 section 8.3).
    /// `first`, `total`: the position of `candidates[0]` in the whole list and the list's length
    /// (the scroll indicator).
    /// `lineRect`: where the composition's line is (s3b2 section 2.3), `nil` if unknown.
    /// `appearance`: the client's (s3b2 section 10), `nil` for the system's.
    /// `glassTint`: the 0...1 slider value (settings-window section 2.3), applied on every show, 0 included.
    /// `vertical`: the core's `candidate_vertical` of this output (candidate-vertical contract sections 2.2 and 2.4): 0
    /// horizontal, 1 the vertical candidate window (a single column of up to nine rows), 2 the vertical prediction row;
    /// `columns` is 0 for both. The panel draws what it is told and reads no setting.
    func show(_ candidates: [String], notes: [String?], selected: Int, columns: Int, first: Int, total: Int, vertical: Int,
              lineRect: NSRect?, appearance: NSAppearance?, glassTint: Double)
    func hide()
    /// Re-applies the tint to what is on screen now, without a new `show` (the slider moved while the panel is up).
    func setGlassTint(_ glassTint: Double)
    /// A mouse click on a cell, by position in what `show` last received.
    var onSelect: ((Int) -> Void)? { get set }
}

/// The keyboard layout. Since docs/contracts/s3b.md section 13.2 it is chosen in the input
/// method's menu and kept in a `LayoutStore` under its raw value; Info.plist has a single input
/// mode, `<bundle ID>.zhuyin`, which maps to no layout.
public enum InputMode: String, Sendable {
    case standard
    case eten

    var layout: UInt32 { self == .standard ? 0 : 1 }
}

/// Where the chosen layout is kept (section 13.2): the raw value of an `InputMode`. The app keeps
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

/// Where the "avoid ranking sensitive words first" switch is kept (docs/contracts/sw-sensitive-demote.md
/// section 3): `nil` means never chosen, which is on. The app keeps it in its own UserDefaults; ShanjieKit
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

/// Where the "即時預測" switch is kept (docs/contracts/v3-engine.md section 10.5): `nil` means never chosen, which is on.
/// Same arrangement as `DemoteStore`.
@MainActor
public protocol PredictionStore: AnyObject {
    var prediction: Bool? { get set }
}

/// The in-memory PredictionStore.
@MainActor
public final class MemoryPredictionStore: PredictionStore {
    public var prediction: Bool?
    public init(_ prediction: Bool? = nil) { self.prediction = prediction }
}

/// Where the "可省略韻母" switch is kept (docs/contracts/v3-engine.md section 12.1): `nil` means never chosen, which is off.
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

/// Where the "中英混打（實驗）" switch is kept (docs/contracts/zhen-mixed.md section 3.2): `nil` means never chosen, which is off.
/// Same arrangement as `AbbreviationStore`.
@MainActor
public protocol EnglishStore: AnyObject {
    var english: Bool? { get set }
}

/// The in-memory EnglishStore.
@MainActor
public final class MemoryEnglishStore: EnglishStore {
    public var english: Bool?
    public init(_ english: Bool? = nil) { self.english = english }
}

/// Where the "動漫與遊戲詞" switch is kept (docs/contracts/acg-pack.md A.2): `nil` means never chosen, which is on
/// (user decision 2026-10-09, after the A.3 numbers). Same arrangement as `PredictionStore`.
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

/// Where the candidate glass tint slider is kept (docs/contracts/settings-window.md section 2.3): 0...1,
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

/// Where the "候選窗方向" choice is kept (docs/contracts/candidate-vertical.md section 2.1): `true` is the vertical window, `nil`
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

/// Where the "語意校正" switch is kept (docs/contracts/s7c2b-shell-rerank.md section 2.2): `nil` means never chosen, which is on
/// (docs/contracts/s7h-tinylm-v3.md section 2.3; off before S7h).
/// Same arrangement as `PredictionStore`.
@MainActor
public protocol RerankStore: AnyObject {
    var rerank: Bool? { get set }
}

/// The in-memory RerankStore.
@MainActor
public final class MemoryRerankStore: RerankStore {
    public var rerank: Bool?
    public init(_ rerank: Bool? = nil) { self.rerank = rerank }
}

/// Runs a rerank job off the main thread (s7c2b section 2.1): `job.run()` on another thread, then `done` with its code on the
/// main thread. The app passes `QueueRerankRunner`; the tests pass runners they complete by hand.
@MainActor
public protocol RerankRunner: AnyObject {
    func run(_ job: RerankJob, done: @escaping @MainActor @Sendable (Int32) -> Void)
}

/// The app's runner (F2): one serial `.utility` queue with a fixed label. The closure holds the job wrapper (never its
/// pointer) until run returns; the code comes back through `DispatchQueue.main.async`, which orders the run before the
/// main-thread apply. The main thread never waits for the queue.
public final class QueueRerankRunner: RerankRunner {
    private let queue = DispatchQueue(label: "com.nyanako.inputmethod.shanjie.rerank", qos: .utility)

    public init() {}

    public func run(_ job: RerankJob, done: @escaping @MainActor @Sendable (Int32) -> Void) {
        queue.async {
            let code = job.run()
            DispatchQueue.main.async { done(code) }
        }
    }
}

/// How the chat or formal profile is chosen (docs/contracts/profile-choice.md section 2.2): by the app (`chatApps` and the
/// user's per-app choices), or always one of the two.
public enum ProfileMode: String, Sendable {
    case auto
    case chat
    case formal
}

/// Where the profile mode is kept (profile-choice section 2.1): the raw value of a `ProfileMode`; `nil` or any other string is
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

/// Where the per-app choices are kept (profile-choice section 2.1): bundle ID to `chat` or `formal`, only for apps the user
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

/// The stored per-app choices, converted both ways (profile-choice section 2.1).
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
/// candidate panel and the page it shows, and which session owns the composition (section 5).
/// All calls happen on the main thread.
@MainActor
public final class Shell {
    /// Built-in chat apps (section 9); every other app gets the formal profile, unless the user switched it
    /// (profile-choice section 2.5). A bundle ID is never passed to the core or logged; it is stored only for an
    /// app the user switched from the menu (`appProfiles`).
    static let chatApps: Set<String> = [
        "com.hnc.Discord", "jp.naver.line.mac", "com.apple.MobileSMS", "com.tinyspeck.slackmacgap",
        "ru.keepcoder.Telegram", "com.tdesktop.Telegram", "net.whatsapp.WhatsApp", "com.facebook.archon",
    ]
    static let chat: UInt32 = 0, formal: UInt32 = 1

    /// The one place the profile is decided (profile-choice section 2.2). `auto`: no bundle ID is formal, as before; then the
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
    private let abbreviationStore: AbbreviationStore
    /// V3 section 12: the abbreviation composer (default off); sent to every engine `build()` makes, next to the prediction switch.
    private(set) var abbreviationOn = false
    /// Whether the abbreviation switch is offered at all (menu item, settings toggle). The app passes false: the feature does not
    /// ship in v0.4.0 (user, 2026-10-10, after a device check: a whole sentence cannot be abbreviated, and keys seemed stuck; the
    /// stuck keys are read against V3 section 12.2, not reproduced: Space and tone keys are eaten while two or more units are
    /// pending). Hidden means always off; the stored value is neither read nor written.
    public let showsAbbreviation: Bool
    private let englishStore: EnglishStore
    /// zhen-mixed: the experimental English window (default off); sent to every engine `build()` makes. Unlike the abbreviation
    /// switch it ships (the app shows it).
    private(set) var englishOn = false
    /// Whether this engine loaded the word list (`Resources/english-words.txt`); false greys the menu item and the settings toggle
    /// (the checkmark keeps the setting), as `rerankAvailable` does for the small model.
    private(set) var englishAvailable = false
    private let acgPackStore: AcgPackStore
    /// The ACG word pack (default on, user decision 2026-10-09): parsed into the lexicon, so a change rebuilds the engine like a layout change.
    private(set) var acgPackOn = true
    /// The pack's source date for the grey menu line (acg-pack contract A2.4): `latest_source_revision` of `Resources/packs/acg.json`
    /// as a UTC date, read once at init. `nil` (file or field missing or unreadable) means no line.
    let acgDataDate: String?
    private let glassTintStore: GlassTintStore
    private let candidateOrientationStore: CandidateOrientationStore
    /// The orientation setting (default horizontal); sent to every engine `build()` makes and on every change (candidate-vertical
    /// contract section 2.1). A candidate window already open keeps the orientation it opened with, so a change reaches the
    /// next window. The core also re-renders a prediction row that is on screen in the new orientation (section 2.4); with the
    /// setting only in the settings window, where clicking is inferred (not measured) to commit the composition and hide the
    /// panel, that is reachable through the API and the tests but not observable from the window as shipped.
    public private(set) var candidateVertical = false
    private let rerankStore: RerankStore
    /// s7c2b section 2.2: whether the core makes rerank jobs (default on since S7h, the maintainer's decision 2026-10-12: on, with a
    /// break rate <= 0.5%); sent to every engine `build()` makes and on every change.
    private(set) var rerankOn: Bool
    /// The small model's file in Resources (section 2.3). The only path it is loaded from: `build()` and the selftest join it to
    /// `resources`; no init parameter, defaults key or environment variable names another.
    static let tinyLMFile = "tinylm.sjtl"
    /// zhen-mixed section 3.2: the English word list, in Resources next to the model (build-app.sh generates it).
    static let englishFile = "english-words.txt"
    private let rerankRunner: RerankRunner
    /// A job is with the runner (section 2.1: one at a time).
    private var rerankRunning = false
    /// The latest job taken while another ran. A newer one replaces it, and the replaced job is freed as it is dropped here.
    private var rerankPending: PendingRerank?
    /// Jobs taken from the core; the tests compare it with the frees.
    private(set) var rerankTaken = 0
    /// Frees a job; every wrapper calls it once from its deinit. The tests count here.
    var rerankFree: @Sendable (OpaquePointer) -> Void = { shanjie_rerank_free($0) }
    /// `CoreEngine.rerankTake`; the tests replace it to see a non-zero take handled (F5).
    var rerankTake: (CoreEngine, @escaping @Sendable (OpaquePointer) -> Void) -> (RerankJob?, Int32) = { $0.rerankTake(free: $1) }
    /// `RerankJob.apply(on:)`; the tests replace it to see apply's non-zero codes other than 5 handled.
    var rerankApply: (RerankJob, CoreEngine) -> CoreResult = { $0.apply(on: $1) }
    /// Every value `build()` passed to `setRerank`, in order (the tests read it: a nil store must send true, not nothing).
    private(set) var rerankSent: [Bool] = []
    /// Whether the current engine has the small model (set by every `build()`): false when there is no engine, when load_lm
    /// failed (load_tinylm is then skipped) or when load_tinylm returned non-zero. Without it no job is ever made.
    private(set) var rerankAvailable = false
    /// The 語意校正 menu item and settings toggle are enabled only when the switch can do something: the demotion table on (the
    /// core makes no job without it, F11) and the small model loaded. The checkmark still shows the stored choice.
    var rerankEnabled: Bool { demoteOn && rerankAvailable }

    /// A job waiting for the runner, with the session and engine it was taken for. Weak (F3): a waiting job keeps neither alive,
    /// and an identity is never compared by address.
    private struct PendingRerank {
        let job: RerankJob
        weak var session: Session?
        weak var engine: CoreEngine?
    }
    private let profileModeStore: ProfileModeStore
    private let appProfileStore: AppProfileStore
    /// profile-choice section 2.1: both read once at init, then changed only through the setters below.
    public private(set) var profileMode = ProfileMode.auto
    /// Bundle ID to profile, only where it differs from `chatApps` (section 2.3).
    public private(set) var appProfiles: [String: UInt32] = [:]
    /// The `vertical` value (0, 1 or 2) of what the panel shows now; 0 once it hid. Page Up / Down go to the core only while
    /// it is 1, the vertical candidate window (section 2.2); the vertical prediction row, 2, does not count.
    private(set) var shownVertical = 0
    var verticalOpen: Bool { shownVertical == VerticalLayout.candidates }
    /// Posted (object: this shell) after any setting changes, from the menu or the settings window;
    /// the window's model re-reads on it (settings-window section 2.2).
    public static let didChangeSettings = Notification.Name("ShanjieShellDidChangeSettings")
    let panel: CandidatePanel
    let isSecureInput: () -> Bool
    private(set) var engine: CoreEngine?
    private(set) var mode: InputMode = .standard
    private(set) var profile: UInt32 = Shell.chat
    /// S4: the learning directory (section 4), `nil` for none (no file is opened or written).
    let learningDirectory: URL?
    /// The last `shanjie_engine_learning_open` of the current engine failed.
    var learningOpenFailed = false
    /// The clear's confirmation and failure windows (section 4).
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
    /// start without its preference (section 13.2); a missing or unknown value is the standard
    /// layout.
    /// `learningDirectory`: where the core keeps learning.tsv (S4 section 4), `Shell.learningURL()`
    /// in the app; tests pass a temporary directory or nil. Required, with no default, so no caller
    /// can reach the real Application Support by omission.
    /// `dialogs`: the clear's windows (`AlertDialogs` in the app; tests answer for the user). Required,
    /// so a build cannot ship a clear that silently does nothing.
    /// `demoteStore`: the demotion switch (sw). Required, with no default, like `layoutStore`: the app passes
    /// its UserDefaults-backed store, tests the in-memory one.
    /// `predictionStore`: the prediction switch (V3 section 10.5). Required, like `demoteStore`.
    /// `abbreviationStore`: the abbreviation switch (V3 section 12.1). Required, like `predictionStore`.
    /// `englishStore`: the 中英混打 switch (zhen-mixed section 3.2). Required, like `abbreviationStore`.
    /// `acgPackStore`: the ACG word pack switch (acg-pack contract A.2). Required, like `demoteStore`.
    /// `glassTintStore`: the candidate glass tint (settings-window section 2.3). Required, like `demoteStore`.
    /// `candidateOrientationStore`: the candidate window's orientation (candidate-vertical contract section 2.1). Required, like `glassTintStore`.
    /// `rerankStore`: the 語意校正 switch (s7c2b section 2.2). Required, like `predictionStore`.
    /// `rerankRunner`: runs rerank jobs off the main thread (section 2.1). Required, with no default: the app passes
    /// `QueueRerankRunner`, the tests a runner they complete by hand.
    /// `profileModeStore`, `appProfileStore`: how chat or formal is chosen and the per-app choices (profile-choice section 2.1).
    /// Required, like `glassTintStore`.
    /// `punctuationTable`: Apple's punctuation candidate table (s3e); tests pass another path.
    /// `punctuationNames`: Apple's punctuation names (s3f); tests pass another path.
    /// `showsAbbreviation`: whether the abbreviation switch is offered. The default `true` is for the tests; the app passes `false`
    /// (v0.4.0). No test sees the app's value: the release device check does (no 可省略韻母 in the menu).
    public init(resources: URL, panel: CandidatePanel, isSecureInput: @escaping () -> Bool,
                layoutStore: LayoutStore, learningDirectory: URL?, dialogs: LearningDialogs,
                demoteStore: DemoteStore, predictionStore: PredictionStore, abbreviationStore: AbbreviationStore, englishStore: EnglishStore, acgPackStore: AcgPackStore,
                glassTintStore: GlassTintStore, candidateOrientationStore: CandidateOrientationStore,
                rerankStore: RerankStore, rerankRunner: RerankRunner,
                profileModeStore: ProfileModeStore, appProfileStore: AppProfileStore,
                punctuationTable: URL = PunctuationTable.systemURL,
                punctuationNames: URL = PunctuationNames.systemURL,
                showsAbbreviation: Bool = true) {
        self.resources = resources
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
        self.abbreviationStore = abbreviationStore
        self.showsAbbreviation = showsAbbreviation
        abbreviationOn = showsAbbreviation && abbreviationStore.abbreviation == true
        self.englishStore = englishStore
        englishOn = englishStore.english == true
        self.acgPackStore = acgPackStore
        acgDataDate = Shell.readAcgDataDate(resources.appendingPathComponent("packs/acg.json"))
        self.glassTintStore = glassTintStore
        glassTint = glassTintStore.glassTint ?? 0
        self.candidateOrientationStore = candidateOrientationStore
        candidateVertical = candidateOrientationStore.candidateVertical ?? false
        self.rerankStore = rerankStore
        rerankOn = rerankStore.rerank ?? true
        self.rerankRunner = rerankRunner
        self.profileModeStore = profileModeStore
        profileMode = profileModeStore.profileMode.flatMap(ProfileMode.init(rawValue:)) ?? .auto
        self.appProfileStore = appProfileStore
        appProfiles = AppProfiles.parse(appProfileStore.appProfiles)
        acgPackOn = acgPackStore.acgPack ?? true
        // The preference is read before the one engine is built (about 240 MB): building first
        // and switching after would build twice.
        mode = layoutStore.layout.flatMap(InputMode.init(rawValue:)) ?? .standard
        build()
        panel.onSelect = { [weak self] i in self?.owner?.candidateSelected(at: i) }
    }

    /// The current layout (the menu's checkmark).
    public var layout: InputMode { mode }

    /// The menu's choice (section 13.2): the existing switch (commit, rebuild), then the choice is
    /// stored.
    public func selectLayout(_ newMode: InputMode) {
        switchMode(to: newMode)
        layoutStore.layout = newMode.rawValue
        changed()
    }

    /// Set by the app to show the settings window; the menu's 善解設定… calls it.
    public var onOpenSettings: (() -> Void)?
    func openSettings() { onOpenSettings?() }

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

    func applyCandidateVertical(_ on: Bool) {
        if let o = owner { o.applyCandidateVertical(on) } else { dropOnFailure(setCandidateVertical(on)) }
    }

    func applyRerank(_ on: Bool) {
        if let o = owner { o.applyRerank(on) } else { dropOnFailure(setRerank(on)) }
    }

    /// No session to ask: a failed core call is logged and the composition dropped, as `Session.fail` does.
    private func dropOnFailure(_ r: CoreResult?) {
        guard case .failed(let c)? = r else { return }
        Log.shell.error("core call failed, code \(c)")
        discardOrphan()
    }

    /// The clear with its confirmation window (S4 section 4), for the menu and the settings window.
    /// `done` runs after the answer, cleared or not.
    func confirmAndClear(done: (@MainActor () -> Void)? = nil) {
        dialogs.confirmClear { [weak self] clear in
            if clear { self?.clearLearning() }
            done?()
        }
    }

    func changed() { NotificationCenter.default.post(name: Shell.didChangeSettings, object: self) }

    /// The slider value 0...1 (settings-window section 2.3); unset is 0. Cached like the other settings.
    public private(set) var glassTint: Double = 0

    public func setGlassTint(_ value: Double) {
        glassTint = value
        glassTintStore.glassTint = value
        panel.setGlassTint(value)  // section 2.3: a panel that is up changes at once
        changed()
    }

    /// The settings window's 切換方式 (profile-choice section 2.2): stored, posted, then re-applied to the owner of the
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

    /// Section 2.2: no claim, the owner already owns the composition (as the window's other switches go through the owner).
    private func reapplyProfileToOwner() {
        guard let o = owner else { return }
        o.applyProfile(bundle: o.client.bundleIdentifier)
    }

    /// The menu's choice for one app (section 2.3): a value equal to `chatApps`' removes the entry, so the list keeps only the
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

    /// The menu's choice (V3 section 10.5): stored, and sent to the engine; the snapshot is for the caller to show.
    func setPrediction(_ on: Bool) -> CoreResult? {
        predictionOn = on
        predictionStore.prediction = on
        defer { changed() }
        return engine?.setPrediction(on)
    }

    /// The menu's choice (V3 section 12.1): stored, and sent to the engine; the snapshot is for the caller to show.
    func setAbbreviation(_ on: Bool) -> CoreResult? {
        guard showsAbbreviation else { return nil }
        abbreviationOn = on
        abbreviationStore.abbreviation = on
        defer { changed() }
        return engine?.setAbbreviation(on)
    }

    /// The menu's choice (zhen-mixed section 3.2): stored, and sent to the engine; the snapshot is for the caller to show (turning
    /// it off closes an open English window).
    func setEnglish(_ on: Bool) -> CoreResult? {
        englishOn = on
        englishStore.english = on
        defer { changed() }
        return engine?.setEnglish(on)
    }

    /// The settings window's choice (candidate-vertical contract section 2.1): stored, and sent to the engine, which applies it
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

    /// The menu's choice (s7c2b section 2.2): stored, and sent to the engine, which recomputes the composition (off: the n-gram
    /// result at once); the snapshot is for the caller to show. A job waiting for the runner is dropped; one running is not applied.
    func setRerank(_ on: Bool) -> CoreResult? {
        rerankOn = on
        rerankStore.rerank = on
        if !on { rerankPending = nil }
        defer { changed() }
        return engine?.setRerank(on)
    }

    /// Section 2.1: a job taken for `session` from `engine` runs now, or waits if another is running (only the latest waits).
    func enqueueRerank(_ job: RerankJob, session: Session, engine: CoreEngine) {
        rerankTaken += 1
        let pending = PendingRerank(job: job, session: session, engine: engine)
        if rerankRunning { rerankPending = pending } else { startRerank(pending) }
    }

    private func startRerank(_ p: PendingRerank) {
        rerankRunning = true
        let job = p.job, session = p.session, engine = p.engine
        rerankRunner.run(job) { [weak self, weak session, weak engine] code in
            self?.rerankFinished(job, code: code, session: session, engine: engine)
        }
    }

    /// On the main thread once run returned. Applied only while the switch is on, the session still owns the composition and the
    /// engine is still the current one (weak references compared with ===, F3); the core's generation check is the second line.
    private func rerankFinished(_ job: RerankJob, code: Int32, session: Session?, engine: CoreEngine?) {
        // F4: whatever is returned below, the running flag is cleared and a waiting job starts.
        defer {
            rerankRunning = false
            if let next = rerankPending {
                rerankPending = nil
                startRerank(next)
            }
        }
        guard code == 0 else {
            Log.shell.error("shanjie_rerank_run failed, code \(code)")
            return
        }
        guard rerankOn, let session, session === owner, let engine, engine === self.engine else { return }
        session.applyRerankJob(job, on: engine)
    }

    /// The menu's choice (acg-pack contract A.2): the pack is part of the lexicon, so like a layout change the
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

    /// Creates the engine for the current mode, then loads the LM and the current profile. A
    /// failure leaves an engine without the LM (still usable) or no engine (every key passes).
    private func build() {
        engine = nil  // free the old engine first: only one exists at a time
        rerankAvailable = false
        englishAvailable = false
        rerankPending = nil  // the old engine's; one still running is not applied (its engine is gone)
        let packsDir = resources.appendingPathComponent("packs").path
        var (made, code) = CoreEngine.make(dataDir: resources.path, layout: mode.layout, packsDir: acgPackOn ? packsDir : nil)
        if made == nil && acgPackOn {
            // Any build failure retries without the pack, so the first code alone does not say whose fault it is:
            // blame the pack only if the pack-less build works; otherwise the second code is the real cause.
            // The stored choice is untouched and nothing is shown; the next rebuild tries the pack again.
            let packCode = code
            (made, code) = CoreEngine.make(dataDir: resources.path, layout: mode.layout)
            if made != nil { Log.shell.error("shanjie_engine_new_packs failed, code \(packCode); built without the pack") }
        }
        guard let e = made else {
            Log.shell.error("shanjie_engine_new failed, code \(code)")
            return
        }
        let lm = e.loadLM(path: resources.appendingPathComponent("bigram.sjlm").path)
        if lm != 0 { Log.shell.error("shanjie_engine_load_lm failed, code \(lm)") }
        // s7c2b section 2.3: after load_lm (which drops a small model). Missing or failed: the n-gram alone, no rerank.
        if lm == 0 {
            let tiny = e.loadTinyLM(path: resources.appendingPathComponent(Shell.tinyLMFile).path)
            if tiny != 0 { Log.shell.error("shanjie_engine_load_tinylm failed, code \(tiny); no rerank") }
            rerankAvailable = tiny == 0
        }
        // zhen-mixed section 3.2: the word list. A failure leaves the engine without it (the switch then has no effect) and greys the
        // item; only the code is logged.
        let english = e.loadEnglish(path: resources.appendingPathComponent(Shell.englishFile).path)
        if english != 0 { Log.shell.error("shanjie_engine_load_english failed, code \(english)") }
        englishAvailable = english == 0
        if case .failed(let c) = e.setDemote(demoteOn) {
            Log.shell.error("shanjie_engine_set_demote failed, code \(c)")
        }
        if case .failed(let c) = e.setPrediction(predictionOn) {
            Log.shell.error("shanjie_engine_set_prediction failed, code \(c)")
        }
        // A rebuilt engine (layout, word pack) starts with the core's default, so this is sent every time (section 12.1).
        if case .failed(let c) = e.setAbbreviation(abbreviationOn) {
            Log.shell.error("shanjie_engine_set_abbreviation failed, code \(c)")
        }
        // And the English window's switch, every time: a rebuilt engine (layout, word pack) starts with the core's default, off.
        if case .failed(let c) = e.setEnglish(englishOn) {
            Log.shell.error("shanjie_engine_set_english failed, code \(c)")
        }
        // Same for the candidate window's orientation (candidate-vertical contract section 2.2).
        if case .failed(let c) = e.setCandidateVertical(candidateVertical) {
            Log.shell.error("shanjie_engine_set_candidate_vertical failed, code \(c)")
        }
        // A rebuilt engine starts with the core's default (off since S7c-2c), and load_tinylm leaves the switch alone: the shell's
        // own value is sent every time, so the core's default never decides.
        rerankSent.append(rerankOn)
        if case .failed(let c) = e.setRerank(rerankOn) {
            Log.shell.error("shanjie_engine_set_rerank failed, code \(c)")
        }
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

    /// Section 5: commit the composition to its owner, free the engine, rebuild with the new
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
        panel.show(list, notes: notes, selected: selected, columns: columns, first: first, total: total, vertical: vertical,
                   lineRect: lineRect, appearance: appearance, glassTint: glassTint)
    }

    func hideCandidates() {
        lineCache = nil
        shownVertical = 0
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

    public init(shell: Shell, client: TextClient) {
        self.shell = shell
        self.client = client
    }

    /// Section 5: weak references already read nil here, so an owner that reads nil while the
    /// composition is non-empty means this session (or an earlier owner) is gone: discard.
    isolated deinit {
        if shell.owner == nil && shell.composing { shell.discardOrphan() }
    }

    // MARK: IMK callbacks

    /// Returns whether the key was consumed. `event` is nil when IMK passes nil.
    public func handle(_ event: NSEvent?) -> Bool {
        guard shell.engine != nil else { return false }
        guard event == nil || event?.type == .keyDown else { return false }
        if claim() { applyProfile(bundle: client.bundleIdentifier) }
        // candidate-vertical contract section 2.2: Page Up / Down reach the core only while the vertical window is on screen
        // and no modifier (Shift, Control, Option, Command, Caps Lock) is held; otherwise they are keys outside the tables.
        if let event, shell.verticalOpen, KeyMap.modifiers(event.modifierFlags) == 0, let kind = KeyMap.pageKeys[event.keyCode] {
            return send(ShanjieKey(kind: kind, ch: 0, modifiers: 0))
        }
        guard let event,
              let key = KeyMap.translate(keyCode: event.keyCode, flags: event.modifierFlags)
        else {
            // nil, or a key outside the tables: never sent to the core.
            if shell.composing { finish(mode: 0) }
            return false
        }
        return send(key)
    }

    /// Section 5 order: ownership first, then the profile for this client's app.
    public func activate() {
        claim()
        applyProfile(bundle: client.bundleIdentifier)
    }

    /// Only the owner acts; a late call from any other controller does nothing at all.
    public func deactivate() {
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

    /// A mouse click on a candidate (s3b2 section 8.3): `i` is its position in the output the panel
    /// shows. The core picks it through the same path as Enter and checks the range (code 2 leaves
    /// everything as it was); the learning gate is sampled first, as for a key.
    public func candidateSelected(at i: Int) {
        guard shell.owner === self, let engine = shell.engine else { return }
        applyLearningGate(engine)
        switch engine.pick(UInt32(truncatingIfNeeded: i)) {
        case .ok(let o): apply(o)
        case .failed(2): Log.shell.error("core call failed, code 2")
        case .failed(let c): _ = fail(c)
        }
    }

    /// The input method menu (section 13.2).
    public var layout: InputMode { shell.layout }
    public func selectLayout(_ m: InputMode) { shell.selectLayout(m) }

    // MARK: internals

    /// Section 5: a composition owned by another session goes back to that session's client if it
    /// is still alive, else it is discarded; then this session becomes the owner. Returns whether
    /// the owner changed.
    @discardableResult
    func claim() -> Bool {
        guard shell.owner !== self else { return false }
        if shell.composing {
            if let previous = shell.owner { previous.finish(mode: 0) } else { shell.discardOrphan() }
        }
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

    /// The menu's abbreviation switch: the snapshot is shown, if this session owns the composition.
    func applyAbbreviation(_ on: Bool) {
        switch shell.setAbbreviation(on) {
        case .ok(let o)? where shell.owner === self: apply(o)
        case .failed(let c)?: _ = fail(c)
        default: break
        }
    }

    /// The orientation setting: the snapshot is shown, if this session owns the composition, but only when its `vertical` value
    /// differs from what the panel shows, i.e. a prediction row that turns (candidate-vertical contract section 2.4). An open
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

    /// Section 2.1: a finished job, for the engine it was taken from (the shell checked that it is current). 0 shows the
    /// snapshot like any output; 5 changes nothing; anything else (4: the engine has reset itself) is handled like a failed key.
    func applyRerankJob(_ job: RerankJob, on engine: CoreEngine) {
        switch shell.rerankApply(job, engine) {
        case .ok(let o): apply(o)
        case .failed(let c) where c == SHANJIE_NO_CHANGE: break
        case .failed(let c): _ = fail(c)
        }
    }

    /// Section 2.1: the job the output just applied left, if any, goes to the shell's runner. A non-zero take is handled like
    /// apply's 4 (F5): composition and panel cleared, only the code logged.
    private func takeRerank() {
        guard let engine = shell.engine else { return }
        let (job, code) = shell.rerankTake(engine, shell.rerankFree)
        if code != 0 {
            _ = fail(code)
        } else if let job {
            shell.enqueueRerank(job, session: self, engine: engine)
        }
    }

    /// The menu's 這個 App 用聊天設定 (profile-choice section 2.3): the opposite of this client's profile is stored for its
    /// app, then it is re-applied in `activate()`'s order: `claim()` first sends another session's composition back to its
    /// own client, so `applyProfile` recomputes only this session's (security review P2-1). The bundle ID is read once (a
    /// synchronous IPC) and passed on. Nothing to store without a bundle ID, and the item is greyed outside `auto`.
    func toggleAppChat() {
        guard let bundle = client.bundleIdentifier, !bundle.isEmpty, shell.profileMode == .auto else { return }
        shell.setAppProfile(bundle, profile(for: bundle) == Shell.chat ? Shell.formal : Shell.chat)
        claim()
        applyProfile(bundle: bundle)
        shell.changed()
    }

    /// The profile `bundle` (this client's, read by the caller) gets now (profile-choice section 2.2).
    func profile(for bundle: String?) -> UInt32 {
        Shell.profile(bundle: bundle, mode: shell.profileMode, overrides: shell.appProfiles)
    }

    /// The profile for this client's app, `bundle` being its bundle ID as the caller read it. No owner check: callers claim
    /// first (`activate`, `handle`, `toggleAppChat`) or are the owner (`Shell.reapplyProfileToOwner`).
    fileprivate func applyProfile(bundle: String?) {
        switch shell.setProfile(profile(for: bundle)) {
        case .ok(let o)?: apply(o)
        case .failed(let c)?: _ = fail(c)
        case nil: break
        }
    }

    /// The single place a key reaches the core.
    func send(_ key: ShanjieKey) -> Bool {
        guard let engine = shell.engine else { return false }
        let paused = applyLearningGate(engine)
        // Section 2: once per composition, before the key that may start it and so before its
        // first setMarkedText. Only a character key without Control, Option, Command or Caps Lock
        // can start one, so pass-through keys outside a composition never read the client.
        if !shell.composing && key.kind == KeyMap.char && key.modifiers & ~1 == 0 {
            let code = engine.setLeftContext(paused ? nil : leftContext())
            if code != 0 { Log.shell.error("shanjie_engine_set_left_context failed, code \(code)") }
        }
        switch engine.key(key) {
        case .ok(let o):
            // handled 0 with nothing to commit is a pure pass-through (s3a rules 1 and 22): the
            // core's state is unchanged, so the client is not touched either.
            if o.handled || !o.commit.isEmpty { apply(o) }
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

    /// Section 7: commit first, then the preedit (single underline, caret at cursor_utf16), then
    /// the candidates.
    private func apply(_ o: CoreOutput) {
        if !o.commit.isEmpty {
            client.insertText(o.commit, replacementRange: NSRange(location: NSNotFound, length: 0))
        }
        if !o.preedit.isEmpty {
            let marked = NSAttributedString(
                string: o.preedit, attributes: [.underlineStyle: NSUnderlineStyle.single.rawValue])
            client.setMarkedText(marked, selectionRange: NSRange(location: o.cursorUTF16, length: 0))
        } else if shell.composing {
            clearMarkedText()
        }
        shell.composing = !o.preedit.isEmpty
        if o.candidates.isEmpty {
            shell.hideCandidates()
        } else {
            let cursor = Int(o.cursorUTF16)
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
        takeRerank()
    }

    /// Section 7, non-zero return code: the core discards its composition too (reset mode 1; it
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
    }
}
