import AppKit
import Carbon
import InputMethodKit
import ShanjieKit

/// Created once in main.swift after the IMK server; every controller shares it.
@MainActor
enum App {
    static var shell: Shell!
}

/// IMK creates one controller per client. Each owns a ShanjieKit Session (the testable part) and
/// forwards every callback to it. IMK calls all of these on the main thread; `assumeIsolated`
/// traps (with a static message) if that ever stops being true. IMK's headers carry no actor
/// annotations, so this target builds in the Swift 5 language mode (Package.swift).
@objc(ShanjieInputController)
final class ShanjieInputController: IMKInputController {
    private var session: Session!

    override init!(server: IMKServer!, delegate: Any!, client inputClient: Any!) {
        super.init(server: server, delegate: delegate, client: inputClient)
        MainActor.assumeIsolated {
            session = Session(shell: App.shell, client: ClientAdapter(controller: self))
        }
    }

    override func recognizedEvents(_ sender: Any!) -> Int {
        Int(NSEvent.EventTypeMask.keyDown.rawValue)
    }

    override func handle(_ event: NSEvent!, client sender: Any!) -> Bool {
        MainActor.assumeIsolated { session.handle(event) }
    }

    override func activateServer(_ sender: Any!) {
        MainActor.assumeIsolated { session.activate() }
    }

    override func deactivateServer(_ sender: Any!) {
        MainActor.assumeIsolated { session.deactivate() }
    }

    override func commitComposition(_ sender: Any!) {
        MainActor.assumeIsolated { session.commitComposition() }
    }

    // No setValue(_:forTag:client:) override: input mode IDs never change the layout, not even the
    // two-mode IDs of earlier versions while they are still enabled; only the menu and the stored
    // preference do (s3b section 13.3, local review of PR #5).

    /// docs/contracts/s3b.md section 13.2 and S4 sections 3-4: the entries ShanjieKit builds
    /// (`Session.menu`). Each action has its own selector, as McBopomofo does, rather than relying
    /// on `sender` being the NSMenuItem (what IMK passes as sender is not measured here). The clear
    /// is confirmed in the menu itself, in two steps, so it needs no window.
    override func menu() -> NSMenu! {
        let entries = MainActor.assumeIsolated { session.menu }
        let menu = NSMenu()
        for entry in entries {
            let item = NSMenuItem(title: entry.title, action: entry.action.map(Self.selector(for:)), keyEquivalent: "")
            item.state = entry.checked ? .on : .off
            menu.addItem(item)
        }
        return menu
    }

    private static func selector(for action: MenuEntry.Action) -> Selector {
        switch action {
        case .layout(.standard): #selector(selectStandardLayout(_:))
        case .layout(.eten): #selector(selectEtenLayout(_:))
        case .askClear: #selector(askClearLearning(_:))
        case .confirmClear: #selector(confirmClearLearning(_:))
        case .cancelClear: #selector(cancelClearLearning(_:))
        case .toggleBackup: #selector(toggleLearningBackup(_:))
        }
    }

    private func perform(_ action: MenuEntry.Action) {
        MainActor.assumeIsolated { session.perform(action) }
    }

    @objc func selectStandardLayout(_ sender: Any?) { perform(.layout(.standard)) }
    @objc func selectEtenLayout(_ sender: Any?) { perform(.layout(.eten)) }
    @objc func askClearLearning(_ sender: Any?) { perform(.askClear) }
    @objc func confirmClearLearning(_ sender: Any?) { perform(.confirmClear) }
    @objc func cancelClearLearning(_ sender: Any?) { perform(.cancelClear) }
    @objc func toggleLearningBackup(_ sender: Any?) { perform(.toggleBackup) }

    override func candidates(_ sender: Any!) -> [Any]! {
        MainActor.assumeIsolated { session.candidates }
    }

    override func candidateSelected(_ candidateString: NSAttributedString!) {
        guard let text = candidateString?.string else { return }
        MainActor.assumeIsolated { session.candidateSelected(text) }
    }
}

/// The controller's current IMKTextInput client.
@MainActor
final class ClientAdapter: TextClient {
    private weak var controller: IMKInputController?

    init(controller: IMKInputController) { self.controller = controller }

    private var client: (any IMKTextInput & NSObjectProtocol)? { controller?.client() }

    var bundleIdentifier: String? { client?.bundleIdentifier() }

    func insertText(_ text: String, replacementRange: NSRange) {
        client?.insertText(text, replacementRange: replacementRange)
    }

    func setMarkedText(_ text: NSAttributedString, selectionRange: NSRange) {
        client?.setMarkedText(
            text, selectionRange: selectionRange,
            replacementRange: NSRange(location: NSNotFound, length: 0))
    }

    // S4 left context (docs/contracts/s4-learning.md section 2). No client: no insertion point,
    // so no read follows.
    func selectedRange() -> NSRange { client?.selectedRange() ?? NSRange(location: NSNotFound, length: 0) }

    func markedRange() -> NSRange { client?.markedRange() ?? NSRange(location: NSNotFound, length: 0) }

    func attributedSubstring(from range: NSRange) -> NSAttributedString? {
        client?.attributedSubstring(from: range)
    }
}

/// The one IMKCandidates of the server, as a display only (docs/contracts/s3b.md section 8): single row, numbers 1-9,
/// below the composition. `IMKCandidatesSendServerKeyEventFirst` makes IMK offer every key to the
/// controller first while the panel is visible (IMKCandidates.h); the core handles every key
/// while its candidates are open, so the panel is not meant to act on keys itself.
@MainActor
final class CandidatePanelAdapter: CandidatePanel {
    private let panel: IMKCandidates

    init(server: IMKServer) {
        panel = IMKCandidates(server: server, panelType: kIMKSingleRowSteppingCandidatePanel)
        panel.setAttributes([IMKCandidatesSendServerKeyEventFirst: NSNumber(value: true)])
    }

    func show(_ candidates: [String], selected: Int) {
        panel.setCandidateData(candidates.map { $0 as NSString })
        panel.show(kIMKLocateCandidatesBelowHint)
        guard candidates.indices.contains(selected) else { return }
        // User report 2026-10-04 (v0.1.1): the highlight stayed on the first candidate while the
        // core's selection moved. Selecting by `candidateStringIdentifier`, before or after
        // show(), did not move it. The panel is a single row, so a cell's line number is its
        // position (IMKCandidates.h); select by that and read the selection back.
        let target = panel.candidateIdentifier(atLineNumber: selected)
        if target != NSNotFound, panel.selectCandidate(withIdentifier: target), panel.selectedCandidate() == target {
            return
        }
        // Fallback, as DINKIssTyle-IME (MIT) drives IMKCandidates: from the first cell, step the
        // panel's own highlight with its responder actions.
        // The start is read back too: if selecting the first cell did not take, step left past the
        // page's start first, so the steps right never begin from a stale cell.
        let first = panel.candidateIdentifier(atLineNumber: 0)
        if first == NSNotFound || !panel.selectCandidate(withIdentifier: first) || panel.selectedCandidate() != first {
            for _ in 0..<candidates.count { panel.moveLeft(nil) }
        }
        for _ in 0..<selected { panel.moveRight(nil) }
    }

    func hide() {
        panel.hide()
    }
}
