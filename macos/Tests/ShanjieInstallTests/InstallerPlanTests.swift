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
        XCTAssertFalse(plan.copiesFiles, "reopening after a log out must not rerun install-ime.sh")
    }

    func testNewerInstalledNeverDowngrades() {
        let plan = InstallerPlan.decide(installed: "0.10.0", bundled: "0.9.0")
        XCTAssertEqual(plan, .enableNewer)
        XCTAssertFalse(plan.copiesFiles)
    }

    func testCopyingPlans() {
        XCTAssertTrue(InstallerPlan.install.copiesFiles)
        XCTAssertTrue(InstallerPlan.update.copiesFiles)
    }
}
