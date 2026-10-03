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

/// The two input modes of Info.plist. A mode ID is `<bundle ID>.<raw value>` (scripts/build-app.sh
/// derives both from BUNDLE_ID), so a mode is recognised by its last component.
public enum InputMode: String, Sendable, CaseIterable {
    case standard
    case eten

    /// The mode for an ID IMK passes to `setValue`, e.g. `com.nyanako.inputmethod.shanjie.eten`.
    public init?(modeID: String) {
        guard let last = modeID.split(separator: ".").last else { return nil }
        self.init(rawValue: String(last))
    }

    var layout: UInt32 { self == .standard ? 0 : 1 }
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
    public init(resources: URL, panel: CandidatePanel, isSecureInput: @escaping () -> Bool) {
        self.resources = resources
        self.panel = panel
        self.isSecureInput = isSecureInput
        build()
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

    public func setInputMode(_ id: String) {
        guard let m = InputMode(modeID: id) else { return }
        shell.switchMode(to: m)
    }

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
