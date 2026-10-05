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
    /// asks in a window (`AlertDialogs`).
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
        case .clear: #selector(clearLearning(_:))
        case .toggleBackup: #selector(toggleLearningBackup(_:))
        }
    }

    private func perform(_ action: MenuEntry.Action) {
        MainActor.assumeIsolated { session.perform(action) }
    }

    @objc func selectStandardLayout(_ sender: Any?) { perform(.layout(.standard)) }
    @objc func selectEtenLayout(_ sender: Any?) { perform(.layout(.eten)) }
    @objc func clearLearning(_ sender: Any?) { perform(.clear) }
    @objc func toggleLearningBackup(_ sender: Any?) { perform(.toggleBackup) }
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

    /// s3b2 section 2.2, as McBopomofo does: from the last marked character back until the client
    /// reports a rectangle other than the (0, 0) origin it leaves untouched when it has none.
    func lineRect() -> NSRect? {
        guard let client else { return nil }
        let marked = client.markedRange()
        guard marked.location != NSNotFound, marked.length > 0 else { return nil }
        var rect = NSRect(x: 0, y: 0, width: 16, height: 16)
        var index = NSMaxRange(marked) - 1
        while rect.origin.x == 0, rect.origin.y == 0, index >= marked.location {
            _ = client.attributes(forCharacterIndex: index, lineHeightRectangle: &rect)
            index -= 1
        }
        return rect.origin == .zero ? nil : rect
    }
}

/// The panel that cannot take focus: keys and clicks never make it key or main (s3b2 section 2.1).
private final class PanelWindow: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// Layout values of the candidate bar (docs/contracts/s3b2-glass-panel.md section 3). The "Apple"
/// column was measured on 1x screenshots (1 px = 1 pt) of Apple Zhuyin, kept in main's scratchpad;
/// these are first values, to be corrected against our own screenshots.
private enum Metrics {
    /// Section 3 "candidate bar": about 30 pt tall (a-3, la-3), corner radius half of it.
    static let barHeight: CGFloat = 30
    /// Section 3 "selection capsule": 24 pt tall (a-3, la-3), centred in the bar.
    static let capsuleHeight: CGFloat = 24
    /// Section 3: the capsule's left edge is about 3 pt from the bar's (a-3).
    static let barInset: CGFloat = 3
    /// Section 3 "cell pitch": 41 pt per single-character cell against a 37 pt capsule (a-3), so
    /// 4 pt between capsules.
    static let cellSpacing: CGFloat = 4
    /// Section 3: capsule left edge to the number, about 5 pt (a-3).
    static let numberLeading: CGFloat = 5
    /// Section 3: number to candidate, about 8 pt (a-3).
    static let numberToCandidate: CGFloat = 8
    /// Section 3: candidate to the capsule's right edge, about 6 pt (a-3).
    static let trailing: CGFloat = 6
    /// Section 3 "name": between a mark and its name, the capsules in p-1 leave almost none; 2 pt.
    static let candidateToName: CGFloat = 2
    /// Section 3 font sizes: candidate 16, number 9, name 11 (initial values, "the ink of a
    /// Han character is about 0.88 em" so 16 pt gives the 14 pt ink of a-3).
    static let candidateFont = NSFont.systemFont(ofSize: 16)
    static let numberFont = NSFont.systemFont(ofSize: 9)
    static let nameFont = NSFont.systemFont(ofSize: 11)
}

/// One cell of the bar: number, candidate, optional name. The selected cell is an accent-colour
/// capsule with white text (section 2.4). A click reports the cell's position.
private final class CellView: NSView {
    private let number: NSTextField, candidate: NSTextField, name: NSTextField?
    private let selected: Bool
    private let onClick: () -> Void

    init(index: Int, text: String, note: String?, selected: Bool, onClick: @escaping () -> Void) {
        func label(_ s: String, _ font: NSFont, _ color: NSColor) -> NSTextField {
            let t = NSTextField(labelWithString: s)
            t.font = font
            t.textColor = selected ? .white : color
            t.sizeToFit()
            return t
        }
        number = label(String(index + 1), Metrics.numberFont, .secondaryLabelColor)
        candidate = label(text, Metrics.candidateFont, .labelColor)
        name = note.map { label($0, Metrics.nameFont, .secondaryLabelColor) }
        self.selected = selected
        self.onClick = onClick

        var width = Metrics.numberLeading + number.frame.width + Metrics.numberToCandidate + candidate.frame.width
        if let name { width += Metrics.candidateToName + name.frame.width }
        width += Metrics.trailing
        super.init(frame: NSRect(x: 0, y: 0, width: width, height: Metrics.capsuleHeight))

        var x = Metrics.numberLeading
        for (view, isNumber) in [(number, true), (candidate, false)] + (name.map { [($0, false)] } ?? []) {
            view.setFrameOrigin(NSPoint(x: x, y: ((Metrics.capsuleHeight - view.frame.height) / 2).rounded()))
            addSubview(view)
            x += view.frame.width + (isNumber ? Metrics.numberToCandidate : Metrics.candidateToName)
        }
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityLabel(note.map { text + " " + $0 } ?? text)
        setAccessibilitySelected(selected)
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    /// Where the candidate glyph starts, in this cell's coordinates.
    var candidateMinX: CGFloat { candidate.frame.minX }

    override func draw(_ dirtyRect: NSRect) {
        guard selected else { return }
        NSColor.controlAccentColor.setFill()
        NSBezierPath(roundedRect: bounds, xRadius: bounds.height / 2, yRadius: bounds.height / 2).fill()
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) { onClick() }
}

/// The candidate bar (docs/contracts/s3b2-glass-panel.md): a borderless, non-activating panel with
/// a Liquid Glass row of cells, drawn by us because IMKCandidates ignores fonts and cannot show a
/// smaller name. Display only: it never becomes key and never receives keys; a click on a cell is
/// reported by position through `onSelect`. It logs nothing (section 2.5).
@MainActor
final class CandidatePanelAdapter: CandidatePanel {
    var onSelect: ((Int) -> Void)?
    private let window: PanelWindow
    private let glass = NSGlassEffectView()
    private var lastOrigin: NSPoint?

    init() {
        window = PanelWindow(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
                             backing: .buffered, defer: true)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.level = NSWindow.Level(rawValue: NSWindow.Level.popUpMenu.rawValue + 1)  // McBopomofo's value
        window.hidesOnDeactivate = false
        window.isReleasedWhenClosed = false
        window.contentView = glass
    }

    func show(_ candidates: [String], notes: [String?], selected: Int, lineRect: NSRect?) {
        var cells: [CellView] = []
        for (i, text) in candidates.enumerated() {
            cells.append(CellView(index: i, text: text, note: notes[i], selected: i == selected) { [weak self] in
                self?.onSelect?(i)
            })
        }
        let row = NSView()
        var x = Metrics.barInset
        let y = (Metrics.barHeight - Metrics.capsuleHeight) / 2
        for cell in cells {
            cell.setFrameOrigin(NSPoint(x: x, y: y))
            row.addSubview(cell)
            x += cell.frame.width + Metrics.cellSpacing
        }
        let size = NSSize(width: x - Metrics.cellSpacing + Metrics.barInset, height: Metrics.barHeight)
        row.frame = NSRect(origin: .zero, size: size)
        glass.contentView = row
        glass.cornerRadius = size.height / 2
        window.setContentSize(size)

        let screens = NSScreen.screens
        let rectScreen = lineRect.flatMap { r in screens.firstIndex { $0.frame.contains(r.origin) } }
        let main = NSScreen.main.flatMap { m in screens.firstIndex(of: m) } ?? 0
        guard !screens.isEmpty else { return }
        let alignOffset = Metrics.barInset + (cells.first?.candidateMinX ?? 0)
        let origin = PanelPlacement.topLeft(
            lineRect: lineRect, lastOrigin: lastOrigin, size: size, alignOffset: alignOffset,
            screens: screens.map(\.visibleFrame), rectScreen: rectScreen, main: main)
        lastOrigin = origin
        window.setFrameTopLeftPoint(origin)
        window.orderFrontRegardless()
    }

    func hide() {
        window.orderOut(nil)
    }
}

/// The clear's windows (S4 section 4). The input method is an agent app (LSUIElement): it comes
/// forward for the window, then gives focus back to the app the user was typing in. The window is
/// not run modally: `runModal` inside the menu action would stop the input method from serving
/// every other app while it is open (2026-10-05 review), so the buttons call back instead.
@MainActor
final class AlertDialogs: NSObject, LearningDialogs {
    private var open: NSAlert?
    private var reply: ((Int) -> Void)?
    /// The app to give focus back to, captured when the first window opens; a follow-up window
    /// (the failure after a confirmed clear) keeps it, since the input method is frontmost then.
    private var previous: NSRunningApplication?

    func confirmClear(_ answer: @escaping @MainActor (Bool) -> Void) {
        // 取消 first: it is the default button (Return), so a destructive action is never one
        // keystroke away; 清除 is marked destructive.
        show(.warning, DialogText.clearTitle, DialogText.clearMessage,
             buttons: [DialogText.cancel, DialogText.clearButton], destructive: 1) { answer($0 == 1) }
    }

    func clearFailed() {
        show(.critical, DialogText.failedTitle, DialogText.failedMessage, buttons: [DialogText.ok], destructive: nil) { _ in }
    }

    private func show(_ style: NSAlert.Style, _ title: String, _ message: String, buttons: [String],
                      destructive: Int?, then: @escaping (Int) -> Void) {
        guard open == nil else { return }
        let alert = NSAlert()
        alert.alertStyle = style
        alert.messageText = title
        alert.informativeText = message
        for (i, title) in buttons.enumerated() {
            let button = alert.addButton(withTitle: title)
            button.tag = i
            button.target = self
            button.action = #selector(pressed(_:))
            button.hasDestructiveAction = i == destructive
        }
        if let front = NSWorkspace.shared.frontmostApplication, front != NSRunningApplication.current {
            previous = front
        }
        open = alert
        reply = then
        alert.layout()
        alert.window.level = .modalPanel
        alert.window.center()
        NSApp.activate()
        alert.window.makeKeyAndOrderFront(nil)
    }

    @objc private func pressed(_ sender: NSButton) {
        guard let alert = open else { return }
        alert.window.orderOut(nil)
        open = nil
        let then = reply
        reply = nil
        then?(sender.tag)
        // A follow-up window (the failure) opened from `then` keeps the input method forward.
        if open == nil {
            previous?.activate()
            previous = nil
        }
    }
}
