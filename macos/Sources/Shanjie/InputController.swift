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
        case .toggleDemote: #selector(toggleDemote(_:))
        }
    }

    private func perform(_ action: MenuEntry.Action) {
        MainActor.assumeIsolated { session.perform(action) }
    }

    @objc func selectStandardLayout(_ sender: Any?) { perform(.layout(.standard)) }
    @objc func selectEtenLayout(_ sender: Any?) { perform(.layout(.eten)) }
    @objc func clearLearning(_ sender: Any?) { perform(.clear) }
    @objc func toggleLearningBackup(_ sender: Any?) { perform(.toggleBackup) }
    @objc func toggleDemote(_ sender: Any?) { perform(.toggleDemote) }
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
    /// Section 3 "selection capsule": 24 pt tall (a-3, la-3), centred in the bar. The cell's own
    /// spacing and fonts are `CellMetrics` in ShanjieKit.
    static let capsuleHeight = CellMetrics.capsuleHeight
    /// Section 3: the capsule's left edge is about 3 pt from the bar's (a-3).
    static let barInset: CGFloat = 3
    /// Section 3 "cell pitch": 41 pt per single-character cell against a 37 pt capsule (a-3), so
    /// 4 pt between capsules.
    static let cellSpacing: CGFloat = 4 - 2  // our unselected cells measured 2 pt wider apart than a-3 (s-7)

    // Expanded grid (s3b2 sections 8, 9). The grid constants below are first values for main's
    // on-device tuning against a-4; none was measured against a screenshot (the contract's sections 1
    // and 3 hold no a-4 row-pitch or inset data). Column widths come from the cells (`GridLayout`).
    /// One grid row: the 24 pt capsule (section 3) plus a 4 pt gap, the bar's capsule spacing. Derived
    /// from those values, first value, not measured against a screenshot.
    static let gridRowPitch: CGFloat = 28
    /// Top and bottom padding of the grid inside the glass, and its corner radius. Both are first values,
    /// not measured against a screenshot.
    static let gridInset: CGFloat = 5
    static let gridCornerRadius: CGFloat = 16
    /// Scroll indicator (a-4): a thin pill at the right edge, in a gutter beside the last column. The
    /// gutter's 9 pt is a first value, not measured against a screenshot.
    static let scrollGutter: CGFloat = 9
    /// About 5 pt wide in a-4 (measured at 2x zoom); 3 was thinner than Apple's.
    static let scrollThumbWidth: CGFloat = 5
    /// The thumb's shortest height, so a long list still leaves a visible pill: first value, not measured
    /// against a screenshot.
    static let scrollThumbMinHeight: CGFloat = 12
    /// The collapsed bar's expand mark (a-3): a thin separator after the last cell, then a chevron;
    /// the area from the last cell to the bar's end is about 28 pt.
    static let chevronArea: CGFloat = 28
    static let chevronSeparatorHeight: CGFloat = 18
    static let chevronPointSize: CGFloat = 11
    /// The separator sits 2 pt right of the chevron area's left edge (a-3 leaves a thin gap after the
    /// last cell's capsule), and the chevron 3 pt, so the chevron's ink is centred in the rest of the area.
    static let chevronSeparatorInset: CGFloat = 2
    static let chevronImageInset: CGFloat = 3
    /// Section 9: a column widening while the grid is open, the system's default 0.2 s.
    static let widenDuration: TimeInterval = 0.2
    /// bv.mov 420-438: about 0.3 s, system default timing (no custom curve).
    static let expandDuration: TimeInterval = 0.3
    /// cv.mov 499-514 (Apple, 60 fps): the grid folds back into the bar in about 15 frames.
    static let collapseDuration: TimeInterval = 0.25
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
    /// (user report 2026-10-05). Since 9.1 a move updates the existing cells and a scroll swaps only
    /// the entering and leaving rows.
    private let row = GridView()
    private var lastOrigin: NSPoint?
    /// The last shown output, to animate the collapsed -> expanded change from where the bar's cells
    /// were, and to update only the selection when nothing else changed.
    private var shownColumns = 0
    private var shownFirst = 0
    private var shownTotal = 0
    private var shownCandidates: [String] = []
    private var shownNotes: [String?] = []
    private var shownSize = NSSize.zero
    private var shownFrame = NSRect.zero
    private var shownTargets: [NSPoint] = []
    /// Widen-only column widths of the open grid (section 9); empty when collapsed or hidden.
    private var columnWidths: [CGFloat] = []
    /// The cells and the decision which survive an output (ShanjieKit); this class only adds and removes
    /// the views it reports and positions them.
    private let cellSet = CandidateCells()
    /// The chevron/separator or scroll thumb in `row`, and the ones held back until the animation ends.
    private let decor = DecorSet()
    /// True from the start of an expand or collapse animation until it ends; a `show` or `hide` in
    /// that time replaces the running animation (see `settle`). `animation` numbers the groups, so the
    /// completion of an older group cannot end a newer one.
    private var animating = false
    private var animation = 0
    /// Grid cells that stay in place while the grid collapses (cv.mov: the lower rows are cut off by the
    /// shrinking window rather than vanishing); removed when the collapse ends or is replaced.
    private var leaving: [CandidateCell] = []

    init() {
        window = PanelWindow(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
                             backing: .buffered, defer: true)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.level = NSWindow.Level(rawValue: NSWindow.Level.popUpMenu.rawValue + 1)  // McBopomofo's value
        window.hidesOnDeactivate = false
        window.isReleasedWhenClosed = false
        cellSet.onSelect = { [weak self] position in self?.onSelect?(position) }
        glass.contentView = row
        window.contentView = glass
    }

    /// Puts the window and every cell at their final places through a zero-duration animation group.
    /// The intent is that an animator() change made in a group replaces the same property's running
    /// animation, where a plain setFrame would not; unverified until main's on-device recording of a
    /// show during a running expand or collapse confirms it. Every cell is set, even one whose model
    /// frame already reports the target, so a running animation is always replaced.
    private func settle(frame: NSRect) {
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0
            window.animator().setFrame(frame, display: true)
            for (cell, target) in zip(cellSet.cells, shownTargets) { cell.animator().setFrameOrigin(target) }
        }
        animation += 1
        finishAnimation()
    }

    /// The end of an expand or collapse, or of one replaced by `settle`.
    private func finishAnimation() {
        row.frame = NSRect(origin: .zero, size: shownSize)
        leaving.forEach { $0.removeFromSuperview() }
        leaving = []
        decor.flush(into: row)
        animating = false
    }

    /// Runs one expand or collapse animation: the window to `frame`, each cell to its target, with the
    /// system's default timing (no custom curve). At the end the content view takes its final size.
    private func animate(to frame: NSRect, duration: TimeInterval, cells: [CandidateCell], targets: [NSPoint]) {
        animating = true
        animation += 1
        let id = animation
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = duration
            window.animator().setFrame(frame, display: true)
            for (cell, target) in zip(cells, targets) where cell.frame.origin != target {
                cell.animator().setFrameOrigin(target)
            }
        } completionHandler: { [weak self] in
            MainActor.assumeIsolated {
                guard let self, self.animation == id else { return }
                self.finishAnimation()
            }
        }
    }

    func show(_ candidates: [String], notes: [String?], selected: Int, columns: Int, first: Int, total: Int,
              lineRect: NSRect?) {
        let grid = columns > 0

        // Only the selection moved (section 8.7): keep the cells, change which one is selected and which
        // row shows numbers. The glass's content view is never replaced either way.
        let inPlace = window.isVisible && columns == shownColumns && first == shownFirst && total == shownTotal
            && candidates == shownCandidates && notes == shownNotes
        let expanding = grid && shownColumns == 0 && window.isVisible
        // User request 2026-10-05 ("收回沒作動畫"), cv.mov: up on the first row folds the grid back into
        // the bar the way it opened, the window shrinking upward while the cells fly back to the bar.
        let collapsing = !grid && shownColumns > 0 && window.isVisible
        let size: NSSize
        let targets: [NSPoint]
        let oldCells = cellSet.cells
        let update = cellSet.update(candidates: candidates, notes: notes, selected: selected, first: first, columns: columns)
        let newCells = update.cells
        var gridWidth: CGFloat = 0
        var widened = false
        var oldGridXs: [CGFloat] = []
        if inPlace {
            size = shownSize
            targets = shownTargets
        } else {
            // Section 9: the grid's x positions and width come only from GridLayout. Widths only grow
            // while the grid stays open.
            var gridXs: [CGFloat] = []
            if grid {
                let current = shownColumns > 0 ? columnWidths : []
                let lay = GridLayout.layout(cellWidths: newCells.map(\.frame.width), columns: columns, current: current,
                                            inset: Metrics.barInset, spacing: Metrics.cellSpacing,
                                            trailing: Metrics.barInset + Metrics.scrollGutter)
                gridXs = lay.xs
                oldGridXs = GridLayout.layout(cellWidths: [], columns: columns, current: current, inset: Metrics.barInset,
                                              spacing: Metrics.cellSpacing, trailing: 0).xs
                widened = !current.isEmpty && lay.widths != current
                columnWidths = lay.widths
                gridWidth = lay.totalWidth
            } else {
                columnWidths = []
            }

            // Final positions, in the flipped content view: y counts down from the top.
            var t: [NSPoint] = []
            if grid {
                let rows = (candidates.count + columns - 1) / columns
                for i in candidates.indices {
                    t.append(NSPoint(x: gridXs[i % columns],
                                     y: Metrics.gridInset + CGFloat(i / columns) * Metrics.gridRowPitch
                                         + (Metrics.gridRowPitch - Metrics.capsuleHeight) / 2))
                }
                size = NSSize(width: gridWidth, height: Metrics.gridInset * 2 + CGFloat(rows) * Metrics.gridRowPitch)
            } else {
                var x = Metrics.barInset
                for cell in newCells {
                    t.append(NSPoint(x: x, y: (Metrics.barHeight - Metrics.capsuleHeight) / 2))
                    x += cell.frame.width + Metrics.cellSpacing
                }
                size = NSSize(width: x - Metrics.cellSpacing + Metrics.chevronArea, height: Metrics.barHeight)
            }
            targets = t

            // Where the cells start: a new cell whose candidate (global index first + i) was in the old
            // output (shownFirst ..< shownFirst + oldCells.count) starts at that old cell. Expanding, those
            // are the bar's candidates, in grid row 0 or, from the bar's page 2, in later rows;
            // collapsing, each bar cell starts at the grid cell showing its candidate (cv.mov: the
            // second row's candidates fly up into the bar).
            var starts = targets
            if widened && !expanding {
                // A column widened while the grid stays open: cells slide from where the old widths put them.
                for i in starts.indices { starts[i].x = oldGridXs[i % columns] }
            }
            if expanding || collapsing {
                for i in newCells.indices {
                    let j = first + i - shownFirst
                    if oldCells.indices.contains(j) { starts[i] = oldCells[j].frame.origin }
                }
            }

            // Collapsing: the grid cells whose candidates do not move into the bar stay where they are
            // until the window has shrunk past them.
            let staying = collapsing
                ? oldCells.enumerated().filter { j, _ in !(first..<first + newCells.count).contains(shownFirst + j) }.map { $0.1 }
                : []
            leaving.forEach { $0.removeFromSuperview() }
            update.removed.forEach { if !staying.contains($0) { $0.removeFromSuperview() } }
            staying.forEach { $0.ignoresMouse = true }
            leaving = staying
            for (i, cell) in newCells.enumerated() {
                cell.setFrameOrigin(starts[i])
                if !update.reused[i] { row.addSubview(cell) }
            }
            var newDecor: [NSView] = grid ? [] : chevron(barSize: size)
            if grid, total > candidates.count {
                newDecor.append(scrollThumb(first: first, total: total, columns: columns, count: candidates.count,
                                         width: size.width, height: size.height))
            }
            decor.replace(with: newDecor, deferred: expanding || collapsing, in: row)
            if shownSize != size {
                // Collapsing keeps the grid-sized content view until the animation ends, so the cells
                // flying up from the lower rows are not cut off at the start (`animate` sets the size).
                if !collapsing { row.frame = NSRect(origin: .zero, size: size) }
                glass.cornerRadius = grid ? Metrics.gridCornerRadius : size.height / 2
            }
            shownColumns = columns
            shownFirst = first
            shownTotal = total
            shownCandidates = candidates
            shownNotes = notes
            shownSize = size
            shownTargets = targets
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
        // Only the selection moved while an expand or collapse runs (autorepeat on the arrow keys):
        // the cells were updated above and the running animation already goes to this frame and these
        // targets, so leave it running instead of cutting it short.
        let keepRunning = inPlace && animating && frame == shownFrame
        shownFrame = frame
        if keepRunning {
            // nothing to replace
        } else if expanding {
            // bv.mov 420-438: the window grows downward while the first row's cells slide to their columns.
            animate(to: frame, duration: Metrics.expandDuration, cells: newCells, targets: targets)
        } else if collapsing {
            animate(to: frame, duration: Metrics.collapseDuration, cells: newCells, targets: targets)
        } else if widened {
            animate(to: frame, duration: Metrics.widenDuration, cells: newCells, targets: targets)
        } else if animating {
            settle(frame: frame)
        } else {
            if window.frame.size != size { window.setContentSize(size) }
            if window.frame.origin.x != origin.x || window.frame.maxY != origin.y {
                window.setFrameTopLeftPoint(origin)
            }
        }
        if !window.isVisible { window.orderFrontRegardless() }
    }

    /// a-3's expand mark at the bar's right end: a separator line and a chevron, both secondary.
    /// Display only: the bar expands with the down arrow; a click on it does nothing.
    private func chevron(barSize: NSSize) -> [NSView] {
        let left = barSize.width - Metrics.chevronArea
        let line = FilledView(frame: NSRect(x: left + Metrics.chevronSeparatorInset, y: ((barSize.height - Metrics.chevronSeparatorHeight) / 2).rounded(),
                                            width: 1, height: Metrics.chevronSeparatorHeight), color: .separatorColor)
        let config = NSImage.SymbolConfiguration(pointSize: Metrics.chevronPointSize, weight: .medium)
        guard let image = NSImage(systemSymbolName: "chevron.down", accessibilityDescription: nil)?
            .withSymbolConfiguration(config) else { return [line] }
        let view = NSImageView(image: image)
        view.contentTintColor = .tertiaryLabelColor  // h-3: secondary and semibold were brighter than a-3
        view.frame = NSRect(x: left + Metrics.chevronImageInset, y: 0, width: Metrics.chevronArea - Metrics.chevronImageInset, height: barSize.height)
        return [line, view]
    }

    /// a-4's scroll indicator: a thin pill in the gutter, sized and placed by the visible rows' share
    /// of all rows.
    private func scrollThumb(first: Int, total: Int, columns: Int, count: Int, width: CGFloat, height: CGFloat) -> NSView {
        let totalRows = CGFloat((total + columns - 1) / columns)
        let visibleRows = CGFloat((count + columns - 1) / columns)
        let track = height - Metrics.gridInset * 2
        let h = max(Metrics.scrollThumbMinHeight, (track * visibleRows / totalRows).rounded())
        let y = Metrics.gridInset + ((track - h) * CGFloat(first / columns) / max(totalRows - visibleRows, 1)).rounded()
        return FilledView(frame: NSRect(x: width - Metrics.barInset - Metrics.scrollThumbWidth, y: y,
                                        width: Metrics.scrollThumbWidth, height: h),
                          color: .tertiaryLabelColor, cornerRadius: Metrics.scrollThumbWidth / 2)
    }

    func hide() {
        // A running expand animation would keep moving the window after it is ordered out and show it
        // at its animated frame next time; end it first.
        if animating { settle(frame: shownFrame) }
        window.orderOut(nil)
        shownColumns = 0
        shownFirst = 0
        shownTotal = 0
        shownCandidates = []
        shownNotes = []
        shownSize = .zero
        shownTargets = []
        columnWidths = []
        cellSet.reset().forEach { $0.removeFromSuperview() }
        decor.clear()
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
