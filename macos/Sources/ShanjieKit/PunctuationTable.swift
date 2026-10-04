import Foundation

/// docs/contracts/s3e-punctuation-candidates.md: Apple's punctuation candidate table, read at run
/// time from the user's own macOS (never copied into the repository or the release), converted to
/// the core's line format for `shanjie_engine_set_punctuation`.
public enum PunctuationTable {
    /// A private framework's resource: may move or change format in a system update (unverified
    /// across versions; present on macOS 27.0.1). Without it the core keeps its built-in table.
    public static let systemURL = URL(fileURLWithPath:
        "/System/Library/PrivateFrameworks/CoreChineseEngine.framework/Versions/A/Resources/CIMPunctuationCandidates.plist")

    /// The core's limits on a table (s3e section 3), applied here too.
    static let maxBytes = 64 * 1024, maxLines = 1000

    /// Lines `mark\talt\talt…`, keys sorted. Validation matches the core's, so the core never
    /// rejects what this produces: a key must be one Unicode scalar; an entry whose key or any
    /// alternative is empty or holds a tab, a line feed or NUL (checked per Unicode scalar: a CRLF
    /// is one Swift Character) is skipped whole; non-string array elements are dropped; an entry
    /// left without alternatives is skipped. `nil` when the file is unreadable, is not a
    /// dictionary, yields no entry, or exceeds the core's size or line limits.
    public static func load(from url: URL) -> String? {
        guard let data = try? Data(contentsOf: url),
              let dict = (try? PropertyListSerialization.propertyList(from: data, format: nil)) as? [String: Any]
        else { return nil }
        let bad: (String) -> Bool = { $0.isEmpty || $0.unicodeScalars.contains { $0 == "\t" || $0 == "\n" || $0 == "\0" } }
        var lines: [String] = []
        for key in dict.keys.sorted() {
            guard key.unicodeScalars.count == 1, !bad(key), let values = dict[key] as? [Any] else { continue }
            let alternatives = values.compactMap { $0 as? String }
            guard !alternatives.isEmpty, !alternatives.contains(where: bad) else { continue }
            lines.append(([key] + alternatives).joined(separator: "\t"))
        }
        let table = lines.joined(separator: "\n") + "\n"
        guard !lines.isEmpty, lines.count <= maxLines, table.utf8.count <= maxBytes else { return nil }
        return table
    }
}
