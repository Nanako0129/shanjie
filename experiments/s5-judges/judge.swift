// S5j Apple judge (docs/contracts/s5j-judges.md §3). Reads a TSV, appends picks to another TSV.
//   judge <in.tsv> <out.tsv> [--force-fail]
// in : id <TAB> context <TAB> cand1 <TAB> ... <TAB> candK      (K >= 2; context may be empty)
// out: id <TAB> pick(1..K, 0 if none) <TAB> latency_ms <TAB> status(ok|blocked|unparsable|error)
// Sentence text never reaches stdout/stderr: only counts and fixed strings. --force-fail replaces every
// model answer with a non-numeric string (exercises the unparsable path).
import Foundation
import FoundationModels

let INSTRUCTIONS = "你是台灣繁體中文注音輸入法的選字助手。使用者用注音打了一句話，下面列出讀音相同的候選句子。選出使用者最可能想打、最符合台灣日常用法的那一句，只回答編號。"

func parsePick(_ s: String, _ k: Int) -> Int? {
    for ch in s { if let d = ch.wholeNumberValue, ch.isASCII, d >= 1, d <= k { return d } }
    return nil
}

@main struct Judge {
    static func main() async {
        let a = CommandLine.arguments
        guard a.count >= 3 else { print("usage: judge <in> <out> [--force-fail]"); exit(2) }
        let force = a.contains("--force-fail")
        guard case .available = SystemLanguageModel(guardrails: .permissiveContentTransformations).availability else {
            print("model unavailable"); exit(3)
        }
        guard let text = try? String(contentsOfFile: a[1], encoding: .utf8) else { print("cannot read input"); exit(2) }
        guard let out = FileHandle(forWritingAtPath: a[2]) ?? { FileManager.default.createFile(atPath: a[2], contents: nil); return FileHandle(forWritingAtPath: a[2]) }() else {
            print("cannot open output"); exit(2)
        }
        out.seekToEndOfFile()
        var counts: [String: Int] = [:]
        for (n, line) in text.split(separator: "\n", omittingEmptySubsequences: true).enumerated() {
            let f = line.split(separator: "\t", maxSplits: .max, omittingEmptySubsequences: false).map(String.init)
            guard f.count >= 4, Int(f[0]) != nil else { print("bad input line \(n + 1)"); exit(2) }
            let cands = Array(f[2...]), k = cands.count
            var prompt = f[1].isEmpty ? "" : "前文：\(f[1])\n"
            prompt += cands.enumerated().map { "\($0.offset + 1). \($0.element)" }.joined(separator: "\n")
            prompt += "\n只回答一個數字（1 到 \(k)）。"
            let model = SystemLanguageModel(guardrails: .permissiveContentTransformations)
            let session = LanguageModelSession(model: model, instructions: INSTRUCTIONS)
            let t = Date()
            var pick = 0, status = "ok"
            do {
                let r = try await session.respond(to: prompt, options: GenerationOptions(sampling: .greedy, maximumResponseTokens: 8))
                if let p = parsePick(force ? "無" : r.content, k) { pick = p } else { status = "unparsable" }
            } catch let e as LanguageModelSession.GenerationError {
                if case .guardrailViolation = e { status = "blocked" } else { status = "error" }
            } catch { status = "error" }
            let ms = Int(Date().timeIntervalSince(t) * 1000)
            counts[status, default: 0] += 1
            out.write("\(f[0])\t\(pick)\t\(ms)\t\(status)\n".data(using: .utf8)!)
        }
        print("done", counts.sorted { $0.key < $1.key }.map { "\($0.key)=\($0.value)" }.joined(separator: " "))
    }
}
