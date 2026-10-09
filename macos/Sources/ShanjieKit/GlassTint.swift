import Foundation

/// The candidate panel's glass tint (docs/contracts/settings-window.md section 2.3). One slider value
/// 0...1 becomes a black (dark appearance) or white (light appearance) overlay on the glass; the
/// material is untouched and 0 means no tint at all, i.e. the measured Apple look.
public enum GlassTint {
    /// The overlay opacity at slider value 1. Source: the Syrtis maintainer picked 0.5 by looking at the
    /// real glass on a device (relayed by the user, 2026-10-10); it was not measured against Apple's
    /// screenshots, so treat it as a taste value.
    public static let GLASS_TINT_MAX = 0.5

    public struct Tint: Equatable, Sendable {
        /// Black over dark appearances, white over light ones.
        public var black: Bool
        public var opacity: Double
    }

    /// `nil` (no tint) when the clamped value is 0; NaN counts as 0. The caller sets the result on the
    /// glass including `nil`, so going from a tint back to 0 removes it.
    public static func tint(value: Double, dark: Bool) -> Tint? {
        let v = value.isNaN ? 0 : min(max(value, 0), 1)
        guard v > 0 else { return nil }
        return Tint(black: dark, opacity: v * GLASS_TINT_MAX)
    }

    /// What the glass must be told to change.
    public enum Change: Equatable, Sendable {
        case set(Tint)
        /// Remove the tint (assign nil): going from a tint back to 0.
        case clear
    }

    /// Remembers the last tint applied to the glass, so the adapter assigns only on a change (needless
    /// glass updates flickered, s3b2) and a return to 0 is never missed. Compares `Tint` values, not NSColor.
    public struct Applier: Sendable {
        private var last: Tint?
        public init() {}

        /// `nil`: nothing to assign.
        public mutating func update(value: Double, dark: Bool) -> Change? {
            let t = GlassTint.tint(value: value, dark: dark)
            guard t != last else { return nil }
            last = t
            return t.map(Change.set) ?? .clear
        }
    }
}
