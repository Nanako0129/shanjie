#if SHANJIE_TIMING
import Foundation
import Synchronization
import os

/// The timing build's event store (docs/spec/semantic-rerank.md §8.6): compiled only with `-DSHANJIE_TIMING`, never shipped.
/// A fixed buffer allocated once; the writers (the main thread and the rerank queue) only ever try the lock, and an event that
/// finds it held, or the buffer full, is counted in `skipped` instead of written. An event is five integers: nothing is
/// formatted or allocated on the writing side.
final class TimingBuffer: @unchecked Sendable {
    struct Event: Equatable {
        var kind: UInt64
        var micros: UInt64
        var a: UInt64 = 0
        var b: UInt64 = 0
        var c: UInt64 = 0
    }

    /// How many fields follow the time, by event kind (index 0 is unused; 8 is the flush's own count line).
    static let fields = [0, 1, 2, 2, 1, 2, 2, 3, 2]

    let capacity: Int
    private var events: UnsafeMutablePointer<Event>
    private var spare: UnsafeMutablePointer<Event>
    private var count = 0
    private let lock: os_unfair_lock_t
    /// Events not written: the lock was held, or the buffer was full.
    let skipped = Atomic<UInt64>(0)
    /// Only the flushing side reads and writes these two.
    private var emitted: UInt64 = 0
    private var reportedSkipped: UInt64 = 0

    /// 65,536 holds a whole measured round (about 200 keys of at most about 10 events each) many times over.
    init(capacity: Int = 65_536) {
        self.capacity = capacity
        events = .allocate(capacity: capacity)
        spare = .allocate(capacity: capacity)
        events.initialize(repeating: Event(kind: 0, micros: 0), count: capacity)
        spare.initialize(repeating: Event(kind: 0, micros: 0), count: capacity)
        lock = .allocate(capacity: 1)
        lock.initialize(to: os_unfair_lock())
    }

    deinit {
        events.deallocate()
        spare.deallocate()
        lock.deallocate()
    }

    /// Never waits: without the lock, or with a full buffer, the event is counted as skipped.
    func record(_ kind: UInt64, _ a: UInt64 = 0, _ b: UInt64 = 0, _ c: UInt64 = 0, at micros: UInt64 = Timing.now()) {
        guard os_unfair_lock_trylock(lock) else {
            skipped.add(1, ordering: .relaxed)
            return
        }
        if count < capacity {
            events[count] = Event(kind: kind, micros: micros, a: a, b: b, c: c)
            count += 1
        } else {
            skipped.add(1, ordering: .relaxed)
        }
        os_unfair_lock_unlock(lock)
    }

    /// The waiting lock, for the flushing side only (and the test that holds it to see a writer not wait).
    func locked<T>(_ body: () -> T) -> T {
        os_unfair_lock_lock(lock)
        defer { os_unfair_lock_unlock(lock) }
        return body()
    }

    /// Takes what was written so far. The lock is held for a pointer swap only; the copy is made outside it. One caller at a time.
    func drain() -> [Event] {
        let (taken, n) = locked { () -> (UnsafeMutablePointer<Event>, Int) in
            let full = events, n = count
            events = spare
            spare = full
            count = 0
            return (full, n)
        }
        return Array(UnsafeBufferPointer(start: taken, count: n))
    }

    /// One line per event, then one line of kind 8 with the events skipped and the lines written so far (the reader compares the
    /// second number with the lines it got). Nothing is written when nothing happened since the last flush. One caller at a time.
    func flush(_ write: (String) -> Void) {
        let taken = drain()
        let nowSkipped = skipped.load(ordering: .relaxed)
        guard !taken.isEmpty || nowSkipped != reportedSkipped else { return }
        for e in taken { write(Self.line(e)) }
        emitted += UInt64(taken.count)
        reportedSkipped = nowSkipped
        write(Self.line(Event(kind: 8, micros: Timing.now(), a: nowSkipped, b: emitted)))
    }

    /// `<kind>\t<uptime in microseconds>\t<fields…>`: digits and tabs only.
    static func line(_ e: Event) -> String {
        let n = e.kind < UInt64(fields.count) ? fields[Int(e.kind)] : 3
        return ([e.kind, e.micros] + [e.a, e.b, e.c].prefix(n)).map(String.init).joined(separator: "\t")
    }
}

/// The timing build's events (docs/spec/semantic-rerank.md §8.6), written beside the shell's existing points; nothing here changes
/// a branch, an order or a queue. Kinds: 1 key into `Session.send` (key number); 2 `send` returned (key number, whether the key
/// called setMarkedText); 3 job taken (job number, the key being handled or 0); 4 job started on the queue (job number); 5
/// `run()` returned (job number, code); 6 the job's end, once per job (job number, reason: 0 applied and the composition
/// changed, 1 applied with no change, 2 apply returned another code, 3 replaced while waiting, 4 engine rebuilt or session
/// changed, 5 the switch is off, 6 run's code was not 0); 7 Enter's bounded wait (microseconds, jobs it got, timed out).
public enum Timing {
    static let shared = TimingBuffer()

    static func now() -> UInt64 { DispatchTime.now().uptimeNanoseconds / 1000 }

    @MainActor private static var keys: UInt64 = 0
    @MainActor private static var jobs: UInt64 = 0
    /// The key `send` is handling, 0 outside it.
    @MainActor private static var currentKey: UInt64 = 0
    @MainActor private static var markedCalls = 0

    /// What one `Session.applyRerankJob` call saw: `apply` (reason 0) or `fail` (2) being reached first, neither (1), and when
    /// the first setMarkedText of the call returned.
    struct ApplyState {
        var active = false
        var armed = false
        var reason: UInt64 = 1
        var stamp: UInt64 = 0
    }
    @MainActor private static var applyState = ApplyState()

    @MainActor static func keyBegin() -> UInt64 {
        keys += 1
        currentKey = keys
        markedCalls = 0
        shared.record(1, keys)
        return keys
    }

    @MainActor static func keyEnd(_ key: UInt64) {
        shared.record(2, key, markedCalls > 0 ? 1 : 0)
        currentKey = 0
    }

    /// Right after a client.setMarkedText call returned.
    @MainActor static func marked() {
        markedCalls += 1
        if applyState.active && applyState.stamp == 0 { applyState.stamp = now() }
    }

    /// A job was taken: its number.
    @MainActor static func took() -> UInt64 {
        jobs += 1
        shared.record(3, jobs, currentKey)
        return jobs
    }

    @MainActor static func end(_ job: UInt64, _ reason: UInt64) { shared.record(6, job, reason) }

    @MainActor static func beginApply() -> ApplyState {
        let saved = applyState
        applyState = ApplyState(active: true, armed: true)
        return saved
    }

    /// From `Session.apply` (0) and `Session.fail` (2): the first one reached inside an `applyRerankJob` call names its outcome.
    @MainActor static func applyHit(_ reason: UInt64) {
        guard applyState.armed else { return }
        applyState.armed = false
        applyState.reason = reason
    }

    /// The job's end. An applied change is timed at the return of its setMarkedText call, not at the end of the panel work
    /// after it; every other outcome is timed now.
    @MainActor static func endApply(_ job: UInt64, _ saved: ApplyState) {
        let s = applyState
        applyState = saved
        shared.record(6, job, s.reason, at: s.reason == 0 && s.stamp != 0 ? s.stamp : now())
    }

    @MainActor static func enterWait(micros: UInt64, jobs: UInt64, timedOut: Bool) {
        shared.record(7, micros, jobs, timedOut ? 1 : 0)
    }

    private static let logger = Logger(subsystem: "com.nyanako.inputmethod.shanjie.timing", category: "t")
    private static let flushQueue = DispatchQueue(label: "com.nyanako.inputmethod.shanjie.timing", qos: .background)
    nonisolated(unsafe) private static var timer: DispatchSourceTimer?

    /// The app calls this once at launch (the tests never do, so they read the buffer themselves): every 2 s a `.background`
    /// queue takes the events and writes each as one `.notice` line of the unified log, integers only. No file is written, and
    /// the main thread and the rerank queue never wait for this queue.
    @MainActor public static func start() {
        guard timer == nil else { return }
        let t = DispatchSource.makeTimerSource(queue: flushQueue)
        t.schedule(deadline: .now() + 2, repeating: 2.0)
        t.setEventHandler { shared.flush { line in logger.notice("\(line, privacy: .public)") } }
        t.resume()
        timer = t
    }
}
#endif
