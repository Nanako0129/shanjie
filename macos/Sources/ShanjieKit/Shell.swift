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
}

/// A display-only candidate list (IMKCandidates in the app). It never receives keys.
@MainActor
public protocol CandidatePanel: AnyObject {
    func show(_ candidates: [String], selected: Int)
    func hide()
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
    private let layoutStore: LayoutStore
    let panel: CandidatePanel
    let isSecureInput: () -> Bool
    private(set) var engine: CoreEngine?
    private(set) var mode: InputMode = .standard
    private(set) var profile: UInt32 = Shell.chat

    /// The owning session. "Still valid" means this weak reference is not nil; an address
    /// (ObjectIdentifier) is never used, as a freed controller's address can be reused.
    weak var owner: Session?
    /// Whether the composition (as last applied to a client) is non-empty.
    var composing = false
    /// The page the panel shows, kept only to map a mouse click back to a number key.
    private(set) var candidates: [String] = []

    /// `resources`: the absolute Resources directory holding the lexicon files and bigram.sjlm.
    /// `panel`: the one candidate panel (IMKCandidates in the app).
    /// `isSecureInput`: IsSecureEventInputEnabled in the app (Carbon lives in the executable only);
    /// tests pass a fake.
    /// `layoutStore`: the chosen layout. Required, with no default, so the app cannot silently
    /// start without its preference (section 13.2); a missing or unknown value is the standard
    /// layout.
    /// `punctuationTable`: Apple's punctuation candidate table (s3e); tests pass another path.
    public init(resources: URL, panel: CandidatePanel, isSecureInput: @escaping () -> Bool,
                layoutStore: LayoutStore, punctuationTable: URL = PunctuationTable.systemURL) {
        self.resources = resources
        // Read once: the converted table never changes while the process runs, and the layout
        // switch that rebuilds the engine already blocks.
        punctuation = PunctuationTable.load(from: punctuationTable)
        if punctuation == nil {
            Log.shell.notice("punctuation candidates: system table unavailable, using the built-in list")
        }
        self.panel = panel
        self.isSecureInput = isSecureInput
        self.layoutStore = layoutStore
        // The preference is read before the one engine is built (about 240 MB): building first
        // and switching after would build twice.
        mode = layoutStore.layout.flatMap(InputMode.init(rawValue:)) ?? .standard
        build()
    }

    /// The current layout (the menu's checkmark).
    public var layout: InputMode { mode }

    /// The menu's choice (section 13.2): the existing switch (commit, rebuild), then the choice is
    /// stored.
    public func selectLayout(_ newMode: InputMode) {
        switchMode(to: newMode)
        layoutStore.layout = newMode.rawValue
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
        if case .failed(let c) = e.setProfile(profile) {
            Log.shell.error("shanjie_engine_set_profile failed, code \(c)")
        }
        // s3e: the system's punctuation candidates; without them the core's built-in list stays.
        if let table = punctuation {
            let code = e.setPunctuation(table)
            if code != 0 { Log.shell.error("punctuation candidates: core rejected the system table, code \(code)") }
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

    func showCandidates(_ list: [String], selected: Int) {
        candidates = list
        panel.show(list, selected: selected)
    }

    func hideCandidates() {
        panel.hide()
        candidates = []
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

    /// The page the panel shows (IMK's `candidates(_:)`).
    public var candidates: [String] { shell.candidates }

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

    /// A mouse click on a candidate: sent to the core as that candidate's number key, so the
    /// core decides what happens (section 8). Never inserted directly.
    public func candidateSelected(_ text: String) {
        guard shell.owner === self, let i = shell.candidates.firstIndex(of: text), i < 9 else { return }
        _ = send(ShanjieKey(kind: KeyMap.char, ch: UInt32(UInt8(ascii: "1")) + UInt32(i), modifiers: 0))
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
            shell.showCandidates(o.candidates, selected: o.selected)
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
