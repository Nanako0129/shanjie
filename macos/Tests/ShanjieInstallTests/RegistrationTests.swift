import Foundation
import ShanjieInstall
import XCTest

// docs/contracts/installer-v2.md section 9.3, item 1. No test calls TIS: the state is injected and
// the full flow (the only code that registers, enables or disables) is replaced by a counter.
final class RegistrationTests: XCTestCase {
    func testDecisionTable() {
        for listed in [false, true] {
            for accepted in [false, true] {
                for legacy in [false, true] {
                    let expected: Registration.Decision =
                        !listed || legacy ? .fullFlow : accepted ? .skipDone : .skipNotAccepted
                    XCTAssertEqual(Registration.decide(modeListed: listed, accepted: accepted, legacyEnabled: legacy),
                                   expected, "listed \(listed) accepted \(accepted) legacy \(legacy)")
                }
            }
        }
        // The case that matters: known but not accepted, no earlier mode: nothing is called.
        XCTAssertEqual(Registration.decide(modeListed: true, accepted: false, legacyEnabled: false), .skipNotAccepted)
        XCTAssertEqual(Registration.decide(modeListed: true, accepted: true, legacyEnabled: false), .skipDone)
    }

    private func run(_ state: Registration.State, mutations: inout Int) -> Registration.Result {
        var count = 0
        let result = Registration.run(
            bundleURL: URL(fileURLWithPath: "/nonexistent"), bundleID: "x", defaults: UserDefaults(suiteName: "RegistrationTests")!,
            readState: { _ in state },
            fullFlow: { _, _, _ in count += 1; return Registration.Result(outcome: .done) })
        mutations = count
        return result
    }

    func testRunSkipsWithoutMutating() {
        var calls = -1
        let done = run(.init(modeListed: true, accepted: true, legacyEnabled: false), mutations: &calls)
        XCTAssertEqual(done, Registration.Result(outcome: .done, skipped: true))
        XCTAssertEqual(calls, 0)
        let waiting = run(.init(modeListed: true, accepted: false, legacyEnabled: false), mutations: &calls)
        XCTAssertEqual(waiting, Registration.Result(outcome: .notAccepted, skipped: true))
        XCTAssertEqual(calls, 0)
    }

    func testRunTakesTheFullFlowWhenNeeded() {
        var calls = 0
        _ = run(.init(modeListed: false, accepted: false, legacyEnabled: false), mutations: &calls)
        XCTAssertEqual(calls, 1)
        _ = run(.init(modeListed: true, accepted: true, legacyEnabled: true), mutations: &calls)
        XCTAssertEqual(calls, 1)
    }

    /// `shanjie install` after a skipped notAccepted: the fake clock advances only through `sleep`.
    private func wait(acceptedAfter seconds: Double?) -> (accepted: Bool, waited: Double, mutations: Int) {
        var mutations = 0
        var now = 0.0
        let result = Registration.run(
            bundleURL: URL(fileURLWithPath: "/nonexistent"), bundleID: "x", defaults: UserDefaults(suiteName: "RegistrationTests")!,
            readState: { _ in .init(modeListed: true, accepted: false, legacyEnabled: false) },
            fullFlow: { _, _, _ in mutations += 1; return Registration.Result(outcome: .done) })
        XCTAssertTrue(result.skipped)
        let accepted = Registration.waitUntilAccepted(result, isAccepted: { seconds.map { now >= $0 } ?? false }, sleep: { now += $0 })
        return (accepted, now, mutations)
    }

    func testWaitAcceptedWithinFiveSeconds() {
        let r = wait(acceptedAfter: 1.0)
        XCTAssertTrue(r.accepted)
        XCTAssertEqual(r.waited, 1.0)
        XCTAssertEqual(r.mutations, 0)
        XCTAssertTrue(wait(acceptedAfter: 5.0).accepted)
    }

    func testWaitNeverAcceptedGivesUpAfterFiveSeconds() {
        let r = wait(acceptedAfter: nil)
        XCTAssertFalse(r.accepted)
        XCTAssertEqual(r.waited, 5.0)
        XCTAssertEqual(r.mutations, 0)
    }

    func testNoWaitAfterTheFullFlow() {
        var slept = 0
        let notAccepted = Registration.Result(outcome: .notAccepted)
        XCTAssertFalse(Registration.waitUntilAccepted(notAccepted, isAccepted: { true }, sleep: { _ in slept += 1 }))
        XCTAssertEqual(slept, 0)
    }
}
