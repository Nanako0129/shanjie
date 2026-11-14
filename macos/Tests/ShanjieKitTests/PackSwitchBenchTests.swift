import AppKit
import CShanjie
import XCTest
@testable import ShanjieKit

/// docs/spec/macos-shell.md §2.1 (measurement): the two times a pack switch costs the main thread, and the time from the switch to the new engine being the current one. With the
/// app's queue builder, the real Resources (every pack file linked), the release core, and a learning file at its limits (50,000 ordinary records and 2,000 new words, made up
/// here from invented words: no real learning file is read). Ten switches of the ACG pack, off and on in turn, each waited out before the next. A measurement that also asserts its
/// limits, so it runs only with
///   `make rust && SHANJIE_BENCH=1 swift test -c release --package-path macos --filter testBenchmarkPackSwitch`
/// and prints three lines starting `pack-switch bench:` (min, median and max in milliseconds, and the load at the start and at the end of the ten switches).
@MainActor
final class PackSwitchBenchTests: XCTestCase {
    /// The limits: the call that switches (the old rebuild took 1,186–2,324 ms) and the swap on the main thread (the old engine's release alone was measured at 4.5–18.2 ms).
    static let callLimitMs = 5.0
    static let swapLimitMs = 50.0
    static let switches = 10

    /// `learning.tsv` of the core's limits (docs/spec/learning.md §10; `CAPACITY` 50,000 ordinary records, `NEWWORD_CAP` 2,000 new words): the header and one record per line
    /// (context, reading, word, weight, day), every field made up. Contexts are two Han characters that are unique per record, so every record is its own.
    static func writeLearningFile(in dir: URL) throws {
        let today = Int(Date().timeIntervalSince1970 / 86_400)
        func han(_ i: Int) -> Character { Character(UnicodeScalar(0x4E00 + i)!) }
        var text = "#shanjie-learning v1\n"
        for i in 0..<50_000 {
            text += "\(han(i % 2_000))\(han(i / 2_000 + 3_000))\tㄒㄩㄝˊ-ㄒㄧˊ\t學習\t1.0\t\(today)\n"
        }
        for i in 0..<2_000 {
            text += "#\tㄅㄚ-ㄅㄚ\t\(han(6_000 + i / 40))\(han(6_000 + i % 40 + 100))\t1.0\t\(today)\n"
        }
        let file = dir.appendingPathComponent("learning.tsv")
        try text.write(to: file, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
    }

    /// Every file of data/packs linked into a Resources of the repository's real data.
    static func resourcesWithRealPacks() throws -> URL {
        let dir = try XCTUnwrap(TestData.resources())
        let packs = dir.appendingPathComponent("packs", isDirectory: true)
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        let source = TestData.repo.appendingPathComponent("data/packs")
        for name in try FileManager.default.contentsOfDirectory(atPath: source.path) {
            try FileManager.default.createSymbolicLink(at: packs.appendingPathComponent(name), withDestinationURL: source.appendingPathComponent(name))
        }
        return dir
    }

    func testBenchmarkPackSwitch() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["SHANJIE_BENCH"] == "1", "set SHANJIE_BENCH=1 to run")
        let learning = TestLearning.directory()
        try Self.writeLearningFile(in: learning)
        let acg = MemoryAcgPackStore(true)
        let shell = Shell(resources: try Self.resourcesWithRealPacks(), panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: learning,
                          dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(),
                          englishStore: MemoryEnglishStore(overwrite: true), acgPackStore: acg, quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(),
                          profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), packBuilder: QueuePackBuilder(), spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        XCTAssertFalse(shell.learningOpenFailed, "the synthetic learning file opens")
        func ms(_ d: Duration) -> Double { Double(d.components.seconds) * 1000 + Double(d.components.attoseconds) / 1e15 }
        func load() -> Double { var l = [Double](repeating: 0, count: 1); getloadavg(&l, 1); return l[0] }
        let loadStart = load()
        var calls: [Double] = [], swaps: [Double] = [], toSwap: [Double] = []
        for i in 0..<Self.switches {
            weak var before = shell.engine   // weak: the old engine is released inside the swap, which is part of what is timed
            let on = i % 2 == 1   // the pack starts on: off, on, off, ...
            let start = ContinuousClock.now
            shell.setAcgPack(on)
            calls.append(ms(ContinuousClock.now - start))
            XCTAssertTrue(spin(timeout: 60) { shell.engine !== before && shell.installNanos.count == i + 1 }, "switch \(i) was swapped in")
            XCTAssertNil(before, "switch \(i): the swap released the old engine")
            toSwap.append(ms(ContinuousClock.now - start))
            swaps.append(Double(shell.installNanos[i]) / 1e6)
        }
        let loadEnd = load()
        XCTAssertEqual(shell.installNanos.count, Self.switches, "every switch swapped in")
        XCTAssertEqual(shell.engineCounter.live, 1)
        func line(_ name: String, _ v: [Double]) {
            let s = v.sorted()
            print(String(format: "pack-switch bench: %@ min_ms=%.3f median_ms=%.3f max_ms=%.3f load_start=%.2f load_end=%.2f",
                         name, s[0], (s[s.count / 2 - 1] + s[s.count / 2]) / 2, s[s.count - 1], loadStart, loadEnd))
        }
        line("setAcgPack_call", calls)
        line("swap_on_main_thread", swaps)
        line("switch_to_swap", toSwap)
        XCTAssertLessThanOrEqual(calls.max()!, Self.callLimitMs, "the switching call")
        XCTAssertLessThanOrEqual(swaps.max()!, Self.swapLimitMs, "the swap on the main thread")
    }
}
