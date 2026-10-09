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
        var layout: MemoryLayoutStore, demote: MemoryDemoteStore, prediction: MemoryPredictionStore
        var acg: MemoryAcgPackStore, tint: MemoryGlassTintStore
        var dialogs: FakeDialogs
    }

    private func rig(secure: Bool = false, learning: URL? = nil) -> Rig {
        let layout = MemoryLayoutStore(), demote = MemoryDemoteStore(), prediction = MemoryPredictionStore()
        let acg = MemoryAcgPackStore(), tint = MemoryGlassTintStore(), dialogs = FakeDialogs()
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { secure }, layoutStore: layout,
                          learningDirectory: learning, dialogs: dialogs, demoteStore: demote, predictionStore: prediction,
                          acgPackStore: acg, glassTintStore: tint)
        XCTAssertNotNil(shell.engine)
        return Rig(shell: shell, model: SettingsModel(shell: shell), controller: Controller(shell),
                   layout: layout, demote: demote, prediction: prediction, acg: acg, tint: tint, dialogs: dialogs)
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
        XCTAssertEqual(r.controller.session.menu.map(\.title), ["標準鍵盤", "倚天鍵盤", "避免把敏感字詞排在前面", "即時預測", "動漫與遊戲詞", "善解設定…", "清除選字記憶…", "不要備份選字記憶"])
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

    func testThereIsNoThisAppRow() {
        // The model has no client; a denylisted app pauses the menu's line only.
        let r = rig()
        let denied = Controller(r.shell, bundle: "com.bitwarden.desktop")
        XCTAssertEqual(denied.session.menu.first, MenuEntry(title: "學習已暫停（此 App）"))
        r.model.refresh()
        XCTAssertFalse(r.model.pausedSecure)
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
}
