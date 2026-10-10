import AppKit

/// docs/contracts/s3b2-glass-panel.md section 2.3: where the candidate bar goes. Pure, so it is
/// testable without a screen. Coordinates are AppKit's (origin bottom-left, y up).
public enum PanelPlacement {
    /// Gap between the line and the bar (section 2.3). Was 4 (McBopomofo's value); measured 2026-10-08
    /// with imeshot in light and dark windows at 1 px = 1 pt, our bar (barHeight 28) sat 2 pt higher than
    /// Apple Zhuyin's under the same composing line (rim and capsule both), so 6 lines it up. The gap
    /// above the line (no room below) uses the same value; Apple's there is not measured.
    public static let gap: CGFloat = 6

    /// The bar's top-left corner.
    /// - `lineRect`: the text line (origin bottom-left); `nil` reuses `lastOrigin` (clamped into its screen's visible frame
    ///   for `size`), and with none either, the bar sits at the bottom-left of the main screen's visible frame.
    /// - `size`: the bar's size.
    /// - `alignOffset`: how far the first candidate glyph sits from the bar's left edge, so the
    ///   glyph lines up with the composed text.
    /// - `screens`: the visibleFrames; `rectScreen` is the index of the one containing the line
    ///   (`nil` if none), `main` the index of the main screen.
    public static func topLeft(lineRect: NSRect?, lastOrigin: NSPoint?, size: NSSize, alignOffset: CGFloat,
                               screens: [NSRect], rectScreen: Int?, main: Int) -> NSPoint {
        let visible = screens[rectScreen ?? main]
        guard let line = lineRect else {
            guard let last = lastOrigin else { return NSPoint(x: visible.minX, y: visible.minY + size.height) }
            // The last place may no longer fit: another panel size (the vertical window is much taller than the bar), another
            // screen layout. Clamp it into the visible frame of the screen it was on, for this size.
            let home = screens.first { $0.contains(NSPoint(x: last.x, y: last.y - 1)) } ?? visible
            return NSPoint(x: max(min(last.x, home.maxX - size.width), home.minX),
                           y: min(max(last.y, home.minY + size.height), home.maxY))
        }
        var x = line.minX - alignOffset
        var top = line.minY - gap
        // No room below: above the line instead.
        if top - size.height < visible.minY { top = line.maxY + gap + size.height }
        x = min(x, visible.maxX - size.width)
        x = max(x, visible.minX)
        top = min(top, visible.maxY)
        return NSPoint(x: x, y: top)
    }
}
