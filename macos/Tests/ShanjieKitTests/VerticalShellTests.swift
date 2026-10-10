import AppKit
import Carbon.HIToolbox
import XCTest
@testable import ShanjieKit

/// docs/contracts/candidate-vertical.md section 3.1, the Swift side through the real C core: the orientation reaches every
/// engine the Shell builds, the panel is told what the core output says, Page Up / Down go to the core only while the
/// vertical window is on screen, and anywhere else they take today's path for a key outside the tables.
@MainActor
final class VerticalShellTests: XCTestCase {
    private var resources: URL!
    private let pageDown = UInt16(kVK_PageDown), pageUp = UInt16(kVK_PageUp), down = UInt16(kVK_DownArrow), tab = UInt16(kVK_Tab)

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell(_ store: MemoryCandidateOrientationStore = MemoryCandidateOrientationStore()) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(),
                          predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(),
                          acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: store)
        XCTAssertNotNil(shell.engine)
        return shell
    }

    /// A controller on a shell whose orientation store holds `vertical`.
    private func controller(vertical: Bool?) -> Controller {
        let c = Controller(makeShell(MemoryCandidateOrientationStore(vertical)))
        c.session.activate()
        return c
    }

    // MARK: the store and the setting

    func testNeverSetIsHorizontal() {
        XCTAssertNil(MemoryCandidateOrientationStore().candidateVertical)
        let c = controller(vertical: nil)
        XCTAssertFalse(c.session.shell.candidateVertical)
        c.type("g4 ")
        XCTAssertTrue(c.panel.visible)
        XCTAssertEqual(c.panel.vertical, 0)
        XCTAssertEqual(c.panel.columns, 0)
        XCTAssertEqual(c.panel.items.count, 9)
        XCTAssertTrue(c.press(down), "horizontal Down still expands the grid")
        XCTAssertEqual(c.panel.columns, 9)
        XCTAssertEqual(c.panel.vertical, 0)
    }

    /// The Shell passes the stored orientation to the engine it builds without anyone calling a setter, and again to the one
    /// a layout switch rebuilds. Removing the call from `Shell.build()` makes both windows horizontal and this fails.
    func testBuildPathSendsTheStoredOrientationToEveryEngine() {
        let c = controller(vertical: true)
        let first = c.session.shell.engine
        c.type("g4 ")
        XCTAssertEqual(c.panel.vertical, 1, "the engine built at init did not get the orientation")
        XCTAssertEqual(c.panel.columns, 0)
        XCTAssertEqual(c.panel.items.count, 9)
        c.press(Keys.esc)
        c.press(Keys.esc)
        c.session.selectLayout(.eten)  // rebuilds the engine
        XCTAssertTrue(c.session.shell.engine !== first, "no rebuild")
        c.type("ne3 ")  // ㄋㄧˇ on the ETen layout, then space opens the window
        XCTAssertTrue(c.panel.visible)
        XCTAssertEqual(c.panel.vertical, 1, "the rebuilt engine did not get the orientation")
        XCTAssertEqual(c.panel.columns, 0)
    }

    /// The settings window's control: stored, remembered by the Shell, sent to the core, and read back by the model; it
    /// applies to the next window only.
    func testSettingChangeIsStoredSentToTheCoreAndAppliesToTheNextWindow() {
        let store = MemoryCandidateOrientationStore()
        let c = Controller(makeShell(store))
        c.session.activate()
        let model = SettingsModel(shell: c.session.shell)
        XCTAssertFalse(model.candidateVertical)
        c.type("g4 ")
        XCTAssertEqual(c.panel.vertical, 0)
        let shownBefore = c.panel.items

        model.setCandidateVertical(true)
        XCTAssertEqual(store.candidateVertical, true)
        XCTAssertTrue(c.session.shell.candidateVertical)
        XCTAssertTrue(model.candidateVertical)
        // The open candidate window keeps its orientation: the snapshot is the output already shown, and the next key still works on the bar and its grid.
        XCTAssertEqual(c.panel.vertical, 0)
        XCTAssertEqual(c.panel.items, shownBefore)
        c.press(down)
        XCTAssertEqual(c.panel.columns, 9, "the open window turned into a grid, not a vertical list")
        XCTAssertEqual(c.panel.vertical, 0)
        // Closed and opened again, it is vertical.
        c.press(Keys.esc)
        c.press(Keys.space)
        XCTAssertEqual(c.panel.vertical, 1)
        XCTAssertEqual(c.panel.columns, 0)

        model.setCandidateVertical(false)
        XCTAssertEqual(store.candidateVertical, false)
        XCTAssertEqual(c.panel.vertical, 1, "the open vertical window stays vertical")
        c.press(Keys.esc)
        c.press(Keys.space)
        XCTAssertEqual(c.panel.vertical, 0)
    }

    /// A new Shell reads the stored choice.
    func testStoredChoiceSurvivesARestart() {
        let store = MemoryCandidateOrientationStore()
        let a = Controller(makeShell(store))
        SettingsModel(shell: a.session.shell).setCandidateVertical(true)
        let b = Controller(makeShell(store))
        b.session.activate()
        b.type("g4 ")
        XCTAssertEqual(b.panel.vertical, 1)
    }

    // MARK: what the panel receives

    func testPanelReceivesTheCoreVerticalFlag() {
        let v = controller(vertical: true)
        v.type("g4 ")
        XCTAssertEqual(v.panel.vertical, 1)
        XCTAssertEqual([v.panel.columns, v.panel.first, v.panel.selected], [0, 0, 0])
        XCTAssertGreaterThan(v.panel.total, 27)
        // Punctuation windows follow the orientation too.
        v.press(Keys.esc)
        v.press(Keys.esc)
        v.type(",", flags: .shift)  // ，
        v.press(Keys.space)
        XCTAssertEqual(v.panel.vertical, 1)
        XCTAssertEqual(v.panel.items.first, "，")
        XCTAssertEqual(v.panel.columns, 0)
        // Closing the window clears the flag the shell remembers.
        v.press(Keys.esc)
        XCTAssertFalse(v.panel.visible)
        XCTAssertFalse(v.session.shell.verticalOpen)

        let h = controller(vertical: false)
        h.type("g4 ")
        XCTAssertEqual(h.panel.vertical, 0)
    }

    /// Candidate-vertical contract section 2.4: with the setting on, the prediction row (not entered, then entered) reaches the
    /// panel as 2, the candidate window opened over it as 1, and the row that comes back after a pick as 2 again; with the
    /// setting off it stays 0.
    func testPredictionRowFollowsTheSettingAndTheWindowOverItIsOne() {
        let c = controller(vertical: true)
        c.type("s")  // ㄋ: the passive row
        XCTAssertTrue(c.panel.visible)
        XCTAssertEqual(c.panel.vertical, 2)
        XCTAssertEqual(c.panel.columns, 0)
        XCTAssertEqual(c.panel.selected, -1)
        XCTAssertFalse(c.session.shell.verticalOpen, "the row is not the window: Page keys are not forwarded")
        c.press(tab)  // entered row
        XCTAssertEqual(c.panel.selected, 0)
        XCTAssertEqual(c.panel.vertical, 2)
        c.press(down)  // the entered vertical row moves with Down
        XCTAssertEqual(c.panel.selected, 1)
        XCTAssertEqual(c.panel.vertical, 2)
        c.press(Keys.esc)
        c.press(Keys.esc)
        // 2 -> 1 -> 2: a syllable's row, the window opened over it by Down, the row after a pick.
        c.type("su3")
        XCTAssertTrue(c.panel.visible)
        XCTAssertEqual(c.panel.vertical, 2, "the row after a complete syllable")
        let rowItems = c.panel.items
        c.press(down)  // rule 15 on the not-entered row: opens the candidates
        XCTAssertEqual(c.panel.vertical, 1)
        XCTAssertEqual(c.panel.selected, 0)
        XCTAssertNotEqual(c.panel.items, rowItems)
        XCTAssertTrue(c.session.shell.verticalOpen)
        c.type("1")  // picks the first row; the window closes
        XCTAssertFalse(c.panel.visible)
        c.type("s")
        XCTAssertTrue(c.panel.visible)
        XCTAssertEqual(c.panel.vertical, 2, "the row comes back vertical")

        let h = controller(vertical: false)
        h.type("s")
        XCTAssertTrue(h.panel.visible)
        XCTAssertEqual(h.panel.vertical, 0)
        h.press(tab)
        XCTAssertEqual(h.panel.vertical, 0)
    }

    /// Changing the setting while a row is on screen: the snapshot is applied to the owner's panel in the new orientation; a
    /// vertical candidate window stays.
    func testSettingChangeReRendersAShownPredictionRow() {
        let c = Controller(makeShell())
        c.session.activate()
        let model = SettingsModel(shell: c.session.shell)
        c.type("s")
        XCTAssertEqual(c.panel.vertical, 0)
        let items = c.panel.items
        model.setCandidateVertical(true)
        XCTAssertEqual(c.panel.vertical, 2, "the row on screen turned vertical at once")
        XCTAssertEqual(c.panel.items, items)
        model.setCandidateVertical(false)
        XCTAssertEqual(c.panel.vertical, 0)
    }

    /// Changing the setting during a horizontal composition (no row, or an open window) is invisible: no client call, no panel
    /// show. Only a prediction row that turns is applied.
    func testSettingChangeLeavesAHorizontalCompositionAlone() {
        let c = Controller(makeShell())
        c.session.activate()
        let model = SettingsModel(shell: c.session.shell)
        c.type("su3cl3")  // two syllables; a row may show, but then it is already vertical-free
        c.press(Keys.esc)
        c.type("g4 ")  // an open horizontal window
        XCTAssertEqual(c.panel.vertical, 0)
        let calls = c.client.calls.count, shows = c.panel.glassTints.count
        model.setCandidateVertical(true)
        model.setCandidateVertical(false)
        XCTAssertEqual(c.client.calls.count, calls, "no marked text was set again")
        XCTAssertEqual(c.panel.glassTints.count, shows, "the panel was not shown again")
    }

    // MARK: Page Up / Down inside the vertical window

    func testPageKeysTurnPagesInsideTheVerticalWindow() {
        let c = controller(vertical: true)
        c.type("g4 ")
        let page0 = c.panel.items
        XCTAssertEqual(page0.count, 9)
        XCTAssertTrue(c.session.shell.verticalOpen)

        XCTAssertTrue(c.press(pageDown), "Page Down is consumed")
        XCTAssertEqual(c.panel.first, 9)
        XCTAssertEqual(c.panel.selected, 0)
        XCTAssertEqual(c.panel.vertical, 1)
        let page1 = c.panel.items
        XCTAssertEqual(page1.count, 9)
        XCTAssertNotEqual(page1, page0)
        XCTAssertEqual(c.client.text, "", "nothing was committed")
        XCTAssertTrue(c.panel.visible)

        XCTAssertTrue(c.press(pageUp))
        XCTAssertEqual(c.panel.items, page0)
        XCTAssertEqual([c.panel.first, c.panel.selected], [0, 0])
        XCTAssertTrue(c.press(pageUp), "Page Up on the first page is consumed and does nothing")
        XCTAssertEqual(c.panel.items, page0)

        // Down nine times scrolls one row; the new row is the first of the next page.
        for _ in 0..<9 { c.press(down) }
        XCTAssertEqual([c.panel.first, c.panel.selected], [1, 8])
        XCTAssertEqual(Array(c.panel.items.dropLast()), Array(page0.dropFirst()))
        XCTAssertEqual(c.panel.items.last, page1.first)

        // A digit picks the n-th visible row; a click picks by row.
        c.press(pageDown)
        let visible = c.panel.items
        XCTAssertEqual(c.panel.first, 10)
        c.panel.click(2)
        XCTAssertEqual(c.client.marked, visible[2], "a click on row 3")
        XCTAssertFalse(c.panel.visible)
    }

    func testDigitPicksTheVisibleRowAfterAPageTurn() {
        let c = controller(vertical: true)
        c.type("g4 ")
        c.press(pageDown)
        let visible = c.panel.items
        c.type("4")
        XCTAssertEqual(c.client.marked, visible[3])
        XCTAssertFalse(c.panel.visible)
    }

    func testPageKeysAreKeysOutsideTheTablesOnceTheWindowIsClosed() {
        let c = controller(vertical: true)
        c.type("g4 ")
        c.press(Keys.esc)  // closes the window; the composition stays
        XCTAssertFalse(c.session.shell.verticalOpen)
        XCTAssertFalse(c.press(pageDown))
        XCTAssertEqual(c.client.marked, "")
        XCTAssertNotEqual(c.client.text, "", "the composition was committed before the key went on")
    }

    // MARK: Page Up / Down outside the vertical window = today's off-table path

    private struct Observed: Equatable {
        var handled: Bool
        var calls: [FakeClient.Call]
        var text: String
        var marked: String
        var panelVisible: Bool
    }

    private func observe(vertical: Bool, setup: (Controller) -> Void, key: UInt16, flags: NSEvent.ModifierFlags = []) -> Observed {
        let c = controller(vertical: vertical)
        setup(c)
        let handled = c.press(key, flags: flags)
        return Observed(handled: handled, calls: c.client.calls, text: c.client.text, marked: c.client.marked, panelVisible: c.panel.visible)
    }

    /// Page Down and Cmd+Page Down in each state outside the vertical window do what a keypad 0 (a key in neither table)
    /// does today: the composition, if any, is sent as `reset(0)` sends it (unfinished syllable included), the marked text and
    /// the panel go, and the key is not consumed. With no composition nothing is sent at all.
    func testPageKeysOutsideTheVerticalWindowTakeTheOffTablePath() {
        let scenarios: [(name: String, vertical: Bool, composing: Bool, keys: [(UInt16, NSEvent.ModifierFlags)], setup: (Controller) -> Void)] = [
            ("horizontal window", false, true, [(pageDown, []), (pageDown, .command)], { c in
                c.type("g4 ")
                XCTAssertTrue(c.panel.visible && c.panel.vertical == 0 && c.panel.columns == 0)
            }),
            ("grid", false, true, [(pageDown, []), (pageDown, .command)], { c in
                c.type("g4 ")
                c.press(self.down)
                XCTAssertEqual(c.panel.columns, 9)
            }),
            ("composing", true, true, [(pageDown, []), (pageDown, .command)], { c in
                c.type("su3cl3")
                XCTAssertEqual(c.client.marked, "你好")
            }),
            ("unfinished syllable", true, true, [(pageDown, []), (pageDown, .command)], { c in
                c.type("su3c")
                XCTAssertTrue(c.client.marked.hasSuffix("ㄏ"))
            }),
            ("prediction row entered", true, true, [(pageDown, []), (pageDown, .command)], { c in
                c.type("s")
                c.press(self.tab)
                XCTAssertEqual(c.panel.selected, 0, "the row is entered")
                XCTAssertEqual(c.panel.vertical, 2, "a vertical row, which is not the window")
            }),
            ("prediction row, not entered", true, true, [(pageDown, []), (pageDown, .command)], { c in
                c.type("s")
                XCTAssertEqual(c.panel.selected, -1)
                XCTAssertEqual(c.panel.vertical, 2)
            }),
            ("empty composition", true, false, [(pageDown, []), (pageDown, .command)], { _ in }),
            ("vertical window, Command", true, true, [(pageDown, .command), (pageUp, .command), (pageDown, .shift), (pageDown, .control)], { c in
                c.type("g4 ")
                XCTAssertEqual(c.panel.vertical, 1)
            }),
        ]
        for s in scenarios {
            let reference = observe(vertical: s.vertical, setup: s.setup, key: UInt16(Keys.keypad0))
            XCTAssertFalse(reference.handled, s.name)
            for (key, flags) in s.keys {
                let got = observe(vertical: s.vertical, setup: s.setup, key: key, flags: flags)
                XCTAssertEqual(got, reference, "\(s.name): key \(key) flags \(flags.rawValue)")
                XCTAssertFalse(got.handled, s.name)
                XCTAssertEqual(got.marked, "", s.name)
                XCTAssertFalse(got.panelVisible, s.name)
                if s.composing { XCTAssertNotEqual(got.text, "", "\(s.name): the composition was committed") }
                else { XCTAssertEqual(got.calls, [], "\(s.name): nothing was sent to the client") }
            }
        }
    }
}
