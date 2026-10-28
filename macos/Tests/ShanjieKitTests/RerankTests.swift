import AppKit
import XCTest
@testable import ShanjieKit

/// docs/contracts/s7c2b-shell-rerank.md section 3 items 1-2: the shell's rerank queue with the real core, the real n-gram model
/// and the real small model, in a chat app (the selftest's sentence, dev302 row 65 since the 通語 SJ-LM-7-9.7M files). The runner is held by the test, except in
/// the last test, which uses the app's queue runner.
@MainActor
final class RerankTests: XCTestCase {
    static let chatApp = "com.apple.MobileSMS"
    static let leftArrow: UInt16 = 123
    private var resources: URL!
    private var counters: [FreeCounter] = []

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    override func tearDown() async throws {
        counters.forEach { $0.release() }
        counters = []
    }

    private struct Rig {
        var shell: Shell
        var c: Controller
        var runner: HeldRerankRunner
        var frees: FreeCounter
    }

    /// The shipped default is on (s7h section 2.3); every test but the default's own still switches rerank on explicitly, through
    /// a stored true.
    private func makeShell(store: RerankStore = MemoryRerankStore(true), runner: RerankRunner, demote: DemoteStore = MemoryDemoteStore(),
                           frees: FreeCounter, resources: URL? = nil) -> Shell {
        let shell = Shell(resources: resources ?? self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: demote, predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: store, rerankRunner: runner,
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        shell.rerankFree = frees.free  // before any job is taken (build() takes none)
        return shell
    }

    private func rig(store: RerankStore = MemoryRerankStore(true), resources: URL? = nil) -> Rig {
        let runner = HeldRerankRunner(), frees = FreeCounter()
        counters.append(frees)
        let shell = makeShell(store: store, runner: runner, frees: frees, resources: resources)
        let c = Controller(shell, bundle: Self.chatApp)
        c.session.activate()
        return Rig(shell: shell, c: c, runner: runner, frees: frees)
    }

    private func marks(_ c: Controller) -> Int { c.client.calls.filter { if case .mark = $0 { true } else { false } }.count }

    /// The selftest types the same keys; the two are derived independently.
    func testSentenceIsTheSelftests() {
        XCTAssertEqual(RerankRow.standardKeys, Selftest.rerankRowStandard)
        XCTAssertEqual(RerankRow.ngram, Selftest.ngramText)
        XCTAssertEqual(RerankRow.reranked, Selftest.rerankedText)
        XCTAssertNotEqual(RerankRow.ngram, RerankRow.reranked)
    }

    /// s7h section 2.3: the device sentence under the shell's conditions, the bundled pack loaded like the shipped app (the other
    /// tests here run without it). On, the finished job shows 程式; off, the n-gram's sentence stays.
    func testDeviceSentenceIsRerankedOnlyWhenOn() throws {
        let packed = try XCTUnwrap(TestData.resources())
        try TestData.addBundledPack(to: packed)
        let on = rig(resources: packed)
        on.c.type(DeviceRow.standardKeys)
        XCTAssertEqual(on.c.client.marked, DeviceRow.ngram, "the n-gram result shows first")
        on.runner.drain()
        XCTAssertEqual(on.c.client.marked, DeviceRow.reranked)
        let off = rig(store: MemoryRerankStore(false), resources: packed)
        off.c.type(DeviceRow.standardKeys)
        off.runner.drain()
        XCTAssertEqual(off.c.client.marked, DeviceRow.ngram)
        XCTAssertNotEqual(DeviceRow.ngram, DeviceRow.reranked)
    }

    // MARK: the queue

    /// A key leaves a job for the runner; one runs at a time (the latest waits); the stale first one changes nothing, and the
    /// last one, once complete, shows the reranked sentence with exactly one more setMarkedText.
    func testJobRunsOneAtATimeAndAppliesTheRerankedSentence() {
        let r = rig()
        r.c.type(RerankRow.standardKeys)
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram, "the n-gram result shows first; the key never waits for the model")
        XCTAssertEqual(r.runner.received, 1, "one job at a time: the later ones wait")
        XCTAssertEqual(r.runner.held.count, 1)
        let before = marks(r.c)
        r.runner.complete()  // the first job, taken many keys ago: stale
        XCTAssertEqual(marks(r.c), before, "a stale result is not applied")
        XCTAssertEqual(r.runner.received, 2, "the waiting job starts once the first is done")
        r.runner.complete()
        XCTAssertEqual(marks(r.c), before + 1)
        XCTAssertEqual(r.c.client.calls.last, .mark(RerankRow.reranked, cursor: RerankRow.reranked.count, underlined: true))
        XCTAssertEqual(r.c.client.marked, RerankRow.reranked)
        XCTAssertTrue(r.runner.held.isEmpty)
        r.c.press(Keys.enter)
        XCTAssertEqual(r.c.client.text, RerankRow.reranked, "Enter sends what is shown")
    }

    /// A finished job that changes nothing (code 5): no extra client call. `NoChangeRow` keeps its n-gram first in the chat profile
    /// (row 65 did from classes-v5 to model-v6; the 通語 SJ-LM-7-9.7M n-gram gets it wrong again, so it is `RerankRow` now).
    func testNoChangeMakesNoCall() {
        let r = rig()
        r.c.type(NoChangeRow.standardKeys)
        XCTAssertEqual(r.c.client.marked, NoChangeRow.text)
        let before = marks(r.c)
        r.runner.drain()
        XCTAssertGreaterThanOrEqual(r.runner.received, 2)
        XCTAssertEqual(marks(r.c), before)
        XCTAssertEqual(r.c.client.marked, NoChangeRow.text)
    }

    /// A key that re-decodes after the job was taken makes its result stale; a key that does not (←) keeps it valid.
    func testRedecodingKeyMakesTheResultStaleACursorMoveDoesNot() {
        let r = rig()
        r.c.type(RerankRow.standardKeys)
        r.runner.complete()  // stale; the job of the last key starts
        r.c.type("su3")      // ㄋㄧˇ: a completed syllable re-decodes
        let shown = r.c.client.marked
        let before = marks(r.c)
        r.runner.complete()  // the job of the last key, now stale
        XCTAssertEqual(marks(r.c), before, "a result made stale by a re-decoding key is not applied")
        XCTAssertEqual(r.c.client.marked, shown)
        XCTAssertEqual(r.runner.held.count, 1, "the job of ㄋㄧˇ runs next")
        r.c.press(Keys.esc)
        r.runner.drain()

        let s = rig()
        s.c.type(RerankRow.standardKeys)
        s.runner.complete()
        s.c.press(Self.leftArrow)  // does not re-decode
        XCTAssertEqual(s.c.client.calls.last, .mark(RerankRow.ngram, cursor: RerankRow.ngram.count - 1, underlined: true))
        s.runner.complete()
        XCTAssertEqual(s.c.client.calls.last, .mark(RerankRow.reranked, cursor: RerankRow.reranked.count - 1, underlined: true), "applied after a cursor move")
    }

    /// Completion after another session took the composition, or after the engine was rebuilt: not applied, and the next job
    /// still goes to the runner (F4: the running flag is cleared on every way out).
    func testSessionChangeOrRebuildBeforeCompletionIsNotAppliedAndTheQueueGoesOn() {
        let r = rig()
        r.c.type(RerankRow.standardKeys)
        r.runner.complete()  // the last key's job is running now
        let other = Controller(r.shell, bundle: Self.chatApp)
        other.session.activate()  // commits the n-gram text to the first client
        XCTAssertEqual(r.c.client.text, RerankRow.ngram)
        let callsA = r.c.client.calls.count
        r.runner.complete()
        XCTAssertEqual(r.c.client.calls.count, callsA, "the old owner's client is not touched")
        XCTAssertTrue(other.client.calls.isEmpty, "nor the new owner's")
        let received = r.runner.received
        other.type(RerankRow.standardKeys)
        XCTAssertEqual(r.runner.received, received + 1, "after a completion that applied nothing, the next job still runs")
        other.press(Keys.esc)
        r.runner.drain()

        let s = rig()
        s.c.type(RerankRow.standardKeys)
        s.runner.complete()
        weak var old = s.shell.engine
        s.c.session.selectLayout(.eten)  // commits and rebuilds
        XCTAssertNil(old, "a held job keeps no engine alive (F3): never two engines")
        XCTAssertEqual(s.c.client.text, RerankRow.ngram)
        // The same words in the new engine, without the committed sentence as left context (it changes the small model's choice).
        s.c.client.selectedOverride = NSRange(location: NSNotFound, length: 0)
        s.c.type(RerankRow.etenKeys)
        XCTAssertEqual(s.c.client.marked, RerankRow.ngram)
        let calls = s.c.client.calls.count
        s.runner.complete()  // the old engine's job
        XCTAssertEqual(s.c.client.calls.count, calls, "a job of the old engine is not applied")
        XCTAssertEqual(s.runner.held.count, 1, "the new engine's latest job runs next")
        s.runner.complete()
        XCTAssertEqual(s.c.client.marked, RerankRow.reranked, "the rebuilt engine reranks too")
    }

    /// Rerank on together with profile-choice. The first held job is stale anyway (later keys re-decoded), so the test completes
    /// it first: the job the runner then holds is the newest one, taken for the final chat composition. Control: with no
    /// switch it applies (再). Then the same state, but the app's chat/formal choice (menu) or the global mode is switched to
    /// formal first: `set_profile` re-decodes under a new generation, so that held job is stale and applies nothing (s7c2b
    /// section 2.1). Taken == freed throughout.
    func testProfileChangeWhileTheNewestJobIsHeldDiscardsTheStaleResult() {
        func held() -> Rig {
            let r = rig()
            r.c.type(RerankRow.standardKeys)
            r.runner.complete()  // the first job, stale: the newest one starts and stays held
            XCTAssertEqual(r.runner.held.count, 1, "premise: the newest job is with the runner")
            XCTAssertEqual(r.c.client.marked, RerankRow.ngram, "premise: nothing applied yet")
            return r
        }
        func finish(_ r: Rig) {
            r.runner.drain()
            r.c.press(Keys.esc)
            XCTAssertEqual(r.frees.freed, r.shell.rerankTaken, "taken == freed")
            XCTAssertEqual(r.frees.doubleFrees, 0)
        }

        let control = held()
        let controlBefore = marks(control.c)
        control.runner.complete()
        XCTAssertEqual(marks(control.c), controlBefore + 1, "control: with no switch the held job applies")
        XCTAssertEqual(control.c.client.marked, RerankRow.reranked)
        finish(control)

        let viaMenu = held()
        viaMenu.c.session.toggleAppChat()  // Messages is a chat app: formal for this app now
        let menuShown = viaMenu.c.client.marked, menuBefore = marks(viaMenu.c)
        viaMenu.runner.complete()
        XCTAssertEqual(marks(viaMenu.c), menuBefore, "a result from before the app's profile switch is not applied")
        XCTAssertEqual(viaMenu.c.client.marked, menuShown)
        finish(viaMenu)

        let viaMode = held()
        viaMode.shell.setProfileMode(.formal)  // the global mode: the owner's composition is re-decoded
        let modeShown = viaMode.c.client.marked, modeBefore = marks(viaMode.c)
        viaMode.runner.complete()
        XCTAssertEqual(marks(viaMode.c), modeBefore, "a result from before the mode change is not applied")
        XCTAssertEqual(viaMode.c.client.marked, modeShown)
        finish(viaMode)
    }

    /// F4 again, on the code path: run's non-zero code (a caught panic) applies nothing, and the queue goes on.
    func testRunFailureAppliesNothingAndTheQueueGoesOn() {
        let r = rig()
        r.c.type(RerankRow.standardKeys)
        r.runner.complete()
        let calls = r.c.client.calls.count
        r.runner.complete(code: 4)
        XCTAssertEqual(r.c.client.calls.count, calls)
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram)
        r.c.type("su3")
        XCTAssertEqual(r.runner.held.count, 1, "the next job was handed over")
    }

    /// F5: a non-zero take is handled like apply's 4: composition and panel cleared.
    func testTakeFailureClearsTheComposition() {
        let r = rig()
        r.shell.rerankTake = { _, _ in (nil, 4) }
        r.c.type("su3")
        XCTAssertEqual(r.c.client.marked, "")
        XCTAssertFalse(r.c.panel.visible)
        XCTAssertEqual(r.runner.received, 0)
        r.shell.rerankTake = { $0.rerankTake(free: $1) }
        r.c.type("su3cl3")
        XCTAssertEqual(r.c.client.marked, "你好", "typing goes on")
    }

    /// Apply's non-zero codes other than 5 (4: the engine has reset itself) are handled like a failed key: composition and
    /// panel cleared. Each job is still freed exactly once.
    func testApplyFailureClearsTheComposition() {
        let r = rig()
        r.shell.rerankApply = { _, _ in .failed(4) }
        r.c.type(RerankRow.standardKeys)
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram, "premise: the composition is shown")
        r.runner.complete()  // reaches the injected apply whatever the job's generation
        XCTAssertEqual(r.c.client.marked, "")
        XCTAssertFalse(r.c.panel.visible)
        r.runner.drain()
        XCTAssertEqual(r.frees.freed, r.shell.rerankTaken, "each job freed once")
        XCTAssertEqual(r.frees.doubleFrees, 0)
        r.shell.rerankApply = { $0.apply(on: $1) }
        r.c.type("su3cl3")
        XCTAssertEqual(r.c.client.marked, "你好", "typing goes on")
    }

    // MARK: exactly one free per job

    /// Every job taken is freed exactly once, on each path: replaced while waiting, the engine rebuilt, the switch turned off,
    /// stale, run's 4, applied normally; and the shell going away frees what it still held.
    func testEveryJobIsFreedExactlyOnce() {
        let frees = FreeCounter()
        counters.append(frees)
        var taken = 0
        do {
            let runner = HeldRerankRunner()
            let shell = makeShell(runner: runner, frees: frees)
            let c = Controller(shell, bundle: Self.chatApp)
            c.session.activate()

            // replaced: one runs, each later one replaces the waiting one, which is freed at once
            c.type(RerankRow.standardKeys)
            XCTAssertGreaterThanOrEqual(shell.rerankTaken, 3)
            XCTAssertEqual(frees.freed, shell.rerankTaken - 2, "all but the running and the waiting job are freed")
            // stale, then applied normally
            runner.complete()
            XCTAssertEqual(frees.freed, shell.rerankTaken - 1, "the stale job is freed once its completion returns")
            runner.complete()
            XCTAssertEqual(c.client.marked, RerankRow.reranked)
            XCTAssertEqual(frees.freed, shell.rerankTaken, "the applied job is freed")
            c.press(Keys.esc)

            // switch off while one waits: the waiting job is freed at once, the running one when it completes
            c.type(RerankRow.standardKeys)
            let waitingOff = frees.freed
            c.session.perform(.toggleRerank)
            XCTAssertEqual(frees.freed, waitingOff + 1, "the waiting job is dropped with the switch")
            runner.complete()
            XCTAssertEqual(frees.freed, shell.rerankTaken)
            c.session.perform(.toggleRerank)
            c.press(Keys.esc)
            runner.drain()

            // engine rebuild while one waits
            c.type(RerankRow.standardKeys)
            let waitingRebuild = frees.freed
            c.session.selectLayout(.eten)
            XCTAssertEqual(frees.freed, waitingRebuild + 1, "the waiting job is dropped with the engine")
            runner.complete()
            XCTAssertEqual(frees.freed, shell.rerankTaken)

            // run's 4
            c.type(RerankRow.etenKeys)
            runner.complete(code: 4)
            runner.drain()
            XCTAssertEqual(frees.freed, shell.rerankTaken)

            // left with one running and one waiting when the shell goes
            c.press(Keys.esc)
            c.type(RerankRow.etenKeys)
            XCTAssertEqual(runner.held.count, 1)
            taken = shell.rerankTaken
        }
        XCTAssertGreaterThan(taken, 10)
        XCTAssertEqual(frees.freed, taken, "taken == freed")
        XCTAssertEqual(frees.doubleFrees, 0, "no job freed twice")
    }

    // MARK: the switch

    /// s7h section 2.3: never chosen (a nil store) means on, although the core's own default is off: the menu item is checked,
    /// the engine was told on (jobs are taken, the reranked sentence shows), and the window's model agrees. This is the test
    /// that fails if the shipped default goes back to off.
    func testNeverChosenIsOnAndTheEngineIsToldOn() {
        let store = MemoryRerankStore()
        XCTAssertNil(store.rerank)
        let r = rig(store: store)
        XCTAssertTrue(r.shell.rerankOn, "default on")
        XCTAssertTrue(SettingsModel(shell: r.shell).rerank, "the window shows on too")
        let item = r.c.session.menu.first { $0.action == .toggleRerank }
        XCTAssertEqual(item?.checked, true, "default on")
        XCTAssertEqual(item?.enabled, true)
        r.c.type(RerankRow.standardKeys)
        XCTAssertGreaterThan(r.runner.received, 0, "jobs: the engine was told setRerank(true)")
        r.runner.drain()
        XCTAssertGreaterThan(r.shell.rerankTaken, 0)
        XCTAssertEqual(r.c.client.marked, RerankRow.reranked)
        XCTAssertNil(store.rerank, "building the engine chose nothing")
        // The call itself, not only its effect: the core's own default is off, so the shell must send true on every build.
        XCTAssertEqual(r.shell.rerankSent, [true], "the first build sent setRerank(true)")
        r.c.session.perform(.layout(.eten))  // a rebuild
        r.c.session.perform(.layout(.standard))
        XCTAssertEqual(r.shell.rerankSent, [true, true, true], "every rebuild sends setRerank(true) again")
    }

    /// s7h section 2.3: a stored false stays off under the new default: the build sends false and no job is taken.
    func testStoredOffStaysOff() {
        let store = MemoryRerankStore(false)
        let r = rig(store: store)
        XCTAssertFalse(r.shell.rerankOn)
        XCTAssertEqual(r.c.session.menu.first { $0.action == .toggleRerank }?.checked, false)
        r.c.type(RerankRow.standardKeys)
        r.runner.drain()
        XCTAssertEqual(r.runner.received, 0, "no job: the engine was told setRerank(false)")
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram)
        XCTAssertEqual(r.shell.rerankSent, [false])
        XCTAssertEqual(store.rerank, false)
        let on = rig(store: MemoryRerankStore(true))
        XCTAssertEqual(on.shell.rerankSent, [true], "a stored true is what the build sends")
    }

    /// Section 2.2: 語意校正 follows 即時預測, 可省略韻母 and 中英混打（實驗）; a stored on makes jobs, off shows the n-gram result at once and
    /// makes no job, also after the two rebuilds (layout, word pack); a new shell with the stored off makes none either.
    func testSwitchStoresAndSurvivesRebuildsAndRestart() {
        let store = MemoryRerankStore(true)
        let r = rig(store: store)
        let item = { r.c.session.menu.first { $0.action == .toggleRerank } }
        let titles = r.c.session.menu.map(\.title)
        XCTAssertEqual(titles.firstIndex(of: "語意校正"), titles.firstIndex(of: "中英混打（實驗）").map { $0 + 1 })
        XCTAssertEqual(item()?.checked, true, "a stored true is on")
        XCTAssertEqual(item()?.enabled, true)
        r.c.type(RerankRow.standardKeys)
        r.runner.drain()
        XCTAssertEqual(r.c.client.marked, RerankRow.reranked)
        r.c.session.perform(.toggleRerank)  // mid-composition: back to the n-gram result at once
        XCTAssertEqual(store.rerank, false)
        XCTAssertEqual(item()?.checked, false)
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram)
        r.c.press(Keys.esc)
        let received = r.runner.received
        r.c.type(RerankRow.standardKeys)
        XCTAssertEqual(r.runner.received, received, "off: no job")
        r.c.press(Keys.esc)
        r.c.session.perform(.layout(.eten))
        r.c.type(RerankRow.etenKeys)
        XCTAssertEqual(r.runner.received, received, "the engine rebuilt by a layout switch got the setting")
        r.c.press(Keys.esc)
        r.c.session.perform(.layout(.standard))
        r.c.session.perform(.toggleAcgPack)
        r.c.session.perform(.toggleAcgPack)
        r.c.type(RerankRow.standardKeys)
        XCTAssertEqual(r.runner.received, received, "the engine rebuilt by the word pack switch got the setting")
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram)
        r.c.press(Keys.esc)

        let again = rig(store: store)
        again.c.type(RerankRow.standardKeys)
        XCTAssertEqual(again.runner.received, 0, "a new shell reads the stored off")
        XCTAssertEqual(again.c.session.menu.first { $0.action == .toggleRerank }?.checked, false)
        again.c.session.perform(.toggleRerank)  // on mid-composition: the snapshot leaves a job
        XCTAssertEqual(store.rerank, true)
        XCTAssertEqual(again.runner.received, 1)
        again.runner.drain()
        XCTAssertEqual(again.c.client.marked, RerankRow.reranked)
    }

    /// F11: with 避免把敏感字詞排在前面 off the core makes no job, so the item is greyed (checkmark kept) and no job is made.
    func testGreyedWhileDemotionIsOff() {
        let r = rig()
        r.c.session.perform(.toggleDemote)
        let item = r.c.session.menu.first { $0.action == .toggleRerank }
        XCTAssertEqual(item?.enabled, false)
        XCTAssertEqual(item?.checked, true)
        r.c.type(RerankRow.standardKeys)
        XCTAssertEqual(r.runner.received, 0)
        r.c.press(Keys.esc)
        r.c.session.perform(.toggleDemote)
        XCTAssertEqual(r.c.session.menu.first { $0.action == .toggleRerank }?.enabled, true)
    }

    /// The menu action reaches the owner of the composition even when another session gets it: the core re-decodes the
    /// owner's composition, so the owner's client shows the snapshot; the other client is not touched.
    func testMenuToggleFromAnotherSessionReachesTheOwner() {
        let r = rig()
        r.c.type(RerankRow.standardKeys)
        r.runner.drain()
        XCTAssertEqual(r.c.client.marked, RerankRow.reranked, "premise")
        let other = Controller(r.shell, bundle: Self.chatApp)  // not activated: r.c keeps the composition
        other.session.perform(.toggleRerank)
        XCTAssertFalse(r.shell.rerankOn)
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram, "the owner shows the n-gram result at once")
        other.session.perform(.toggleRerank)
        r.runner.drain()
        XCTAssertEqual(r.c.client.marked, RerankRow.reranked, "on again: the owner's snapshot left a job")
        XCTAssertTrue(other.client.calls.isEmpty, "the session that got the menu action is not the owner")
    }

    /// Without the small model (file missing or corrupt) the switch can do nothing: the menu item and the settings toggle are
    /// greyed, the checkmark still shows the stored choice, and no job is made. With the file both are enabled.
    func testGreyedWithoutTheSmallModel() throws {
        let names: Set<String> = ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv", "bigram.sjlm", "classes.sjc"]
        let missing = try XCTUnwrap(TestData.resources(only: names))
        let corrupt = try XCTUnwrap(TestData.resources(only: names))
        try Data("not a model".utf8).write(to: corrupt.appendingPathComponent(Shell.tinyLMFile))
        for (res, label) in [(missing, "missing"), (corrupt, "corrupt")] {
            let r = rig(resources: res)
            let item = r.c.session.menu.first { $0.action == .toggleRerank }
            XCTAssertEqual(item?.enabled, false, label)
            XCTAssertEqual(item?.checked, true, label)
            XCTAssertFalse(SettingsModel(shell: r.shell).rerankEnabled, label)
            r.c.type(RerankRow.standardKeys)
            XCTAssertEqual(r.runner.received, 0, label)
            XCTAssertEqual(r.c.client.marked, RerankRow.ngram, label)
        }
        let r = rig()
        XCTAssertEqual(r.c.session.menu.first { $0.action == .toggleRerank }?.enabled, true)
        XCTAssertTrue(SettingsModel(shell: r.shell).rerankEnabled)
    }

    // MARK: the strength (docs/contracts/rerank-strength.md section 3)

    /// Two Resources directories from the real data/lm/tinylm.sjtl, whichever version ships: one with it as version 2 (the standard
    /// sets only), one as version 3. From a version 2 file the version 3 one has four extra sets equal to the standard ones (section 3:
    /// v2 -> v3 inserts at 116 the chat standard triple twice, then the formal one twice, and relabels; v3 -> v2 drops bytes 116..<212
    /// and relabels). From a version 3 file it is the file itself. From a version 4 file (SJTL0004, tinylm-v5) it is the file without its
    /// 32 kn.sjkn hash bytes (212..<244), relabelled, so its conservative and aggressive sets are tinylm-v5's real values, not copies of
    /// the standard ones (in tinylm-v5: chat conservative 1/2/3, aggressive equal to standard; formal standard 1/1/3, conservative
    /// 1/1/4; sj-sr-6-14m, shipped since PR #200, changes only the formal standard and aggressive sets to 0.7/2/1).
    /// Under model-v6 + kn-v1 + tinylm-v5 (chat standard 0.3/2/0.25) the RerankTests passed on 2026-10-15 (`make test-swift`).
    private func versionedResources() throws -> (v2: URL, v3: URL) {
        let real = try Data(contentsOf: TestData.repo.appendingPathComponent("data/lm/tinylm.sjtl"))
        let label = { (data: Data, version: UInt8) -> Data in
            var d = data
            d[7] = UInt8(ascii: "0") + version
            d.replaceSubrange(8..<12, with: [version, 0, 0, 0])
            return d
        }
        let v2: Data, v3: Data
        // model-v6 section 7 P3-6: a version 4 file records the kn.sjkn it was tuned with and pairs only with it; the version 2 and 3
        // files derived from it record none, so their Resources below carry no kn.sjkn (the model then loads without KN and they pair).
        switch String(decoding: real.prefix(8), as: UTF8.self) {
        case "SJTL0004":
            var d3 = real
            d3.removeSubrange(212..<244)
            var d2 = d3
            d2.removeSubrange(116..<212)
            (v2, v3) = (label(d2, 2), label(d3, 3))
        case "SJTL0002":
            let chat = real[36..<52] + real[100..<108], formal = real[52..<68] + real[108..<116]
            var d = real
            d.insert(contentsOf: chat + chat + formal + formal, at: 116)
            (v2, v3) = (real, label(d, 3))
        case "SJTL0003":
            var d = real
            d.removeSubrange(116..<212)
            (v2, v3) = (label(d, 2), real)
        default:
            throw CocoaError(.fileReadCorruptFile)  // fails the test: neither version 2 nor 3 ships
        }
        XCTAssertEqual(v3.count, v2.count + 96)
        let names: Set<String> = ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv", "bigram.sjlm", "classes.sjc"]
        let dirs = (try XCTUnwrap(TestData.resources(only: names)), try XCTUnwrap(TestData.resources(only: names)))
        try v2.write(to: dirs.0.appendingPathComponent(Shell.tinyLMFile))
        try v3.write(to: dirs.1.appendingPathComponent(Shell.tinyLMFile))
        addTeardownBlock { for d in [dirs.0, dirs.1] { try? FileManager.default.removeItem(at: d) } }  // two 14.6 MB files each
        // Each derived file must actually pair (the model loaded): a refused one would grey the strength control for the wrong reason.
        for d in [dirs.0, dirs.1] {
            XCTAssertFalse(FileManager.default.fileExists(atPath: d.appendingPathComponent("kn.sjkn").path), "no kn.sjkn beside a version 2 or 3 file")
            XCTAssertTrue(SettingsModel(shell: rig(resources: d).shell).rerankEnabled, "the derived small model loads")
        }
        return dirs
    }

    /// A version 2 file greys the control (all three choices still shown by the window); a version 3 file enables it, a choice is
    /// stored as its raw value and sent to the engine at once with its fixed code, and a new shell (a restart) reads it back and
    /// sends it on its first build.
    func testLevelControlFollowsTheFileAndTheChoiceIsSentStoredAndRestored() throws {
        let (v2, v3) = try versionedResources()
        let old = rig(resources: v2)
        XCTAssertEqual(old.shell.rerankLevels, 2)
        XCTAssertTrue(SettingsModel(shell: old.shell).rerankEnabled, "the model itself loaded")
        XCTAssertFalse(SettingsModel(shell: old.shell).rerankLevelEnabled, "version 2: greyed")

        let store = MemoryRerankStore(true)
        let r = rig(store: store, resources: v3)
        let model = SettingsModel(shell: r.shell)
        XCTAssertEqual(r.shell.rerankLevels, 7)
        XCTAssertTrue(model.rerankLevelEnabled, "version 3: enabled")
        XCTAssertEqual(model.rerankLevel, .standard, "never chosen is standard")
        XCTAssertEqual(r.shell.rerankLevelSent, [1], "the build sent standard")
        model.setRerankLevel(.aggressive)
        XCTAssertEqual(r.shell.rerankLevelSent, [1, 2])
        XCTAssertEqual(store.rerankLevel, "aggressive")
        XCTAssertEqual(model.rerankLevel, .aggressive, "the window re-read it")
        model.setRerankLevel(.conservative)
        XCTAssertEqual(r.shell.rerankLevelSent, [1, 2, 0])
        XCTAssertEqual(store.rerankLevel, "conservative")

        let again = rig(store: store, resources: v3)
        XCTAssertEqual(again.shell.rerankLevel, .conservative, "restored")
        XCTAssertEqual(SettingsModel(shell: again.shell).rerankLevel, .conservative)
        XCTAssertEqual(again.shell.rerankLevelSent, [0], "the first build sends the stored level")
    }

    /// Greyed with the 語意校正 toggle, and with everything that greys the toggle (demotion off, no small model).
    func testLevelControlGreyedWithTheSwitch() throws {
        let (_, v3) = try versionedResources()
        let r = rig(resources: v3)
        let model = SettingsModel(shell: r.shell)
        XCTAssertTrue(model.rerankLevelEnabled)
        model.setRerank(false)
        XCTAssertFalse(model.rerankLevelEnabled, "語意校正 off")
        model.setRerank(true)
        model.setDemote(false)
        XCTAssertFalse(model.rerankLevelEnabled, "demotion off")
        model.setDemote(true)
        XCTAssertTrue(model.rerankLevelEnabled)
        let names: Set<String> = ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv", "bigram.sjlm", "classes.sjc"]
        let none = rig(resources: try XCTUnwrap(TestData.resources(only: names)))
        XCTAssertEqual(none.shell.rerankLevels, 0)
        XCTAssertFalse(SettingsModel(shell: none.shell).rerankLevelEnabled, "no small model")
    }

    /// P3-7: an unknown stored value (or none) is standard, and that is what the build sends; the store is not rewritten by reading.
    func testUnknownStoredLevelIsStandard() {
        for stored in [nil, "bold", "Aggressive", ""] as [String?] {
            let store = MemoryRerankStore(true, level: stored)
            let r = rig(store: store)
            XCTAssertEqual(r.shell.rerankLevel, .standard, String(describing: stored))
            XCTAssertEqual(r.shell.rerankLevelSent, [1], String(describing: stored))
            XCTAssertEqual(store.rerankLevel, stored, "reading chose nothing")
        }
        XCTAssertEqual(RerankLevel.allCases.map(\.code), [0, 1, 2])
    }

    /// P4-13: every rebuilt engine (layout switch, word pack switch) gets the stored level again.
    func testRebuildsResendTheLevel() throws {
        let (_, v3) = try versionedResources()
        let r = rig(store: MemoryRerankStore(true, level: "aggressive"), resources: v3)
        XCTAssertEqual(r.shell.rerankLevelSent, [2])
        r.c.session.perform(.layout(.eten))
        r.c.session.perform(.layout(.standard))
        r.c.session.perform(.toggleAcgPack)
        XCTAssertEqual(r.shell.rerankLevelSent, [2, 2, 2, 2], "two layout rebuilds and a word pack rebuild")
        XCTAssertEqual(r.shell.rerankLevels, 7, "the rebuilt engine has the model and its levels")
    }

    /// A change mid-composition goes through the owner: the core re-decodes, the owner shows the n-gram snapshot at once, and the
    /// snapshot's job reranks it again with the new level. Independent of the shipped values: the file's chat aggressive set gets
    /// alpha 0 (the small model's LL adds nothing, so the n-gram first always stays), so the job's result tells which level it ran
    /// with: standard reranks the row, aggressive leaves the n-gram text.
    func testLevelChangeMidCompositionShowsTheSnapshotAndRerunsTheJob() throws {
        let (_, v3) = try versionedResources()
        let file = v3.appendingPathComponent(Shell.tinyLMFile)
        var bytes = try Data(contentsOf: file)
        bytes.replaceSubrange(140..<148, with: Data(count: 8))  // chat aggressive alpha (rerank-strength section 2.1), 0.0
        try bytes.write(to: file)
        let r = rig(resources: v3)
        r.c.type(RerankRow.standardKeys)
        r.runner.drain()
        XCTAssertEqual(r.c.client.marked, RerankRow.reranked, "premise")
        let received = r.runner.received
        SettingsModel(shell: r.shell).setRerankLevel(.aggressive)
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram, "the snapshot: re-decoded")
        XCTAssertEqual(r.runner.received, received + 1, "the snapshot left a job")
        r.runner.drain()
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram, "the job ran with the aggressive set (alpha 0): nothing moves")
        SettingsModel(shell: r.shell).setRerankLevel(.standard)
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram, "the snapshot again")
        r.runner.drain()
        XCTAssertEqual(r.c.client.marked, RerankRow.reranked, "back to the standard set: reranked")
        XCTAssertEqual(r.frees.freed, r.shell.rerankTaken)
    }

    // MARK: the app's runner (F2)

    /// `QueueRerankRunner`, as the app passes it: the key returns at once with the n-gram result, the job runs on the queue and
    /// the main thread applies it; every job taken is freed once.
    func testQueueRunnerAppliesOnTheMainThread() {
        let frees = FreeCounter()
        counters.append(frees)
        let shell = makeShell(runner: QueueRerankRunner(), frees: frees)
        let c = Controller(shell, bundle: Self.chatApp)
        c.session.activate()
        c.type(RerankRow.standardKeys)
        XCTAssertTrue(spin { c.client.marked == RerankRow.reranked }, "the result did not arrive")
        c.press(Keys.esc)
        XCTAssertTrue(spin { frees.freed == shell.rerankTaken }, "taken == freed")
        XCTAssertGreaterThan(shell.rerankTaken, 0)
        XCTAssertEqual(frees.doubleFrees, 0)
    }

    /// Runs the main run loop (where `DispatchQueue.main.async` blocks run) until `done` or 10 s.
    private func spin(_ done: () -> Bool) -> Bool {
        let deadline = Date().addingTimeInterval(10)
        while !done() {
            if Date() > deadline { return false }
            RunLoop.main.run(until: Date().addingTimeInterval(0.01))
        }
        return true
    }
}
