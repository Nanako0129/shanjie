import CoreGraphics

/// docs/contracts/candidate-vertical.md section 2.3: the vertical candidate window's geometry. Pure, so it is testable without
/// a screen; the panel adapter in the app target only positions what these return. Coordinates are those of the glass's
/// flipped content view (y counts down from the top).
///
/// Sources: the Apple Zhuyin vertical measurements of 2026-10-10 (contract section 1; main's imeshot screenshots, which are
/// private and were not available to the implementation). Values the contract does not list are first guesses and say so;
/// the user's on-device rounds converge them.
public enum VerticalLayout {
    /// Rows visible at once; the core's `PAGE_SIZE` (contract section 1: nine rows, the tenth only half in view).
    public static let visibleRows = 9
    /// Row pitch: the selection box's lower edge moved 109, 137, 165, 193 over four steps in Apple's screenshots at 1 px = 1 pt
    /// (contract section 1).
    public static let rowPitch: CGFloat = 28
    /// The panel is at least this wide. Source: the selection capsule of Apple's vertical window measured about 226 pt wide
    /// (contract section 1); the contract (section 2.3) applies it to the panel's width, so the capsule here is a few points
    /// narrower than Apple's. First guess for the difference, not measured.
    public static let minWidth: CGFloat = 226
    /// Space above the first row and below the last, and beside the capsule. First guess (the horizontal grid's value,
    /// `Metrics.gridInset`); Apple's was not measured.
    public static let inset: CGFloat = 5
    /// Space between the panel's left edge and the capsule. First guess (the horizontal bar's `barInset`).
    public static let sideInset: CGFloat = 3
    /// The glass's corner radius. First guess (the horizontal grid's value); Apple's was not measured.
    public static let cornerRadius: CGFloat = 16
    /// The scroll indicator's gutter at the right edge, reserved only while the list is longer than the window, so the width
    /// never changes with it. First guess (the grid's gutter); Apple's was not measured.
    public static let scrollGutter: CGFloat = 9
    /// The indicator's width and shortest height. First guess (the grid's values, a-4: about 5 pt wide).
    public static let thumbWidth: CGFloat = 5
    public static let thumbMinHeight: CGFloat = 12

    /// The values of the core's `candidate_vertical` (candidate-vertical contract sections 2.2 and 2.4); 0 is horizontal.
    public static let candidates = 1
    public static let predictions = 2

    /// What is on screen after one output, and what the next one needs to decide about: `kind` is the output's value, `rows`
    /// the row count the height comes from, `width` the panel's width.
    public struct Plan: Equatable, Sendable {
        public var kind: Int
        public var rows: Int
        public var width: CGFloat
        /// Room reserved at the right for the scroll indicator: only the candidate window, only when its list is longer than
        /// the rows it shows. Decided here once; the panel uses this and no other condition.
        public var gutter: CGFloat
        public var size: CGSize { CGSize(width: width, height: VerticalLayout.height(rows: rows)) }
        /// What the bar and the grid (kind 0) leave behind: they size themselves, only the kind matters.
        public static let horizontal = Plan(kind: 0, rows: 0, width: 0, gutter: 0)
    }

    /// Candidate-vertical contract section 2.4: the size of the panel for one output (whether the content resets is `resets`).
    /// - `previous`: the plan of the last output that is still on screen, `nil` when nothing is.
    /// - `kind`: this output's `candidate_vertical`. After a change of value (`resets`) nothing carries over from `previous`:
    ///   not the rows and not the width (a window opening over a wide prediction row starts at the minimum).
    /// - Kind 1 (candidate window): the height is fixed when it opens (`min(count, 9)`); the width only grows.
    /// - Kind 2 (prediction row): the height follows `count` on every output; the width only grows while the outputs stay 2.
    /// - Kind 0 sizes nothing here (the bar and the grid have their own layout): `Plan.horizontal`.
    /// - `count`, `total`: the shown rows and the whole list (the indicator is needed when `total > count`).
    /// - `contentWidths`: each shown cell's `CandidateCell.contentWidth`, taken from the cells that exist after `resets`
    ///   cleared what had to go, so nothing is measured twice.
    public static func plan(previous: Plan?, kind: Int, count: Int, total: Int, contentWidths: [CGFloat]) -> Plan {
        guard kind != 0 else { return .horizontal }
        let base = resets(previous: previous, kind: kind) ? nil : previous
        let rows = kind == predictions ? min(count, visibleRows) : (base?.rows ?? min(count, visibleRows))
        let gutter = kind == candidates && total > count ? scrollGutter : 0
        return Plan(kind: kind, rows: rows,
                    width: width(contentWidths: contentWidths, current: base?.width ?? 0, gutter: gutter), gutter: gutter)
    }

    /// Whether an output of this `kind` clears what `previous` left on screen: any change of value, `nil` (nothing on screen)
    /// included. The panel asks this first, clears, makes its cells and only then asks `plan` for the size.
    public static func resets(previous: Plan?, kind: Int) -> Bool { previous?.kind != kind }

    /// Row separators. Source: Apple Zhuyin's vertical window, main's imeshot shots of 2026-10-10 (1x external display,
    /// 1 px = 1 pt, dark mode): a line in the gap between two consecutive visible rows at 111, 139, 167 ... for capsules whose
    /// tops are at 86, 114, 142 ..., i.e. the capsule's top + 25; it runs from 6 pt right of the capsule's left edge to 6 pt
    /// left of its right edge (capsule x 11...237, line x 17...231); no line above the first row or below the last.
    public static let separatorBelowCapsuleTop: CGFloat = 25
    public static let separatorInset: CGFloat = 6

    /// One separator between row `index` and row `index + 1` of the vertical panel (flipped content coordinates).
    public struct Separator: Equatable {
        public var index: Int
        public var visible: Bool
        public var x: CGFloat
        public var y: CGFloat
        public var width: CGFloat
        public var height: CGFloat
    }

    /// The `rows - 1` separators between consecutive rows, with the rule of which show: the two touching the selected row
    /// (above and below it) are hidden, the others show; with no selection (`selected` < 0, the not-entered prediction row)
    /// all show.
    /// - `thickness`: 1 device pixel (the caller passes 1 / backingScaleFactor). Measured only at 1x; the thickness at 2x is
    ///   inferred, not measured. The line's colour is the system's `separatorColor` (set by the panel's adapter): Apple's line
    ///   measured about +12 brightness over the glass in dark mode (41 -> 53); light mode was not measured.
    /// - `rowWidth`: the capsule's width (the panel's width less the side insets and the gutter).
    public static func separators(rows: Int, selected: Int, rowWidth: CGFloat, capsuleHeight: CGFloat, thickness: CGFloat) -> [Separator] {
        guard rows > 1 else { return [] }
        return (0..<(rows - 1)).map { i in
            Separator(index: i, visible: selected != i && selected != i + 1,
                      x: sideInset + separatorInset,
                      y: rowY(i, capsuleHeight: capsuleHeight) + separatorBelowCapsuleTop,
                      width: max(rowWidth - separatorInset * 2, 0), height: thickness)
        }
    }

    /// The window's height for `rows` visible rows. Fixed when the window opens (contract section 2.3).
    public static func height(rows: Int) -> CGFloat {
        inset * 2 + CGFloat(rows) * rowPitch
    }

    /// The height that decides below or above the line (`PanelPlacement.topLeft`'s `sideHeight`) for an output of `kind`: the
    /// prediction row's height changes with its count, so it decides by the nine-row height and stays on one side of the line
    /// while it shows; a vertical window's height is fixed while it is open (`nil`: its own).
    public static func sideHeight(kind: Int) -> CGFloat? {
        kind == predictions ? height(rows: visibleRows) : nil
    }

    /// Top edge of the capsule of row `i` (0 = first visible row), with a capsule `capsuleHeight` tall centred in its pitch.
    public static func rowY(_ i: Int, capsuleHeight: CGFloat) -> CGFloat {
        inset + CGFloat(i) * rowPitch + (rowPitch - capsuleHeight) / 2
    }

    /// The panel's width: at least `minWidth`, at least `current` (the width so far; it never shrinks while the window is
    /// open, so scrolling does not resize the window on every key, the 2026-10-05 glass flicker), and wide enough for the widest of
    /// `contentWidths` (a cell's own width: number, candidate and its name) plus the side insets and the gutter.
    public static func width(contentWidths: [CGFloat], current: CGFloat, gutter: CGFloat) -> CGFloat {
        let needed = (contentWidths.max() ?? 0) + sideInset * 2 + gutter
        return max(minWidth, current, needed.rounded(.up))
    }

    /// The scroll indicator for the rows `first ..< first + visible` of `total`: its top and height in a track of `height`
    /// (inset above and below). `nil` while everything fits.
    public static func thumb(first: Int, total: Int, visible: Int, height: CGFloat) -> (y: CGFloat, height: CGFloat)? {
        guard total > visible, visible > 0 else { return nil }
        let track = height - inset * 2
        let h = max(thumbMinHeight, (track * CGFloat(visible) / CGFloat(total)).rounded())
        let y = inset + ((track - h) * CGFloat(first) / CGFloat(total - visible)).rounded()
        return (y, h)
    }
}
