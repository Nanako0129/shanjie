import XCTest
@testable import ShanjieKit

/// docs/spec/macos-shell.md §9.2 左欄頁尾: the three icon-only links, in order, with their URLs and SF Symbols.
final class SidebarLinksTests: XCTestCase {
    func testLinksAreTheWebsiteGitHubAndSponsorInOrder() {
        XCTAssertEqual(SidebarLinks.all, [
            .init(name: "網站", symbol: "globe", url: "https://shanjie.nyanako.com"),
            .init(name: "GitHub", symbol: "chevron.left.forwardslash.chevron.right", url: "https://github.com/Nanako0129/shanjie"),
            .init(name: "贊助", symbol: "heart", url: "https://www.patreon.com/cw/Nanako0129/membership"),
        ])
    }

    func testEveryURLParses() {
        for item in SidebarLinks.all { XCTAssertNotNil(URL(string: item.url), item.name) }
    }
}
