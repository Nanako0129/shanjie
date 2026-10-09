import Foundation
import ShanjieInstall
import XCTest

// docs/contracts/installer-v2.md section 9.3, item 1. No test calls TIS: the state is injected and
// the full flow (the only code that registers, enables or disables) is replaced by a counter.
final class RegistrationTests: XCTestCase {
    func testDecisionTable() {
        // (modeListed, accepted, legacyEnabled) -> decision, written out row by row.
        let rows: [(Bool, Bool, Bool, Registration.Decision)] = [
            (false, false, false, .fullFlow),  // first install
            (false, false, true, .fullFlow),
            (false, true, false, .fullFlow),
            (false, true, true, .fullFlow),
            (true, false, false, .skipNotAccepted),  // known, not accepted: nothing is called
            (true, false, true, .fullFlow),  // an earlier version's mode is still enabled
            (true, true, false, .skipDone),
            (true, true, true, .fullFlow),
        ]
        for (listed, accepted, legacy, expected) in rows {
            XCTAssertEqual(Registration.decide(modeListed: listed, accepted: accepted, legacyEnabled: legacy),
                           expected, "listed \(listed) accepted \(accepted) legacy \(legacy)")
        }
    }

    func testAcceptedNeedsTheParentAndTheZhuyinMode() {
        let id = "com.nyanako.inputmethod.shanjie"
        XCTAssertTrue(Registration.accepted(enabledModeIDs: [nil, "\(id).zhuyin"], bundleID: id))
        XCTAssertFalse(Registration.accepted(enabledModeIDs: [nil], bundleID: id), "parent only: known, not accepted")
        XCTAssertFalse(Registration.accepted(enabledModeIDs: ["\(id).zhuyin"], bundleID: id))
        XCTAssertFalse(Registration.accepted(enabledModeIDs: [nil, "\(id).standard"], bundleID: id))
        XCTAssertFalse(Registration.accepted(enabledModeIDs: [], bundleID: id))
    }

    /// A mode that is not listed yet is re-read (no mutation) before the full flow is chosen.
    func testNotListedIsReReadBeforeTheFullFlow() {
        var reads = 0
        var pauses: [TimeInterval] = []
        var flows = 0
        let result = Registration.run(
            bundleURL: URL(fileURLWithPath: "/nonexistent"), bundleID: "x", defaults: UserDefaults(suiteName: "RegistrationTests")!,
            readState: { _ in reads += 1; return .init(modeListed: reads >= 3, accepted: true, legacyEnabled: false) },
            fullFlow: { _, _, _ in flows += 1; return Registration.Result(outcome: .done) },
            pause: { pauses.append($0) })
        XCTAssertEqual(result, Registration.Result(outcome: .done, skipped: true))
        XCTAssertEqual(flows, 0)
        XCTAssertEqual(pauses, [0.25, 0.25])
    }

    func testNeverListedTakesTheFullFlowOnceAfterTwoSeconds() {
        var waited: TimeInterval = 0
        var flows = 0
        _ = Registration.run(
            bundleURL: URL(fileURLWithPath: "/nonexistent"), bundleID: "x", defaults: UserDefaults(suiteName: "RegistrationTests")!,
            readState: { _ in .init(modeListed: false, accepted: false, legacyEnabled: false) },
            fullFlow: { _, _, _ in flows += 1; return Registration.Result(outcome: .done) },
            pause: { waited += $0 })
        XCTAssertEqual(flows, 1)
        XCTAssertEqual(waited, 2.0)
    }

    func testPauseWaitsOnTheRunLoopEvenWithoutSources() {
        let start = Date()
        Registration.pause(0.2)
        let elapsed = Date().timeIntervalSince(start)
        XCTAssertGreaterThanOrEqual(elapsed, 0.2)
        XCTAssertLessThan(elapsed, 1.0)
    }

    private func run(_ state: Registration.State, mutations: inout Int) -> Registration.Result {
        var count = 0
        let result = Registration.run(
            bundleURL: URL(fileURLWithPath: "/nonexistent"), bundleID: "x", defaults: UserDefaults(suiteName: "RegistrationTests")!,
            readState: { _ in state },
            fullFlow: { _, _, _ in count += 1; return Registration.Result(outcome: .done) },
            pause: { _ in })
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
