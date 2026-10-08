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
    func show(_ candidates: [String], notes: [String?], selected: Int, columns: Int, first: Int, total: Int,
              lineRect: NSRect?, appearance: NSAppearance?)
    func hide()
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

/// Process-wide state: the single engine (about 240 MB each, so never two at once), the single
/// candidate panel and the page it shows, and which session owns the composition (section 5).
/// All calls happen on the main thread.
@MainActor
public final class Shell {
    /// Built-in chat apps (section 9); every other app gets the formal profile. The bundle ID is
    /// only looked up here: it is never passed to the core, logged or stored.
    static let chatApps: Set<String> = [
        "com.hnc.Discord", "jp.naver.line.mac", "com.apple.MobileSMS", "com.tinyspeck.slackmacgap",
        "ru.keepcoder.Telegram", "com.tdesktop.Telegram", "net.whatsapp.WhatsApp", "com.facebook.archon",
    ]
    static let chat: UInt32 = 0, formal: UInt32 = 1

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
    /// `punctuationTable`: Apple's punctuation candidate table (s3e); tests pass another path.
    /// `punctuationNames`: Apple's punctuation names (s3f); tests pass another path.
    public init(resources: URL, panel: CandidatePanel, isSecureInput: @escaping () -> Bool,
                layoutStore: LayoutStore, learningDirectory: URL?, dialogs: LearningDialogs,
                demoteStore: DemoteStore, predictionStore: PredictionStore,
                punctuationTable: URL = PunctuationTable.systemURL,
                punctuationNames: URL = PunctuationNames.systemURL) {
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
    }

    /// The menu's choice (sw): stored, and sent to the engine, which recomputes the composition; the
    /// snapshot is for the caller to show (like `setProfile`).
    func setDemote(_ on: Bool) -> CoreResult? {
        demoteOn = on
        demoteStore.demote = on
        return engine?.setDemote(on)
    }

    /// The menu's choice (V3 section 10.5): stored, and sent to the engine; the snapshot is for the caller to show.
    func setPrediction(_ on: Bool) -> CoreResult? {
        predictionOn = on
        predictionStore.prediction = on
        return engine?.setPrediction(on)
    }

    /// Creates the engine for the current mode, then loads the LM and the current profile. A
    /// failure leaves an engine without the LM (still usable) or no engine (every key passes).
    private func build() {
        engine = nil  // free the old engine first: only one exists at a time
        let (e, code) = CoreEngine.make(dataDir: resources.path, layout: mode.layout)
        guard let e else {
            Log.shell.error("shanjie_engine_new failed, code \(code)")
            return
        }
        let lm = e.loadLM(path: resources.appendingPathComponent("bigram.sjlm").path)
        if lm != 0 { Log.shell.error("shanjie_engine_load_lm failed, code \(lm)") }
        if case .failed(let c) = e.setDemote(demoteOn) {
            Log.shell.error("shanjie_engine_set_demote failed, code \(c)")
        }
        if case .failed(let c) = e.setPrediction(predictionOn) {
            Log.shell.error("shanjie_engine_set_prediction failed, code \(c)")
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
        if composing {
            if let o = owner { o.finish(mode: 0) } else { discardOrphan() }
        }
        mode = newMode
        build()
        Log.shell.debug("input mode switched")
    }

    func setProfile(_ p: UInt32) -> CoreResult? {
        profile = p
        return engine?.setProfile(p)
    }

    func showCandidates(_ list: [String], selected: Int, columns: Int, first: Int, total: Int, lineRect: NSRect?,
                        appearance: NSAppearance?) {
        // Only an exact punctuation mark has a name: a word candidate is never a key of the table.
        let notes = list.map { names[$0] }
        panel.show(list, notes: notes, selected: selected, columns: columns, first: first, total: total,
                   lineRect: lineRect, appearance: appearance)
    }

    func hideCandidates() {
        lineCache = nil
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
        if claim() { applyProfile() }
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
        applyProfile()
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

    /// The profile for this client's app, from its bundle ID (looked up, never kept).
    private func applyProfile() {
        let chat = client.bundleIdentifier.map { Shell.chatApps.contains($0) } ?? false
        switch shell.setProfile(chat ? Shell.chat : Shell.formal) {
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
                                 lineRect: rect, appearance: client.appearance)
        }
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
