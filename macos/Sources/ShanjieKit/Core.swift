import CShanjie

/// One output of the core, copied into Swift values in the call that received it; the C output is
/// freed before this is returned (docs/contracts/s3b.md section 7).
///
/// R2: this holds user input. It is deliberately not an `Error` and not `CustomStringConvertible`
/// or `CustomDebugStringConvertible`, so it cannot reach a crash report or a log by description.
struct CoreOutput {
    var handled: Bool
    var commit: String
    var preedit: String
    var cursorUTF16: Int
    var candidates: [String]
    var selected: Int
    /// s3b2 section 8.2: 0 = collapsed single row, > 0 = expanded grid with this many columns.
    var columns: Int
    /// Position of `candidates[0]` in the whole list, and the whole list's length (scroll bar).
    var first: Int
    var total: Int
    /// candidate-vertical contract section 2.2: 0 horizontal (or no candidates), 1 vertical candidate window, 2 vertical
    /// prediction row (entered or not).
    var vertical: Int
    /// zhen-inline section 1.3f: the open English run in `preedit`, UTF-16 start and length; both 0 when no run is open.
    var englishRunStart: Int = 0
    var englishRunLength: Int = 0
}

/// The result of a core call: an output, or the non-zero C ABI return code (carries no input).
enum CoreResult {
    case ok(CoreOutput)
    case failed(Int32)
}

/// Owns one `ShanjieEngine` handle. Every call is on the main thread (IMK's callback thread), as
/// the handle is not thread-safe (s3a section 6).
@MainActor
final class CoreEngine {
    /// fileprivate, not private: only `RerankJob.apply(on:)` in this file reads it.
    fileprivate let handle: OpaquePointer

    private init(handle: OpaquePointer) { self.handle = handle }

    /// `shanjie_engine_new`; on failure returns the code and nothing is allocated.
    /// `packsDir` is the directory of the word pack files and turns the ACG pack on (acg-pack contract A.2); nil is
    /// `shanjie_engine_new`, so the engine is the one the app had before the pack existed.
    static func make(dataDir: String, layout: UInt32, packsDir: String? = nil) -> (CoreEngine?, Int32) {
        var out: OpaquePointer?
        let code: Int32
        if let packsDir {
            code = shanjie_engine_new_packs(dataDir, layout, packsDir, UInt32(SHANJIE_PACK_ACG), &out)
        } else {
            code = shanjie_engine_new(dataDir, layout, &out)
        }
        guard code == 0, let out else { return (nil, code == 0 ? 4 : code) }
        return (CoreEngine(handle: out), 0)
    }

    isolated deinit { shanjie_engine_free(handle) }

    func loadLM(path: String) -> Int32 { shanjie_engine_load_lm(handle, path) }

    /// S7c-2a: the small model for the loaded n-gram model; call it after `loadLM`. Non-zero keeps the n-gram only.
    func loadTinyLM(path: String) -> Int32 { shanjie_engine_load_tinylm(handle, path) }

    /// S7c-2a: whether rerank jobs are made (the core's default is off since S7c-2c; the shell sends its own value to every engine); returns the snapshot, like `setDemote`.
    func setRerank(_ on: Bool) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_set_rerank(handle, on ? 1 : 0, &out), out)
    }

    /// S7c-2a: the job of the last change, once; `(nil, 0)` when there is none, `(nil, code)` on a non-zero code (the header
    /// guarantees `*job` is NULL then, so there is nothing to free). The job is wrapped at once, so the pointer never leaves this
    /// file and is freed exactly once, by `free`, when the wrapper goes.
    func rerankTake(free: @escaping @Sendable (OpaquePointer) -> Void) -> (RerankJob?, Int32) {
        var job: OpaquePointer?
        let code = shanjie_engine_rerank_take(handle, &job)
        guard code == 0 else { return (nil, code) }
        return (job.map { RerankJob($0, free: free) }, 0)
    }

    /// s3e: replace the punctuation alternatives; 0 on success, 2 on an invalid table (kept as before).
    func setPunctuation(_ table: String) -> Int32 { shanjie_engine_set_punctuation(handle, table) }

    // S4 (docs/contracts/s4-learning.md sections 2-4); each returns the C ABI code.

    /// `nil` means no left context (NULL).
    func setLeftContext(_ text: String?) -> Int32 {
        guard let text else { return shanjie_engine_set_left_context(handle, nil) }
        return text.withCString { shanjie_engine_set_left_context(handle, $0) }
    }

    /// sw: whether the demotion table applies (the core's default is on); recomputes the composition and
    /// returns the snapshot, like `setProfile`.
    func setDemote(_ on: Bool) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_set_demote(handle, on ? 1 : 0, &out), out)
    }

    /// V3: whether the prediction row is computed (the core's default is on); returns the snapshot, like `setDemote`.
    func setPrediction(_ on: Bool) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_set_prediction(handle, on ? 1 : 0, &out), out)
    }

    /// candidate-vertical contract section 2.2: the orientation of the candidate windows that open from now on (the core's
    /// default is horizontal); an open window keeps its own, while a prediction row on screen is re-rendered in the new
    /// orientation in the snapshot (section 2.4). Sent to every engine `Shell.build()` makes, like `setPrediction`.
    func setCandidateVertical(_ on: Bool) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_set_candidate_vertical(handle, on ? 1 : 0, &out), out)
    }

    /// V3 section 12: whether a symbol typed into an occupied column opens a new unfinished unit (the core's default is
    /// off); returns the snapshot. Sent to every engine `Shell.build()` makes.
    func setAbbreviation(_ on: Bool) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_set_abbreviation(handle, on ? 1 : 0, &out), out)
    }

    /// zhen-mixed: the English word list, `[a-z]{3,32}` per line; 0 ok, 3 refused (the previous list, or none, stays). Call it once
    /// after `make`, like `loadLM`.
    func loadEnglish(path: String) -> Int32 { shanjie_engine_load_english(handle, path) }

    /// zhen-mixed: whether Space after an English word typed into one unfinished syllable opens the English window (the core's
    /// default is off; it needs the loaded list and the standard layout); returns the snapshot. Sent to every engine
    /// `Shell.build()` makes.
    func setEnglish(_ on: Bool) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_set_english(handle, on ? 1 : 0, &out), out)
    }

    /// zhen-inline: whether the English typed into the unfinished syllable shows as letters while it can still become a listed word
    /// (the core's default is true); returns the snapshot, so a composition on screen is drawn again. Sent to every engine
    /// `Shell.build()` makes.
    func setEnglishInline(_ on: Bool) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_set_english_inline(handle, on ? 1 : 0, &out), out)
    }

    func setLearning(_ enabled: Bool) -> Int32 { shanjie_engine_set_learning(handle, enabled ? 1 : 0) }

    func learningOpen(dir: String) -> Int32 { shanjie_engine_learning_open(handle, dir) }

    func learningClear() -> Int32 { shanjie_engine_learning_clear(handle) }

    /// bit0: a full rewrite of the learning file failed and no full rewrite or clear has succeeded
    /// since (S4 section 4; a failed append that falls back to a successful rewrite does not set it).
    /// `nil` on a non-zero code.
    func learningStatus() -> UInt32? {
        var flags: UInt32 = 0
        return shanjie_engine_learning_status(handle, &flags) == 0 ? flags : nil
    }

    func key(_ key: ShanjieKey) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_key(handle, key, &out), out)
    }

    /// s3b2 section 8.2 mouse pick: `index` is a position in the last output's candidates. With the
    /// candidate window closed and a prediction row showing (entered or not), it selects that row's item
    /// (v3-engine section 4). Code 2 when neither is showing or `index` is outside that output (state unchanged).
    func pick(_ index: UInt32) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_pick(handle, index, &out), out)
    }

    /// mode 0 commits the composition then clears, 1 discards.
    func reset(mode: UInt32) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_reset(handle, mode, &out), out)
    }

    /// profile 0 chat, 1 formal.
    func setProfile(_ profile: UInt32) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return Self.take(shanjie_engine_set_profile(handle, profile, &out), out)
    }

    /// Copies every field into Swift values, then frees the C output at once.
    fileprivate static func take(_ code: Int32, _ out: UnsafeMutablePointer<ShanjieOutput>?) -> CoreResult {
        guard code == 0, let out else {
            shanjie_output_free(out)  // NULL on a non-zero code (s3a section 6); freeing NULL is a no-op
            return .failed(code == 0 ? 4 : code)
        }
        defer { shanjie_output_free(out) }
        let o = out.pointee
        var candidates: [String] = []
        if let list = o.candidates {
            for i in 0..<Int(o.candidate_count) {
                if let c = list[i] { candidates.append(String(cString: c)) }
            }
        }
        return .ok(CoreOutput(
            handled: o.handled != 0,
            commit: String(cString: o.commit),
            preedit: String(cString: o.preedit),
            cursorUTF16: Int(o.cursor_utf16),
            candidates: candidates,
            selected: Int(o.candidate_selected),
            columns: Int(o.candidate_columns),
            first: Int(o.candidate_first),
            total: Int(o.candidate_total),
            vertical: Int(o.candidate_vertical),
            englishRunStart: Int(o.english_run_utf16_start),
            englishRunLength: Int(o.english_run_utf16_len)
        ))
    }
}

/// One rerank job of the core (S7c-2a; docs/contracts/s7c2b-shell-rerank.md section 2.1). It holds candidate text and the left
/// context, so it is not `CustomStringConvertible` and is never logged (R2).
///
/// `@unchecked Sendable` rests on three invariants the shell keeps (the header's rules for one job):
/// 1. One run at a time: `run()` is called once, on one thread, by the shell's runner, which runs a single job at a time.
/// 2. `apply(on:)` only after `run()` returned: the runner hands the code back through `DispatchQueue.main.async`, which
///    orders the background run before the main-thread apply.
/// 3. The job is freed only in `deinit`, exactly once: the last reference may go on any thread (the header allows free on
///    any thread once run returned), and the runner's closure holds this wrapper, never the pointer, while run is running.
public final class RerankJob: @unchecked Sendable {
    private let job: OpaquePointer
    private let free: @Sendable (OpaquePointer) -> Void

    fileprivate init(_ job: OpaquePointer, free: @escaping @Sendable (OpaquePointer) -> Void) {
        self.job = job
        self.free = free
    }

    deinit { free(job) }

    /// `shanjie_rerank_run`, off the main thread: 0, or 4 (caught panic; nothing touched, no result kept).
    nonisolated func run() -> Int32 { shanjie_rerank_run(job) }

    /// `shanjie_engine_rerank_apply` on the main thread: `.ok` with the snapshot when the composition changed, `.failed(5)`
    /// for no change (stale, nothing to do, applied before), `.failed(4)` internal (the engine has reset itself).
    @MainActor func apply(on engine: CoreEngine) -> CoreResult {
        var out: UnsafeMutablePointer<ShanjieOutput>?
        return CoreEngine.take(shanjie_engine_rerank_apply(engine.handle, job, &out), out)
    }
}
