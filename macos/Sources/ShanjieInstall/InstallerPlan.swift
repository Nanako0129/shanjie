import Foundation

/// docs/contracts/s3c-installer.md section 2.1: what the installer offers, from the installed and
/// the bundled version. Pure, so it is unit tested; nothing here touches the system.
public enum InstallerPlan: Equatable, Sendable {
    /// Nothing installed (or no readable version): copy, then enable.
    case install
    /// An older version is installed: copy, then enable.
    case update
    /// The same version is installed (e.g. reopened after a log out): enable and check only. A
    /// full reinstall stays available as a secondary action, because rerunning install-ime.sh keeps
    /// the current copy as the previous version and drops the real previous one.
    case enableSame
    /// A newer version is installed: never downgrade, enable and check only.
    case enableNewer

    public static func decide(installed: String?, bundled: String) -> InstallerPlan {
        guard let installed, !installed.isEmpty else { return .install }
        switch installed.compare(bundled, options: .numeric) {
        case .orderedAscending: return .update
        case .orderedSame: return .enableSame
        case .orderedDescending: return .enableNewer
        }
    }

    /// Whether the primary action runs the file copy (install-ime.sh) before enabling.
    public var copiesFiles: Bool { self == .install || self == .update }
}
