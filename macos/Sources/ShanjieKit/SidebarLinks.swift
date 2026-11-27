import Foundation

/// The three icon-only links at the bottom of the settings sidebar (docs/spec/macos-shell.md §9.2 左欄頁尾). The shell reads
/// this list; it lives here so the tests can check the URLs and symbols.
public enum SidebarLinks {
    public struct Item: Equatable, Sendable {
        public let name: String      // 中文名, used for the help text and the accessibility label
        public let symbol: String    // SF Symbol
        public let url: String
    }

    public static let all: [Item] = [
        Item(name: "網站", symbol: "globe", url: "https://shanjie.nyanako.com"),
        // The system has no GitHub mark; Syrtis uses the same symbol (TokenBar SettingsWindowView.swift).
        Item(name: "GitHub", symbol: "chevron.left.forwardslash.chevron.right", url: "https://github.com/Nanako0129/shanjie"),
        Item(name: "贊助", symbol: "heart", url: "https://www.patreon.com/cw/Nanako0129/membership"),
    ]
}
