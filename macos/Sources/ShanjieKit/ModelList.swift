import Foundation

/// docs/contracts/about-models.md: the models this build bundles, read from the `models.tsv` in Resources (the same file
/// scripts/check-models.sh checks against the model files at build time and in check-app.sh).
public struct ModelRow: Equatable {
    public let file: String
    public let name: String      // 中文名
    public let code: String      // 對外名稱, e.g. SJ-LM-7-9.7M
}

public enum ModelList {
    /// Rows `file<TAB>SHA-256<TAB>中文名<TAB>對外名稱` in file order. The format rules are scripts/check-models.sh's (which the
    /// build and check-app.sh run on this file): it ends with a newline, holds no CR, and every line is four non-empty fields;
    /// the shell check also requires a 64-hex hash and no file listed twice, which this parser does not repeat. Anything else makes the whole file unreadable: `[]`, and the About page shows no section.
    public static func parse(_ text: String) -> [ModelRow] {
        guard text.hasSuffix("\n"), !text.unicodeScalars.contains("\r") else { return [] }
        var rows: [ModelRow] = []
        for line in text.dropLast().components(separatedBy: "\n") {
            let f = line.components(separatedBy: "\t")
            guard f.count == 4, !f.contains(where: \.isEmpty) else { return [] }
            rows.append(ModelRow(file: f[0], name: f[2], code: f[3]))
        }
        return rows
    }

    /// `[]` when the file is missing or unreadable (development runs, tests).
    public static func load(from url: URL?) -> [ModelRow] {
        guard let url, let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        return parse(text)
    }
}
