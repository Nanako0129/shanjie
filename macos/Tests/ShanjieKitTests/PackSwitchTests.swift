import AppKit
import CShanjie
import XCTest
@testable import ShanjieKit

/// Records an order of events from any thread (the build's thread and the test's).
final class OrderLog: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [String] = []
    func add(_ s: String) { lock.lock(); items.append(s); lock.unlock() }
    var all: [String] { lock.lock(); defer { lock.unlock() }; return items }
    func count(_ s: String) -> Int { all.filter { $0 == s }.count }
}

/// Counts frees at an injectable free function, like `FreeCounter` but for any handle kind: the real free is done by `release` at the end of the test, so no address
/// is reused while it runs and a handle freed twice is counted as such.
final class HandleFrees: @unchecked Sendable {
    private let lock = NSLock()
    private var handles: [OpaquePointer] = []
    private var seen: Set<UInt> = []
    private var twice = 0

    var free: @Sendable (OpaquePointer) -> Void { { [self] in record($0) } }

    private func record(_ h: OpaquePointer) {
        let bits = UInt(bitPattern: Int(bitPattern: UnsafeRawPointer(h)))
        lock.lock()
        defer { lock.unlock() }
        if seen.insert(bits).inserted { handles.append(h) } else { twice += 1 }
    }

    var freed: Int { lock.lock(); defer { lock.unlock() }; return handles.count }
    var doubleFrees: Int { lock.lock(); defer { lock.unlock() }; return twice }

    func release(using real: (OpaquePointer) -> Void) {
        lock.lock()
        let hs = handles
        handles = []
        lock.unlock()
        hs.forEach(real)
    }
}

final class NoticeCount: @unchecked Sendable {
    var n = 0
}

/// docs/spec/macos-shell.md §2.1, docs/spec/lexicon-and-packs.md §4.1–§4.3: a pack switch builds the new engine in the background and swaps it in when the composition is
/// empty. Real Resources, the real core, a builder the tests complete by hand (`HeldPackBuilder`). The word ㄕㄣˊ ㄧㄡˊ ㄕˇ is 榊遊矢 with the ACG pack and something else without it.
@MainActor
final class PackSwitchTests: XCTestCase {
    static let word = Layouts.keys("ㄕㄣˊ ㄧㄡˊ ㄕˇ", eten: false)
    static let withPack = "榊遊矢"

    struct Rig {
        let shell: Shell
        let builder: HeldPackBuilder
        let controller: Controller
        let acg: MemoryAcgPackStore
        let resources: URL
    }

    private func resourcesWithPacks(without: Set<String> = []) throws -> URL {
        let names = Set(TestData.files.map(\.name)).subtracting(without)
        let dir = try XCTUnwrap(TestData.resources(only: names))
        try TestData.addBundledPack(to: dir)
        return dir
    }

    private func rig(resources: URL? = nil, acgOn: Bool = true, learning: URL? = nil, english: EnglishStore = MemoryEnglishStore(overwrite: true)) throws -> Rig {
        let dir = try resources ?? resourcesWithPacks()
        let builder = HeldPackBuilder()
        let acg = MemoryAcgPackStore(acgOn)
        let shell = Shell(resources: dir, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: learning,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(),
                          englishStore: english, acgPackStore: acg, quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), packBuilder: builder, spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        XCTAssertEqual(shell.engineCounter.live, 1)
        let c = Controller(shell)
        c.session.activate()
        return Rig(shell: shell, builder: builder, controller: c, acg: acg, resources: dir)
    }

    /// What the check word becomes now; the composition is discarded afterwards.
    private func typed(_ r: Rig) -> String {
        r.controller.type(Self.word)
        let marked = r.controller.client.marked
        r.controller.press(Keys.esc)
        return marked
    }

    /// Lets a hop to the main queue (`scheduleInstall`) happen.
    private func settle() { _ = spin(timeout: 0.05) }

    // a. typing goes on with the old engine while the new one is built.
    func testTheCallReturnsAndTheOldEngineKeepsTyping() throws {
        let r = try rig()
        weak var before = r.shell.engine
        r.shell.setAcgPack(false)
        XCTAssertTrue(r.shell.engine === before, "the call did not wait for a build")
        XCTAssertEqual(r.builder.received, 1)
        XCTAssertEqual(r.acg.acgPack, false, "the choice is stored at once")
        XCTAssertFalse(r.shell.acgPackOn)
        r.controller.type(Self.word)
        XCTAssertEqual(r.controller.client.marked, Self.withPack, "the old engine still has the pack")
        XCTAssertTrue(r.shell.loadedPackTitles.contains(MenuEntry.Text.acgPack))
        XCTAssertEqual(r.controller.client.text, "", "nothing was committed for the switch")
    }

    // b. after the swap the pack is gone.
    func testSwitchOffRemovesThePack() throws {
        let r = try rig()
        weak var before = r.shell.engine
        let notices = NoticeCount()
        let token = NotificationCenter.default.addObserver(forName: Shell.didChangeSettings, object: r.shell, queue: nil) { _ in notices.n += 1 }
        defer { NotificationCenter.default.removeObserver(token) }
        r.shell.setAcgPack(false)
        let afterCall = notices.n
        r.builder.complete()
        XCTAssertTrue(r.shell.engine !== before)
        XCTAssertNotEqual(typed(r), Self.withPack)
        XCTAssertEqual(r.shell.loadedPackMask & CoreEngine.packACG, 0)
        XCTAssertFalse(r.shell.loadedPackTitles.contains(MenuEntry.Text.acgPack))
        XCTAssertGreaterThan(notices.n, afterCall, "the swap told the settings window")
        XCTAssertEqual(r.shell.engineCounter.live, 1)
    }

    // c. and back.
    func testSwitchOnBringsThePackBack() throws {
        let r = try rig(acgOn: false)
        XCTAssertNotEqual(typed(r), Self.withPack)
        weak var before = r.shell.engine
        r.shell.setAcgPack(true)
        r.builder.complete()
        XCTAssertTrue(r.shell.engine !== before)
        XCTAssertEqual(typed(r), Self.withPack)
        XCTAssertEqual(r.shell.loadedPackMask & CoreEngine.packACG, CoreEngine.packACG)
    }

    // d. a composition is never sent for the swap: it waits for the end of the composition.
    func testNoSwapWhileComposing() throws {
        let r = try rig()
        weak var before = r.shell.engine
        r.shell.setAcgPack(false)
        r.controller.type(Self.word)
        XCTAssertEqual(r.controller.client.marked, Self.withPack)
        r.builder.complete()
        XCTAssertTrue(r.shell.engine === before, "the composition keeps its engine")
        XCTAssertEqual(r.controller.client.marked, Self.withPack)
        XCTAssertEqual(r.controller.client.text, "", "nothing was sent")
        XCTAssertEqual(r.shell.engineCounter.live, 2, "the finished engine waits")
        r.controller.press(Keys.enter)
        XCTAssertEqual(r.controller.client.text, Self.withPack)
        XCTAssertTrue(spin { r.shell.engine !== before }, "after the commit the engine is swapped")
        XCTAssertEqual(r.shell.engineCounter.live, 1)
        XCTAssertNotEqual(typed(r), Self.withPack, "the next input uses the new engine")
    }

    /// The other things that are a composition: an unfinished symbol, a number buffer, an English run.
    func testNoSwapWhileTheOtherKindsOfCompositionAreOpen() throws {
        for keys in ["g", "12", "hello"] {
            let r = try rig()
            weak var before = r.shell.engine
            r.shell.setAcgPack(false)
            r.controller.type(keys)
            XCTAssertTrue(r.shell.composing, "\(keys) is a composition")
            let marked = r.controller.client.marked
            r.builder.complete()
            XCTAssertTrue(r.shell.engine === before, "\(keys): not swapped")
            XCTAssertEqual(r.controller.client.marked, marked, "\(keys): the composition is unchanged")
            r.controller.press(Keys.esc)
            XCTAssertTrue(spin { r.shell.engine !== before }, "\(keys): swapped after the composition ended")
        }
    }

    // e. quick switches merge: at most two builds, at most two engines, the shared parts freed after the build that used them.
    func testRapidSwitchesMergeIntoTwoBuilds() throws {
        let r = try rig()
        let log = OrderLog()
        let real = PackBuildOps().newShared
        r.shell.packBuildOps.newShared = { a, b, c, d in
            let out = real(a, b, c, d)
            log.add("newShared")
            return out
        }
        r.shell.sharedFree = { log.add("sharedFree"); shanjie_shared_free($0) }
        weak var before = r.shell.engine
        let base = r.shell.engineBuilds
        var maxLive = 0
        // The builder's queue frees what it is handed before the next build starts (it is serial); the held builder frees on `flushDisposed`.
        func note() { r.builder.flushDisposed(); maxLive = max(maxLive, r.shell.engineCounter.live) }
        r.shell.setAcgPack(false)
        note()
        r.shell.setItPack(false)
        r.shell.setMemePack(false)
        note()
        XCTAssertEqual(r.builder.received, 1, "no second build while one runs")
        XCTAssertEqual(log.all, [], "the build is still using its shared parts: not freed by the switches that made it stale")
        r.builder.runWork()
        note()
        XCTAssertEqual(log.all, ["newShared", "sharedFree"], "freed after the new_shared that used it returned")
        r.builder.deliver()
        XCTAssertEqual(r.builder.received, 2, "the stale result is dropped and the latest mask is built")
        XCTAssertTrue(r.shell.engine === before)
        XCTAssertEqual(r.builder.disposed, 1, "the stale engine goes to the builder to free, not freed on the main thread")
        note()
        XCTAssertEqual(r.shell.engineCounter.live, 1, "the stale engine is freed")
        r.builder.complete()
        note()
        XCTAssertTrue(r.shell.engine !== before)
        XCTAssertEqual(r.shell.engineBuilds - base, 2)
        XCTAssertEqual(r.shell.packMask, CoreEngine.packBeauty)
        XCTAssertEqual(r.shell.loadedPackMask, CoreEngine.packBeauty, "the last mask")
        XCTAssertLessThanOrEqual(maxLive, 2)
        XCTAssertEqual(log.count("sharedFree"), 2)
    }

    func testSwitchingBackBuildsNothingMore() throws {
        let r = try rig()
        weak var before = r.shell.engine
        let base = r.shell.engineBuilds
        r.shell.setAcgPack(false)
        r.shell.setAcgPack(true)
        r.builder.complete()
        XCTAssertTrue(r.shell.engine === before, "the current engine already has what is wanted")
        XCTAssertEqual(r.builder.pending, 0)
        XCTAssertEqual(r.builder.received, 1)
        XCTAssertEqual(r.shell.engineBuilds - base, 1)
        r.builder.flushDisposed()
        XCTAssertEqual(r.shell.engineCounter.live, 1)
        settle()
        XCTAssertEqual(r.builder.received, 1)
    }

    // f. a build that cannot make an engine leaves the current one.
    func testFailureKeepsTheCurrentEngine() throws {
        let r = try rig()
        weak var before = r.shell.engine
        let loaded = r.shell.loadedPackMask
        let demote = r.resources.appendingPathComponent("demote.tsv")
        let original = try FileManager.default.destinationOfSymbolicLink(atPath: demote.path)
        try FileManager.default.removeItem(at: demote)
        let base = r.shell.engineBuilds
        r.shell.setAcgPack(false)
        r.builder.complete()
        XCTAssertTrue(r.shell.engine === before, "no engine came out: the current one stays")
        XCTAssertEqual(r.shell.loadedPackMask, loaded)
        XCTAssertEqual(r.acg.acgPack, false, "the choice is stored")
        XCTAssertEqual(r.shell.engineBuilds - base, 2, "the combined build and the build without packs")
        XCTAssertEqual(r.shell.engineCounter.live, 1)
        XCTAssertEqual(typed(r), Self.withPack, "the current engine still types, with its pack")
        try FileManager.default.createSymbolicLink(atPath: demote.path, withDestinationPath: original)
        r.shell.setItPack(false)
        r.builder.complete()
        XCTAssertTrue(r.shell.engine !== before, "with the file back the next switch swaps")
        XCTAssertNotEqual(typed(r), Self.withPack)
    }

    /// The failure that comes late: the engine is built, then the reading table needs `std-exempt.tsv`.
    func testLateFailureOfTheSharedPathKeepsTheCurrentEngine() throws {
        let r = try rig()
        weak var before = r.shell.engine
        let loaded = r.shell.loadedPackMask
        let file = r.resources.appendingPathComponent("std-exempt.tsv")
        let original = try FileManager.default.destinationOfSymbolicLink(atPath: file.path)
        try FileManager.default.removeItem(at: file)
        let base = r.shell.engineBuilds
        r.shell.setAcgPack(false)
        r.builder.complete()
        XCTAssertEqual(r.shell.engineBuilds - base, 2, "the shared path with the mask, then with mask 0 (docs/spec/lexicon-and-packs.md §4.3)")
        XCTAssertEqual(r.shell.packBuildShared, [false, true], "the first build was the initial one from the files; this one used the shared parts")
        XCTAssertTrue(r.shell.engine === before)
        XCTAssertEqual(r.shell.loadedPackMask, loaded)
        XCTAssertEqual(typed(r), Self.withPack)
        try FileManager.default.createSymbolicLink(atPath: file.path, withDestinationPath: original)
        r.shell.setItPack(false)
        r.builder.complete()
        XCTAssertTrue(r.shell.engine !== before)
    }

    // g. the fall-back of docs/spec/lexicon-and-packs.md §4.3 runs in the background, with the same builds and the same loaded mask.
    func testBrokenPackIsDroppedInTheBackgroundAndSwapped() throws {
        // The same fall-back as ShellTests.testBrokenMemeFileKeepsTheOtherPacks (all four on, only meme-add.tsv broken: six builds, mask 21), but reached by a switch.
        let r = try rig(acgOn: false)
        weak var before = r.shell.engine
        let meme = r.resources.appendingPathComponent("packs/meme-add.tsv")
        try "not a row\n".write(to: meme, atomically: true, encoding: .utf8)
        let base = r.shell.engineBuilds
        r.shell.setAcgPack(true)
        r.builder.complete()
        XCTAssertTrue(r.shell.engine !== before, "swapped although the loaded mask differs from the asked one")
        XCTAssertEqual(r.shell.packMask, 29)
        XCTAssertEqual(r.shell.loadedPackMask, 21, "only the broken pack is dropped")
        XCTAssertEqual(r.shell.engineBuilds - base, 6, "combined, no packs, drop ACG (still broken), drop IT (still broken), drop beauty (still broken), drop meme (works)")
        XCTAssertEqual(r.controller.session.menu.packLines, ["動漫與遊戲詞", "資訊與 AI 詞", "美妝與保養詞"])
        XCTAssertTrue(r.shell.memePackOn, "the stored choice is untouched")
        XCTAssertEqual(typed(r), Self.withPack)
        settle()
        XCTAssertEqual(r.builder.received, 1, "no build is running and none starts")
        XCTAssertEqual(r.shell.engineBuilds - base, 6)
    }

    func testMissingPackFileSwapsWithOneBuild() throws {
        let dir = try resourcesWithPacks()
        try FileManager.default.removeItem(at: dir.appendingPathComponent("packs/beauty-add.tsv"))
        let r = try rig(resources: dir)
        weak var before = r.shell.engine
        let base = r.shell.engineBuilds
        r.shell.setAcgPack(false)
        r.builder.complete()
        XCTAssertTrue(r.shell.engine !== before)
        XCTAssertEqual(r.shell.engineBuilds - base, 1, "a missing file contributes nothing: no retry")
        XCTAssertEqual(r.shell.packMask, 28)
        XCTAssertEqual(r.shell.loadedPackMask, 12, "no beauty bit")
        settle()
        XCTAssertEqual(r.builder.received, 1)
    }

    // h. the settings of now, not of when the build started.
    func testSettingsAreSentAgainAtTheSwap() throws {
        let r = try rig()
        r.controller.type("s")
        XCTAssertTrue(r.controller.panel.visible, "with prediction on, the first key shows the row")
        r.controller.press(Keys.esc)
        r.shell.setAcgPack(false)
        r.shell.applyPrediction(false)
        r.builder.complete()
        r.controller.type("s")
        XCTAssertFalse(r.controller.panel.visible, "the new engine has prediction off")
        XCTAssertFalse(r.shell.predictionOn)
    }

    // i. a layout change is built here and now; the build that was running is for the old layout.
    func testLayoutChangeMakesTheRunningBuildStale() throws {
        let r = try rig()
        let log = OrderLog()
        let real = PackBuildOps().newShared
        r.shell.packBuildOps.newShared = { a, b, c, d in
            let out = real(a, b, c, d)
            log.add("newShared")
            return out
        }
        r.shell.sharedFree = { log.add("sharedFree"); shanjie_shared_free($0) }
        weak var before = r.shell.engine
        let base = r.shell.engineBuilds
        r.shell.setAcgPack(false)
        r.shell.selectLayout(.eten)
        XCTAssertTrue(r.shell.engine !== before, "the layout is built at once")
        XCTAssertEqual(r.shell.packMask, 28)
        XCTAssertEqual(r.shell.loadedPackMask & CoreEngine.packACG, 0, "with the mask of now")
        XCTAssertEqual(log.all, [], "the running build's shared parts are not freed by the layout change")
        let current = r.shell.engine
        r.builder.runWork()
        XCTAssertEqual(log.all, ["newShared", "sharedFree"])
        r.builder.deliver()
        XCTAssertTrue(r.shell.engine === current, "the stale result was freed, not swapped in")
        XCTAssertEqual(r.shell.engineBuilds - base, 2, "the layout's build and the stale one")
        XCTAssertEqual(r.builder.received, 1)
        r.builder.flushDisposed()
        XCTAssertEqual(r.shell.engineCounter.live, 1)
    }

    // j. every handle is freed exactly once.
    func testHandlesAreFreedExactlyOnce() throws {
        let engines = HandleFrees(), shared = HandleFrees()
        defer {
            engines.release(using: { shanjie_engine_free($0) })
            shared.release(using: { shanjie_shared_free($0) })
        }
        var r: Rig? = try rig()
        let builder = try XCTUnwrap(r?.builder)
        r?.shell.engineFree = engines.free
        r?.shell.sharedFree = shared.free
        r?.shell.setAcgPack(false)
        r?.shell.setItPack(false)
        builder.complete()   // stale: its engine goes to the builder
        builder.flushDisposed()
        builder.complete()   // the latest mask: swapped, the old engine freed by the default free of its build
        XCTAssertEqual(engines.freed, 1, "the stale build's engine")
        XCTAssertEqual(shared.freed, 2, "one shared handle per build")
        XCTAssertEqual(engines.doubleFrees + shared.doubleFrees, 0)
        XCTAssertEqual(r?.shell.engineCounter.live, 1)
        r = nil   // the shell frees the engine the swap installed, through the recording free too
        XCTAssertEqual(engines.freed, 2, "the installed engine is freed with the shell")
        XCTAssertEqual(engines.doubleFrees, 0)
    }

    /// Item of docs/spec/macos-shell.md §2.1: an engine nobody wants is not freed on the main thread inside the switching call.
    func testSwitchWhileWaitingHandsTheEngineToTheBuilder() throws {
        let engines = HandleFrees()
        defer { engines.release(using: { shanjie_engine_free($0) }) }
        let r = try rig()
        r.shell.engineFree = engines.free
        r.shell.setAcgPack(false)
        r.controller.type(Self.word)
        r.builder.complete()
        XCTAssertTrue(r.shell.hasReady)
        r.shell.setItPack(false)
        XCTAssertFalse(r.shell.hasReady)
        XCTAssertEqual(r.builder.disposed, 1, "handed to the builder")
        XCTAssertEqual(engines.freed, 0, "not freed inside the switching call")
        r.builder.flushDisposed()
        XCTAssertEqual(engines.freed, 1, "freed once, by the builder")
        XCTAssertEqual(engines.doubleFrees, 0)
        XCTAssertEqual(r.shell.engineCounter.live, 1)
    }

    /// The app's builder frees it on its own queue, not on the main thread.
    func testQueueBuilderFreesTheDiscardedEngineOffTheMainThread() throws {
        let dir = try resourcesWithPacks()
        let where_ = OrderLog()
        let shell = Shell(resources: dir, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(),
                          englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(true), quotePackStore: MemoryQuotePackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(),
                          rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(),
                          packBuilder: QueuePackBuilder(), spacingStore: MemorySpacingStore())
        let c = Controller(shell)
        c.session.activate()
        shell.engineFree = { where_.add(Thread.isMainThread ? "main" : "queue"); shanjie_engine_free($0) }
        shell.setAcgPack(false)
        c.type(Self.word)   // a composition: the finished build has to wait
        XCTAssertTrue(spin { shell.hasReady }, "the build finished and waits")
        shell.setItPack(false)   // frees the waiting engine through the builder
        XCTAssertTrue(spin { where_.all.count == 1 }, "the discarded engine was freed")
        XCTAssertEqual(where_.all, ["queue"])
    }

    func testAShellFreedDuringABuildStillFreesBothHandlesOnce() throws {
        let engines = HandleFrees(), shared = HandleFrees()
        defer {
            engines.release(using: { shanjie_engine_free($0) })
            shared.release(using: { shanjie_shared_free($0) })
        }
        var r: Rig? = try rig()
        weak var weakShell = r?.shell
        let builder = try XCTUnwrap(r?.builder)
        r?.shell.engineFree = engines.free
        r?.shell.sharedFree = shared.free
        r?.shell.setAcgPack(false)
        r = nil
        XCTAssertNil(weakShell, "the build does not keep the shell alive")
        builder.runWork()
        XCTAssertEqual(shared.freed, 1, "the build releases its shared parts when its last call returned")
        builder.deliver()
        XCTAssertEqual(engines.freed, 1, "the engine nobody takes is freed with its result")
        XCTAssertEqual(engines.doubleFrees + shared.doubleFrees, 0)
    }

    // k. the shared path only when the current engine has everything to share.
    func testSharedPathWhenAllThreeAreLoadedAndFilesOtherwise() throws {
        let r = try rig()
        r.shell.setAcgPack(false)
        r.builder.complete()
        XCTAssertEqual(r.shell.packBuildShared, [false, true], "the first build is the initial one (files), the switch shares")
        XCTAssertTrue(r.shell.rerankAvailable)
        let dir = try resourcesWithPacks(without: [Shell.tinyLMFile])
        let bare = try rig(resources: dir)
        XCTAssertFalse(bare.shell.rerankAvailable, "no small model")
        bare.shell.setAcgPack(false)
        bare.builder.complete()
        XCTAssertEqual(bare.shell.packBuildShared, [false, false], "the switch builds from the files again")
        XCTAssertFalse(bare.shell.rerankAvailable)
        XCTAssertNotEqual(typed(bare), Self.withPack)
    }

    // l. a finished build that waits is freed at the next switch.
    func testNextSwitchFreesTheWaitingResultAtOnce() throws {
        let r = try rig()
        weak var before = r.shell.engine
        var maxLive = 0
        func note() { maxLive = max(maxLive, r.shell.engineCounter.live) }
        r.shell.setAcgPack(false)
        r.controller.type(Self.word)
        r.builder.complete()
        XCTAssertEqual(r.shell.engineCounter.live, 2, "built, waiting for the composition to end")
        note()
        r.shell.setItPack(false)
        XCTAssertEqual(r.builder.disposed, 1, "handed to the builder in the call, not kept until the next build is back")
        r.builder.flushDisposed()
        XCTAssertEqual(r.shell.engineCounter.live, 1)
        XCTAssertEqual(r.builder.pending, 1, "the newest mask is being built")
        note()
        r.controller.press(Keys.esc)
        settle()
        XCTAssertTrue(r.shell.engine === before)
        r.builder.complete()
        note()
        XCTAssertTrue(r.shell.engine !== before)
        XCTAssertEqual(r.shell.loadedPackMask & (CoreEngine.packACG | CoreEngine.packIT), 0, "the latest mask")
        XCTAssertLessThanOrEqual(maxLive, 2)
        XCTAssertEqual(r.shell.engineCounter.live, 1)
    }

    func testSwitchingBackFreesTheWaitingResultAndBuildsNothing() throws {
        let r = try rig()
        weak var before = r.shell.engine
        let base = r.shell.engineBuilds
        r.shell.setAcgPack(false)
        r.controller.type(Self.word)
        r.builder.complete()
        XCTAssertEqual(r.shell.engineCounter.live, 2)
        r.shell.setAcgPack(true)
        XCTAssertEqual(r.builder.disposed, 1, "handed to the builder in the call")
        r.builder.flushDisposed()
        XCTAssertEqual(r.shell.engineCounter.live, 1)
        XCTAssertEqual(r.builder.pending, 0, "the current engine has what is wanted")
        r.controller.press(Keys.esc)
        settle()
        XCTAssertTrue(r.shell.engine === before)
        XCTAssertEqual(r.shell.engineBuilds - base, 1)
        XCTAssertEqual(r.builder.received, 1)
    }

    // The app's builder: the work on its queue, the swap on the main thread, typing right after.
    func testQueueBuilderSwapsAfterTheWorkRanOnAnotherThread() throws {
        let dir = try resourcesWithPacks()
        let learning = TestLearning.directory()
        let shell = Shell(resources: dir, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: learning,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(),
                          englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(true), quotePackStore: MemoryQuotePackStore(),
                          glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(),
                          rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(),
                          packBuilder: QueuePackBuilder(), spacingStore: MemorySpacingStore())
        let c = Controller(shell)
        c.session.activate()
        weak var before = shell.engine
        shell.setAcgPack(false)
        XCTAssertTrue(shell.engine === before)
        XCTAssertTrue(spin { shell.engine !== before }, "the swap came")
        XCTAssertFalse(shell.learningOpenFailed)
        c.type(Self.word)
        XCTAssertNotEqual(c.client.marked, Self.withPack)
        XCTAssertFalse(c.client.marked.isEmpty, "the engine handed over from the queue's thread types")
        XCTAssertEqual(shell.engineCounter.live, 1)
        XCTAssertEqual(shell.packBuildShared, [false, true])
    }
}
