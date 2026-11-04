import AppKit
import XCTest
@testable import ShanjieKit

/// docs/contracts/english-defaults-notice.md section 4.1: the one-time reset and the upgrade rule (pure), the new defaults, and the
/// decisions of the explanation window through `Session` with a fake panel and a fake store, in both IMK event orders.
@MainActor
final class FakeEnglishNotice: EnglishNoticePanel {
    private(set) var shown: [NSRect?] = []
    private(set) var hides = 0
    private(set) var isShown = false
    private var done: (@MainActor (Bool) -> Void)?
    func show(caret: NSRect?, done: @escaping @MainActor (Bool) -> Void) {
        shown.append(caret)
        isShown = true
        self.done = done
    }
    func hide() { hides += 1; isShown = false }
    /// The person clicks 打開設定 (true) or 知道了 / the close button (false).
    func click(openSettings: Bool) { done?(openSettings) }
}

@MainActor
final class EnglishDefaultsNoticeTests: XCTestCase {
    private var resources: URL!

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    // MARK: the one-time reset (section 2)

    private func writes(_ keys: Set<String> = [], files: Bool = false, marker: Bool = false) -> [String: Bool] {
        EnglishDefaultsReset.writes(domainKeys: keys, learningHasFiles: files, markerPresent: marker)
    }
    private let off: [String: Bool] = ["englishOverwrite": false, "shiftToggle": false, "englishDefaults20261104": true]

    func testEmptyDomainAndNoLearningFilesIsAFreshInstall() {
        XCTAssertEqual(writes(), off, "both off, marker written, no pending flag")
    }

    func testOldSettingsKeysWithBothSwitchesOnResetThemAndOweTheNotice() {
        XCTAssertEqual(writes(["englishOverwrite", "shiftToggle", "layout"]), off.merging(["englishDefaultsNoticePending": true]) { $1 })
    }

    func testAnyOtherKeyMeansAnEarlierVersion() {
        for key in ["updateLastCheck", "updateStatus", "SUEnableAutomaticChecks", "SULastCheckTime", "layout", "glassTint"] {
            XCTAssertEqual(writes([key])["englishDefaultsNoticePending"], true, key)
            XCTAssertEqual(writes([key])["englishOverwrite"], false, key)
            XCTAssertEqual(writes([key])["shiftToggle"], false, key)
        }
    }

    func testOnlyTheContractsOwnKeysAndNoLearningFilesIsNotAnUpdate() {
        XCTAssertEqual(writes(["englishDefaults20261104", "englishDefaultsNoticePending"]), off, "no pending key written")
    }

    func testLearningFilesAloneMeanAnUpdate() {
        XCTAssertEqual(writes(files: true), off.merging(["englishDefaultsNoticePending": true]) { $1 })
    }

    func testTheMarkerMakesTheResetANoOp() {
        XCTAssertEqual(writes(["englishDefaults20261104", "englishOverwrite", "shiftToggle", "layout"], files: true, marker: true), [:],
                       "a switch the person turned on afterwards is left alone")
        XCTAssertEqual(writes(["englishDefaults20261104"], marker: true), [:])
    }

    // MARK: defaults (section 1)

    private func makeShell(english: EnglishStore = MemoryEnglishStore(), shift: ShiftToggleStore = MemoryShiftToggleStore(),
                           pending: Bool = false, notice: FakeEnglishNotice? = nil, store: MemoryEnglishNoticeStore? = nil) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: english, acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(),
                          rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(),
                          appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore(),
                          shiftToggleStore: shift, englishNoticeStore: store ?? MemoryEnglishNoticeStore(pending), englishNotice: notice)
        XCTAssertNotNil(shell.engine)
        return shell
    }

    func testNoStoredValuesMeanBothSwitchesOff() {
        let shell = makeShell()
        XCTAssertFalse(shell.englishOverwriteOn)
        XCTAssertFalse(shell.shiftToggleOn)
        XCTAssertFalse(SettingsModel(shell: shell).englishOverwrite)
        XCTAssertFalse(SettingsModel(shell: shell).shiftToggle)
    }

    func testStoredOnStaysOn() {
        let shell = makeShell(english: MemoryEnglishStore(true, overwrite: true), shift: MemoryShiftToggleStore(true))
        XCTAssertTrue(shell.englishOverwriteOn)
        XCTAssertTrue(shell.shiftToggleOn)
    }

    // MARK: the window's decisions (section 3, 4.1)

    private var a: Controller!
    private var b: Controller!

    private func rig(pending: Bool = true) -> (shell: Shell, notice: FakeEnglishNotice, store: MemoryEnglishNoticeStore) {
        let notice = FakeEnglishNotice()
        let store = MemoryEnglishNoticeStore(pending)
        let shell = makeShell(notice: notice, store: store)
        a = Controller(shell)
        b = Controller(shell, bundle: "com.apple.Safari")
        return (shell, notice, store)
    }

    func testFirstKeyAfterActivateShowsTheWindowAndIsHandledAsUsual() {
        let (_, notice, _) = rig()
        // The same key in a shell with no window, as the reference for "handled as usual".
        let plain = Controller(makeShell())
        plain.session.activate()
        let expected = plain.type("h")
        a.session.activate()
        XCTAssertTrue(notice.shown.isEmpty, "activation alone shows nothing")
        XCTAssertEqual(a.type("h"), expected, "the key is not eaten or delayed")
        XCTAssertEqual(a.client.marked, plain.client.marked)
        XCTAssertEqual(notice.shown.count, 1)
        XCTAssertEqual(notice.shown.last ?? nil, a.client.line, "shown after the key: the composition's line, since the key started one")
        a.type("i")
        XCTAssertEqual(notice.shown.count, 1, "already shown: later keys do not show it again")
    }

    func testNothingOwedMeansNothingShown() {
        let (_, notice, _) = rig(pending: false)
        a.session.activate()
        a.type("hi")
        XCTAssertTrue(notice.shown.isEmpty)
    }

    func testAFlagsChangedEventDoesNotShowIt() {
        let (_, notice, _) = rig()
        a.session.activate()
        a.session.handle(Keys.event(56, flags: .shift, type: .flagsChanged))
        XCTAssertTrue(notice.shown.isEmpty)
        a.type("h")
        XCTAssertEqual(notice.shown.count, 1, "the next keyDown does")
    }

    func testEitherButtonOrTheCloseButtonClearsTheFlagAndHides() {
        for openSettings in [false, true] {
            let (shell, notice, store) = rig()
            var opened = 0
            shell.onOpenCandidateSettings = { opened += 1 }
            a.session.activate()
            a.type("h")
            XCTAssertTrue(notice.isShown)
            notice.click(openSettings: openSettings)
            XCTAssertFalse(store.pending, "openSettings \(openSettings)")
            XCTAssertFalse(notice.isShown)
            XCTAssertEqual(opened, openSettings ? 1 : 0, "打開設定 opens the 選字 settings, 知道了 and the close button do not")
            a.session.deactivate()
            a.session.activate()
            a.type("h")
            b.session.activate()
            b.type("h")
            XCTAssertEqual(notice.shown.count, 1, "never again after a click, openSettings \(openSettings)")
        }
    }

    /// The new app's activate arrives before the old app's deactivate.
    func testActivateOfTheNextAppBeforeTheLateDeactivateOfTheFirst() {
        let (_, notice, store) = rig()
        a.session.activate()
        a.type("h")
        XCTAssertEqual(notice.shown.count, 1)
        b.session.activate()
        a.session.deactivate()  // late, and A is no longer the owner of anything: still hides it
        XCTAssertFalse(notice.isShown, "the window is gone")
        XCTAssertTrue(store.pending, "a deactivate does not clear the flag")
        b.type("h")
        XCTAssertEqual(notice.shown.count, 2, "B's first key shows it again")
    }

    /// The old app's deactivate arrives first.
    func testDeactivateOfTheFirstAppBeforeActivateOfTheNext() {
        let (_, notice, store) = rig()
        a.session.activate()
        a.type("h")
        XCTAssertEqual(notice.shown.count, 1)
        a.session.deactivate()
        XCTAssertFalse(notice.isShown)
        XCTAssertTrue(store.pending)
        b.session.activate()
        b.type("h")
        XCTAssertEqual(notice.shown.count, 2)
        XCTAssertTrue(notice.isShown)
    }

    func testANewProcessWithTheFlagStillSetShowsItAgain() {
        let (_, notice, store) = rig()
        a.session.activate()
        a.type("h")
        a.session.deactivate()  // switched away without pressing anything
        let again = makeShell(notice: notice, store: store)  // the next process start reads the same store
        let c = Controller(again)
        c.session.activate()
        c.type("h")
        XCTAssertEqual(notice.shown.count, 2)
    }

    // MARK: placement (section 3)

    func testNoticeSitsInTheHalfOfTheCaretsScreenThatTheCaretIsNotIn() {
        let main = PanelPlacement.Screen(frame: NSRect(x: 0, y: 0, width: 1000, height: 800), visibleFrame: NSRect(x: 0, y: 0, width: 1000, height: 780))
        let second = PanelPlacement.Screen(frame: NSRect(x: 1000, y: 0, width: 1000, height: 800), visibleFrame: NSRect(x: 1000, y: 0, width: 1000, height: 780))
        let size = NSSize(width: 400, height: 200)
        func origin(_ caret: NSRect?) -> NSPoint { PanelPlacement.noticeOrigin(caret: caret, size: size, screens: [main, second], main: 0) }
        // Caret in the upper half (AppKit y up): the window is centred in the lower half of the visible frame (centre y 780/4).
        XCTAssertEqual(origin(NSRect(x: 300, y: 700, width: 2, height: 18)), NSPoint(x: 300, y: 195 - 100))
        // Caret in the lower half: centred in the upper half (centre y 780*3/4).
        XCTAssertEqual(origin(NSRect(x: 300, y: 100, width: 2, height: 18)), NSPoint(x: 300, y: 585 - 100))
        // The caret's screen is the second one.
        XCTAssertEqual(origin(NSRect(x: 1500, y: 700, width: 2, height: 18)), NSPoint(x: 1300, y: 95))
        // No caret: the main screen, treated as the lower half, so the window goes to the upper half.
        XCTAssertEqual(origin(nil), NSPoint(x: 300, y: 485))
    }

    func testNoticeTextIsTheContractsWording() {
        XCTAssertEqual(EnglishNoticeText.title, "英打相關設定改成預設關")
        XCTAssertTrue(EnglishNoticeText.overwrite.hasPrefix("重打同一個位置算英文：開著時"))
        XCTAssertTrue(EnglishNoticeText.shift.hasPrefix("單按 Shift 切換中英：開著時"))
        XCTAssertEqual(EnglishNoticeText.how, "要再打開，到「設定 → 選字」。")
        XCTAssertEqual([EnglishNoticeText.openSettings, EnglishNoticeText.acknowledge], ["打開設定", "知道了"])
    }
}
