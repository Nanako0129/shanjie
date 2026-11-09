import AppKit
import XCTest
@testable import ShanjieKit

/// docs/spec/macos-shell.md §13: the in-memory diagnostic log.
@MainActor
final class DiagLogTests: XCTestCase {
    private var resources: URL!
    private var time: TimeInterval = 100
    private var uptimeNow: TimeInterval = 0

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell(shiftToggle: Bool = true, hint: FakeShiftHint = FakeShiftHint()) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(),
                          quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
                          shiftToggleStore: MemoryShiftToggleStore(shiftToggle), shiftHint: hint)
        shell.mouseAgo = { 1000 }
        shell.uptime = { [unowned self] in self.uptimeNow }
        XCTAssertNotNil(shell.engine)
        return shell
    }

    /// A left Shift event as the system sends it: the generic bit and the left device bit while down, no bits when up.
    private func shift(_ c: Controller, down: Bool) {
        uptimeNow = time
        let flags: NSEvent.ModifierFlags = down ? NSEvent.ModifierFlags(rawValue: NSEvent.ModifierFlags.shift.rawValue | 0x2) : []
        c.session.handle(Keys.event(56, flags: flags, type: .flagsChanged, time: time))
    }

    private func tap(_ c: Controller) {
        time += 5
        shift(c, down: true)
        time += 0.1
        shift(c, down: false)
    }

    /// A key event that types `ch` (with its characters, so it counts as a typing key).
    private func typeKey(_ c: Controller, _ ch: Character) {
        c.session.handle(Keys.event(Keys.code(for: ch), characters: String(ch)))
    }

    // MARK: k. client reads

    /// The reads each step makes of both clients together: bundle ID, the three text reads (`reads`), caret rectangle, line rectangle.
    private func readSteps(shiftToggle: Bool) -> [String: [Int]] {
        let shell = makeShell(shiftToggle: shiftToggle)
        let a = Controller(shell, bundle: "com.example.A"), b = Controller(shell, bundle: "com.example.B")
        var out: [String: [Int]] = [:]
        func counts() -> [Int] {
            [a.client.bundleReads + b.client.bundleReads, a.client.reads + b.client.reads,
             a.client.caretAsks + b.client.caretAsks, a.client.lineAsks + b.client.lineAsks]
        }
        func step(_ name: String, _ body: () -> Void) {
            let before = counts()
            body()
            out[name] = zip(counts(), before).map { $0 - $1 }
        }
        step("activate") { a.session.activate() }
        step("key s") { typeKey(a, "s") }
        step("key u") { typeKey(a, "u") }
        step("key 3") { typeKey(a, "3") }
        step("commitComposition") { a.session.commitComposition() }
        step("tap 1") { tap(a) }
        step("key h (English)") { typeKey(a, "h") }
        step("tap 2") { tap(a) }
        step("key s again") { typeKey(a, "s") }
        step("tap 3 (composing)") { tap(a) }
        step("tap 4 (composing)") { tap(a) }
        step("toggleAppChat") { a.session.toggleAppChat() }
        step("key b claims") { typeKey(b, "s") }
        step("deactivate a (not owner)") { a.session.deactivate() }
        step("deactivate b (owner)") { b.session.deactivate() }
        step("activate a") { a.session.activate() }
        return out
    }

    /// The client reads of activate, deactivate, commitComposition, the Shift toggle, toggleAppChat and the keys, with the Shift
    /// toggle on and off. The numbers were measured before the diagnostic log existed and must not change with it: recording
    /// adds no client read (docs/spec/macos-shell.md §13).
    func testRecordingAddsNoClientRead() {
        // [bundleReads, reads, caretAsks, lineAsks]
        // Measured on the shell before the diagnostic log (origin/main 12a85fb4), 2026-11-10. Never edit these to make a test pass.
        let on: [String: [Int]] = [
            "activate": [1, 0, 0, 0], "key s": [1, 2, 0, 2], "key u": [1, 0, 0, 1], "key 3": [1, 0, 0, 1], "commitComposition": [1, 0, 0, 0],
            "tap 1": [1, 0, 1, 0], "key h (English)": [0, 0, 0, 0], "tap 2": [1, 0, 1, 0], "key s again": [1, 3, 0, 1],
            "tap 3 (composing)": [1, 0, 0, 2], "tap 4 (composing)": [1, 0, 0, 1], "toggleAppChat": [1, 0, 0, 0], "key b claims": [3, 2, 0, 2],
            "deactivate a (not owner)": [0, 0, 0, 0], "deactivate b (owner)": [1, 0, 0, 0], "activate a": [1, 0, 0, 0],
        ]
        let off: [String: [Int]] = [
            "activate": [1, 0, 0, 0], "key s": [1, 2, 0, 1], "key u": [1, 0, 0, 1], "key 3": [1, 0, 0, 1], "commitComposition": [1, 0, 0, 0],
            "tap 1": [0, 0, 0, 0], "key h (English)": [1, 3, 0, 1], "tap 2": [0, 0, 0, 0], "key s again": [1, 0, 0, 1],
            "tap 3 (composing)": [0, 0, 0, 0], "tap 4 (composing)": [0, 0, 0, 0], "toggleAppChat": [1, 0, 0, 0], "key b claims": [3, 2, 0, 1],
            "deactivate a (not owner)": [0, 0, 0, 0], "deactivate b (owner)": [1, 0, 0, 0], "activate a": [1, 0, 0, 0],
        ]
        XCTAssertEqual(readSteps(shiftToggle: true), on, "toggle on")
        XCTAssertEqual(readSteps(shiftToggle: false), off, "toggle off")
    }
}
