import AppKit
import XCTest
@testable import ShanjieKit

/// docs/spec/keys-and-composition.md §8.1 (Swift): 部分符號使用半形 defaults to off with `halfPunctDefaultMask`,
/// masks unknown bits on load and write, sends the mask on every engine build/swap, applies live toggles immediately,
/// preserves the custom mask while off, and keeps Eten's `'` / `=` as Zhuyin keys.
@MainActor
final class HalfPunctShellTests: XCTestCase {
    private var resources: URL!

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell(
        _ store: MemoryHalfPunctStore,
        english: Bool = false,
        packBuilder: PackBuilder = ImmediatePackBuilder()
    ) -> Shell {
        let shell = Shell(
            resources: resources,
            panel: FakePanel(),
            isSecureInput: { false },
            layoutStore: MemoryLayoutStore(),
            learningDirectory: nil,
            dialogs: FakeDialogs(),
            demoteStore: MemoryDemoteStore(),
            predictionStore: MemoryPredictionStore(),
            abbreviationStore: MemoryAbbreviationStore(),
            englishStore: MemoryEnglishStore(english),
            acgPackStore: MemoryAcgPackStore(),
            quotePackStore: MemoryQuotePackStore(),
            glassTintStore: MemoryGlassTintStore(),
            candidateOrientationStore: MemoryCandidateOrientationStore(),
            rerankStore: MemoryRerankStore(),
            rerankRunner: HeldRerankRunner(),
            profileModeStore: MemoryProfileModeStore(),
            appProfileStore: MemoryAppProfileStore(),
            packBuilder: packBuilder,
            spacingStore: MemorySpacingStore(),
            halfPunctStore: store
        )
        XCTAssertNotNil(shell.engine)
        return shell
    }

    private func sent(_ c: Controller, _ keys: String, flags: NSEvent.ModifierFlags = []) -> String {
        let before = c.client.text
        c.type(keys, flags: flags)
        c.press(Keys.enter)
        return String(c.client.text.dropFirst(before.count))
    }

    func testDefaultsAndCatalogCoverAll27Bits() {
        let store = MemoryHalfPunctStore()
        let shell = makeShell(store)
        let model = SettingsModel(shell: shell)

        XCTAssertFalse(model.halfPunct)
        XCTAssertFalse(shell.halfPunctOn)
        XCTAssertEqual(CoreEngine.halfPunctDefaultMask, 0x0060_99FF)
        XCTAssertEqual(CoreEngine.halfPunctAllMask, 0x07FF_FFFF)
        XCTAssertEqual(model.halfPunctMask, CoreEngine.halfPunctDefaultMask)
        XCTAssertEqual(shell.halfPunctMask, CoreEngine.halfPunctDefaultMask)
        XCTAssertEqual(shell.halfPunctSent, [0], "first engine gets 0 when switch is off")
        XCTAssertNil(store.halfPunct, "store stays nil until written")
        XCTAssertNil(store.halfPunctMask, "store mask stays nil until written")

        XCTAssertEqual(HalfPunctCatalog.groups.map(\.title), ["程式與指令", "括號與引號", "中文句讀鍵位"])
        XCTAssertEqual(HalfPunctCatalog.groups.map(\.items.count), [14, 9, 4])

        let bits = HalfPunctCatalog.groups.flatMap(\.items).map(\.bit)
        XCTAssertEqual(bits.count, 27)
        XCTAssertEqual(Set(bits), Set(0..<27))

        let group1Mask = HalfPunctCatalog.groups[0].items.reduce(UInt32(0)) { $0 | (UInt32(1) << UInt32($1.bit)) }
        XCTAssertEqual(group1Mask, CoreEngine.halfPunctDefaultMask)

        XCTAssertEqual(MenuEntry.Text.halfPunct, "部分符號使用半形")
        XCTAssertEqual(
            MenuEntry.Text.halfPunctNote,
            "開啟後，下方勾選的符號鍵一律直接送出半形（中英混打關著也有效）；原本的全形仍可用 Option 組合鍵或按 ↓ 選取。"
        )
        XCTAssertEqual(MenuEntry.Text.halfPunctCustomize, "自訂半形符號")
        XCTAssertEqual(MenuEntry.Text.halfPunctReset, "回復預設勾選")
        XCTAssertEqual(MenuEntry.Text.halfPunctEtenNote, "倚天排列下 '、= 為注音鍵，維持注音。")
    }

    func testUnknownBitsAreMaskedOnLoadAndWrite() {
        let store = MemoryHalfPunctStore(halfPunct: true, halfPunctMask: 0xFFFF_FFFF)
        let shell = makeShell(store)
        let model = SettingsModel(shell: shell)

        XCTAssertTrue(model.halfPunct)
        XCTAssertEqual(shell.halfPunctMask, CoreEngine.halfPunctAllMask)
        XCTAssertEqual(model.halfPunctMask, CoreEngine.halfPunctAllMask)
        XCTAssertEqual(shell.halfPunctSent, [CoreEngine.halfPunctAllMask])

        shell.applyHalfPunctMask(0xFFFF_FFFF)
        XCTAssertEqual(shell.halfPunctMask, CoreEngine.halfPunctAllMask)
        XCTAssertEqual(store.halfPunctMask, CoreEngine.halfPunctAllMask)
    }

    func testToggleBitsResetPreservesMaskWhenOffAndWorksWithEnglishOff() {
        let store = MemoryHalfPunctStore()
        let shell = makeShell(store, english: false)
        let model = SettingsModel(shell: shell)
        let c = Controller(shell)
        c.session.activate()

        // Default off: Shift+` sends ～.
        XCTAssertEqual(sent(c, "`", flags: .shift), "～")

        // Turn on: Shift+` immediately sends ~ (even with english = false); unchecked Shift+9 stays （.
        model.setHalfPunct(true)
        XCTAssertEqual(store.halfPunct, true)
        XCTAssertTrue(model.halfPunct)
        XCTAssertEqual(sent(c, "`", flags: .shift), "~")
        XCTAssertEqual(sent(c, "9", flags: .shift), "（")

        // Live toggle single bit 9: next Shift+9 immediately sends (.
        model.setHalfPunct(bit: 9, true)
        let customMask = CoreEngine.halfPunctDefaultMask | (UInt32(1) << 9)
        XCTAssertEqual(model.halfPunctMask, customMask)
        XCTAssertEqual(store.halfPunctMask, customMask)
        XCTAssertEqual(sent(c, "9", flags: .shift), "(")

        // Turn off: Shift+` returns to ～, while custom mask is preserved in model & store.
        model.setHalfPunct(false)
        XCTAssertFalse(model.halfPunct)
        XCTAssertEqual(store.halfPunct, false)
        XCTAssertEqual(model.halfPunctMask, customMask)
        XCTAssertEqual(store.halfPunctMask, customMask)
        XCTAssertEqual(sent(c, "`", flags: .shift), "～")

        // Turn back on: custom mask is restored.
        model.setHalfPunct(true)
        XCTAssertEqual(sent(c, "9", flags: .shift), "(")

        // Reset mask restores default (Shift+9 back to （).
        model.resetHalfPunctMask()
        XCTAssertEqual(model.halfPunctMask, CoreEngine.halfPunctDefaultMask)
        XCTAssertEqual(store.halfPunctMask, CoreEngine.halfPunctDefaultMask)
        XCTAssertEqual(sent(c, "9", flags: .shift), "（")
        XCTAssertEqual(sent(c, "`", flags: .shift), "~")
    }

    func testSurvivesLayoutAndPackRebuildsBackgroundInstallAndRestart() {
        let store = MemoryHalfPunctStore()
        let shell = makeShell(store)
        let model = SettingsModel(shell: shell)
        let c = Controller(shell)
        c.session.activate()

        model.setHalfPunct(true)
        model.setHalfPunct(bit: 9, true)
        let expectedMask = CoreEngine.halfPunctDefaultMask | (UInt32(1) << 9)
        XCTAssertEqual(sent(c, "9", flags: .shift), "(")

        // Layout rebuild: .eten -> .standard.
        c.session.perform(.layout(.eten))
        c.session.perform(.layout(.standard))
        XCTAssertEqual(shell.halfPunctSent.suffix(2), [expectedMask, expectedMask])
        XCTAssertEqual(sent(c, "9", flags: .shift), "(")

        // Synchronous pack rebuild.
        c.session.perform(.toggleAcgPack)
        c.session.perform(.toggleAcgPack)
        XCTAssertEqual(sent(c, "9", flags: .shift), "(")

        // Background pack rebuild via HeldPackBuilder -> installIfReady() -> setUp().
        let builder = HeldPackBuilder()
        let bgShell = makeShell(store, packBuilder: builder)
        let bgController = Controller(bgShell)
        bgController.session.activate()
        let sentBefore = bgShell.halfPunctSent.count
        bgShell.setAcgPack(false)
        builder.complete()
        XCTAssertEqual(bgShell.halfPunctSent.count, sentBefore + 1)
        XCTAssertEqual(bgShell.halfPunctSent.last, expectedMask)
        XCTAssertEqual(sent(bgController, "9", flags: .shift), "(")

        // Simulated restart with the same store.
        let restarted = makeShell(store)
        let restartedModel = SettingsModel(shell: restarted)
        let restartedController = Controller(restarted)
        restartedController.session.activate()
        XCTAssertTrue(restartedModel.halfPunct)
        XCTAssertEqual(restartedModel.halfPunctMask, expectedMask)
        XCTAssertEqual(sent(restartedController, "9", flags: .shift), "(")
        XCTAssertEqual(sent(restartedController, "`", flags: .shift), "~")
    }

    func testEtenDisablesPrimeAndEqualsAndKeepsThemAsZhuyin() {
        let store = MemoryHalfPunctStore(halfPunct: true, halfPunctMask: CoreEngine.halfPunctAllMask)
        let shell = makeShell(store)
        let model = SettingsModel(shell: shell)
        let c = Controller(shell)
        c.session.activate()

        XCTAssertFalse(model.showsHalfPunctEtenNote)
        XCTAssertFalse(model.isHalfPunctBitDisabled(22))
        XCTAssertFalse(model.isHalfPunctBitDisabled(26))

        model.selectLayout(.eten)
        XCTAssertTrue(model.showsHalfPunctEtenNote)
        XCTAssertTrue(model.isHalfPunctBitDisabled(22))
        XCTAssertTrue(model.isHalfPunctBitDisabled(26))
        for bit in [0, 1, 9, 15, 21, 23, 24, 25] {
            XCTAssertFalse(model.isHalfPunctBitDisabled(bit))
        }

        // Plain ' and = stay Zhuyin ㄘ and ㄦ even with all 27 bits enabled.
        c.type("'")
        XCTAssertEqual(c.client.marked, "ㄘ")
        XCTAssertEqual(c.client.text, "")
        c.press(Keys.esc)

        c.type("=")
        XCTAssertEqual(c.client.marked, "ㄦ")
        XCTAssertEqual(c.client.text, "")
        c.press(Keys.esc)

        // Non-Zhuyin Shift+` still sends ~ in Eten.
        XCTAssertEqual(sent(c, "`", flags: .shift), "~")
    }
}
