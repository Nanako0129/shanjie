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

    /// The shipping bundle ID, the only one the installer installs.
    public static let shippingBundleID = "com.nyanako.inputmethod.shanjie"
}

/// docs/contracts/system-install.md section 2.1: the two options on the welcome screen.
public enum InstallChoice: Equatable, Sendable {
    /// /Library/Input Methods, root-owned, put there by the root stage (section 2.3).
    case thisMac
    /// ~/Library/Input Methods, the path before system-install.md.
    case accountOnly
}

/// docs/contracts/system-install.md section 2.2: the decision table. Pure, unit tested row by row.
public struct InstallDecision: Equatable, Sendable {
    public enum Action: Equatable, Sendable {
        /// The root stage puts the bundled copy into /Library (none there, an older one, or a reinstall).
        case copyToSystem
        /// /Library holds the same version: enable only; the secondary action is `reinstallSystem`.
        case enableSystemSame
        /// /Library holds a newer version: enable only, never downgrade.
        case enableSystemNewer
        /// /Library holds the same or a newer version and the account layer a copy: only the user
        /// stage (the account copy moves away), no root stage and no password.
        case moveToSystem
        /// The account layer, as before system-install.md, with the plan from the account copy.
        case account(InstallerPlan)
        /// "My Account Only" while /Library holds a copy: stop and change nothing.
        case blocked
    }

    public var action: Action
    /// The user stage moves the account-layer copy (and the legacy shanjie.app) to .shanjie-previous.
    public var movesAccountCopy: Bool
    /// The location changed: registration is forced (`Registration.decide(moved:)`) and the last
    /// screen asks for a log out (`InstallerFlow.next(after:moved:)`).
    public var moved: Bool

    public init(action: Action, movesAccountCopy: Bool = false, moved: Bool = false) {
        self.action = action
        self.movesAccountCopy = movesAccountCopy
        self.moved = moved
    }

    /// Only the root stage asks for the administrator password.
    public var needsPassword: Bool { action == .copyToSystem }
    /// Section 2.2's last column: the same rows as forced registration.
    public var logoutRequired: Bool { moved }

    /// The secondary action of `enableSystemSame`: the row "older, no account copy".
    public static let reinstallSystem = InstallDecision(action: .copyToSystem)

    /// `system`: the version of /Library's copy (nil: none; "" when one is there but its version
    /// cannot be read). `accountCopy`: whether the account layer
    /// holds the shipping-ID copy under either name; `account`: the version of its 善解輸入法.app.
    /// `listedBeforeCopy`: whether the system listed the shipping ID's input mode, read once at the
    /// welcome screen before anything was copied (section 2.2: `Registration.run`'s own read comes
    /// after the copy and may already see the bundle just put in place).
    public static func decide(choice: InstallChoice, system: String?, accountCopy: Bool, account: String?,
                              bundled: String, listedBeforeCopy: Bool) -> InstallDecision {
        let systemPlan = InstallerPlan.decide(installed: system, bundled: bundled)
        switch choice {
        case .thisMac:
            switch systemPlan {
            case .install:
                return InstallDecision(action: .copyToSystem, movesAccountCopy: accountCopy, moved: true)
            case .update:
                return InstallDecision(action: .copyToSystem, movesAccountCopy: accountCopy, moved: accountCopy)
            case .enableSame, .enableNewer:
                if accountCopy { return InstallDecision(action: .moveToSystem, movesAccountCopy: true, moved: true) }
                return InstallDecision(action: systemPlan == .enableSame ? .enableSystemSame : .enableSystemNewer)
            }
        case .accountOnly:
            // Any copy in /Library blocks, also one whose version cannot be read ("").
            if system != nil { return InstallDecision(action: .blocked) }
            // The reverse move: installed in /Library before, removed per the README, still listed.
            if !accountCopy && listedBeforeCopy { return InstallDecision(action: .account(.install), moved: true) }
            return InstallDecision(action: .account(InstallerPlan.decide(installed: account, bundled: bundled)))
        }
    }
}
