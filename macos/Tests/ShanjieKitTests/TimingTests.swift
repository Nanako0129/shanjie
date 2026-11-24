#if SHANJIE_TIMING
import AppKit
import Synchronization
import XCTest
@testable import ShanjieKit

/// docs/spec/semantic-rerank.md §8.6: the timing build's events. Compiled and run only with `-DSHANJIE_TIMING` (`make test-swift-timing`).
/// The shell tests read `Timing.shared` themselves. Only `testTheTimerFlushesOffTheMainThread` runs a flush timer, for well under
/// a second, so a test run writes a few integer lines to the unified log under the timing subsystem.
@MainActor
final class TimingTests: XCTestCase {
    private typealias Event = TimingBuffer.Event
    private var counters: [FreeCounter] = []

    override func tearDown() async throws {
        counters.forEach { $0.release() }
        counters = []
    }

    private func rig(_ runner: RerankRunner) throws -> (shell: Shell, c: Controller) {
        let frees = FreeCounter()
        counters.append(frees)
        let shell = Shell(resources: try XCTUnwrap(TestData.resources()), panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(true), rerankRunner: runner,
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        shell.rerankFree = frees.free
        let c = Controller(shell, bundle: "com.apple.MobileSMS")
        c.session.activate()
        return (shell, c)
    }

    /// Every job taken (3) has exactly one end (6), no end is without its job, and every key in (1) has its return (2).
    private func checkPairs(_ events: [TimingBuffer.Event], file: StaticString = #filePath, line: UInt = #line) {
        let taken = events.filter { $0.kind == 3 }.map(\.a), ends = events.filter { $0.kind == 6 }.map(\.a)
        XCTAssertFalse(taken.isEmpty, file: file, line: line)
        XCTAssertEqual(Set(taken).count, taken.count, "job numbers are not reused", file: file, line: line)
        XCTAssertEqual(ends.sorted(), taken.sorted(), "one end per job taken", file: file, line: line)
        XCTAssertEqual(events.filter { $0.kind == 1 }.map(\.a), events.filter { $0.kind == 2 }.map(\.a), "each key in has its return", file: file, line: line)
    }

    private func reasons(_ events: [TimingBuffer.Event]) -> [UInt64] { events.filter { $0.kind == 6 }.map(\.b) }

    // MARK: the buffer

    /// Only integers are stored, and a flushed line is digits and tabs: kind, time, then the kind's own number of fields; the
    /// flush ends with a kind 8 line (skipped, lines written so far) and writes nothing when nothing happened.
    func testFlushedLinesAreDigitsAndTabs() throws {
        let b = TimingBuffer(capacity: 16)
        for kind in UInt64(1)...7 { b.record(kind, .max, 12, 3) }
        b.record(5, 9, UInt64(UInt32(bitPattern: -1)))  // a negative code is written as its bit pattern, never with a sign
        var lines: [String] = []
        b.flush { lines.append($0) }
        XCTAssertEqual(lines.count, 9)
        let shape = try NSRegularExpression(pattern: "^[0-9]+(\t[0-9]+)+$")
        for l in lines {
            XCTAssertEqual(shape.numberOfMatches(in: l, range: NSRange(l.startIndex..., in: l)), 1, "not digits and tabs")
            XCTAssertTrue(l.unicodeScalars.allSatisfy { $0 == "\t" || ("0"..."9").contains($0) })
        }
        XCTAssertEqual(lines.map { $0.split(separator: "\t").count - 2 }, [1, 2, 2, 1, 2, 2, 3, 2, 2], "fields after the time, by kind")
        XCTAssertEqual(lines[0].split(separator: "\t").first, "1")
        XCTAssertEqual(lines[0].split(separator: "\t").last.map(String.init), String(UInt64.max))
        XCTAssertEqual(lines[7].split(separator: "\t").last, "4294967295")
        let last = lines[8].split(separator: "\t")
        XCTAssertEqual([last[0], last[2], last[3]], ["8", "0", "8"], "kind 8: nothing skipped, 8 lines written")
        b.flush { _ in XCTFail("nothing happened since the last flush") }
    }

    /// A writer never waits for the lock: while another thread holds it (until this thread lets it go, so a writer that waited
    /// would never return), `record` returns, writes nothing and counts one skipped event. A full buffer counts the same way.
    func testRecordDoesNotWaitForTheLockAndCountsWhatItSkips() {
        let b = TimingBuffer(capacity: 2)
        let held = DispatchSemaphore(value: 0), release = DispatchSemaphore(value: 0), done = DispatchSemaphore(value: 0)
        Thread.detachNewThread {
            b.locked {
                held.signal()
                release.wait()
            }
            done.signal()
        }
        held.wait()
        b.record(1, 7)
        XCTAssertEqual(b.skipped.load(ordering: .relaxed), 1, "the lock was held: skipped, not waited for")
        release.signal()
        done.wait()
        XCTAssertTrue(b.drain().isEmpty, "the skipped event was not written")
        b.record(1, 1)
        b.record(1, 2)
        b.record(1, 3)  // full
        XCTAssertEqual(b.skipped.load(ordering: .relaxed), 2)
        XCTAssertEqual(b.drain().map(\.a), [1, 2])
        var lines: [String] = []
        b.flush { lines.append($0) }
        XCTAssertEqual(lines.count, 1, "only the count line: the skipped number changed")
        XCTAssertEqual(lines[0].split(separator: "\t")[2], "2")
    }

    // MARK: the shell's events

    /// Each way a job ends, made once with the held runner, each checked as it happens: every job taken has exactly one end.
    func testEveryJobTakenHasExactlyOneEndOnEachPath() throws {
        _ = Timing.shared.drain()
        let runner = HeldRerankRunner()
        let (shell, c) = try rig(runner)

        // 3 replaced while waiting, 1 applied with no change (stale), 0 applied
        c.type(RerankRow.standardKeys)
        var e = Timing.shared.drain()
        let keys = e.filter { $0.kind == 1 }.map(\.a)
        XCTAssertEqual(keys.count, RerankRow.standardKeys.count)
        XCTAssertEqual(keys, Array(keys[0]..<keys[0] + UInt64(keys.count)), "one number per key, in order")
        XCTAssertTrue(e.filter { $0.kind == 2 }.contains { $0.b == 1 }, "a key that wrote the composition says so")
        XCTAssertTrue(e.filter { $0.kind == 3 }.allSatisfy { keys.contains($0.b) }, "a job taken inside send names its key")
        let taken = e.filter { $0.kind == 3 }.count
        XCTAssertGreaterThanOrEqual(taken, 3)
        XCTAssertEqual(reasons(e), Array(repeating: 3, count: taken - 2), "all but the running and the waiting job were replaced")
        runner.complete()
        let stale = Timing.shared.drain()
        XCTAssertEqual(reasons(stale), [1], "the stale job changed nothing")
        e += stale
        let before = Timing.now()
        runner.complete()
        XCTAssertEqual(c.client.marked, RerankRow.reranked)
        let applied = Timing.shared.drain()
        XCTAssertEqual(reasons(applied), [0])
        XCTAssertGreaterThanOrEqual(try XCTUnwrap(applied.first { $0.kind == 6 }).micros, before, "timed at its setMarkedText")
        XCTAssertTrue(applied.filter { $0.kind == 3 }.allSatisfy { $0.b == 0 }, "a job taken outside send has key 0")
        e += applied
        runner.drain()
        c.press(Keys.esc)

        // 2 apply returned another code
        shell.rerankApply = { _, _ in .failed(4) }
        c.type(RerankRow.standardKeys)
        runner.complete()
        e += Timing.shared.drain()
        XCTAssertEqual(reasons(e).last, 2)
        shell.rerankApply = { $0.apply(on: $1) }
        runner.drain()

        // 5 the switch goes off: the waiting job at once, the running one when it completes
        c.type(RerankRow.standardKeys)
        e += Timing.shared.drain()
        c.session.perform(.toggleRerank)
        runner.complete()
        let off = Timing.shared.drain()
        XCTAssertEqual(reasons(off), [5, 5])
        e += off
        c.session.perform(.toggleRerank)
        c.press(Keys.esc)
        runner.drain()

        // 6 run's code is not 0
        c.type(RerankRow.standardKeys)
        runner.complete(code: 4)
        e += Timing.shared.drain()
        XCTAssertEqual(reasons(e).last, 6)
        runner.drain()
        c.press(Keys.esc)

        // 4 the engine is rebuilt: the waiting job at once, the running one when it completes; then another session owns the composition
        c.type(RerankRow.standardKeys)
        e += Timing.shared.drain()
        c.session.selectLayout(.eten)
        runner.complete()
        let rebuilt = Timing.shared.drain()
        XCTAssertEqual(reasons(rebuilt), [4, 4])
        e += rebuilt
        runner.drain()
        c.type(RerankRow.etenKeys)
        runner.complete()
        e += Timing.shared.drain()
        let other = Controller(shell, bundle: "com.apple.MobileSMS")
        other.session.activate()
        runner.complete()
        let moved = Timing.shared.drain()
        XCTAssertEqual(reasons(moved).first, 4)
        e += moved
        runner.drain()
        e += Timing.shared.drain()

        XCTAssertEqual(Set(reasons(e)), Set(0...6), "every reason was made")
        checkPairs(e)
        XCTAssertTrue(e.allSatisfy { (1...7).contains($0.kind) })
    }

    /// Enter's bounded wait writes one event per Enter: the time, the jobs it got and whether it ran out; an Enter with nothing
    /// to wait for writes zeros.
    func testEnterWritesOneWaitEvent() throws {
        _ = Timing.shared.drain()
        let runner = HeldRerankRunner()
        runner.waitMode = .complete
        let (_, c) = try rig(runner)
        c.type(RerankRow.standardKeys)
        XCTAssertTrue(c.press(Keys.enter))
        XCTAssertEqual(c.client.text, RerankRow.reranked)
        var e = Timing.shared.drain()
        var waits = e.filter { $0.kind == 7 }
        XCTAssertEqual(waits.count, 1)
        XCTAssertEqual([waits[0].b, waits[0].c], [2, 0], "the running and the waiting job, inside the limit")
        runner.fireLate()
        runner.fireLate()

        runner.waitMode = .timeout
        c.type(RerankRow.standardKeys)
        XCTAssertTrue(c.press(Keys.enter))
        let late = Timing.shared.drain()
        waits = late.filter { $0.kind == 7 }
        XCTAssertEqual(waits.count, 1)
        XCTAssertEqual([waits[0].b, waits[0].c], [0, 1], "no job inside the limit: timed out")
        e += late
        runner.drain()

        c.type("su3")
        runner.drain()
        e += Timing.shared.drain()
        XCTAssertTrue(c.press(Keys.enter))
        let none = Timing.shared.drain()
        XCTAssertEqual(none.filter { $0.kind == 7 }.map { [$0.a, $0.b, $0.c] }, [[0, 0, 0]], "nothing to wait for")
        e += none
        checkPairs(e)
    }

    /// The timer `start()` builds fires its handler on a background queue: the handler must not carry the main actor's
    /// isolation (Swift 6 traps when a main-actor closure runs off the main thread; the first VM run of the timing build crashed
    /// there at launch). With a closure in `makeTimer` this test process traps instead of passing.
    func testTheTimerFlushesOffTheMainThread() {
        _ = Timing.shared.drain()
        Timing.keyEnd(Timing.keyBegin())
        let t = Timing.makeTimer(after: 0.01, every: 0.05)
        defer { t.cancel() }
        let flushed = expectation(description: "the timer took the events")
        func poll() {
            if Timing.shared.pending == 0 { flushed.fulfill() } else { DispatchQueue.main.asyncAfter(deadline: .now() + 0.02, execute: poll) }
        }
        poll()
        wait(for: [flushed], timeout: 5)
    }

    /// The app's queue runner: the applied job has its take, start, run and end in that order, with run's code 0.
    func testQueueRunnerWritesStartAndRunAroundTheJob() throws {
        _ = Timing.shared.drain()
        let (shell, c) = try rig(QueueRerankRunner())
        c.type(RerankRow.standardKeys)
        let deadline = Date().addingTimeInterval(10)
        while c.client.marked != RerankRow.reranked && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.01)) }
        XCTAssertEqual(c.client.marked, RerankRow.reranked, "the result did not arrive")
        let e = Timing.shared.drain()
        XCTAssertEqual(Timing.shared.skipped.load(ordering: .relaxed), 0)
        checkPairs(e)
        let job = try XCTUnwrap(e.first { $0.kind == 6 && $0.b == 0 }).a
        let chain = try (3...6).map { kind in try XCTUnwrap(e.first { $0.kind == UInt64(kind) && $0.a == job }, "no event \(kind)") }
        XCTAssertEqual(chain.map(\.micros), chain.map(\.micros).sorted(), "take, start, run, end in time order")
        XCTAssertEqual(chain[2].b, 0, "run's code")
        XCTAssertEqual(e.filter { $0.kind == 4 }.count, e.filter { $0.kind == 5 }.count)
        XCTAssertGreaterThan(shell.rerankTaken, 0)
        c.press(Keys.esc)
    }
}
#endif
