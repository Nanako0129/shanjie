import AppKit
import XCTest
@testable import ShanjieKit

/// docs/spec/macos-shell.md §4: a key that leaves the composition unchanged (Tab into the prediction row) sends the
/// client no marked text; a key that changes it, and the first composition after the marked text was cleared, still do.
@MainActor
final class UnchangedMarkedTextTests: XCTestCase {
    private var resources: URL!

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell() -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(false), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        return shell
    }

    /// 你 typed with nothing pending, the prediction row shown and not entered.
    private func composed() -> Controller {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("su3")
        XCTAssertEqual(c.client.marked, "你")
        XCTAssertTrue(c.panel.visible)
        XCTAssertEqual(c.panel.selected, -1, "the prediction row is shown, not entered")
        return c
    }

    /// Tab enters the row and Tab again moves along it: the composition is the same, so the client gets no call.
    func testTabIntoThePredictionRowSendsNoMarkedText() {
        let c = composed()
        let calls = c.client.calls.count
        XCTAssertTrue(c.press(48))  // Tab
        XCTAssertEqual(c.panel.selected, 0)
        XCTAssertTrue(c.press(48))  // Tab
        XCTAssertEqual(c.panel.selected, 1)
        XCTAssertEqual(c.client.calls.count, calls, "an unchanged composition is not sent again")
        XCTAssertEqual(c.client.marked, "你")
    }

    /// A key that changes the composition is still sent.
    func testAChangedCompositionIsSent() {
        let c = composed()
        c.type("c")  // ㄏ
        XCTAssertEqual(c.client.marked, "你ㄏ")
    }

    /// After Esc clears the marked text, the same composition as the last one sent before it (ㄋ, Esc, ㄋ) is sent again.
    func testTheSameCompositionAfterClearingIsSentAgain() {
        let c = Controller(makeShell())
        c.session.activate()
        c.type("s")
        XCTAssertEqual(c.client.marked, "ㄋ")
        XCTAssertTrue(c.press(Keys.esc))
        XCTAssertEqual(c.client.marked, "", "Esc clears a lone pending syllable")
        let calls = c.client.calls.count
        c.type("s")
        XCTAssertEqual(c.client.marked, "ㄋ")
        XCTAssertGreaterThan(c.client.calls.count, calls, "the composition is sent again after the clear")
    }
}
