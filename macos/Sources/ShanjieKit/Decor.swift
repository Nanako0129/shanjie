import AppKit

/// A plain filled view (scroll thumb, separator). The colour is resolved in `updateLayer`, which
/// AppKit calls again when the effective appearance changes, so a dynamic colour follows light/dark
/// while the panel is open.
public final class FilledView: NSView {
    private let color: NSColor

    public init(frame: NSRect, color: NSColor, cornerRadius: CGFloat = 0) {
        self.color = color
        super.init(frame: frame)
        wantsLayer = true
        layer?.cornerRadius = cornerRadius
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    public override var wantsUpdateLayer: Bool { true }
    public override func updateLayer() { layer?.backgroundColor = color.cgColor }
}

/// The chevron/separator or scroll thumb views of the open panel (contract 9.1). Every view that
/// `replace` or `flush` puts into the row is tracked until the next `replace` or `clear` removes it,
/// whichever path added it: at the start of a show, or when a running animation ends.
@MainActor
public final class DecorSet {
    private var views: [NSView] = []
    private var pending: [NSView] = []

    public init() {}

    /// Removes the current views and installs `new`: straight into `row`, or held back for `flush`.
    public func replace(with new: [NSView], deferred: Bool, in row: NSView) {
        clear()
        if deferred { pending = new } else { new.forEach { row.addSubview($0) }; views = new }
    }

    /// The end of an animation: adds the held-back views. Views already in `row` stay tracked.
    public func flush(into row: NSView) {
        pending.forEach { row.addSubview($0) }
        views += pending
        pending = []
    }

    public func clear() {
        views.forEach { $0.removeFromSuperview() }
        views = []
        pending = []
    }
}
