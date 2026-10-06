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
    private let handle: OpaquePointer

    private init(handle: OpaquePointer) { self.handle = handle }

    /// `shanjie_engine_new`; on failure returns the code and nothing is allocated.
    static func make(dataDir: String, layout: UInt32) -> (CoreEngine?, Int32) {
        var out: OpaquePointer?
        let code = shanjie_engine_new(dataDir, layout, &out)
        guard code == 0, let out else { return (nil, code == 0 ? 4 : code) }
        return (CoreEngine(handle: out), 0)
    }

    isolated deinit { shanjie_engine_free(handle) }

    func loadLM(path: String) -> Int32 { shanjie_engine_load_lm(handle, path) }

    /// s3e: replace the punctuation alternatives; 0 on success, 2 on an invalid table (kept as before).
    func setPunctuation(_ table: String) -> Int32 { shanjie_engine_set_punctuation(handle, table) }

    // S4 (docs/contracts/s4-learning.md sections 2-4); each returns the C ABI code.

    /// `nil` means no left context (NULL).
    func setLeftContext(_ text: String?) -> Int32 {
        guard let text else { return shanjie_engine_set_left_context(handle, nil) }
        return text.withCString { shanjie_engine_set_left_context(handle, $0) }
    }

    /// sw: whether the demotion table applies; 0 on success (the core's default is on).
    func setDemote(_ on: Bool) -> Int32 { shanjie_engine_set_demote(handle, on ? 1 : 0) }

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

    /// s3b2 section 8.2 mouse pick: `index` is a position in the last output's candidates. Code 2
    /// when the candidates are closed or `index` is outside that output (state unchanged).
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
    private static func take(_ code: Int32, _ out: UnsafeMutablePointer<ShanjieOutput>?) -> CoreResult {
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
            total: Int(o.candidate_total)
        ))
    }
}
