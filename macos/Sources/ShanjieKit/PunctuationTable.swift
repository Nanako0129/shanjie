import Foundation

/// docs/contracts/s3e-punctuation-candidates.md: Apple's punctuation candidate table, read at run
/// time from the user's own macOS (never copied into the repository or the release), converted to
/// the core's line format for `shanjie_engine_set_punctuation`.
public enum PunctuationTable {
    /// A private framework's resource: may move or change format in a system update (unverified
    /// across versions; present on macOS 27.0.1). Without it the core keeps its built-in table.
    public static let systemURL = URL(fileURLWithPath:
        "/System/Library/PrivateFrameworks/CoreChineseEngine.framework/Versions/A/Resources/CIMPunctuationCandidates.plist")

    /// Lines `mark\talt\talt…`, keys sorted. Validation matches the core's, so the core never
    /// rejects what this produces: a key must be one Unicode scalar; an entry whose key or any
    /// alternative is empty or holds a tab, a newline or NUL is skipped whole; non-string array
    /// elements are dropped; an entry left without alternatives is skipped. `nil` when the file is
    /// unreadable, is not a dictionary, or yields no entry.
    public static func load(from url: URL) -> String? {
        guard let data = try? Data(contentsOf: url),
              let dict = (try? PropertyListSerialization.propertyList(from: data, format: nil)) as? [String: Any]
        else { return nil }
        let bad: (String) -> Bool = { $0.isEmpty || $0.contains("\t") || $0.contains("\n") || $0.contains("\0") }
        var lines: [String] = []
        for key in dict.keys.sorted() {
            guard key.unicodeScalars.count == 1, !bad(key), let values = dict[key] as? [Any] else { continue }
            let alternatives = values.compactMap { $0 as? String }
            guard !alternatives.isEmpty, !alternatives.contains(where: bad) else { continue }
            lines.append(([key] + alternatives).joined(separator: "\t"))
        }
        return lines.isEmpty ? nil : lines.joined(separator: "\n") + "\n"
    }
}
