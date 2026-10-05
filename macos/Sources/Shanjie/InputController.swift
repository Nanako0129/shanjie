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

    /// s3b2 section 2.2, as McBopomofo does (InputMethodController.swift:910): the index is within
    /// the marked text, starting at the character before the cursor, back until the client reports
    /// a rectangle other than the (0, 0) origin it leaves untouched when it has none.
    func lineRect(cursor: Int) -> NSRect? {
        guard let client else { return nil }
        let marked = client.markedRange()
        guard marked.location != NSNotFound, marked.length > 0 else { return nil }
        var rect = NSRect(x: 0, y: 0, width: 16, height: 16)
        var index = min(max(cursor - 1, 0), marked.length - 1)
        while rect.origin.x == 0, rect.origin.y == 0, index >= 0 {
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
    static let cellSpacing: CGFloat = 4 - 2  // our unselected cells measured 2 pt wider apart than a-3 (s-7)
    // The gaps below are between label frames, and an NSTextField label's frame is wider than its
    // ink (padding plus the glyph's side bearings). The values are the a-3 gaps minus what our own
    // bar (s-3, s-7, 2026-10-05) added; s-7 then matched a-3 within 1 pt.
    /// Section 3: capsule left edge to the number's ink, 5 pt in a-3.
    static let numberLeading: CGFloat = 2
    /// Section 3: number ink to candidate ink, 8 pt in a-3.
    static let numberToCandidate: CGFloat = 2
    /// Section 3: candidate ink to the capsule's right edge, 8 pt in a-3 (37 pt cell).
    static let trailing: CGFloat = 5
    /// Section 3 "name": between a mark and its name, the capsules in p-1 leave almost none; 2 pt.
    static let candidateToName: CGFloat = 2
    /// Section 3 font sizes: candidate 16, number 9, name 11 (initial values, "the ink of a
    /// Han character is about 0.88 em" so 16 pt gives the 14 pt ink of a-3).
    static let candidateFont = NSFont.systemFont(ofSize: 16)
    static let numberFont = NSFont.systemFont(ofSize: 9)
    /// 12, not 11: at 11 pt 「全形逗號」 measured 42 pt of ink in s-11 against 46 in p-1.
    static let nameFont = NSFont.systemFont(ofSize: 12)
    /// A label's ink starts about 2 pt inside its frame (s-7: the first candidate's ink sat 2 pt right
    /// of the composed text's); the bar aligns ink, not frames.
    static let labelInset: CGFloat = 2

    // Expanded grid (s3b2 section 8). The grid's row pitch, column width and corner radius are first
    // values for main's on-device tuning against a-4 (six columns, five rows) and p-2 (three columns).
    /// a-4: five rows are visible; more candidates scroll.
    static let gridRows = 5
    /// One grid row: the 24 pt capsule plus a 4 pt gap (the bar's 4 pt capsule spacing, section 3).
    static let gridRowPitch: CGFloat = 28
    /// Fixed column width of the six-column grid (a-4): wide enough for a two-character word.
    static let gridColumnWidth: CGFloat = 64
    /// Fixed column width of the three-column punctuation grid: two word columns, so the grid is as wide
    /// as the six-column one, as in p-2 (about 398 pt, the same as a-4). 104 left it narrower (g-16).
    static let gridPunctuationColumnWidth: CGFloat = 2 * 64
    /// Top and bottom padding of the grid inside the glass, and its corner radius.
    static let gridInset: CGFloat = 5
    static let gridCornerRadius: CGFloat = 16
    /// Scroll indicator (a-4): a thin pill at the right edge, in a gutter beside the last column.
    static let scrollGutter: CGFloat = 9
    /// About 5 pt wide in a-4 (measured at 2x zoom); 3 was thinner than Apple's.
    static let scrollThumbWidth: CGFloat = 5
    static let scrollThumbMinHeight: CGFloat = 12
    /// The collapsed bar's expand mark (a-3): a thin separator after the last cell, then a chevron;
    /// the area from the last cell to the bar's end is about 28 pt.
    static let chevronArea: CGFloat = 28
    static let chevronSeparatorHeight: CGFloat = 18
    static let chevronPointSize: CGFloat = 11
    /// bv.mov 420-438: about 0.3 s, system default timing (no custom curve).
    static let expandDuration: TimeInterval = 0.3
}

/// One cell of the bar: number, candidate, optional name. The selected cell is an accent-colour
/// capsule with white text (section 2.4). A click reports the cell's position.
private final class CellView: NSView {
    private let number: NSTextField, candidate: NSTextField, name: NSTextField?
    private let selected: Bool
    private let onClick: () -> Void

    /// `numberText`: the shown number; `showsNumber` false keeps its room but hides it (grid rows other
    /// than the selected one, p-2). `fixedWidth`: a fixed cell width (grid), `nil` to fit the content (bar).
    init(numberText: String, showsNumber: Bool = true, fixedWidth: CGFloat? = nil, text: String, note: String?,
         selected: Bool, onClick: @escaping () -> Void) {
        func label(_ s: String, _ font: NSFont, _ color: NSColor) -> NSTextField {
            let t = NSTextField(labelWithString: s)
            t.font = font
            t.textColor = selected ? .white : color
            t.sizeToFit()
            return t
        }
        number = label(numberText, Metrics.numberFont, .secondaryLabelColor)
        number.isHidden = !showsNumber
        candidate = label(text, Metrics.candidateFont, .labelColor)
        name = note.map { label($0, Metrics.nameFont, .secondaryLabelColor) }
        self.selected = selected
        self.onClick = onClick

        var width = Metrics.numberLeading + number.frame.width + Metrics.numberToCandidate + candidate.frame.width
        if let name { width += Metrics.candidateToName + name.frame.width }
        width += Metrics.trailing
        if let fixed = fixedWidth {
            // A fixed column: a candidate wider than its room is truncated, never widens the column.
            let room = fixed - width + candidate.frame.width
            if room < candidate.frame.width {
                candidate.lineBreakMode = .byTruncatingTail
                candidate.setFrameSize(NSSize(width: max(room, 0), height: candidate.frame.height))
            }
            width = fixed
        }
        super.init(frame: NSRect(x: 0, y: 0, width: width, height: Metrics.capsuleHeight))

        var x = Metrics.numberLeading
        for (view, isNumber) in [(number, true), (candidate, false)] + (name.map { [($0, false)] } ?? []) {
            view.setFrameOrigin(NSPoint(x: x, y: ((Metrics.capsuleHeight - view.frame.height) / 2).rounded()))
            addSubview(view)
            x += view.frame.width + (isNumber ? Metrics.numberToCandidate : Metrics.candidateToName)
        }
        // p-2: in a fixed grid column the name sits at the column's right edge, not right after the mark.
        if fixedWidth != nil, let name {
            name.setFrameOrigin(NSPoint(x: max(x - Metrics.candidateToName - name.frame.width, width - Metrics.trailing - name.frame.width),
                                        y: name.frame.origin.y))
        }
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityLabel(note.map { text + " " + $0 } ?? text)
        setAccessibilitySelected(selected)
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    /// Where the candidate glyph starts, in this cell's coordinates.
    var candidateMinX: CGFloat { candidate.frame.minX + Metrics.labelInset }

    override func draw(_ dirtyRect: NSRect) {
        guard selected else { return }
        NSColor.controlAccentColor.setFill()
        NSBezierPath(roundedRect: bounds, xRadius: bounds.height / 2, yRadius: bounds.height / 2).fill()
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) { onClick() }
}

/// The glass's one content view. Flipped, so cells are placed from the top-left and a window that
/// grows downward leaves the first row where it was.
private final class GridView: NSView {
    override var isFlipped: Bool { true }
}

/// The candidate bar and its expanded grid (docs/contracts/s3b2-glass-panel.md): a borderless,
/// non-activating panel with a Liquid Glass row of cells, drawn by us because IMKCandidates ignores
/// fonts and cannot show a smaller name. Display only: it never becomes key and never receives keys;
/// a click on a cell is reported by position through `onSelect`. It logs nothing (section 2.5).
@MainActor
final class CandidatePanelAdapter: CandidatePanel {
    var onSelect: ((Int) -> Void)?
    private let window: PanelWindow
    private let glass = NSGlassEffectView()
    /// The glass's one content view, kept for the panel's lifetime: replacing the glass's content,
    /// resizing or re-ordering the window on every selection move made the glass's glow flicker
    /// (user report 2026-10-05), so a move only swaps the cells inside.
    private let row = GridView()
    private var lastOrigin: NSPoint?
    /// The last shown output's columns, first and the cells in it, to animate the collapsed -> expanded
    /// change from where the bar's cells were.
    private var shownColumns = 0
    private var shownFirst = 0
    private var cells: [CellView] = []

    init() {
        window = PanelWindow(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
                             backing: .buffered, defer: true)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.level = NSWindow.Level(rawValue: NSWindow.Level.popUpMenu.rawValue + 1)  // McBopomofo's value
        window.hidesOnDeactivate = false
        window.isReleasedWhenClosed = false
        glass.contentView = row
        window.contentView = glass
    }

    func show(_ candidates: [String], notes: [String?], selected: Int, columns: Int, first: Int, total: Int,
              lineRect: NSRect?) {
        let grid = columns > 0
        let selectedRow = grid ? selected / columns : 0
        let columnWidth = columns == 3 ? Metrics.gridPunctuationColumnWidth : Metrics.gridColumnWidth
        var newCells: [CellView] = []
        for (i, text) in candidates.enumerated() {
            let onClick: () -> Void = { [weak self] in self?.onSelect?(i) }
            newCells.append(grid
                ? CellView(numberText: String(i % columns + 1), showsNumber: i / columns == selectedRow,
                           fixedWidth: columnWidth - Metrics.cellSpacing, text: text, note: notes[i],
                           selected: i == selected, onClick: onClick)
                : CellView(numberText: String(i + 1), text: text, note: notes[i], selected: i == selected,
                           onClick: onClick))
        }

        // Final positions, in the flipped content view: y counts down from the top.
        var targets: [NSPoint] = []
        var size: NSSize
        if grid {
            let rows = (candidates.count + columns - 1) / columns
            for i in candidates.indices {
                targets.append(NSPoint(x: Metrics.barInset + CGFloat(i % columns) * columnWidth,
                                       y: Metrics.gridInset + CGFloat(i / columns) * Metrics.gridRowPitch
                                           + (Metrics.gridRowPitch - Metrics.capsuleHeight) / 2))
            }
            size = NSSize(width: Metrics.barInset * 2 + CGFloat(columns) * columnWidth + Metrics.scrollGutter,
                          height: Metrics.gridInset * 2 + CGFloat(rows) * Metrics.gridRowPitch)
        } else {
            var x = Metrics.barInset
            for cell in newCells {
                targets.append(NSPoint(x: x, y: (Metrics.barHeight - Metrics.capsuleHeight) / 2))
                x += cell.frame.width + Metrics.cellSpacing
            }
            size = NSSize(width: x - Metrics.cellSpacing + Metrics.chevronArea, height: Metrics.barHeight)
        }

        // Where the first row's cells start when the bar expands: the bar cell showing the same candidate.
        let expanding = grid && shownColumns == 0 && window.isVisible
        var starts = targets
        if expanding {
            for i in 0..<min(columns, newCells.count) {
                let j = first + i - shownFirst
                if cells.indices.contains(j) { starts[i] = cells[j].frame.origin }
            }
        }

        row.subviews.forEach { $0.removeFromSuperview() }
        for (i, cell) in newCells.enumerated() {
            cell.setFrameOrigin(starts[i])
            row.addSubview(cell)
        }
        if !grid { addChevron(barSize: size) }
        if grid, total > candidates.count {
            let thumb = scrollThumb(first: first, total: total, columns: columns, count: candidates.count, height: size.height)
            row.addSubview(thumb)
        }
        cells = newCells
        shownColumns = columns
        shownFirst = first

        let fits = window.frame.size == size
        if !fits {
            row.frame = NSRect(origin: .zero, size: size)
            glass.cornerRadius = grid ? Metrics.gridCornerRadius : size.height / 2
        }

        let screens = NSScreen.screens
        let rectScreen = lineRect.flatMap { r in screens.firstIndex { $0.frame.contains(r.origin) } }
        let main = NSScreen.main.flatMap { m in screens.firstIndex(of: m) } ?? 0
        guard !screens.isEmpty else { return }
        let alignOffset = Metrics.barInset + (newCells.first?.candidateMinX ?? 0)
        let origin = PanelPlacement.topLeft(
            lineRect: lineRect, lastOrigin: lastOrigin, size: size, alignOffset: alignOffset,
            screens: screens.map(\.visibleFrame), rectScreen: rectScreen, main: main)
        lastOrigin = origin
        let frame = NSRect(x: origin.x, y: origin.y - size.height, width: size.width, height: size.height)
        if expanding {
            // bv.mov 420-438: the window grows downward while the first row's cells slide to their
            // columns; the system's default timing, no custom curve.
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = Metrics.expandDuration
                window.animator().setFrame(frame, display: true)
                for (i, cell) in newCells.enumerated() where starts[i] != targets[i] {
                    cell.animator().setFrameOrigin(targets[i])
                }
            }
        } else {
            if !fits { window.setContentSize(size) }
            if window.frame.origin.x != origin.x || window.frame.maxY != origin.y {
                window.setFrameTopLeftPoint(origin)
            }
        }
        if !window.isVisible { window.orderFrontRegardless() }
    }

    /// a-3's expand mark at the bar's right end: a separator line and a chevron, both secondary.
    /// Display only: the bar expands with the down arrow; a click on it does nothing.
    private func addChevron(barSize: NSSize) {
        let left = barSize.width - Metrics.chevronArea
        let line = NSView(frame: NSRect(x: left + 2, y: ((barSize.height - Metrics.chevronSeparatorHeight) / 2).rounded(),
                                        width: 1, height: Metrics.chevronSeparatorHeight))
        line.wantsLayer = true
        line.layer?.backgroundColor = NSColor.separatorColor.cgColor
        row.addSubview(line)
        let config = NSImage.SymbolConfiguration(pointSize: Metrics.chevronPointSize, weight: .medium)
        guard let image = NSImage(systemSymbolName: "chevron.down", accessibilityDescription: nil)?
            .withSymbolConfiguration(config) else { return }
        let view = NSImageView(image: image)
        view.contentTintColor = .tertiaryLabelColor  // h-3: secondary and semibold were brighter than a-3
        view.frame = NSRect(x: left + 3, y: 0, width: Metrics.chevronArea - 3, height: barSize.height)
        row.addSubview(view)
    }

    /// a-4's scroll indicator: a thin pill in the gutter, sized and placed by the visible rows' share
    /// of all rows.
    private func scrollThumb(first: Int, total: Int, columns: Int, count: Int, height: CGFloat) -> NSView {
        let totalRows = CGFloat((total + columns - 1) / columns)
        let visibleRows = CGFloat((count + columns - 1) / columns)
        let track = height - Metrics.gridInset * 2
        let h = max(Metrics.scrollThumbMinHeight, (track * visibleRows / totalRows).rounded())
        let y = Metrics.gridInset + ((track - h) * CGFloat(first / columns) / max(totalRows - visibleRows, 1)).rounded()
        let width = Metrics.barInset * 2 + CGFloat(columns) * (columns == 3 ? Metrics.gridPunctuationColumnWidth : Metrics.gridColumnWidth) + Metrics.scrollGutter
        let thumb = NSView(frame: NSRect(x: width - Metrics.barInset - Metrics.scrollThumbWidth, y: y,
                                         width: Metrics.scrollThumbWidth, height: h))
        thumb.wantsLayer = true
        thumb.layer?.backgroundColor = NSColor.tertiaryLabelColor.cgColor
        thumb.layer?.cornerRadius = Metrics.scrollThumbWidth / 2
        return thumb
    }

    func hide() {
        window.orderOut(nil)
        shownColumns = 0
        shownFirst = 0
        cells = []
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
