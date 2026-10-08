import ShanjieInstall
import XCTest

// docs/contracts/installer-v2.md section 9.3 item 1: the exit code and message of `shanjie install`.
final class InstallCommandTests: XCTestCase {
    private let steps = "install: open System Settings > Keyboard > Input Sources, press +, choose Shanjie under Chinese (Traditional) and press Add"

    func testSkippedDoneExitsZero() {
        let r = InstallCommand.report(.init(outcome: .done, skipped: true), acceptedAfterWait: true)
        XCTAssertEqual(r, .init(exitCode: 0, messages: ["install: already enabled; registration skipped"]))
    }

    func testAcceptedAfterTheWaitExitsZero() {
        let r = InstallCommand.report(.init(outcome: .notAccepted, skipped: true), acceptedAfterWait: true)
        XCTAssertEqual(r, .init(exitCode: 0, messages: ["install: already enabled; registration skipped"]))
    }

    func testNeverAcceptedExitsThree() {
        let r = InstallCommand.report(.init(outcome: .notAccepted, skipped: true), acceptedAfterWait: false)
        XCTAssertEqual(r, .init(exitCode: 3, messages: ["install: the system has not accepted the input method yet", steps]))
        // The full flow ran and the system still did not take it: no wait result can change that.
        XCTAssertEqual(InstallCommand.report(.init(outcome: .notAccepted), acceptedAfterWait: true).exitCode, 3)
    }

    func testModeNotListedExitsThree() {
        let r = InstallCommand.report(.init(outcome: .modeNotListed), acceptedAfterWait: false)
        XCTAssertEqual(r, .init(exitCode: 3, messages: ["install: the input mode is not listed yet", steps]))
    }

    func testFailuresExitOne() {
        XCTAssertEqual(InstallCommand.report(.init(outcome: .registrationFailed, registerFailed: true), acceptedAfterWait: false),
                       .init(exitCode: 1, messages: ["install: warning: TISRegisterInputSource failed",
                                                     "install: registration failed and the input mode is not listed"]))
        XCTAssertEqual(InstallCommand.report(.init(outcome: .enableFailed), acceptedAfterWait: false),
                       .init(exitCode: 1, messages: ["install: TISEnableInputSource failed"]))
    }

    func testFullFlowDone() {
        XCTAssertEqual(InstallCommand.report(.init(outcome: .done), acceptedAfterWait: true),
                       .init(exitCode: 0, messages: ["install: registered and enabled"]))
        XCTAssertEqual(InstallCommand.report(.init(outcome: .done, legacyDisableFailed: true), acceptedAfterWait: true),
                       .init(exitCode: 0, messages: ["install: warning: TISDisableInputSource failed for an input mode of an earlier version",
                                                     "install: registered and enabled"]))
    }
}
