import AppKit
import CShanjie
import XCTest
@testable import ShanjieKit

/// docs/contracts/context-return.md section 4, the latency stop condition: the first key of a composition, which reads the left
/// context, passes it to the core and sends the key (`Session.send`: the learning gate, the left-context read, `setLeftContext`, the first
/// `engine.key`, and applying its output to the fake client), 1,000 times with a 64-character mixed Chinese and English text before
/// the insertion point; prints p50 / p95 / max and the load. A measurement, not a check, so it runs only with
/// `SHANJIE_BENCH=1 swift test -c release --package-path macos --filter testBenchmarkLeftContext`. The file uses only what main
/// already has (no context-return symbol), so the same file measures main and the branch in one session.
@MainActor
final class LeftContextBenchTests: XCTestCase {
    func testBenchmarkLeftContext() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["SHANJIE_BENCH"] == "1", "set SHANJIE_BENCH=1 to run")
        let shell = Shell(resources: try XCTUnwrap(TestData.resources()), panel: FakePanel(), isSecureInput: { false },
                          layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), quotePackStore: MemoryQuotePackStore(), glassTintStore: MemoryGlassTintStore(), candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: MemoryProfileModeStore(), appProfileStore: MemoryAppProfileStore(), spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        let c = Controller(shell)
        let text = "昨天和 Joe 去看了 Taylor Swift 的演唱會，現場超級熱鬧，大家一起唱 Love Story 唱到最後一首歌才回家"
        XCTAssertEqual(text.count, 64)
        c.client.before = text
        let key = ShanjieKey(kind: KeyMap.char, ch: UInt32(Character("g").asciiValue!), modifiers: 0)  // ㄕ
        var ms: [Double] = []
        for _ in 0..<1_000 {
            let t = ContinuousClock.now
            _ = c.session.send(key)
            let d = ContinuousClock.now - t
            ms.append(Double(d.components.seconds) * 1000 + Double(d.components.attoseconds) / 1e15)
            c.session.finish(mode: 1)  // discard, so the next send starts a composition and reads again
        }
        XCTAssertEqual(c.client.requested.count, 1_000, "every repetition read the left context once")
        let s = ms.sorted()
        func p(_ q: Double) -> Double { s[min(s.count - 1, Int(Double(s.count) * q))] }
        var load = [Double](repeating: 0, count: 1)
        getloadavg(&load, 1)
        print(String(format: "context-return bench: first key with left context p50 %.3f ms  p95 %.3f ms  max %.3f ms  (load %.2f)",
                     p(0.5), p(0.95), s.last!, load[0]))
    }
}
