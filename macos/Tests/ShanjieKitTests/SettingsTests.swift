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
        var layout: MemoryLayoutStore, demote: MemoryDemoteStore, prediction: MemoryPredictionStore, abbreviation: MemoryAbbreviationStore, english: MemoryEnglishStore
        var acg: MemoryAcgPackStore, tint: MemoryGlassTintStore
        var dialogs: FakeDialogs
    }

    private func rig(secure: Bool = false, learning: URL? = nil) -> Rig {
        let layout = MemoryLayoutStore(), demote = MemoryDemoteStore(), prediction = MemoryPredictionStore(), abbreviation = MemoryAbbreviationStore()
        let acg = MemoryAcgPackStore(), tint = MemoryGlassTintStore(), dialogs = FakeDialogs(), english = MemoryEnglishStore(false)  // saved off: the default is on (digits.md A.3.7), the tests below start from off
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { secure }, layoutStore: layout,
                          learningDirectory: learning, dialogs: dialogs, demoteStore: demote, predictionStore: prediction, abbreviationStore: abbreviation, englishStore: english,
                          acgPackStore: acg, glassTintStore: tint, candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        return Rig(shell: shell, model: SettingsModel(shell: shell), controller: Controller(shell),
                   layout: layout, demote: demote, prediction: prediction, abbreviation: abbreviation, english: english, acg: acg, tint: tint, dialogs: dialogs)
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

    /// Digits.md A.3.7: nothing saved is on; a saved off stays off; the line under the toggle is the contract's. (Its place in the window,
    /// right under the toggle, is in the app target's form, which has no tests: the shipping layout cannot be observed here.)
    func testEnglishMixingDefaultsOnAndTheNoteIsTheContractsLine() throws {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(),
                          acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertTrue(SettingsModel(shell: shell).english, "nothing saved is on")
        XCTAssertFalse(rig().model.english, "a saved off stays off")
        XCTAssertEqual(MenuEntry.Text.englishDigitsNote, "打英文不用切換輸入法；數字列的鍵也會打出數字（例如 2026年、3.14），由前文判斷是數字還是注音。")
    }

    /// zhen-mixed section 3.2 / 5.3: the window's 中英混打（實驗） goes through the same setter and store as the menu item, the two stay in
    /// sync, and it is greyed (setting kept) when the engine has no word list, which the window's toggle follows. The toggle's
    /// place in the window (right after 即時預測, as in the menu) is in the app target's form, which has no tests.
    func testEnglishFromTheWindowReachesStoreAndMenuAndFollowsAvailability() throws {
        let r = rig()
        XCTAssertFalse(r.model.english, "saved off")
        XCTAssertTrue(r.model.englishEnabled)
        r.model.setEnglish(true)
        XCTAssertEqual(r.english.english, true)
        XCTAssertEqual(item(r, .toggleEnglish)?.checked, true)
        r.controller.session.perform(.toggleEnglish)
        XCTAssertFalse(r.model.english, "the model did not follow the menu")
        XCTAssertEqual(r.english.english, false)
        // No word list in Resources: the model and the menu item are both unavailable, the setting still shows.
        let bare = try XCTUnwrap(TestData.resources(only: Set(TestData.files.map(\.name)).subtracting(["english-words.txt"])))
        let english = MemoryEnglishStore(true)
        let shell = Shell(resources: bare, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: english,
                          acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        let model = SettingsModel(shell: shell)
        XCTAssertFalse(model.englishEnabled)
        XCTAssertTrue(model.english, "greyed, still showing the setting")
        let entry = Controller(shell).session.menu.first { $0.action == .toggleEnglish }
        XCTAssertEqual(entry?.enabled, false)
        XCTAssertEqual(entry?.checked, true)
    }

    /// zhen-mixed section 2.1: the Eten layout records no shadow, so with the list loaded the menu item and the window's toggle are
    /// greyed there too (setting kept), and enabled again back in the standard layout. Both directions of the layout change: the
    /// window's picker and the menu's item.
    func testEnglishIsGreyedInTheEtenLayout() {
        let r = rig()
        r.model.setEnglish(true)
        XCTAssertTrue(r.shell.englishAvailable, "the list loaded; only the layout greys it below")
        XCTAssertEqual(item(r, .toggleEnglish)?.enabled, true)
        XCTAssertTrue(r.model.englishEnabled)
        r.model.selectLayout(.eten)
        XCTAssertEqual(item(r, .toggleEnglish)?.enabled, false, "the menu item in Eten")
        XCTAssertEqual(item(r, .toggleEnglish)?.checked, true, "greyed, still showing the setting")
        XCTAssertFalse(r.model.englishEnabled, "the window's toggle in Eten")
        XCTAssertTrue(r.model.english)
        r.controller.session.perform(.layout(.standard))
        XCTAssertEqual(item(r, .toggleEnglish)?.enabled, true)
        XCTAssertTrue(r.model.englishEnabled, "the window followed the menu's layout change")
    }

    /// zhen-inline section 2 / 4.3: the window's 英文的顯示 choice defaults to true, goes to the store, and is greyed with the 中英混打
    /// switch off, with the switch greyed (Eten), and with no word list; the setting is kept while greyed.
    func testEnglishInlineChoiceStoresAndFollowsTheSwitch() throws {
        let r = rig()
        XCTAssertTrue(r.model.englishInline, "default true")
        XCTAssertFalse(r.model.englishInlineEnabled, "the switch is off")
        r.model.setEnglish(true)
        XCTAssertTrue(r.model.englishInlineEnabled)
        r.model.setEnglishInline(false)
        XCTAssertEqual(r.english.englishInline, false)
        XCTAssertFalse(r.model.englishInline)
        XCTAssertTrue(r.model.englishInlineEnabled, "the choice itself does not grey it")
        r.model.selectLayout(.eten)
        XCTAssertFalse(r.model.englishInlineEnabled, "Eten greys the switch and so the choice")
        XCTAssertFalse(r.model.englishInline, "the setting is kept")
        r.controller.session.perform(.layout(.standard))
        XCTAssertTrue(r.model.englishInlineEnabled)
        r.controller.session.perform(.toggleEnglish)
        XCTAssertFalse(r.model.englishInlineEnabled, "the menu's switch off greys it too")
        // No word list: greyed, the stored choice still shows.
        let bare = try XCTUnwrap(TestData.resources(only: Set(TestData.files.map(\.name)).subtracting(["english-words.txt"])))
        let english = MemoryEnglishStore(true, inline: false)
        let shell = Shell(resources: bare, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: english,
                          acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        let model = SettingsModel(shell: shell)
        XCTAssertFalse(model.englishInlineEnabled)
        XCTAssertFalse(model.englishInline, "greyed, still showing the setting")
    }

    /// s7c2b section 2.2: the window's 語意校正 goes through the same setter as the menu item, the two stay in sync, and with the
    /// demotion table off the menu item is greyed (checkmark kept), which the window's toggle follows through `demote` (F11).
    func testRerankFromTheWindowReachesMenuAndFollowsDemote() {
        let r = rig()
        XCTAssertTrue(r.model.rerank, "never chosen: on (s7h section 2.3)")
        XCTAssertEqual(item(r, .toggleRerank)?.checked, true)
        r.model.setRerank(false)
        XCTAssertFalse(r.shell.rerankOn)
        XCTAssertEqual(item(r, .toggleRerank)?.checked, false)
        XCTAssertFalse(r.model.rerank)
        r.controller.session.perform(.toggleRerank)
        XCTAssertTrue(r.model.rerank, "the model did not follow the menu")
        r.model.setDemote(false)
        XCTAssertFalse(r.model.demote, "the window greys 語意校正 on this")
        XCTAssertEqual(item(r, .toggleRerank)?.enabled, false)
        XCTAssertEqual(item(r, .toggleRerank)?.checked, true, "greyed, still showing the setting")
        r.model.setDemote(true)
        XCTAssertEqual(item(r, .toggleRerank)?.enabled, true)
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

    func testItPackSwitchFromTheWindowReachesStoreAndMenuLine() {
        let r = rig()
        let before = r.shell.engine
        XCTAssertTrue(r.model.itPack, "default on")
        r.model.setItPack(false)
        XCTAssertFalse(r.model.itPack)
        XCTAssertEqual(r.controller.session.menu.first { $0.title.hasPrefix("已開啟的詞包") }?.title, "已開啟的詞包：動漫與遊戲詞", "the menu line follows the switch")
        XCTAssertTrue(r.shell.engine !== before, "no rebuild")
        r.controller.session.perform(.toggleItPack)
        XCTAssertTrue(r.model.itPack, "the model did not follow the menu")
    }

    /// acg-pack A2.4 (moved here by it-pack 3.2): the window shows the grey data-date line under the switch, also with the pack off; the menu does not.
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
        XCTAssertFalse(on.controller.session.menu.contains { $0.title.hasPrefix("資料更新至") }, "it-pack 3.2: the date is shown here, not in the menu")
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
        XCTAssertEqual(r.controller.session.menu.map(\.title), ["標準鍵盤", "倚天鍵盤", "避免把敏感字詞排在前面", "即時預測", "可省略韻母", "中英混打（實驗）", "語意校正", "已開啟的詞包：動漫與遊戲詞、資訊與 AI 詞", "這個 App 用聊天設定", "善解設定…", "清除選字記憶…", "不要備份選字記憶"])
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
                          predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), glassTintStore: tint, candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertEqual(shell.glassTint, 0.4)
        tint.glassTint = 0.9
        XCTAssertEqual(shell.glassTint, 0.4, "the Shell reads the store only at start and on set")
    }

    /// Status rows follow the external state when asked, not on the change notification.
    func testExternalStateIsReadOnRequest() {
        var secure = false
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { secure }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(),
                          predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        let model = SettingsModel(shell: shell)
        secure = true
        shell.changed()
        XCTAssertFalse(model.pausedSecure, "a settings change does not re-read secure input")
        model.refreshExternal()
        XCTAssertTrue(model.pausedSecure)
    }

    // MARK: settings-sidebar section 2.2

    func testFrameIsClampedIntoTheUsableArea() {
        let area = CGRect(x: 0, y: 80, width: 1470, height: 860)  // Dock below y 80, menu bar above y 940
        let inside = CGRect(x: 100, y: 200, width: 580, height: 500)
        XCTAssertEqual(settingsFrame(inside, clampedInto: area), inside)
        // Grew downward past the Dock: moved up so the bottom edge sits on the area's.
        XCTAssertEqual(settingsFrame(CGRect(x: 100, y: 30, width: 580, height: 500), clampedInto: area), CGRect(x: 100, y: 80, width: 580, height: 500))
        // Sticks out above the menu bar: moved down so the top edge sits on the area's.
        XCTAssertEqual(settingsFrame(CGRect(x: 100, y: 600, width: 580, height: 500), clampedInto: area), CGRect(x: 100, y: 440, width: 580, height: 500))
    }
}
