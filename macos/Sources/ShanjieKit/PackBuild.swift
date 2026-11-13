import CShanjie
import Dispatch
import Foundation
import Synchronization

/// Builds the engine of a pack switch off the main thread (docs/spec/macos-shell.md §2.1, docs/spec/lexicon-and-packs.md §4.1, §4.3). The app passes
/// `QueuePackBuilder`; the tests pass builders they complete by hand, and the many rigs that never switch a pack get `ImmediatePackBuilder`.
@MainActor
public protocol PackBuilder: AnyObject {
    /// `work` runs once on some thread (it reads and writes no `Shell` state), then `done` once on the main thread with its result.
    func run(_ work: @escaping @Sendable () -> PackBuildResult, done: @escaping @MainActor @Sendable (PackBuildResult) -> Void)
}

/// The app's builder: one serial `.utility` queue with a fixed label, like `QueueRerankRunner`. The result comes back through `DispatchQueue.main.async`.
public final class QueuePackBuilder: PackBuilder {
    private let queue = DispatchQueue(label: "com.nyanako.inputmethod.shanjie.packbuild", qos: .utility)

    public init() {}

    public func run(_ work: @escaping @Sendable () -> PackBuildResult, done: @escaping @MainActor @Sendable (PackBuildResult) -> Void) {
        queue.async {
            let result = work()
            DispatchQueue.main.async { done(result) }
        }
    }
}

/// Does the work on the calling thread and calls `done` before `run` returns: the default of `Shell.init`, for the rigs that do not test the background
/// build (the shell then behaves as it did when a switch rebuilt the engine in the call).
public final class ImmediatePackBuilder: PackBuilder {
    public init() {}

    public func run(_ work: @escaping @Sendable () -> PackBuildResult, done: @escaping @MainActor @Sendable (PackBuildResult) -> Void) {
        let result = work()
        MainActor.assumeIsolated { done(result) }
    }
}

/// What one build did, in codes only (R2, docs/spec/macos-shell.md §6): the shell logs these on the main thread when it takes the result.
enum PackBuildNote: Sendable, Equatable {
    case packsFailed(code: Int32, keptMask: UInt32)
    case noPackBuildFailedToo(code: Int32)
    case newFailed(code: Int32)
    case loadLMFailed(code: Int32)
    case loadTinyFailed(code: Int32)
    case loadColloquialFailed(code: Int32)
}

/// Counts the engines made and freed (test hook: the tests read the difference as the live engines). `made` is called when a build creates one, `freed` from
/// every engine's free.
final class EngineCounter: Sendable {
    private let counts = Mutex<(made: Int, freed: Int)>((0, 0))
    func made() { counts.withLock { $0.made += 1 } }
    func freed() { counts.withLock { $0.freed += 1 } }
    var live: Int { counts.withLock { $0.made - $0.freed } }
}

/// The calls a build makes into the core that a test may replace (like `Shell.rerankFree`).
struct PackBuildOps: Sendable {
    var newShared: @Sendable (OpaquePointer, UInt32, String?, UInt32) -> (OpaquePointer?, Int32) = { shared, layout, dir, packs in
        var out: OpaquePointer?
        let code = dir.map { shanjie_engine_new_shared(shared, layout, $0, packs, &out) } ?? shanjie_engine_new_shared(shared, layout, nil, packs, &out)
        return (out, code)
    }
}

/// Everything a build reads: values and paths, never a `Shell` or an engine (docs/spec/macos-shell.md §2.1).
struct PackBuildPlan: Sendable {
    var dataDir: String
    var packsDir: String
    var layout: UInt32
    var mask: UInt32
    /// The settings order: mask bit and file name in `packsDir`.
    var packs: [(bit: UInt32, file: String)]
    var modelPath: String
    var tinyPath: String
    var colloquialPath: String
    /// The shared parts of the engine running now, or nil: build from the files.
    var shared: SharedParts?
    var ops: PackBuildOps
    var counter: EngineCounter
    var engineFree: @Sendable (OpaquePointer) -> Void
}

/// The outcome of one build, handed to the main thread whole.
public final class PackBuildResult: @unchecked Sendable {
    /// nil when no engine could be made, not even without packs.
    let engine: BuiltEngine?
    /// What the build was asked for: the pack mask and the layout.
    let requestedMask: UInt32
    let layout: UInt32
    /// The pack bits really loaded (docs/spec/lexicon-and-packs.md §4.3), 0 without an engine.
    let loadedMask: UInt32
    /// Engines tried, as `Shell.engineBuilds` counts.
    let attempts: Int
    let usedShared: Bool
    /// Whether the model, the small model and the colloquial table are all loaded: the next switch may share them.
    let allLoaded: Bool
    let tinyLoaded: Bool
    let notes: [PackBuildNote]

    init(engine: BuiltEngine?, requestedMask: UInt32, layout: UInt32, loadedMask: UInt32, attempts: Int, usedShared: Bool,
         allLoaded: Bool, tinyLoaded: Bool, notes: [PackBuildNote]) {
        self.engine = engine
        self.requestedMask = requestedMask
        self.layout = layout
        self.loadedMask = loadedMask
        self.attempts = attempts
        self.usedShared = usedShared
        self.allLoaded = allLoaded
        self.tinyLoaded = tinyLoaded
        self.notes = notes
    }
}

enum PackBuild {
    /// One build, on whatever thread calls it: the engine for `plan.mask`, with the retries of docs/spec/lexicon-and-packs.md §4.3 when the combined build fails,
    /// from the files (`new_packs`, then `load_lm`, `load_tinylm`, `load_colloquial`) or, with `plan.shared`, with `shanjie_engine_new_shared` for every attempt (that call
    /// is all or nothing, so an engine it returns has all three loaded). The shared parts are released here, after the last attempt.
    static func run(_ plan: PackBuildPlan) -> PackBuildResult {
        defer { plan.shared?.release() }
        let mask = plan.mask
        // A missing pack file contributes nothing (the core builds without it), so only bits whose file is there count as loaded.
        let present = plan.packs.reduce(UInt32(0)) { FileManager.default.fileExists(atPath: plan.packsDir + "/" + $1.file) ? $0 | $1.bit : $0 }
        var built = mask & present
        var attempts = 0
        var notes: [PackBuildNote] = []
        func make(packsDir: String?, packs: UInt32) -> (OpaquePointer?, Int32) {
            attempts += 1
            var out: OpaquePointer?
            var code: Int32
            if let shared = plan.shared?.pointer {
                (out, code) = plan.ops.newShared(shared, plan.layout, packsDir, packs)
            } else if let packsDir {
                code = shanjie_engine_new_packs(plan.dataDir, plan.layout, packsDir, packs, &out)
            } else {
                code = shanjie_engine_new(plan.dataDir, plan.layout, &out)
            }
            guard code == 0, let out else { return (nil, code == 0 ? 4 : code) }
            plan.counter.made()
            return (out, 0)
        }
        func result(_ engine: OpaquePointer?, loaded: UInt32, allLoaded: Bool, tiny: Bool) -> PackBuildResult {
            PackBuildResult(engine: engine.map { BuiltEngine($0, free: plan.engineFree) }, requestedMask: mask, layout: plan.layout, loadedMask: loaded,
                            attempts: attempts, usedShared: plan.shared != nil, allLoaded: allLoaded, tinyLoaded: tiny, notes: notes)
        }
        var (made, code) = make(packsDir: mask != 0 ? plan.packsDir : nil, packs: mask)
        if made == nil && mask != 0 {
            // The combined build failed, so the first code alone does not say whose fault it is. (a) Build with no packs, only to learn whether
            // it works, and free it at once (a switch keeps one engine and the one being built): if it fails too the cause is not a pack, both codes are logged
            // and these two builds are all there are. (b) Otherwise a pack is broken: for each enabled pack in the settings order build with just
            // that pack dropped (mask & ~bit); the first success wins, so the healthy packs stay on. (c) If no single drop works (two broken
            // files), build the no-pack engine again. Only packs whose file is there are tried (a missing file contributes nothing, so dropping it
            // cannot help); with a single such pack there is no drop to try, and the probe is kept as the engine instead of being freed and built a
            // third time. The stored choices are untouched and nothing is shown; the next rebuild tries the packs again.
            let packCode = code
            built = 0
            let live = mask & present
            let drops = plan.packs.map(\.bit).filter { live & $0 != 0 && live & ~$0 != 0 }
            var noPackWorks = false
            var keptProbe: OpaquePointer?
            do {
                let (probe, probeCode) = make(packsDir: nil, packs: 0)
                noPackWorks = probe != nil
                code = probeCode
                if drops.isEmpty { keptProbe = probe } else if let probe { plan.engineFree(probe) }
            }   // otherwise the probe engine is freed here, before any drop is built
            if let probe = keptProbe {
                made = probe
                notes.append(.packsFailed(code: packCode, keptMask: 0))
            } else if noPackWorks {
                var kept: UInt32 = 0
                for bit in drops {
                    let (dropped, _) = make(packsDir: plan.packsDir, packs: mask & ~bit)
                    if let dropped {
                        made = dropped
                        built = mask & ~bit & present
                        kept = mask & ~bit
                        break
                    }
                }
                if made == nil { (made, code) = make(packsDir: nil, packs: 0) }
                notes.append(.packsFailed(code: packCode, keptMask: kept))
            } else {
                notes.append(.noPackBuildFailedToo(code: code))
            }
        }
        guard let e = made else {
            notes.append(.newFailed(code: code))
            return result(nil, loaded: 0, allLoaded: false, tiny: false)
        }
        if plan.shared != nil {
            // All or nothing: the engine has the model, the small model and the colloquial table.
            return result(e, loaded: built, allLoaded: true, tiny: true)
        }
        var allLoaded = false
        var tinyLoaded = false
        let lm = shanjie_engine_load_lm(e, plan.modelPath)
        if lm != 0 { notes.append(.loadLMFailed(code: lm)) }
        // docs/spec/semantic-rerank.md §8.2, §8.3: after load_lm (which drops a small model). Missing or failed: the n-gram alone, no rerank.
        if lm == 0 {
            let tiny = shanjie_engine_load_tinylm(e, plan.tinyPath)
            if tiny != 0 { notes.append(.loadTinyFailed(code: tiny)) }
            tinyLoaded = tiny == 0
            // docs/spec/semantic-rerank.md §4.6: after load_tinylm, and only when it loaded (the term applies to the small model's rerank, so there is
            // nothing to load it for otherwise). A failure is logged (the code only) and the rerank runs without the term.
            if tiny == 0 {
                let colloquial = shanjie_engine_load_colloquial(e, plan.colloquialPath)
                if colloquial != 0 { notes.append(.loadColloquialFailed(code: colloquial)) }
                allLoaded = colloquial == 0
            }
        }
        return result(e, loaded: built, allLoaded: allLoaded, tiny: tinyLoaded)
    }
}
