import Combine
import Foundation

/// The result of the last update check (docs/contracts/about-update.md section 1), shown on the 關於 page.
public enum UpdateResult: String, Equatable, Sendable {
    case upToDate, available, failed
}

/// Where the update settings and the last check live (contract section 2): the app's UserDefaults domain in the input method
/// (`DefaultsUpdateStore` in the app target), memory in the tests.
@MainActor
public protocol UpdateStore: AnyObject {
    /// 自動檢查更新, key `updateAutoCheck`; absent means on.
    var autoCheck: Bool? { get set }
    /// When the last check started, key `updateLastCheck`.
    var lastCheck: Date? { get set }
    /// The last result, key `updateStatus`: `by` (the version that checked), `result` (an `UpdateResult`), `available` (the
    /// newer version, when known).
    var status: [String: String]? { get set }
}

@MainActor
public final class MemoryUpdateStore: UpdateStore {
    public var autoCheck: Bool?
    public var lastCheck: Date?
    public var status: [String: String]?
    public init() {}
}

/// How a request to start Sparkle's information-only check went (answered by the app's `Updater`).
public enum ProbeStart: Sendable {
    /// The check runs and will report back through `Updates.finished`.
    case started
    /// A Sparkle session is already running (Sparkle would ignore the call and never report back): no check starts; `install`
    /// opens 善解更新 directly instead.
    case busy
    /// The updater is not running (it did not start, or this build does not start it): the check fails.
    case unavailable
}

/// The update state shared by the 關於 page and the input menu (contract sections 1 and 2). Sparkle stays in the app target
/// (`Updater`), which sets `probe` and `launchHelper` and reports each finished check through `finished`; the decisions (when
/// the background check runs, when 善解更新 may open, what the page and the menu show) are here, so the tests
/// reach them without Sparkle or the network.
@MainActor
public final class Updates: ObservableObject {
    /// At most one background check a day (contract section 1).
    public nonisolated static let interval: TimeInterval = 24 * 60 * 60

    /// The Info.plist key scripts/build-app.sh writes: `release` for the release workflow's build, `test` for an update test
    /// build, `local` for every other build. Only `release` and `test` start the updater, so a copy built and installed
    /// locally never reads the production feed.
    public nonisolated static let buildKey = "ShanjieUpdateBuild"

    /// The updater starts only when the process runs from an app bundle (never in `swift test` or a bare executable, which
    /// have no Info.plist with the feed and the key) built as a release or an update test build (`buildKey`).
    public nonisolated static func runsUpdater(bundleURL: URL, build: String?) -> Bool {
        bundleURL.pathExtension == "app" && (build == "release" || build == "test")
    }

    /// `CFBundleShortVersionString`; `0.0.0` (a dispatched dry run, a local build without a tag) and a missing value read 開發版.
    public nonisolated static func versionText(_ version: String?) -> String {
        guard let version, !version.isEmpty, version != "0.0.0" else { return "開發版" }
        return version
    }

    /// The background check's decision: only with 自動檢查更新 on, and only when no check started in the last day. A last check
    /// in the future (the clock was set back) does not hold checks off until the clock catches up.
    public nonisolated static func shouldCheck(enabled: Bool, lastCheck: Date?, now: Date) -> Bool {
        guard enabled else { return false }
        guard let lastCheck else { return true }
        let elapsed = now.timeIntervalSince(lastCheck)
        return elapsed >= interval || elapsed < 0
    }

    public let version: String
    @Published public private(set) var autoCheck: Bool
    @Published public private(set) var lastCheck: Date?
    @Published public private(set) var result: UpdateResult?
    /// The newer version Sparkle found, `nil` when none is known. The 更新到 X button and the menu item exist only while set.
    @Published public private(set) var available: String?
    /// A check started and has not reported back yet. The 立即檢查 and 更新到 X buttons and the menu item are disabled meanwhile.
    @Published public private(set) var checking = false

    /// Starts Sparkle's information-only check (no window).
    public var probe: () -> ProbeStart = { .unavailable }
    /// Closes the settings window. It floats above ordinary app windows (`SettingsWindowController.level`) and covered
    /// 善解更新's dialog in the VM run V1 (standalone-updater.md sections 1.1 and 2.3), so it goes before the helper opens.
    public var closeSettings: () -> Void = {}
    /// Opens 善解更新.app, where the download, the notes, the install and the relaunch happen (standalone-updater.md section
    /// 2.2). A second open while it runs only activates it (LaunchServices).
    public var launchHelper: () -> Void = {}

    private let store: UpdateStore
    /// `install` started the running check: open 善解更新 if it finds a newer version.
    private var installAfterCheck = false

    public init(store: UpdateStore, version: String) {
        self.store = store
        self.version = version
        autoCheck = store.autoCheck ?? true
        lastCheck = store.lastCheck
        // A result recorded by another version (the user updated, or installed another way) says nothing about this one.
        if let s = store.status, s["by"] == version, let r = s["result"].flatMap(UpdateResult.init(rawValue:)) {
            result = r
            available = s["available"]
        } else {
            store.status = nil
        }
    }

    public var versionText: String { Self.versionText(version) }

    public func setAutoCheck(_ on: Bool) {
        autoCheck = on
        store.autoCheck = on
    }

    /// The background check, called by the app's timer: checks only when `shouldCheck` says so; never records a failure.
    public func tick(now: Date = Date()) {
        guard Self.shouldCheck(enabled: autoCheck, lastCheck: lastCheck, now: now) else { return }
        _ = begin(now)
    }

    /// 立即檢查: always connects, whatever 自動檢查更新 says (contract section 1). Ignored while a check runs; an updater that is
    /// not running reads 檢查失敗 with the date.
    public func checkNow(now: Date = Date()) {
        if begin(now) == .unavailable { failNow(now) }
    }

    /// 更新到 X and the menu item: a silent check first, and 善解更新 only if it confirms a newer version (`finished`).
    /// Otherwise the state says 已是最新 or 檢查失敗 and no window opens. While a Sparkle session of the input method runs (its
    /// own probe), the press opens 善解更新 directly, which checks again by itself.
    public func install(now: Date = Date()) {
        guard !checking else { return }  // our own silent check runs and decides; no Sparkle window to bring up
        switch begin(now) {
        case .started: installAfterCheck = true
        case .busy: openHelper()
        case .unavailable: failNow(now)
        }
    }

    /// The start time is recorded only when a check really started, and counts for the daily limit whatever its result.
    private func begin(_ now: Date) -> ProbeStart {
        guard !checking else { return .busy }
        let s = probe()
        if s == .started {
            checking = true
            lastCheck = now
            store.lastCheck = now
        }
        return s
    }

    private func failNow(_ now: Date) {
        lastCheck = now
        store.lastCheck = now
        finished(.failed)
    }

    /// A check ended. A version already known stays known when a check fails or when a finished cycle reports a newer version
    /// without saying which; `nil` only ends the check.
    public func finished(_ r: UpdateResult?, version newer: String? = nil) {
        checking = false
        let wantsInstall = installAfterCheck
        installAfterCheck = false
        guard let r else { return }
        result = r
        switch r {
        case .available: if let newer { available = newer }
        case .upToDate: available = nil
        case .failed: break
        }
        var s = ["by": version, "result": r.rawValue]
        if let available { s["available"] = available }
        store.status = s
        if wantsInstall, r == .available, available != nil { openHelper() }
    }

    /// 更新到 X once a silent check confirmed a newer version, or a press while a Sparkle session runs: the settings window
    /// first, then the helper, in that order (section 2.3).
    private func openHelper() {
        closeSettings()
        launchHelper()
    }
}

/// Fixed strings of the 關於 page and the update menu item.
public enum UpdateText {
    public static let upToDate = "已是最新"
    public static func available(_ v: String) -> String { "有新版本 \(v)" }
    public static let failed = "檢查失敗"
    public static let checking = "檢查中…"
    public static func install(_ v: String) -> String { "更新到 \(v)" }
    public static func menu(_ v: String) -> String { "有新版本 \(v)，按此更新…" }

    /// The 關於 page's note on where this copy is installed and what updating it asks (standalone-updater.md section 2.3,
    /// the U1 wording: the user-folder note ends before the sentence that points to the installer's 「這台 Mac」, which U2 adds).
    /// Only the folder above the bundle decides: the system's Input Methods folder, or anything else (the user's).
    public static let locationSystem = "善解裝給這台 Mac 的所有帳號。按更新後，善解更新會先下載並驗證，再跳出系統的密碼框，文字是「善解更新 wants permission to update 善解輸入法.」。善解只會在你按下這裡的「更新」之後才要密碼。不是管理員的帳號，請管理員更新。"
    public static let locationUser = "善解只裝在你的帳號，更新不需要密碼。有程式開著安全輸入時，系統會暫時停用善解。"

    public static func location(bundleURL: URL) -> String {
        bundleURL.deletingLastPathComponent().path == "/Library/Input Methods" ? locationSystem : locationUser
    }

    public static func result(_ r: UpdateResult?, available: String?) -> String? {
        switch r {
        case .upToDate?: upToDate
        case .available?: available.map(Self.available) ?? upToDate
        case .failed?: failed
        case nil: nil
        }
    }
}
