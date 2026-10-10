import AppKit

/// Layout values of one candidate cell (docs/contracts/s3b2-glass-panel.md sections 3 and 9.1). The
/// cell draws its three strings itself, so every gap below is between the strings' own text origins
/// and ends (`NSAttributedString.size()`); there is no text-field padding in them. They reproduce the
/// layout of the earlier NSTextField cells (4b22c30), which s-7 matched against a-3 within 1 pt: that
/// layout was 2 pt of gap on each side of a label frame whose text sat 2 pt inside it, and
/// `CandidateCellTests` compares the widths of both builds.
public enum CellMetrics {
    /// Section 3 "selection capsule": 24 pt tall (a-3, la-3).
    public static let capsuleHeight: CGFloat = 24
    /// Capsule left edge to the number's text origin (a-3: 5 pt to the number's ink).
    static let numberLeading: CGFloat = 4
    /// The number's text end to the candidate's text origin (a-3: 8 pt between inks).
    static let numberToCandidate: CGFloat = 6
    /// The candidate's text end to the name's text origin (p-1: almost none between capsules).
    static let candidateToName: CGFloat = 6
    /// The last string's text end to the capsule's right edge (a-3: 8 pt after the candidate's ink,
    /// 37 pt capsule).
    static let trailing: CGFloat = 7
    /// Section 3 font sizes: candidate 16, number 9, name 11 (initial values, "the ink of a
    /// Han character is about 0.88 em" so 16 pt gives the 14 pt ink of a-3).
    nonisolated(unsafe) static let candidateFont = NSFont.systemFont(ofSize: 16)
    nonisolated(unsafe) static let numberFont = NSFont.systemFont(ofSize: 9)
    /// 12, not 11: at 11 pt the name 「全形逗號」 measured 42 pt of ink in s-11 against 46 in p-1.
    nonisolated(unsafe) static let nameFont = NSFont.systemFont(ofSize: 12)
}

/// One cell of the bar or grid: number, candidate, optional name, all drawn in `draw(_:)`. The
/// selected cell is an accent-colour capsule with white text (section 2.4). A click reports the
/// cell's current position in the output through its owner (`CandidateCells`).
@MainActor
public final class CandidateCell: NSView {
    public let text: String
    public let note: String?
    public private(set) var numberText: String
    /// Position in the output the cell currently shows; the owner refreshes it on every update.
    public internal(set) var position: Int
    public private(set) var isSelected: Bool
    public private(set) var showsNumber: Bool
    /// Set on grid cells that stay on screen while the grid collapses: their position refers to the
    /// old grid, so they take no clicks.
    public var ignoresMouse = false
    var onClick: ((Int) -> Void)?

    private var numberWidth: CGFloat
    /// The vertical window's numbers take a fixed slot (`numberSlot`); the bar and the grid size each number by itself.
    private let fixedNumberSlot: Bool
    private let candidateWidth: CGFloat, nameWidth: CGFloat
    /// The width the content needs (number, candidate, name), whatever the frame is now: the vertical window stretches every
    /// cell to the row's width, so the layout code reads this, never `frame.width`.
    public private(set) var contentWidth: CGFloat

    private static func measure(_ s: String, _ font: NSFont) -> CGFloat {
        // Rounded up to a device pixel the way a text field's frame is when it is not in a window yet,
        // so the width matches the earlier cells. Measured 2026-10-05: AppKit rounds to the highest
        // scale among the screens, not the main screen's (here: main screen 1x, built-in 2x, text field
        // 37.5 pt; CI's single 1x display: 38.0 pt); a fixed 0.5 pt broke the match on CI.
        // ponytail: a panel shown on a lower-scale screen of a mixed setup lands on half pixels, as the
        // old text fields did; round per panel screen if that ever shows as blur.
        let scale = NSScreen.screens.map(\.backingScaleFactor).max() ?? 2
        return ceil(NSAttributedString(string: s, attributes: [.font: font]).size().width * scale) / scale
    }

    private static func width(numberWidth: CGFloat, candidateWidth: CGFloat, nameWidth: CGFloat, hasNote: Bool) -> CGFloat {
        var width = CellMetrics.numberLeading + numberWidth + CellMetrics.numberToCandidate + candidateWidth
        if hasNote { width += CellMetrics.candidateToName + nameWidth }
        return width + CellMetrics.trailing
    }

    /// The widest of the digits 1-9 in the number font. The vertical window gives every number this much room: the system
    /// font's digits are proportional (measured 2026-10-10: '1' 4.34 pt, '8' 5.92 pt at 9 pt), so with each number's own
    /// width the candidate column would shift between rows and move when a scroll renumbers a cell.
    static let numberSlot: CGFloat = (1...9).map { measure(String($0), CellMetrics.numberFont) }.max() ?? 0

    private var numberRoom: CGFloat { fixedNumberSlot ? Self.numberSlot : Self.measure(numberText, CellMetrics.numberFont) }

    private static func lineHeight(_ font: NSFont) -> CGFloat { ceil(font.ascender - font.descender + font.leading) }
    private static let numberHeight = lineHeight(CellMetrics.numberFont)
    private static let candidateHeight = lineHeight(CellMetrics.candidateFont)
    private static let nameHeight = lineHeight(CellMetrics.nameFont)

    /// The cell is as wide as its content; `showsNumber` false keeps the number's room but hides it
    /// (grid rows other than the selected one, so the rows line up).
    init(position: Int, numberText: String, showsNumber: Bool, text: String, note: String?, selected: Bool,
         fixedNumberSlot: Bool = false) {
        self.fixedNumberSlot = fixedNumberSlot
        self.position = position
        self.numberText = numberText
        self.showsNumber = showsNumber
        self.text = text
        self.note = note
        self.isSelected = selected
        numberWidth = fixedNumberSlot ? Self.numberSlot : Self.measure(numberText, CellMetrics.numberFont)
        candidateWidth = Self.measure(text, CellMetrics.candidateFont)
        nameWidth = note.map { Self.measure($0, CellMetrics.nameFont) } ?? 0
        let width = Self.width(numberWidth: numberWidth, candidateWidth: candidateWidth, nameWidth: nameWidth, hasNote: note != nil)
        contentWidth = width
        super.init(frame: NSRect(x: 0, y: 0, width: width, height: CellMetrics.capsuleHeight))
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityLabel(note.map { text + " " + $0 } ?? text)
        setAccessibilitySelected(selected)
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    /// Where the candidate glyph starts, in this cell's coordinates.
    public var candidateMinX: CGFloat { CellMetrics.numberLeading + numberWidth + CellMetrics.numberToCandidate }

    /// Refreshes everything about the cell that can differ between two outputs showing the same
    /// candidate: its position, whether it is selected, and whether the number shows.
    func update(position: Int, selected: Bool, showsNumber: Bool) {
        self.position = position
        if showsNumber != self.showsNumber {
            self.showsNumber = showsNumber
            needsDisplay = true
        }
        if selected != isSelected {
            isSelected = selected
            setAccessibilitySelected(selected)
            needsDisplay = true
        }
    }

    /// The vertical window renumbers its rows when it scrolls: the cell stays and shows the new number. The frame is not
    /// touched; the owner sets it (a vertical cell is as wide as the row).
    func setNumber(_ text: String) {
        guard text != numberText else { return }
        numberText = text
        numberWidth = numberRoom
        contentWidth = Self.width(numberWidth: numberWidth, candidateWidth: candidateWidth, nameWidth: nameWidth, hasNote: note != nil)
        needsDisplay = true
    }

    public override func draw(_ dirtyRect: NSRect) {
        if isSelected {
            NSColor.controlAccentColor.setFill()
            NSBezierPath(roundedRect: bounds, xRadius: bounds.height / 2, yRadius: bounds.height / 2).fill()
        }
        func put(_ s: String, _ font: NSFont, _ h: CGFloat, _ color: NSColor, x: CGFloat) {
            let y = ((bounds.height - h) / 2).rounded()
            s.draw(at: NSPoint(x: x, y: y), withAttributes: [.font: font, .foregroundColor: isSelected ? NSColor.white : color])
        }
        if showsNumber { put(numberText, CellMetrics.numberFont, Self.numberHeight, .secondaryLabelColor, x: CellMetrics.numberLeading) }
        let cx = candidateMinX
        put(text, CellMetrics.candidateFont, Self.candidateHeight, .labelColor, x: cx)
        if let note { put(note, CellMetrics.nameFont, Self.nameHeight, .secondaryLabelColor, x: cx + candidateWidth + CellMetrics.candidateToName) }
    }

    public override func hitTest(_ point: NSPoint) -> NSView? { ignoresMouse ? nil : super.hitTest(point) }
    public override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    public override func mouseDown(with event: NSEvent) { click() }
    /// What a mouse-down does; also the tests' way to click.
    public func click() { if !ignoresMouse { onClick?(position) } }
}

/// The cells of the candidate bar or grid and the decision which of them survive the next output
/// (docs/contracts/s3b2-glass-panel.md section 9.1). A cell survives when the new output shows the
/// same candidate (same index in the whole list, text, name and number) in the same mode, so
/// scrolling the grid one row keeps all but the entering and leaving rows. The owner only adds and
/// removes the views reported here and positions them.
@MainActor
public final class CandidateCells {
    public struct Update {
        /// One per candidate of the new output, in output order.
        public let cells: [CandidateCell]
        /// `reused[i]` is true when `cells[i]` was already on screen.
        public let reused: [Bool]
        public let removed: [CandidateCell]
    }

    /// Called with the clicked cell's current position in the latest output.
    public var onSelect: ((Int) -> Void)?
    public private(set) var cells: [CandidateCell] = []
    private var columns = 0
    private var renumbered = false
    private var first = 0

    public init() {}

    /// `renumber`: the vertical window's rule (candidate-vertical contract section 2.3): a cell survives when the candidate
    /// and its name are the same, and shows its new number, so a one-row scroll keeps eight of nine cells; its cells give the
    /// number a fixed slot. Off (the bar and the grid), the number is part of the key and sizes its own cell as before.
    public func update(candidates: [String], notes: [String?], selected: Int, first newFirst: Int, columns newColumns: Int,
                       renumber: Bool = false) -> Update {
        let grid = newColumns > 0
        let selectedRow = grid ? selected / newColumns : 0
        // Candidate at global index g sat at old position g - first.
        // The bar and the vertical window both have 0 columns, but their cells differ (a fixed number slot, renumbering), so
        // the rule they were made under is part of the mode. This is CandidateCells' own contract (cells made under one rule
        // are never reused under the other), not something the panel relies on: it also clears everything on a change of kind.
        let sameMode = newColumns == columns && renumber == renumbered
        var next: [CandidateCell] = []
        var reused: [Bool] = []
        var kept = Set<ObjectIdentifier>()
        for (i, text) in candidates.enumerated() {
            let numberText = String(grid ? i % newColumns + 1 : i + 1)
            let shows = selected >= 0 && (!grid || i / newColumns == selectedRow)
            let old = newFirst + i - first
            if sameMode, cells.indices.contains(old), cells[old].text == text, cells[old].note == notes[i],
               renumber || cells[old].numberText == numberText {
                let cell = cells[old]
                cell.setNumber(numberText)
                cell.update(position: i, selected: i == selected, showsNumber: shows)
                next.append(cell)
                reused.append(true)
                kept.insert(ObjectIdentifier(cell))
            } else {
                let cell = CandidateCell(position: i, numberText: numberText, showsNumber: shows, text: text,
                                         note: notes[i], selected: i == selected, fixedNumberSlot: renumber)
                cell.onClick = { [weak self] position in self?.onSelect?(position) }
                next.append(cell)
                reused.append(false)
            }
        }
        let removed = cells.filter { !kept.contains(ObjectIdentifier($0)) }
        cells = next
        columns = newColumns
        renumbered = renumber
        first = newFirst
        return Update(cells: next, reused: reused, removed: removed)
    }

    /// Forgets every cell (the panel hides) and returns them for removal.
    public func reset() -> [CandidateCell] {
        let old = cells
        cells = []
        columns = 0
        renumbered = false
        first = 0
        return old
    }
}
