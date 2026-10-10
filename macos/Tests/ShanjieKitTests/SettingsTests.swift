import AppKit
import XCTest
@testable import ShanjieKit

/// docs/contracts/settings-window.md section 3.1: the settings window's model drives the same Shell
/// setters and stores as the menu, follows the menu through the Shell's notification, and the glass
/// tint is stored, converted and handed to the panel on every show.
@MainActor
final class SettingsTests: XCTestCase {
    private var resources: URL!

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private struct Rig {
        var shell: Shell
        var model: SettingsModel
        var controller: Controller
        var layout: MemoryLayoutStore, demote: MemoryDemoteStore, prediction: MemoryPredictionStore, abbreviation: MemoryAbbreviationStore
        var acg: MemoryAcgPackStore, tint: MemoryGlassTintStore
        var dialogs: FakeDialogs
    }

    private func rig(secure: Bool = false, learning: URL? = nil) -> Rig {
        let layout = MemoryLayoutStore(), demote = MemoryDemoteStore(), prediction = MemoryPredictionStore(), abbreviation = MemoryAbbreviationStore()
        let acg = MemoryAcgPackStore(), tint = MemoryGlassTintStore(), dialogs = FakeDialogs()
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { secure }, layoutStore: layout,
                          learningDirectory: learning, dialogs: dialogs, demoteStore: demote, predictionStore: prediction, abbreviationStore: abbreviation,
                          acgPackStore: acg, glassTintStore: tint, candidateOrientationStore: MemoryCandidateOrientationStore())
        XCTAssertNotNil(shell.engine)
        return Rig(shell: shell, model: SettingsModel(shell: shell), controller: Controller(shell),
                   layout: layout, demote: demote, prediction: prediction, abbreviation: abbreviation, acg: acg, tint: tint, dialogs: dialogs)
    }

    private func item(_ r: Rig, _ a: MenuEntry.Action) -> MenuEntry? { r.controller.session.menu.first { $0.action == a } }

    // MARK: same setters and stores as the menu, both directions

    func testPredictionFromTheWindowReachesStoreAndMenu() {
        let r = rig()
        XCTAssertTrue(r.model.prediction)
        r.model.setPrediction(false)
        XCTAssertEqual(r.prediction.prediction, false)
        XCTAssertEqual(item(r, .togglePrediction)?.checked, false)
        XCTAssertFalse(r.model.prediction)
        r.controller.session.perform(.togglePrediction)  // the menu, then the model follows
        XCTAssertTrue(r.model.prediction, "the model did not follow the menu")
    }

    /// V3 section 12.1: the window's "可省略韻母" goes through the same setter and store as the menu item, the two stay in sync,
    /// and the menu item is greyed (checkmark kept) while the prediction row is off, which is what the window's toggle follows.
    func testAbbreviationFromTheWindowReachesStoreAndMenuAndFollowsPrediction() {
        let r = rig()
        XCTAssertFalse(r.model.abbreviation)
        r.model.setAbbreviation(true)
        XCTAssertEqual(r.abbreviation.abbreviation, true)
        XCTAssertEqual(item(r, .toggleAbbreviation)?.checked, true)
        r.controller.session.perform(.toggleAbbreviation)
        XCTAssertFalse(r.model.abbreviation, "the model did not follow the menu")
        r.model.setAbbreviation(true)
        r.model.setPrediction(false)
        XCTAssertEqual(item(r, .toggleAbbreviation)?.enabled, false)
        XCTAssertEqual(item(r, .toggleAbbreviation)?.checked, true, "greyed, still showing the setting")
        XCTAssertFalse(r.model.prediction)
        XCTAssertTrue(r.model.abbreviation)
        r.model.setPrediction(true)
        XCTAssertEqual(item(r, .toggleAbbreviation)?.enabled, true)
    }

    func testDemoteFromTheWindowReachesStoreAndMenu() {
        let r = rig()
        r.model.setDemote(false)
        XCTAssertEqual(r.demote.demote, false)
        XCTAssertEqual(item(r, .toggleDemote)?.checked, false)
        r.controller.session.perform(.toggleDemote)
        XCTAssertTrue(r.model.demote, "the model did not follow the menu")
    }

    func testLayoutPickerCallsSelectLayout() {
        let r = rig()
        r.model.selectLayout(.eten)
        XCTAssertEqual(r.layout.layout, "eten")
        XCTAssertEqual(r.controller.session.layout, .eten)
        XCTAssertEqual(r.model.layout, .eten)
        r.controller.session.perform(.layout(.standard))
        XCTAssertEqual(r.model.layout, .standard, "the model did not follow the menu")
    }

    func testAcgPackSwitchRebuildsTheEngineLikeTheMenu() {
        let r = rig()
        let before = r.shell.engine
        r.model.setAcgPack(false)
        XCTAssertEqual(r.acg.acgPack, false)
        XCTAssertFalse(r.model.acgPack)
        XCTAssertTrue(r.shell.engine !== before, "no rebuild")
        r.controller.session.perform(.toggleAcgPack)
        XCTAssertTrue(r.model.acgPack, "the model did not follow the menu")
    }

    /// acg-pack A2.4: the window shows the same grey data-date line as the menu, from the same Shell value, also with the pack off.
    func testAcgDataDateLineIsTheMenusLine() throws {
        let packs = resources.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        let manifest = packs.appendingPathComponent("acg.json")
        let make = { (acg: Bool?) -> Rig in
            let r = self.rig()
            r.model.setAcgPack(acg ?? true)
            return r
        }
        XCTAssertNil(make(nil).model.acgDataLine, "no manifest, no line")
        try #"{"latest_source_revision": "2026-10-06T23:59:59Z"}"#.write(to: manifest, atomically: true, encoding: .utf8)
        let on = make(nil)
        XCTAssertEqual(on.model.acgDataLine, "資料更新至 2026-10-06（維基百科）")
        XCTAssertEqual(on.model.acgDataLine, on.controller.session.menu.first { $0.title.hasPrefix("資料更新至") }?.title)
        XCTAssertEqual(make(false).model.acgDataLine, "資料更新至 2026-10-06（維基百科）", "shown with the pack off too")
    }

    func testBackupSwitchAndClear() throws {
        let dir = TestLearning.directory()
        let r = rig(learning: dir)
        r.model.setBackupExcluded(true)
        XCTAssertEqual(try dir.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup, true)
        XCTAssertEqual(item(r, .toggleBackup)?.checked, true)
        XCTAssertTrue(r.model.backupExcluded)
        r.model.clear()
        XCTAssertEqual(r.dialogs.asked, 1, "the clear did not go through the menu's confirmation")
    }

    func testMenuHasTheSettingsItemBeforeClear() {
        let r = rig()
        XCTAssertEqual(r.controller.session.menu.map(\.title), ["標準鍵盤", "倚天鍵盤", "避免把敏感字詞排在前面", "即時預測", "可省略韻母", "動漫與遊戲詞", "善解設定…", "清除選字記憶…", "不要備份選字記憶"])
        var opened = 0
        r.shell.onOpenSettings = { opened += 1 }
        r.controller.session.perform(.openSettings)
        XCTAssertEqual(opened, 1)
    }

    // MARK: status rows

    func testStatusRows() {
        let secure = rig(secure: true)
        XCTAssertTrue(secure.model.pausedSecure)
        XCTAssertFalse(rig().model.pausedSecure)
        // a file where the learning directory should be: the core cannot open it
        let file = TestLearning.directory(create: false)
        try? Data("x".utf8).write(to: file)
        XCTAssertTrue(rig(learning: file).model.unavailable)
        XCTAssertFalse(rig().model.unavailable)
    }

    // MARK: glass tint

    func testGlassTintDefaultsToZeroAndIsHandedToEveryShow() {
        let r = rig()
        XCTAssertEqual(r.shell.glassTint, 0)
        XCTAssertEqual(r.model.glassTint, 0)
        let c = r.controller
        c.session.activate()
        c.type("s")
        XCTAssertEqual(c.panel.glassTints.last, 0)
        r.model.setGlassTint(0.6)
        XCTAssertEqual(r.tint.glassTint, 0.6)
        XCTAssertEqual(r.model.glassTint, 0.6)
        c.type("3")
        XCTAssertEqual(c.panel.glassTints.last, 0.6)
        r.model.setGlassTint(0)
        c.press(Keys.esc)
        c.type("s")
        XCTAssertEqual(c.panel.glassTints.last, 0, "back to 0 must reach the panel")
    }

    func testTintConversion() {
        typealias G = GlassTint
        XCTAssertEqual(G.GLASS_TINT_MAX, 0.5)
        XCTAssertNil(G.tint(value: 0, dark: true))
        XCTAssertNil(G.tint(value: .nan, dark: false))
        XCTAssertNil(G.tint(value: -3, dark: true), "negative clamps to 0")
        XCTAssertEqual(G.tint(value: 1, dark: true), G.Tint(black: true, opacity: 0.5))
        XCTAssertEqual(G.tint(value: 7, dark: false), G.Tint(black: false, opacity: 0.5), "above 1 clamps to 1")
        XCTAssertEqual(G.tint(value: 0.5, dark: false)?.opacity, 0.25)
        // the panel is told the same thing each time, so 0.6 then 0 removes the tint
        XCTAssertNotNil(G.tint(value: 0.6, dark: true))
        XCTAssertNil(G.tint(value: 0, dark: true))
    }

    /// The panel and the settings window's sample bar both pick black or white through this.
    func testIsDarkFollowsTheAppearance() throws {
        XCTAssertTrue(GlassTint.isDark(try XCTUnwrap(NSAppearance(named: .darkAqua))))
        XCTAssertTrue(GlassTint.isDark(try XCTUnwrap(NSAppearance(named: .vibrantDark))))
        XCTAssertFalse(GlassTint.isDark(try XCTUnwrap(NSAppearance(named: .aqua))))
        // the sample bar's tint for a slider value is exactly the panel's: one function, one Applier
        var a = GlassTint.Applier()
        XCTAssertEqual(a.update(value: 0.6, dark: GlassTint.isDark(try XCTUnwrap(NSAppearance(named: .darkAqua)))),
                       .set(try XCTUnwrap(GlassTint.tint(value: 0.6, dark: true))))
    }

    /// Section 2.3: a candidate panel that is up changes when the slider moves, with no key pressed.
    func testSliderReachesAVisiblePanelAtOnce() {
        let r = rig()
        let c = r.controller
        c.session.activate()
        c.type("s")
        XCTAssertTrue(c.panel.visible)
        XCTAssertTrue(c.panel.retints.isEmpty)
        r.model.setGlassTint(0.6)
        XCTAssertEqual(c.panel.retints, [0.6])
        r.model.setGlassTint(0)
        XCTAssertEqual(c.panel.retints, [0.6, 0], "0 must reach the panel too")
    }

    /// The adapter assigns exactly what the Applier returns, so these are its decisions.
    func testApplierSetsClearsAndSkipsUnchanged() {
        var a = GlassTint.Applier()
        XCTAssertNil(a.update(value: 0, dark: true), "never tinted and 0: assign nothing")
        XCTAssertEqual(a.update(value: 0.6, dark: true), .set(GlassTint.Tint(black: true, opacity: 0.3)))
        XCTAssertNil(a.update(value: 0.6, dark: true), "unchanged: no assignment")
        XCTAssertEqual(a.update(value: 0.6, dark: false), .set(GlassTint.Tint(black: false, opacity: 0.3)), "appearance flipped")
        XCTAssertEqual(a.update(value: 0, dark: false), .clear, "0.6 to 0 removes the tint")
        XCTAssertNil(a.update(value: 0, dark: false))
    }

    /// The cached value is what shows read: no store read per key, and the store is written through.
    func testGlassTintIsCachedFromTheStoreAtStart() {
        let tint = MemoryGlassTintStore(0.4)
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(),
                          predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), acgPackStore: MemoryAcgPackStore(), glassTintStore: tint, candidateOrientationStore: MemoryCandidateOrientationStore())
        XCTAssertEqual(shell.glassTint, 0.4)
        tint.glassTint = 0.9
        XCTAssertEqual(shell.glassTint, 0.4, "the Shell reads the store only at start and on set")
    }

    /// Status rows follow the external state when asked, not on the change notification.
    func testExternalStateIsReadOnRequest() {
        var secure = false
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { secure }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(),
                          predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore())
        let model = SettingsModel(shell: shell)
        secure = true
        shell.changed()
        XCTAssertFalse(model.pausedSecure, "a settings change does not re-read secure input")
        model.refreshExternal()
        XCTAssertTrue(model.pausedSecure)
    }
}
