import AppKit

/// docs/contracts/s3b2-glass-panel.md section 2.3: where the candidate bar goes. Pure, so it is
/// testable without a screen. Coordinates are AppKit's (origin bottom-left, y up).
public enum PanelPlacement {
    /// Gap between the line and the bar (section 2.3). Was 4 (McBopomofo's value); measured 2026-10-08
    /// with imeshot in light and dark windows at 1 px = 1 pt, our bar (barHeight 28) sat 2 pt higher than
    /// Apple Zhuyin's under the same composing line (rim and capsule both), so 6 lines it up. The gap
    /// above the line (no room below) uses the same value; Apple's there is not measured.
    public static let gap: CGFloat = 6

    /// One screen: its full frame (which screen holds the line) and its visible frame (where a panel may go).
    public struct Screen: Equatable, Sendable {
        public var frame: NSRect
        public var visibleFrame: NSRect
        public init(frame: NSRect, visibleFrame: NSRect) {
            self.frame = frame
            self.visibleFrame = visibleFrame
        }
    }

    /// docs/contracts/line-jump-guard.md: how many line heights a line may move up or down within one composition before
    /// it is taken as a wrong report. Measured 2026-10-13 (diagnostic rect trace): within a composition Orca's line moved
    /// 0–1 pt and Discord's 0, while Orca's wrong reports jumped 189–840 pt; line heights were 21–23 pt, so 2 lines
    /// (42–46 pt) keeps a move to the next line and stops every measured jump.
    public static let jumpLines: CGFloat = 2
    /// The line height used when the client reports none: the side of the rectangle `lineRect` starts from.
    public static let minLineHeight: CGFloat = 16

    /// docs/contracts/line-jump-guard.md section 2.1: the line to use, given the client's `new` one and the last one used in
    /// this composition (`anchor`). A line more than `jumpLines` line heights above or below the anchor is not used (the
    /// anchor is); `nil` stays `nil` (section 2.3's remembered line takes over), and the first line of a composition is used
    /// as given. Only y is checked: wrapping to the next line moves x far left.
    public static func steadyLine(_ new: NSRect?, anchor: NSRect?) -> NSRect? {
        guard let new, let anchor else { return new }
        return abs(new.minY - anchor.minY) > jumpLines * max(anchor.height, minLineHeight) ? anchor : new
    }

    /// The index of the screen a panel for `line` goes to: the one whose frame holds the line's origin, else the main one.
    public static func screenIndex(for line: NSRect?, in screens: [Screen], main: Int) -> Int {
        line.flatMap { r in screens.firstIndex { $0.frame.contains(r.origin) } } ?? main
    }

    /// The bar's top-left corner, and the index of the screen it goes to.
    /// - `line`: the client's text line (origin bottom-left); `nil` when it gave none.
    /// - `last`: the last line the client gave, used when `line` is `nil` (a remembered line, not a remembered corner, which
    ///   depends on the size of the panel it was worked out for). One whose origin no screen frame holds (its screen is gone) is not used this time (the caller keeps it). With
    ///   neither, the panel sits at the bottom-left of the main screen's visible frame for this size, which is never remembered.
    ///   The screen is the one whose frame holds the line used, else the main one.
    /// - `size`: the panel's size.
    /// - `sideHeight`: the height used to decide below or above the line, `size.height` when `nil`. A panel whose height
    ///   changes from output to output (the vertical prediction row) passes its largest, so it stays on one side while it
    ///   shows; the corner still uses `size`.
    /// - `alignOffset`: how far the first candidate glyph sits from the panel's left edge, so the glyph lines up with the
    ///   composed text.
    /// - `screens`, `main`: every screen, and the index of the main one.
    public static func topLeft(line: NSRect?, last: NSRect? = nil, size: NSSize, sideHeight: CGFloat? = nil,
                               alignOffset: CGFloat, screens: [Screen], main: Int) -> (origin: NSPoint, screen: Int) {
        let line = line ?? last.flatMap { l in screens.contains { $0.frame.contains(l.origin) } ? l : nil }
        let index = screenIndex(for: line, in: screens, main: main)
        let visible = screens[index].visibleFrame
        guard let line else { return (NSPoint(x: visible.minX, y: visible.minY + size.height), index) }
        var x = line.minX - alignOffset
        var top = line.minY - gap
        // No room below: above the line instead.
        if top - (sideHeight ?? size.height) < visible.minY { top = line.maxY + gap + size.height }
        x = min(x, visible.maxX - size.width)
        x = max(x, visible.minX)
        top = min(top, visible.maxY)
        return (NSPoint(x: x, y: top), index)
    }
}
