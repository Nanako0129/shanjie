import AppKit

/// docs/contracts/s3b2-glass-panel.md section 2.3: where the candidate bar goes. Pure, so it is
/// testable without a screen. Coordinates are AppKit's (origin bottom-left, y up).
public enum PanelPlacement {
    /// Gap between the line and the bar (section 2.3; McBopomofo uses the same 4 pt).
    public static let gap: CGFloat = 4

    /// The bar's top-left corner.
    /// - `lineRect`: the text line (origin bottom-left); `nil` reuses `lastOrigin`, and with none
    ///   either, the bar sits at the bottom-left of the main screen's visible frame.
    /// - `size`: the bar's size.
    /// - `alignOffset`: how far the first candidate glyph sits from the bar's left edge, so the
    ///   glyph lines up with the composed text.
    /// - `screens`: the visibleFrames; `rectScreen` is the index of the one containing the line
    ///   (`nil` if none), `main` the index of the main screen.
    public static func topLeft(lineRect: NSRect?, lastOrigin: NSPoint?, size: NSSize, alignOffset: CGFloat,
                               screens: [NSRect], rectScreen: Int?, main: Int) -> NSPoint {
        let visible = screens[rectScreen ?? main]
        guard let line = lineRect else {
            return lastOrigin ?? NSPoint(x: visible.minX, y: visible.minY + size.height)
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
