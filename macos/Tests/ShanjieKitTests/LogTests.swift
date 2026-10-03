import CShanjie
import Foundation
import XCTest
@testable import ShanjieKit

/// Acceptance 5 (R2, docs/contracts/s3b.md section 10.5): drive every shell path that handles
/// input, an app's bundle ID or the data path while `log stream --level debug` captures this
/// whole process, and require that none of it shows up.
@MainActor
final class LogTests: XCTestCase {
    /// While stdout/stderr are captured, an XCTest issue (from this test or any helper, such as
    /// FakeClient's replacement-range check) would print its message, typed text included, into the
    /// capture. Issues are held during the capture and recorded once it has ended. XCTest may record
    /// from any thread, so the override is nonisolated and the holder is lock-protected.
    private let issueLock = NSLock()
    nonisolated(unsafe) private var heldIssues: [XCTIssue]?

    nonisolated override func record(_ issue: XCTIssue) {
        issueLock.lock()
        if heldIssues != nil {
            heldIssues!.append(issue)
            issueLock.unlock()
            return
        }
        issueLock.unlock()
        super.record(issue)
    }

    private func holdIssues() {
        issueLock.lock(); heldIssues = []; issueLock.unlock()
    }

    private func releaseIssues() {
        issueLock.lock()
        let held = heldIssues ?? []
        heldIssues = nil
        issueLock.unlock()
        held.forEach { super.record($0) }
    }

    func testShellLogsNothingButStaticTextAndCodes() throws {
        let nonce = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let start = "shanjie-logtest-start-\(nonce)"
        let end = "shanjie-logtest-end-\(nonce)"
        let bundleMarker = "com.marker.\(nonce)"
        let pathMarker = "pathmarker-\(nonce)"

        // Everything this process logs, through any API or subsystem (NSLog, another Logger, ...);
        // the subsystem is used only for the `<private>` assertion below.
        let capture = try LogCapture(predicate: "processIdentifier == \(getpid())")
        defer { capture.stop() }

        // Start marker: the shell's own Logger and subsystem at debug, the lowest level there is
        // (the shell uses debug and error). Nothing negative happens until it is seen.
        let started = capture.wait(for: start, timeout: 10) {
            Log.shell.debug("\(start, privacy: .public)")
        }
        guard started else {
            return XCTFail("log stream never delivered the start marker within 10 s: the capture is not connected")
        }

        // Negative actions, with this process's stdout and stderr captured too: in a process that
        // has a stderr (a test runner, unlike the launchd-started input method), NSLog and print
        // write there instead of to the unified log, so the log stream alone would miss them.
        // Issues raised while stdout/stderr are captured are held (record(_:) above) so their
        // messages cannot carry typed text into the capture; they are recorded right after it.
        let resources = try XCTUnwrap(TestData.resources())
        holdIssues()
        let std = StdCapture()
        defer { _ = std.finish(); releaseIssues() }  // an early exit must neither leave the pipe nor lose held issues
        let stdProbe = "shanjie-std-probe-\(nonce)"
        FileHandle.standardError.write(Data("\(stdProbe)\n".utf8))
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore())
        let c = Controller(shell, bundle: bundleMarker)
        c.session.activate()                            // profile from a marked bundle ID
        c.type(Row10.standardKeys)
        c.press(Keys.enter)                             // commits the marker sentence
        XCTAssertEqual(c.client.text, Row10.formal, "the typing path did not commit row 10")
        c.type("su3 ")
        let first = c.panel.items.first                 // optional, not XCTUnwrap: no throw while captured
        XCTAssertNotNil(first, "no candidate was shown for the mouse-selection path")
        if let first { c.session.candidateSelected(first) }   // mouse selection
        XCTAssertFalse(c.session.send(ShanjieKey(kind: 99, ch: 0, modifiers: 0)), "the non-zero return code path was not taken")  // non-zero code
        c.type("su3cl3")
        c.session.deactivate()                          // commits 你好
        XCTAssertTrue(c.client.text.hasSuffix("你好"), "deactivate did not commit the composition")
        c.session.activate()
        c.type("su3")
        c.session.selectLayout(.eten) // mode switch commits and rebuilds
        let broken = FileManager.default.temporaryDirectory.appendingPathComponent(pathMarker, isDirectory: true)
        let failed = Shell(resources: broken, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore())  // engine_new fails on a marked path
        XCTAssertNil(failed.engine, "the marked data path did not fail engine creation")

        let stdText = std.finish()
        releaseIssues()
        XCTAssertTrue(stdText.contains(stdProbe), "the stdout/stderr capture is not connected")
        XCTAssertTrue(std.complete, "the stdout/stderr capture did not reach end-of-file: it may be incomplete")

        // End marker: a different one, same Logger and level; the capture runs until it is seen.
        let ended = capture.wait(for: end, timeout: 10) {
            Log.shell.debug("\(end, privacy: .public)")
        }
        XCTAssertTrue(ended, "log stream never delivered the end marker: the capture is incomplete")

        let entries = capture.entries()
        let everything = entries.map(\.text).joined(separator: "\n")
        XCTAssertTrue(everything.contains(start) && everything.contains(end))
        // Positive control on the shell's own error path: its static text and code are captured.
        XCTAssertTrue(entries.contains { $0.subsystem == Log.subsystem && $0.text.contains("shanjie_engine_new failed, code 3") },
                      "the shell's error log did not reach the capture")
        XCTAssertTrue(entries.contains { $0.subsystem == Log.subsystem && $0.text.contains("core call failed, code 2") },
                      "the non-zero return code path (fail, reset 1) did not run")

        let negatives = [
            Row10.formal, Row10.chat, "你好", Row10.zhuyin, "ㄑㄧ", "ㄋㄧ",
            Row10.standardKeys, String(Row10.standardKeys.prefix(6)), "su3cl3",
            bundleMarker, pathMarker,
        ]
        for marker in negatives {
            XCTAssertFalse(everything.contains(marker), "a negative marker reached the log")
            XCTAssertFalse(stdText.contains(marker), "a negative marker reached stdout or stderr")
        }
        let shellLines = entries.filter { $0.subsystem == Log.subsystem }
        XCTAssertFalse(shellLines.isEmpty)
        XCTAssertFalse(shellLines.contains { $0.text.contains("<private>") },
                       "the shell logged a redacted value; it may log static text and return codes only")
    }
}

/// `/usr/bin/log stream` (the full path: zsh's `log` is a builtin) in ndjson, decoded per entry.
/// JSON escapes are decoded before matching, so a marker cannot hide behind `\uXXXX`.
final class LogCapture: @unchecked Sendable {
    struct Entry {
        var subsystem: String
        var text: String  // every string field of the entry, decoded
    }

    private let process = Process()
    private let lock = NSLock()
    private var buffer = Data()

    init(predicate: String) throws {
        process.executableURL = URL(fileURLWithPath: "/usr/bin/log")
        process.arguments = ["stream", "--level", "debug", "--style", "ndjson", "--predicate", predicate]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard let self else { return }
            self.lock.lock()
            self.buffer.append(d)
            self.lock.unlock()
        }
        try process.run()
    }

    func stop() {
        if process.isRunning {
            process.terminate()
            process.waitUntilExit()
        }
    }

    /// Calls `emit` every 200 ms until an entry contains `marker`; false after `timeout` seconds.
    func wait(for marker: String, timeout: TimeInterval, emit: () -> Void) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            emit()
            Thread.sleep(forTimeInterval: 0.2)
            if entries().contains(where: { $0.text.contains(marker) }) { return true }
        }
        return false
    }

    func entries() -> [Entry] {
        lock.lock()
        let data = buffer
        lock.unlock()
        return data.split(separator: UInt8(ascii: "\n")).compactMap { line in
            guard let obj = try? JSONSerialization.jsonObject(with: Data(line)) as? [String: Any] else { return nil }
            return Entry(subsystem: obj["subsystem"] as? String ?? "", text: Self.strings(obj).joined(separator: " "))
        }
    }

    private static func strings(_ v: Any) -> [String] {
        switch v {
        case let s as String: [s]
        case let d as [String: Any]: d.values.flatMap(strings)
        case let a as [Any]: a.flatMap(strings)
        default: []
        }
    }
}

/// Redirects this process's stdout and stderr into a pipe until `finish()`, then restores them.
final class StdCapture: @unchecked Sendable {
    private final class Sink: @unchecked Sendable {
        let lock = NSLock()
        var data = Data()
    }
    private let pipe = Pipe()
    private let savedOut = dup(STDOUT_FILENO), savedErr = dup(STDERR_FILENO)
    private let sink = Sink()
    private let drained = DispatchSemaphore(value: 0)
    private var finished = false
    private var text = ""
    /// Set by finish(): true once the reader reached end-of-file, false on a timeout (something
    /// still held the pipe open). False before finish() has run.
    private(set) var complete = false

    init() {
        fflush(stdout)
        fflush(stderr)
        // The reader runs until end-of-file, which comes only after finish() has restored fd 1/2
        // and closed the pipe's write end; finish() waits for it, so nothing written meanwhile can
        // be missed. It holds only the sink, so this object can still be freed (deinit restores).
        let reader = pipe.fileHandleForReading, sink = sink, drained = drained
        DispatchQueue.global().async {
            while true {
                let d = reader.availableData
                if d.isEmpty { break }
                sink.lock.lock()
                sink.data.append(d)
                sink.lock.unlock()
            }
            drained.signal()
        }
        dup2(pipe.fileHandleForWriting.fileDescriptor, STDOUT_FILENO)
        dup2(pipe.fileHandleForWriting.fileDescriptor, STDERR_FILENO)
    }

    deinit { _ = finish() }

    /// Restores both descriptors and returns everything written meanwhile. Idempotent (the test
    /// also calls it from a defer): every call returns the same text.
    func finish() -> String {
        if finished { return text }
        finished = true
        fflush(stdout)
        fflush(stderr)
        dup2(savedOut, STDOUT_FILENO)
        dup2(savedErr, STDERR_FILENO)
        close(savedOut)
        close(savedErr)
        try? pipe.fileHandleForWriting.close()
        complete = drained.wait(timeout: .now() + 10) == .success
        sink.lock.lock()
        text = String(decoding: sink.data, as: UTF8.self)
        sink.lock.unlock()
        return text
    }
}
