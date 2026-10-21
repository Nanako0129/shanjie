import Foundation

/// docs/contracts/about-models.md: the models this build bundles, read from the `models.tsv` in Resources (the same file
/// scripts/check-models.sh checks against the model files at build time and in check-app.sh).
public struct ModelRow: Equatable {
    public let file: String
    public let name: String      // 中文名
    public let code: String      // 對外名稱, e.g. SJ-LM-6-9.1M
}

public enum ModelList {
    /// Rows `file<TAB>SHA-256<TAB>中文名<TAB>對外名稱` in file order. No header, comments or blank lines exist in the file, so a line of
    /// any other shape (the build refuses one) makes the whole file unreadable: `[]`, and the About page shows no section.
    public static func parse(_ text: String) -> [ModelRow] {
        var rows: [ModelRow] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: false).dropLast(text.hasSuffix("\n") ? 1 : 0) {
            let f = line.split(separator: "\t", omittingEmptySubsequences: false)
            guard f.count == 4, !f.contains(where: \.isEmpty) else { return [] }
            rows.append(ModelRow(file: String(f[0]), name: String(f[2]), code: String(f[3])))
        }
        return rows
    }

    /// `[]` when the file is missing or unreadable (development runs, tests).
    public static func load(from url: URL?) -> [ModelRow] {
        guard let url, let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        return parse(text)
    }
}
