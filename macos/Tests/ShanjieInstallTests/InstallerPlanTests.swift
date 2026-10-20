import ShanjieInstall
import XCTest

// docs/contracts/s3c-installer.md section 6, acceptance 2a.
final class InstallerPlanTests: XCTestCase {
    func testNothingInstalledInstalls() {
        XCTAssertEqual(InstallerPlan.decide(installed: nil, bundled: "0.1.2"), .install)
        XCTAssertEqual(InstallerPlan.decide(installed: "", bundled: "0.1.2"), .install)
    }

    func testOlderUpdatesNumerically() {
        XCTAssertEqual(InstallerPlan.decide(installed: "0.1.1", bundled: "0.1.2"), .update)
        // Numeric, not lexical: 0.9.0 < 0.10.0.
        XCTAssertEqual(InstallerPlan.decide(installed: "0.9.0", bundled: "0.10.0"), .update)
    }

    func testSameVersionOnlyEnables() {
        let plan = InstallerPlan.decide(installed: "0.1.2", bundled: "0.1.2")
        XCTAssertEqual(plan, .enableSame)
    }

    func testNewerInstalledNeverDowngrades() {
        let plan = InstallerPlan.decide(installed: "0.10.0", bundled: "0.9.0")
        XCTAssertEqual(plan, .enableNewer)
    }

    // docs/contracts/system-install.md section 2.2, one test per row of the table (bundled 0.5.0).
    private func decide(_ choice: InstallChoice, system: String?, account: Bool, accountVersion: String? = nil,
                        listed: Bool = false) -> InstallDecision {
        InstallDecision.decide(choice: choice, system: system, accountCopy: account, account: accountVersion ?? (account ? "0.4.7" : nil),
                               bundled: "0.5.0", listedBeforeCopy: listed)
    }

    private func check(_ d: InstallDecision, _ action: InstallDecision.Action, moves: Bool, password: Bool, moved: Bool,
                       first: Bool = false, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(d.action, action, file: file, line: line)
        XCTAssertEqual(d.movesAccountCopy, moves, "moves the account copy", file: file, line: line)
        // The password column: only the root stage asks for it. The log-out column is `moved`.
        XCTAssertEqual(d.action == .copyToSystem, password, "password", file: file, line: line)
        XCTAssertEqual(d.moved, moved, "forced registration and log out", file: file, line: line)
        XCTAssertEqual(d.firstInstall, first, "first install: add it after the log out", file: file, line: line)
    }

    func testThisMacNothingInstalled() {
        // Listed at the welcome screen without any copy (a /Library copy removed while still listed) is a move, not a first install.
        for listed in [false, true] {
            check(decide(.thisMac, system: nil, account: false, listed: listed), .copyToSystem, moves: false, password: true, moved: true,
                  first: !listed)
        }
    }

    func testThisMacFromTheAccountLayer() {
        check(decide(.thisMac, system: nil, account: true, listed: true), .copyToSystem, moves: true, password: true, moved: true)
    }

    func testThisMacUpdateInPlace() {
        check(decide(.thisMac, system: "0.4.9", account: false, listed: true), .copyToSystem, moves: false, password: true, moved: false)
    }

    func testThisMacUpdateWithAnAccountCopy() {
        check(decide(.thisMac, system: "0.4.9", account: true, listed: true), .copyToSystem, moves: true, password: true, moved: true)
    }

    func testThisMacSameVersionOnlyEnables() {
        check(decide(.thisMac, system: "0.5.0", account: false, listed: true), .enableSystemSame, moves: false, password: false, moved: false)
        // Its secondary action 重新安裝 is the row "older, no account copy".
        let reinstall = InstallDecision.reinstallSystem
        check(reinstall, .copyToSystem, moves: false, password: true, moved: false)
        XCTAssertEqual(reinstall, decide(.thisMac, system: "0.4.9", account: false, listed: true))
    }

    func testThisMacSameOrNewerWithAnAccountCopyOnlyMoves() {
        for system in ["0.5.0", "0.6.0"] {
            check(decide(.thisMac, system: system, account: true, listed: true), .moveToSystem, moves: true, password: false, moved: true)
        }
    }

    func testThisMacNewerOnlyEnables() {
        check(decide(.thisMac, system: "0.10.0", account: false, listed: true), .enableSystemNewer, moves: false, password: false, moved: false)
    }

    func testAccountOnlyReverseMove() {
        check(decide(.accountOnly, system: nil, account: false, listed: true), .account(.install), moves: false, password: false, moved: true)
    }

    func testAccountOnlyAsBefore() {
        // Not listed before the copy: a fresh account, the path before system-install.md. The
        // listed read is the one from the welcome screen; whatever the system lists after the copy
        // is not an input here, so a first install is never taken for a move.
        check(decide(.accountOnly, system: nil, account: false, listed: false), .account(.install), moves: false, password: false, moved: false)
        for (version, plan) in [("0.4.7", InstallerPlan.update), ("0.5.0", .enableSame), ("0.6.0", .enableNewer)] {
            for listed in [false, true] {
                check(decide(.accountOnly, system: nil, account: true, accountVersion: version, listed: listed),
                      .account(plan), moves: false, password: false, moved: false)
            }
        }
        // Only the legacy shanjie.app: an account copy without a readable 善解輸入法.app version.
        check(InstallDecision.decide(choice: .accountOnly, system: nil, accountCopy: true, account: nil, bundled: "0.5.0", listedBeforeCopy: true),
              .account(.install), moves: false, password: false, moved: false)
    }

    func testAccountOnlyBlockedWhileThisMacHasACopy() {
        for system in ["0.4.9", "0.5.0", "0.6.0", ""] {
            for account in [false, true] {
                for listed in [false, true] {
                    check(decide(.accountOnly, system: system, account: account, listed: listed), .blocked, moves: false, password: false, moved: false)
                }
            }
        }
    }

    /// Never downgrade (added in implementation from code review): "This Mac" with a newer account
    /// copy stops, whatever /Library holds; "My Account Only" still only enables it.
    func testThisMacStopsOnANewerAccountCopy() {
        for system in [nil, "0.4.9", "0.5.0", "0.6.0", ""] as [String?] {
            for listed in [false, true] {
                check(decide(.thisMac, system: system, account: true, accountVersion: "0.10.0", listed: listed),
                      .accountNewer, moves: false, password: false, moved: false)
            }
        }
        check(decide(.accountOnly, system: nil, account: true, accountVersion: "0.10.0"), .account(.enableNewer),
              moves: false, password: false, moved: false)
    }

    /// A /Library copy whose version cannot be read is present: same location, taken as older.
    func testThisMacWithAnUnreadableSystemCopy() {
        check(decide(.thisMac, system: "", account: false, listed: true), .copyToSystem, moves: false, password: true, moved: false)
        check(decide(.thisMac, system: "", account: true, listed: true), .copyToSystem, moves: true, password: true, moved: true)
    }
}
