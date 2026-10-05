import CoreGraphics

/// docs/contracts/s3b2-glass-panel.md section 9: column positions of the expanded grid. Each cell
/// keeps its own content width; a column is as wide as its widest cell seen so far (widen-only, so
/// scrolling does not make the grid jump sideways). Pure, so it is testable without a screen.
public enum GridLayout {
    public struct Result: Equatable {
        /// Width of each column; 0 for a column with no cell yet. Pass back as `current` next time.
        public var widths: [CGFloat]
        /// Left edge of each column, from the grid's left edge.
        public var xs: [CGFloat]
        public var totalWidth: CGFloat
    }

    /// - `cellWidths`: row-major, `columns` per row.
    /// - `current`: the column widths so far (empty on expand, collapse or hide).
    /// - `inset`: space left of the first column; `trailing`: space right of the last (scroll gutter).
    public static func layout(cellWidths: [CGFloat], columns: Int, current: [CGFloat], inset: CGFloat,
                              spacing: CGFloat, trailing: CGFloat) -> Result {
        var widths = (0..<columns).map { $0 < current.count ? current[$0] : 0 }
        for (i, w) in cellWidths.enumerated() { widths[i % columns] = max(widths[i % columns], w) }
        var xs: [CGFloat] = []
        var x = inset
        for w in widths {
            xs.append(x)
            if w > 0 { x += w + spacing }
        }
        let used = widths.contains { $0 > 0 }
        return Result(widths: widths, xs: xs, totalWidth: (used ? x - spacing : x) + trailing)
    }
}
