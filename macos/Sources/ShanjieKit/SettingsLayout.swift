import CoreGraphics

/// The settings window's height (docs/contracts/settings-fixed-size.md): one fixed comfortable size, clamped into the
/// screen so a small display never gets a window taller than its usable area.
public enum SettingsLayout {
    /// Frame height (content plus title bar) in points. Picked, not measured: about 1:1.07 against the 580 pt width,
    /// close to the ratio of System Settings panes, and on a 13-inch laptop's default resolution (usable height about
    /// 870 pt) it leaves room above and below. The first device round adjusts it.
    public static let preferredHeight: CGFloat = 620

    public static func frameHeight(visibleHeight: CGFloat) -> CGFloat {
        min(preferredHeight, visibleHeight)
    }
}
