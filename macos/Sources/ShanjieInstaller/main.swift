import AppKit
import ShanjieInstall

// docs/contracts/s3c-installer.md: 安裝善解輸入法.app. One window; the input method ships inside as
// Resources/shanjie-<version>.zip (the release asset itself), is extracted without quarantine,
// copied into place by the bundled install-ime.sh (files and processes only), then registered and
// enabled in this process against the installed copy. Writes nothing of its own; no network.

/// Traditional Chinese when the user's first preferred language is (zh-Hant, zh-TW, zh-HK,
/// zh-MO), English otherwise. The strings live here, not in .lproj files (contract section 1).
func L(_ zh: String, _ en: String) -> String {
    let first = Locale.preferredLanguages.first ?? ""
    return ["zh-Hant", "zh-TW", "zh-HK", "zh-MO"].contains(where: first.hasPrefix) ? zh : en
}

enum Paths {
    /// From the user database, not $HOME: the same source the script's HOME is set from.
    static let home: String = {
        guard let pw = getpwuid(getuid()), let dir = pw.pointee.pw_dir else { return NSHomeDirectory() }
        return String(cString: dir)
    }()
    static let installed = URL(fileURLWithPath: home + "/Library/Input Methods/善解輸入法.app")
    static let bundledVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
    static let zip = Bundle.main.url(forResource: "shanjie-\(bundledVersion)", withExtension: "zip")
    static let script = Bundle.main.url(forResource: "install-ime", withExtension: "sh")
    static let licenses = Bundle.main.resourceURL?.appendingPathComponent("LICENSES")

    static func installedVersion() -> String? {
        Bundle(url: installed)?.infoDictionary?["CFBundleShortVersionString"] as? String
    }
}

/// Runs a process with an explicit environment and argument array; stderr is read to EOF before
/// waiting (a full pipe would otherwise block the child) and truncated for display.
func run(_ executable: String, _ arguments: [String], environment: [String: String]) -> (status: Int32, stderr: String) {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    process.environment = environment
    process.standardOutput = FileHandle.nullDevice
    let pipe = Pipe()
    process.standardError = pipe
    do { try process.run() } catch { return (-1, L("無法執行 ", "Cannot run ") + executable) }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    // The end, not the start: install-ime.sh prints warnings first and its error line last.
    let text = String(decoding: data.suffix(4096), as: UTF8.self)
    return (process.terminationStatus, text)
}

/// Extracts the bundled zip without quarantine and runs install-ime.sh on it with
/// SHANJIE_INSTALL_SKIP_REGISTER=1 and no inherited environment (section 2.2).
func copyFiles() -> String? {
    guard let zip = Paths.zip, let script = Paths.script else {
        return L("安裝程式不完整：找不到內附的輸入法或安裝腳本。", "The installer is incomplete: the bundled input method or script is missing.")
    }
    let tmp = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("shanjie-installer-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: tmp) }
    do { try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true) } catch {
        return L("無法建立暫存資料夾。", "Cannot create a temporary folder.")
    }
    let minimal = ["HOME": Paths.home, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"]
    let unzip = run("/usr/bin/ditto", ["-x", "-k", "--noqtn", zip.path, tmp.path], environment: minimal)
    guard unzip.status == 0 else { return L("解壓失敗。\n", "Extraction failed.\n") + unzip.stderr }
    var env = minimal
    env["SHANJIE_INSTALL_SKIP_REGISTER"] = "1"
    let app = tmp.appendingPathComponent("善解輸入法.app").path
    let result = run("/bin/bash", [script.path, app], environment: env)
    guard result.status == 0 else { return L("複製失敗。\n", "Copying failed.\n") + result.stderr }
    return nil
}

@MainActor
final class Controller: NSObject, NSApplicationDelegate, NSWindowDelegate {
    // Section 2.4: poll every 0.5 s for at most 30 s on the main run loop.
    private let pollInterval: TimeInterval = 0.5
    private let pollLimit = 60

    private var window: NSWindow!
    private let titleLabel = NSTextField(labelWithString: "")
    private let bodyLabel = NSTextField(wrappingLabelWithString: "")
    private let statusText = NSTextView()
    private var isBusy = false
    private let spinner = NSProgressIndicator()
    private let primary = NSButton(title: "", target: nil, action: nil)
    private let secondary = NSButton(title: "", target: nil, action: nil)
    private let close = NSButton(title: "", target: nil, action: nil)
    private let licenses = NSButton(title: "", target: nil, action: nil)
    private var plan = InstallerPlan.install
    private var primaryAction: () -> Void = {}
    private var secondaryAction: () -> Void = {}

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 300),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = L("安裝善解輸入法", "Shanjie Installer")
        window.delegate = self
        titleLabel.font = .boldSystemFont(ofSize: 17)
        statusText.isEditable = false
        statusText.isSelectable = true
        statusText.drawsBackground = false
        statusText.textColor = .secondaryLabelColor
        statusText.font = .systemFont(ofSize: NSFont.systemFontSize)
        let statusScroll = NSScrollView()
        statusScroll.documentView = statusText
        statusScroll.hasVerticalScroller = true
        statusScroll.drawsBackground = false
        statusScroll.borderType = .noBorder
        // A long script error scrolls here instead of pushing the buttons out of the window.
        statusScroll.heightAnchor.constraint(equalToConstant: 96).isActive = true
        statusText.autoresizingMask = [.width]
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false
        primary.keyEquivalent = "\r"
        for (button, selector) in [(primary, #selector(primaryPressed)), (secondary, #selector(secondaryPressed)),
                                   (close, #selector(closePressed)), (licenses, #selector(licensesPressed))] {
            button.target = self
            button.action = selector
            button.bezelStyle = .push
        }
        licenses.title = L("顯示授權", "Licenses")
        let buttons = NSStackView(views: [licenses, NSView(), close, secondary, primary])
        buttons.orientation = .horizontal
        let status = NSStackView(views: [spinner, statusScroll])
        status.orientation = .horizontal
        status.alignment = .top
        let stack = NSStackView(views: [titleLabel, bodyLabel, status, NSView(), buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.edgeInsets = NSEdgeInsets(top: 20, left: 20, bottom: 20, right: 20)
        for view in [bodyLabel, status, buttons] {
            view.widthAnchor.constraint(equalTo: stack.widthAnchor, constant: -40).isActive = true
        }
        window.contentView = stack
        installMenu()
        showPlan()
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    // While copying or enabling, neither closing the window nor quitting may cut the work short:
    // the script would be orphaned and nothing would be enabled, with no message.
    func windowShouldClose(_ sender: NSWindow) -> Bool { !isBusy }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        isBusy ? .terminateCancel : .terminateNow
    }

    /// Quit (Cmd-Q) and Copy (Cmd-C, for the error text); nothing else.
    private func installMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        appItem.submenu = NSMenu()
        appItem.submenu?.addItem(withTitle: L("結束", "Quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem()
        editItem.submenu = NSMenu(title: L("編輯", "Edit"))
        editItem.submenu?.addItem(withTitle: L("拷貝", "Copy"), action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editItem.submenu?.addItem(withTitle: L("全選", "Select All"), action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        main.addItem(appItem)
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    private var status: String {
        get { statusText.string }
        set { statusText.string = newValue }
    }

    // MARK: states

    private func showPlan() {
        plan = InstallerPlan.decide(installed: Paths.installedVersion(), bundled: Paths.bundledVersion)
        titleLabel.stringValue = L("善解輸入法 ", "Shanjie ") + Paths.bundledVersion
        let license = L("授權：程式 Apache-2.0；詞庫 MIT（小麥注音）；語言模型與擴充詞庫 CC BY-SA 4.0。",
                        "License: program Apache-2.0; lexicon MIT (McBopomofo); language model and added words CC BY-SA 4.0.")
        let where_ = L("裝到 ~/Library/Input Methods（只有你的帳號），不需要管理員密碼。執行中的舊版會被結束，上一版保留為 .shanjie-previous。",
                       "Installs into ~/Library/Input Methods (your account only); no administrator password. A running older copy is stopped and kept as .shanjie-previous.")
        status = ""
        switch plan {
        case .install:
            bodyLabel.stringValue = where_ + "\n" + license
            setButtons(primary: L("安裝", "Install"), primaryAction: { [weak self] in self?.copyThenEnable() })
        case .update:
            bodyLabel.stringValue = L("已安裝較舊的版本。", "An older version is installed. ") + where_ + "\n" + license
            setButtons(primary: L("更新", "Update"), primaryAction: { [weak self] in self?.copyThenEnable() })
        case .enableSame:
            bodyLabel.stringValue = L("這個版本已經安裝。按「啟用」讓系統啟用它（登出再登入後重開安裝程式時用這個）。「重新安裝」會把目前這份當成上一版保留，取代原本保留的上一版。",
                                      "This version is already installed. Enable lets the system turn it on (use it after logging out and back in). Reinstall keeps the current copy as the previous version, replacing the one kept before.")
            setButtons(primary: L("啟用", "Enable"), primaryAction: { [weak self] in self?.enable() },
                       secondary: L("重新安裝", "Reinstall"), secondaryAction: { [weak self] in self?.copyThenEnable() })
        case .enableNewer:
            bodyLabel.stringValue = L("已安裝的版本比這個安裝程式新，不會降版。可以按「啟用」讓系統啟用已安裝的那一份。",
                                      "A newer version is installed; this installer does not downgrade. Enable turns on the installed copy.")
            setButtons(primary: L("啟用", "Enable"), primaryAction: { [weak self] in self?.enable() })
        }
        // Only copying needs the bundled zip and script; enabling an installed copy does not.
        if Paths.zip == nil || Paths.script == nil || Paths.bundledVersion.isEmpty {
            if plan.copiesFiles {
                fail(L("安裝程式不完整：找不到內附的輸入法或安裝腳本。", "The installer is incomplete: the bundled input method or script is missing."))
            } else {
                secondary.isHidden = true
            }
        }
    }

    private func setButtons(primary title: String?, primaryAction: @escaping () -> Void = {},
                            secondary secondTitle: String? = nil, secondaryAction: @escaping () -> Void = {},
                            closeTitle: String = L("取消", "Cancel")) {
        primary.isHidden = title == nil
        primary.title = title ?? ""
        primary.isEnabled = true
        self.primaryAction = primaryAction
        secondary.isHidden = secondTitle == nil
        secondary.title = secondTitle ?? ""
        secondary.isEnabled = true
        self.secondaryAction = secondaryAction
        close.title = closeTitle
        close.isEnabled = true
    }

    private func busy(_ text: String) {
        isBusy = true
        status = text
        spinner.startAnimation(nil)
        primary.isEnabled = false
        secondary.isEnabled = false
        close.isEnabled = false
    }

    private func succeed(note: String? = nil) {
        isBusy = false
        spinner.stopAnimation(nil)
        status = L("安裝完成。從選單列的輸入法選單選「善解輸入法」。", "Installed. Choose Shanjie (善解輸入法) in the input menu in the menu bar.")
            + (note.map { "\n" + $0 } ?? "")
        setButtons(primary: L("完成", "Done"), primaryAction: { NSApp.terminate(nil) }, closeTitle: "")
        close.isHidden = true
    }

    private func needLogout() {
        isBusy = false
        spinner.stopAnimation(nil)
        status = L("還差一步：請登出再登入，然後再打開這個安裝程式，按「啟用」。",
                                    "One more step: log out and log back in, then open this installer again and choose Enable.")
        setButtons(primary: L("重新檢查", "Check Again"), primaryAction: { [weak self] in self?.enable() },
                   closeTitle: L("完成", "Done"))
    }

    private func fail(_ detail: String) {
        isBusy = false
        spinner.stopAnimation(nil)
        status = detail
        setButtons(primary: nil, closeTitle: L("完成", "Done"))
    }

    // MARK: actions

    @objc private func primaryPressed() { primaryAction() }
    @objc private func secondaryPressed() { secondaryAction() }
    @objc private func closePressed() { NSApp.terminate(nil) }
    @objc private func licensesPressed() { if let url = Paths.licenses { NSWorkspace.shared.open(url) } }

    private func copyThenEnable() {
        busy(L("正在複製…", "Copying…"))
        // The controller lives as long as the app (it is the application delegate).
        DispatchQueue.global(qos: .userInitiated).async {
            let error = copyFiles()
            DispatchQueue.main.async {
                if let error { self.fail(error) } else { self.enable() }
            }
        }
    }

    /// Section 2.3: against the installed copy, with its own bundle ID and preference domain.
    private func enable() {
        busy(L("正在啟用…", "Enabling…"))
        guard let bundleID = Bundle(url: Paths.installed)?.bundleIdentifier,
              let defaults = UserDefaults(suiteName: bundleID) else {
            fail(L("找不到已安裝的善解輸入法。", "The installed input method was not found."))
            return
        }
        let result = Registration.run(bundleURL: Paths.installed, bundleID: bundleID, defaults: defaults)
        switch result.outcome {
        case .done: succeed(note: result.legacyDisableFailed ? Self.legacyNote : nil)
        case .modeNotListed: needLogout()
        case .registrationFailed, .enableFailed:
            fail(L("系統拒絕啟用輸入法。可以到「系統設定 → 鍵盤 → 輸入方式」手動加入「善解輸入法」。",
                   "The system refused to enable the input method. Add Shanjie in System Settings > Keyboard > Input Sources."))
        case .notAccepted: poll(bundleID: bundleID)
        }
    }

    private static let legacyNote = L("如果輸入方式清單裡還有舊版的「善解（標準）」或「善解（倚天）」，請到「系統設定 → 鍵盤 → 輸入方式」移除。",
                                      "If the old Shanjie (Standard) or Shanjie (ETen) entries are still listed, remove them in System Settings > Keyboard > Input Sources.")

    private func poll(bundleID: String) {
        status = L("正在等系統啟用輸入法。如果系統跳出視窗，請允許「善解輸入法」。",
                                    "Waiting for the system to turn the input method on. If it asks, allow Shanjie.")
        var ticks = 0
        Timer.scheduledTimer(withTimeInterval: pollInterval, repeats: true) { [weak self] timer in
            MainActor.assumeIsolated {
                guard let self else { timer.invalidate(); return }
                ticks += 1
                if Registration.isAccepted(bundleID: bundleID) {
                    timer.invalidate()
                    // Once accepted, a second run finishes the steps after the check (ETen
                    // carry-over, disabling the modes of earlier versions).
                    let again = UserDefaults(suiteName: bundleID).map {
                        Registration.run(bundleURL: Paths.installed, bundleID: bundleID, defaults: $0)
                    }
                    let finished = again.map { $0.outcome == .done && !$0.legacyDisableFailed } ?? false
                    self.succeed(note: finished ? nil : Self.legacyNote)
                } else if ticks >= self.pollLimit {
                    timer.invalidate()
                    self.needLogout()
                }
            }
        }
    }
}

let app = NSApplication.shared
let controller = MainActor.assumeIsolated { Controller() }
app.delegate = controller
app.setActivationPolicy(.regular)
app.run()
