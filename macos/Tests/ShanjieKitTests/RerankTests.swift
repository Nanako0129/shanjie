import AppKit
import XCTest
@testable import ShanjieKit

/// docs/contracts/s7c2b-shell-rerank.md section 3 items 1-2: the shell's rerank queue with the real core, the real n-gram model
/// and the real small model, in a chat app (the selftest's sentence, dev302 row 65). The runner is held by the test, except in
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

    /// The shipped default is off (s7c2b section 2.2, revised 2026-10-12): every test but the default's own switches rerank on
    /// explicitly, through a stored true.
    private func makeShell(store: RerankStore = MemoryRerankStore(true), runner: RerankRunner, demote: DemoteStore = MemoryDemoteStore(),
                           frees: FreeCounter, resources: URL? = nil) -> Shell {
        let shell = Shell(resources: resources ?? self.resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: demote, predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: store, rerankRunner: runner,
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore())
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
        XCTAssertEqual(r.c.client.calls.last, .mark(RerankRow.reranked, cursor: 7, underlined: true))
        XCTAssertEqual(r.c.client.marked, RerankRow.reranked)
        XCTAssertTrue(r.runner.held.isEmpty)
        r.c.press(Keys.enter)
        XCTAssertEqual(r.c.client.text, RerankRow.reranked, "Enter sends what is shown")
    }

    /// A finished job that changes nothing (code 5): no extra client call. Row 226 keeps its n-gram first in the chat profile.
    func testNoChangeMakesNoCall() {
        let r = rig()
        r.c.type(Row226.standardKeys)
        XCTAssertEqual(r.c.client.marked, Row226.chat)
        let before = marks(r.c)
        r.runner.drain()
        XCTAssertGreaterThanOrEqual(r.runner.received, 2)
        XCTAssertEqual(marks(r.c), before)
        XCTAssertEqual(r.c.client.marked, Row226.chat)
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
        XCTAssertEqual(s.c.client.calls.last, .mark(RerankRow.ngram, cursor: 6, underlined: true))
        s.runner.complete()
        XCTAssertEqual(s.c.client.calls.last, .mark(RerankRow.reranked, cursor: 6, underlined: true), "applied after a cursor move")
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

    /// Section 2.2 (revised 2026-10-12): never chosen (a nil store) means off, whatever the core's own default: the menu item is
    /// unchecked, the engine was told off (no job is ever taken, the n-gram result stays), and the window's model agrees. This
    /// is the test that fails if the shipped default goes back to on.
    func testNeverChosenIsOffAndTheEngineIsToldOff() {
        let store = MemoryRerankStore()
        XCTAssertNil(store.rerank)
        let r = rig(store: store)
        XCTAssertFalse(r.shell.rerankOn, "default off")
        XCTAssertFalse(SettingsModel(shell: r.shell).rerank, "the window shows off too")
        let item = r.c.session.menu.first { $0.action == .toggleRerank }
        XCTAssertEqual(item?.checked, false, "default off")
        XCTAssertEqual(item?.enabled, true, "off, but the switch can be turned on")
        r.c.type(RerankRow.standardKeys)
        r.runner.drain()
        XCTAssertEqual(r.runner.received, 0, "no job: the engine was told setRerank(false)")
        XCTAssertEqual(r.shell.rerankTaken, 0)
        XCTAssertEqual(r.c.client.marked, RerankRow.ngram)
        XCTAssertNil(store.rerank, "building the engine chose nothing")
    }

    /// Section 2.2: 語意校正 follows 即時預測 and 可省略韻母; a stored on makes jobs, off shows the n-gram result at once and
    /// makes no job, also after the two rebuilds (layout, word pack); a new shell with the stored off makes none either.
    func testSwitchStoresAndSurvivesRebuildsAndRestart() {
        let store = MemoryRerankStore(true)
        let r = rig(store: store)
        let item = { r.c.session.menu.first { $0.action == .toggleRerank } }
        let titles = r.c.session.menu.map(\.title)
        XCTAssertEqual(titles.firstIndex(of: "語意校正"), titles.firstIndex(of: "可省略韻母").map { $0 + 1 })
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
        let names: Set<String> = ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "demote.tsv", "bigram.sjlm", "classes.sjc"]
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
