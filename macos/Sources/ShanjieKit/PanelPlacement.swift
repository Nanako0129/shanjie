import AppKit

/// docs/spec/macos-shell.md §8.3: where the candidate bar goes. Pure, so it is
/// testable without a screen. Coordinates are AppKit's (origin bottom-left, y up).
public enum PanelPlacement {
    /// Gap between the line and the bar (docs/spec/macos-shell.md §8.3). Was 4 (McBopomofo's value); measured 2026-10-08
    /// with a screenshot tool in light and dark windows at 1 px = 1 pt, our bar (barHeight 28) sat 2 pt higher than
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

    /// docs/spec/english-mixed.md §10.3: the bottom-left corner of the explanation window. The screen is the one
    /// holding the caret (the main one when the caret is unknown). A caret in the upper half of that screen puts the window in the
    /// middle of the lower half of its visible frame, a caret in the lower half in the middle of the upper half, so the window never
    /// covers the composition or the candidate bar beside the caret.
    public static func noticeOrigin(caret: NSRect?, size: NSSize, screens: [Screen], main: Int) -> NSPoint {
        let screen = screens[screenIndex(for: caret, in: screens, main: main)]
        let visible = screen.visibleFrame
        let caretInUpperHalf = caret.map { $0.midY >= screen.frame.midY } ?? false
        let centerY = visible.minY + visible.height * (caretInUpperHalf ? 0.25 : 0.75)
        return NSPoint(x: visible.midX - size.width / 2, y: centerY - size.height / 2)
    }
}
