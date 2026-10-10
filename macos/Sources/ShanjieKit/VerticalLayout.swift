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

    /// The window's height for `rows` visible rows. Fixed when the window opens (contract section 2.3).
    public static func height(rows: Int) -> CGFloat {
        inset * 2 + CGFloat(rows) * rowPitch
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
