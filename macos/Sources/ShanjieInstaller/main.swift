import AppKit
import CoreText
import ShanjieInstall

// docs/contracts/s3c-installer.md and installer-v2.md: 安裝善解輸入法.app. One window that walks
// through four steps (welcome, install, activate, try). The input method ships inside as
// Resources/shanjie-<version>.zip (the release asset itself), is extracted without quarantine,
// copied into place by the bundled install-ime.sh (files and processes only), then registered and
// enabled in this process against the installed copy. Writes nothing of its own and no network;
// the one exception is `--render-steps <dir>` (installer-v2 section 1.4), run only by the
// maintainer, which draws every screen to PNG files in that folder and exits.

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

/// A failure shown to the user: a plain sentence, and the technical detail behind it if any.
struct Failure: Error {
    var message: String
    var detail: String?
}

/// Extracts the bundled zip without quarantine and runs install-ime.sh on it with
/// SHANJIE_INSTALL_SKIP_REGISTER=1 and no inherited environment (s3c section 2.2).
func copyFiles() -> Failure? {
    guard let zip = Paths.zip, let script = Paths.script else { return Screens.incomplete }
    let tmp = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("shanjie-installer-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: tmp) }
    do { try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true) } catch {
        return Failure(message: L("無法建立暫存資料夾。", "Cannot create a temporary folder."))
    }
    let minimal = ["HOME": Paths.home, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"]
    let unzip = run("/usr/bin/ditto", ["-x", "-k", "--noqtn", zip.path, tmp.path], environment: minimal)
    guard unzip.status == 0 else {
        return Failure(message: L("解開內附的輸入法時失敗了。", "Extracting the bundled input method failed."), detail: unzip.stderr)
    }
    var env = minimal
    env["SHANJIE_INSTALL_SKIP_REGISTER"] = "1"
    let app = tmp.appendingPathComponent("善解輸入法.app").path
    let result = run("/bin/bash", [script.path, app], environment: env)
    guard result.status == 0 else {
        return Failure(message: L("把輸入法複製到你的帳號時失敗了。", "Copying the input method into your account failed."), detail: result.stderr)
    }
    return nil
}

// MARK: - Look

/// Visual parameters (installer-v2 section 1.3). Colours and the seal follow the site
/// (site/index.html: --seal #B83A2E light, #D4553F dark; a rounded square with a ring in the page
/// colour and 解 in the serif face). Sizes are first-round values for the user to judge.
enum Look {
    static let windowSize = NSSize(width: 560, height: 430)
    static let margin: CGFloat = 28
    /// The site's seal is 108 px; here it sits beside the title, so it is smaller and every seal
    /// measurement keeps the site's proportion to the 108 px original.
    static let sealSize: CGFloat = 56
    static func sealScaled(_ sitePixels: CGFloat) -> CGFloat { sealSize * sitePixels / 108 }
    static let titleFont = NSFont(name: "STSongti-TC-Bold", size: 24) ?? .boldSystemFont(ofSize: 24)
    static let bodyFont = NSFont.systemFont(ofSize: 13)
    static let smallFont = NSFont.systemFont(ofSize: 11)
    static let tryFont = NSFont.systemFont(ofSize: 17)
    static let dotSize: CGFloat = 8
    static let seal = NSColor(name: nil) { appearance in
        appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            ? NSColor(srgbRed: 0xD4 / 255, green: 0x55 / 255, blue: 0x3F / 255, alpha: 1)
            : NSColor(srgbRed: 0xB8 / 255, green: 0x3A / 255, blue: 0x2E / 255, alpha: 1)
    }
}

/// The 解 seal: a rounded square in the seal colour, an inner ring and the glyph in the window
/// colour. The glyph is centred on its ink, not its advance box (a CJK glyph's box sits low).
final class SealView: NSView {
    override var intrinsicContentSize: NSSize { NSSize(width: Look.sealSize, height: Look.sealSize) }

    override func draw(_ dirtyRect: NSRect) {
        let ink = NSColor.windowBackgroundColor
        let radius = Look.sealScaled(14)
        Look.seal.setFill()
        NSBezierPath(roundedRect: bounds, xRadius: radius, yRadius: radius).fill()
        let inset = Look.sealScaled(5) + Look.sealScaled(2.5) / 2
        let ring = NSBezierPath(roundedRect: bounds.insetBy(dx: inset, dy: inset), xRadius: radius - inset, yRadius: radius - inset)
        ring.lineWidth = Look.sealScaled(2.5)
        ink.setStroke()
        ring.stroke()

        guard let context = NSGraphicsContext.current?.cgContext else { return }
        let size = Look.sealScaled(62)
        let font = NSFont(name: "STSongti-TC-Bold", size: size) ?? .boldSystemFont(ofSize: size)
        let line = CTLineCreateWithAttributedString(NSAttributedString(string: "解", attributes: [.font: font, .foregroundColor: ink]))
        // Image bounds are relative to the current text position, so measure from the origin.
        context.textMatrix = .identity
        context.textPosition = .zero
        let box = CTLineGetImageBounds(line, context)
        context.textPosition = CGPoint(x: bounds.midX - box.midX, y: bounds.midY - box.midY)
        CTLineDraw(line, context)
    }
}

/// Four dots with labels: the current step filled in the seal colour, earlier (or skipped) steps
/// filled grey, later steps hollow.
final class StepIndicator: NSStackView {
    private var dots: [DotView] = []
    private var labels: [NSTextField] = []

    convenience init(titles: [String]) {
        self.init(frame: .zero)
        orientation = .horizontal
        spacing = 18
        var items: [NSView] = []
        for title in titles {
            let dot = DotView()
            let label = NSTextField(labelWithString: title)
            label.font = Look.smallFont
            let item = NSStackView(views: [dot, label])
            item.spacing = 6
            items.append(item)
            dots.append(dot)
            labels.append(label)
        }
        // A spacer that takes all extra width, so the dots stay together when the row is wide.
        let spacer = NSView()
        spacer.setContentHuggingPriority(.init(1), for: .horizontal)
        distribution = .fill
        setViews(items + [spacer], in: .leading)
    }

    func show(current: Int) {
        for (i, (dot, label)) in zip(dots, labels).enumerated() {
            dot.state = i == current ? .current : i < current ? .done : .todo
            label.textColor = i == current ? .labelColor : .secondaryLabelColor
            label.font = i == current ? .boldSystemFont(ofSize: Look.smallFont.pointSize) : Look.smallFont
        }
    }

    final class DotView: NSView {
        enum State { case current, done, todo }
        var state = State.todo { didSet { needsDisplay = true } }
        override var intrinsicContentSize: NSSize { NSSize(width: Look.dotSize, height: Look.dotSize) }
        override func draw(_ dirtyRect: NSRect) {
            let circle = NSBezierPath(ovalIn: bounds.insetBy(dx: 0.75, dy: 0.75))
            switch state {
            case .current: Look.seal.setFill(); circle.fill()
            case .done: NSColor.tertiaryLabelColor.setFill(); circle.fill()
            case .todo: circle.lineWidth = 1.5; NSColor.tertiaryLabelColor.setStroke(); circle.stroke()
            }
        }
    }
}

// MARK: - Screens

/// What one screen shows. Built only from its parameters, so the real flow and `--render-steps`
/// draw the same thing (installer-v2 section 1.4).
struct Screen {
    /// 0 welcome, 1 install, 2 activate, 3 try. Earlier steps show as finished, including a
    /// skipped activate step.
    var step: Int
    var title: String
    var body: String
    var seal = false
    var status: String?
    var busy = false
    var detail: String?
    var details: String?
    var tryIt = false
    var primary: String?
    var secondary: String?
    var close: String?
}

enum Screens {
    static let license = L("授權：程式 Apache-2.0；詞庫 MIT（小麥注音）；語言模型與擴充詞庫 CC BY-SA 4.0。",
                           "License: program Apache-2.0; lexicon MIT (McBopomofo); language model and added words CC BY-SA 4.0.")
    static let incomplete = Failure(message: L("安裝程式不完整：找不到內附的輸入法。請重新下載安裝程式。",
                                               "The installer is incomplete: the bundled input method is missing. Download the installer again."))

    static func welcome(plan: InstallerPlan, installed: String?, bundled: String) -> Screen {
        let account = L("只裝在你的帳號，不需要管理員密碼。", "Installs for your account only; no administrator password needed.")
        let line: String
        var primary = L("安裝", "Install")
        var secondary: String?
        switch plan {
        case .install:
            line = account
        case .update:
            line = L("會從 \(installed ?? "") 更新到 \(bundled)。", "Updates \(installed ?? "") to \(bundled). ") + account
            primary = L("更新", "Update")
        case .enableSame:
            line = L("這個版本已經裝好了。按「啟用」讓系統開始使用它；登出再登入後回到這裡，也是按這個。",
                     "This version is already installed. Choose Enable to let the system use it, also after logging out and back in.")
            primary = L("啟用", "Enable")
            secondary = L("重新安裝", "Reinstall")
        case .enableNewer:
            line = L("已經裝了更新的版本（\(installed ?? "")），不會降版。按「啟用」讓系統開始使用已裝好的那一份。",
                     "A newer version (\(installed ?? "")) is installed; this installer does not downgrade. Choose Enable to use it.")
            primary = L("啟用", "Enable")
        }
        let details = L("安裝位置：~/Library/Input Methods（只有你的帳號）。\n執行中的舊版會被結束，上一版保留在同一個資料夾的 .shanjie-previous。\n",
                        "Installs into ~/Library/Input Methods (your account only).\nA running older copy is stopped; the previous version is kept there as .shanjie-previous.\n") + license
        return Screen(step: 0, title: L("善解輸入法 ", "Shanjie ") + bundled,
                      body: L("開源的 macOS 注音輸入法。", "An open-source Zhuyin input method for macOS.") + "\n" + line,
                      seal: true, details: details, primary: primary, secondary: secondary, close: L("取消", "Cancel"))
    }

    static func working(_ status: String) -> Screen {
        Screen(step: 1, title: L("正在安裝", "Installing"), body: "", status: status, busy: true)
    }

    static let copying = L("正在複製…", "Copying…")
    static let enabling = L("正在請系統啟用…", "Asking the system to turn it on…")
    static let waiting = L("正在等系統啟用。如果系統跳出視窗，請允許「善解輸入法」。",
                           "Waiting for the system to turn it on. If it asks, allow Shanjie.")

    static func failed(_ failure: Failure) -> Screen {
        Screen(step: 1, title: L("沒有裝好", "Not installed"), body: failure.message, detail: failure.detail, close: L("完成", "Done"))
    }

    static let activate = Screen(
        step: 2, title: L("還差一步：登出再登入", "One more step: log out and back in"),
        body: L("系統要重新登入後，才會接受新的輸入法。\n\n1. 從蘋果選單選「登出」。\n2. 再登入。\n3. 重新打開這個安裝程式，按「啟用」。",
                "The system accepts a new input method only after you log in again.\n\n1. Choose Log Out from the Apple menu.\n2. Log back in.\n3. Open this installer again and choose Enable."),
        primary: L("重新檢查", "Check Again"), close: L("完成", "Done"))

    static func tryIt(note: String?) -> Screen {
        let hint = L("之後從選單列的輸入法選單，或按 Caps Lock、⌃空白鍵切換到善解。",
                     "Later, switch from the input menu in the menu bar, or with Caps Lock or ⌃Space.")
        return Screen(step: 3, title: L("✓ 已經裝好了", "✓ Installed"),
                      body: L("在下面試打看看。", "Try it below.") + "\n" + hint + (note.map { "\n" + $0 } ?? ""),
                      tryIt: true, primary: L("完成", "Done"))
    }

    static let legacyNote = L("如果輸入方式清單裡還有舊版的「善解（標準）」或「善解（倚天）」，請到「系統設定 → 鍵盤 → 輸入方式」移除。",
                              "If the old Shanjie (Standard) or Shanjie (ETen) entries are still listed, remove them in System Settings > Keyboard > Input Sources.")
}

/// The window's content: every element of every screen, shown or hidden by `show`.
final class StepView: NSView {
    let indicator = StepIndicator(titles: [L("歡迎", "Welcome"), L("安裝", "Install"), L("啟用", "Activate"), L("試打", "Try")])
    let seal = SealView()
    let title = NSTextField(labelWithString: "")
    let body = NSTextField(wrappingLabelWithString: "")
    let spinner = NSProgressIndicator()
    let status = NSTextField(wrappingLabelWithString: "")
    let detailText = NSTextView()
    let detailScroll = NSScrollView()
    let copyDetail = NSButton(title: L("拷貝錯誤細節", "Copy Details"), target: nil, action: nil)
    let disclosure = NSButton(title: "", target: nil, action: nil)
    let detailsLabel = NSTextField(wrappingLabelWithString: "")
    let licenses = NSButton(title: L("顯示授權", "Licenses"), target: nil, action: nil)
    let tryField = NSTextField(string: "")
    let switchButton = NSButton(title: L("切換到善解", "Switch to Shanjie"), target: nil, action: nil)
    let switchNote = NSTextField(wrappingLabelWithString: "")
    let primary = NSButton(title: "", target: nil, action: nil)
    let secondary = NSButton(title: "", target: nil, action: nil)
    let close = NSButton(title: "", target: nil, action: nil)
    private let statusRow: NSStackView
    private let detailRow: NSStackView
    private let detailsRow: NSStackView
    private let detailsBody: NSStackView
    private let tryRow: NSStackView

    override init(frame: NSRect) {
        let width = frame.width - 2 * Look.margin
        let disclosureLabel = NSTextField(labelWithString: L("詳細資訊", "Details"))
        statusRow = NSStackView(views: [spinner, status])
        detailRow = NSStackView(views: [detailScroll, copyDetail])
        detailsBody = NSStackView(views: [detailsLabel, licenses])
        let toggle = NSStackView(views: [disclosure, disclosureLabel])
        detailsRow = NSStackView(views: [toggle, detailsBody])
        tryRow = NSStackView(views: [tryField, NSStackView(views: [switchButton, switchNote])])
        super.init(frame: frame)

        title.font = Look.titleFont
        for label in [body, status, detailsLabel, switchNote] {
            label.preferredMaxLayoutWidth = width
            label.font = Look.bodyFont
        }
        detailsLabel.font = Look.smallFont
        for label in [detailsLabel, switchNote, status, disclosureLabel] { label.textColor = .secondaryLabelColor }
        disclosureLabel.font = Look.smallFont
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false
        statusRow.alignment = .top

        detailText.isEditable = false
        detailText.isSelectable = true
        detailText.drawsBackground = false
        detailText.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        detailText.textColor = .secondaryLabelColor
        detailText.autoresizingMask = [.width]
        detailScroll.documentView = detailText
        detailScroll.hasVerticalScroller = true
        detailScroll.borderType = .bezelBorder

        disclosure.setButtonType(.pushOnPushOff)
        disclosure.bezelStyle = .disclosure
        toggle.spacing = 2
        for column in [detailRow, detailsBody, detailsRow, tryRow] {
            column.orientation = .vertical
            column.alignment = .leading
        }

        tryField.font = Look.tryFont
        tryField.placeholderString = L("在這裡打字", "Type here")
        primary.keyEquivalent = "\r"
        for button in [primary, secondary, close, copyDetail, licenses, switchButton] { button.bezelStyle = .push }
        licenses.controlSize = .small

        let header = NSStackView(views: [seal, title])
        header.spacing = 14
        let buttons = NSStackView(views: [NSView(), close, secondary, primary])
        let stack = NSStackView(views: [indicator, header, body, statusRow, detailRow, detailsRow, tryRow, NSView(), buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 14
        stack.setCustomSpacing(24, after: indicator)
        stack.detachesHiddenViews = true
        stack.edgeInsets = NSEdgeInsets(top: 20, left: Look.margin, bottom: 20, right: Look.margin)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: leadingAnchor), stack.trailingAnchor.constraint(equalTo: trailingAnchor),
            stack.topAnchor.constraint(equalTo: topAnchor), stack.bottomAnchor.constraint(equalTo: bottomAnchor),
            buttons.widthAnchor.constraint(equalToConstant: width),
            detailScroll.widthAnchor.constraint(equalToConstant: width),
            detailScroll.heightAnchor.constraint(equalToConstant: 90),
            tryField.widthAnchor.constraint(equalToConstant: width),
        ])
        disclosure.target = self
        disclosure.action = #selector(toggleDetails)
    }

    required init?(coder: NSCoder) { nil }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.windowBackgroundColor.setFill()
        bounds.fill()
    }

    @objc func toggleDetails() { detailsBody.isHidden = disclosure.state != .on }

    func show(_ screen: Screen) {
        indicator.show(current: screen.step)
        seal.isHidden = !screen.seal
        title.stringValue = screen.title
        body.stringValue = screen.body
        body.isHidden = screen.body.isEmpty
        status.stringValue = screen.status ?? ""
        statusRow.isHidden = screen.status == nil
        if screen.busy { spinner.startAnimation(nil) } else { spinner.stopAnimation(nil) }
        detailText.string = screen.detail ?? ""
        detailRow.isHidden = screen.detail == nil
        detailsLabel.stringValue = screen.details ?? ""
        detailsRow.isHidden = screen.details == nil
        disclosure.state = .off
        detailsBody.isHidden = true
        tryRow.isHidden = !screen.tryIt
        switchNote.stringValue = ""
        for (button, label) in [(primary, screen.primary), (secondary, screen.secondary), (close, screen.close)] {
            button.title = label ?? ""
            button.isHidden = label == nil
            button.isEnabled = !screen.busy
        }
    }
}

// MARK: - Rendering for review (installer-v2 section 1.4)

/// The eight screens with made-up versions and error text, for screenshots only.
func reviewScreens() -> [(String, Screen)] {
    [("1-welcome-install", Screens.welcome(plan: .install, installed: nil, bundled: "0.3.1")),
     ("2-welcome-update", Screens.welcome(plan: .update, installed: "0.3.0", bundled: "0.3.1")),
     ("3-welcome-same", Screens.welcome(plan: .enableSame, installed: "0.3.1", bundled: "0.3.1")),
     ("4-welcome-newer", Screens.welcome(plan: .enableNewer, installed: "0.4.0", bundled: "0.3.1")),
     ("5-installing", Screens.working(Screens.copying)),
     ("6-failed", Screens.failed(Failure(message: L("把輸入法複製到你的帳號時失敗了。", "Copying the input method into your account failed."),
                                         detail: "error: cp: ~/Library/Input Methods/.shanjie-staging-AbC123/善解輸入法.app: No space left on device"))),
     ("7-activate", Screens.activate),
     ("8-try", Screens.tryIt(note: nil))]
}

/// Draws every review screen in light and dark at 2x into `folder`, which must already exist;
/// never overwrites a file. Touches nothing else: no window, no Paths, no install actions.
@MainActor
func renderSteps(into folder: URL) -> Int32 {
    var isFolder: ObjCBool = false
    guard FileManager.default.fileExists(atPath: folder.path, isDirectory: &isFolder), isFolder.boolValue else {
        FileHandle.standardError.write(Data("--render-steps: \(folder.path) is not an existing folder\n".utf8))
        return 1
    }
    for (name, screen) in reviewScreens() {
        for (suffix, appearanceName) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
            guard let appearance = NSAppearance(named: appearanceName),
                  let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(Look.windowSize.width * 2),
                                                pixelsHigh: Int(Look.windowSize.height * 2), bitsPerSample: 8,
                                                samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                                colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { return 1 }
            let view = StepView(frame: NSRect(origin: .zero, size: Look.windowSize))
            view.appearance = appearance
            view.show(screen)
            if screen.details != nil {  // the review shows the details expanded
                view.disclosure.state = .on
                view.toggleDetails()
            }
            view.layoutSubtreeIfNeeded()
            bitmap.size = Look.windowSize
            appearance.performAsCurrentDrawingAppearance { view.cacheDisplay(in: view.bounds, to: bitmap) }
            guard let png = bitmap.representation(using: .png, properties: [:]) else { return 1 }
            do {
                try png.write(to: folder.appendingPathComponent("\(name)-\(suffix).png"), options: .withoutOverwriting)
            } catch {
                FileHandle.standardError.write(Data("--render-steps: \(error.localizedDescription)\n".utf8))
                return 1
            }
        }
    }
    return 0
}

// MARK: - The installer

@MainActor
final class Controller: NSObject, NSApplicationDelegate, NSWindowDelegate {
    // s3c section 2.4: poll every 0.5 s for at most 30 s on the main run loop.
    private let pollInterval: TimeInterval = 0.5
    private let pollLimit = 60
    /// After selecting the input source, how long to wait before reading back the current one.
    /// A first guess; the device run shows whether the switch is visible sooner.
    private let switchCheckDelay: TimeInterval = 0.3

    private var window: NSWindow!
    private let view = StepView(frame: NSRect(origin: .zero, size: Look.windowSize))
    private var isBusy = false
    private var plan = InstallerPlan.install
    private var primaryAction: () -> Void = {}
    private var secondaryAction: () -> Void = {}

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(origin: .zero, size: Look.windowSize),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = L("安裝善解輸入法", "Shanjie Installer")
        window.delegate = self
        window.contentView = view
        for (button, selector) in [(view.primary, #selector(primaryPressed)), (view.secondary, #selector(secondaryPressed)),
                                   (view.close, #selector(closePressed)), (view.licenses, #selector(licensesPressed)),
                                   (view.copyDetail, #selector(copyDetailPressed)), (view.switchButton, #selector(switchPressed))] {
            button.target = self
            button.action = selector
        }
        installMenu()
        showWelcome()
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

    /// Quit (Cmd-Q), and the Edit commands the error text and the try field need.
    private func installMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        appItem.submenu = NSMenu()
        appItem.submenu?.addItem(withTitle: L("結束", "Quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem()
        editItem.submenu = NSMenu(title: L("編輯", "Edit"))
        editItem.submenu?.addItem(withTitle: L("剪下", "Cut"), action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editItem.submenu?.addItem(withTitle: L("拷貝", "Copy"), action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editItem.submenu?.addItem(withTitle: L("貼上", "Paste"), action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editItem.submenu?.addItem(withTitle: L("全選", "Select All"), action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        main.addItem(appItem)
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    private func show(_ screen: Screen, primary: @escaping () -> Void = {}, secondary: @escaping () -> Void = {}) {
        isBusy = screen.busy
        view.show(screen)
        primaryAction = primary
        secondaryAction = secondary
    }

    // MARK: steps

    private func showWelcome() {
        let installed = Paths.installedVersion()
        plan = InstallerPlan.decide(installed: installed, bundled: Paths.bundledVersion)
        show(Screens.welcome(plan: plan, installed: installed, bundled: Paths.bundledVersion),
             primary: { [weak self] in
                 guard let self else { return }
                 if self.plan.copiesFiles { self.copyThenEnable() } else { self.enable() }
             },
             secondary: { [weak self] in self?.copyThenEnable() })
        // Only copying needs the bundled zip and script; enabling an installed copy does not.
        if Paths.zip == nil || Paths.script == nil || Paths.bundledVersion.isEmpty {
            if plan.copiesFiles { fail(Screens.incomplete) } else { view.secondary.isHidden = true }
        }
    }

    private func copyThenEnable() {
        show(Screens.working(Screens.copying))
        // The controller lives as long as the app (it is the application delegate).
        DispatchQueue.global(qos: .userInitiated).async {
            let failure = copyFiles()
            DispatchQueue.main.async {
                if let failure { self.fail(failure) } else { self.enable() }
            }
        }
    }

    /// s3c section 2.3: against the installed copy, with its own bundle ID and preference domain.
    private func enable() {
        show(Screens.working(Screens.enabling))
        guard let bundleID = Bundle(url: Paths.installed)?.bundleIdentifier,
              let defaults = UserDefaults(suiteName: bundleID) else {
            fail(Failure(message: L("找不到已安裝的善解輸入法。", "The installed input method was not found.")))
            return
        }
        let result = Registration.run(bundleURL: Paths.installed, bundleID: bundleID, defaults: defaults)
        switch InstallerFlow.next(after: result.outcome) {
        case .tryIt: showTry(note: result.legacyDisableFailed ? Screens.legacyNote : nil)
        case .activate: showActivate()
        case .waiting: poll(bundleID: bundleID)
        case .failed:
            fail(Failure(message: L("系統沒有接受這個輸入法。可以到「系統設定 → 鍵盤 → 輸入方式」手動加入「善解輸入法」。",
                                    "The system did not accept the input method. Add Shanjie in System Settings > Keyboard > Input Sources.")))
        }
    }

    private func showActivate() {
        show(Screens.activate, primary: { [weak self] in self?.enable() })
    }

    private func showTry(note: String?) {
        show(Screens.tryIt(note: note), primary: { NSApp.terminate(nil) })
        window.makeFirstResponder(view.tryField)
    }

    /// Security review B-4: the detail shows and copies the home folder as ~.
    private func fail(_ failure: Failure) {
        var shown = failure
        shown.detail = failure.detail.map { $0.replacingOccurrences(of: Paths.home, with: "~") }
        show(Screens.failed(shown))
    }

    private func poll(bundleID: String) {
        show(Screens.working(Screens.waiting))
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
                    self.showTry(note: finished ? nil : Screens.legacyNote)
                } else if ticks >= self.pollLimit {
                    timer.invalidate()
                    self.showActivate()
                }
            }
        }
    }

    // MARK: actions

    @objc private func primaryPressed() { primaryAction() }
    @objc private func secondaryPressed() { secondaryAction() }
    @objc private func closePressed() { NSApp.terminate(nil) }
    @objc private func licensesPressed() { if let url = Paths.licenses { NSWorkspace.shared.open(url) } }

    @objc private func copyDetailPressed() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(view.detailText.string, forType: .string)
    }

    /// Section 1.2: only on the user's press; success only if the current input source reads back
    /// as Shanjie's mode (security review B-5: the call can return noErr and not switch).
    @objc private func switchPressed() {
        window.makeFirstResponder(view.tryField)
        let failed = L("切換失敗。請從選單列的輸入法選單選「善解輸入法」。", "Could not switch. Choose Shanjie from the input menu in the menu bar.")
        guard let bundleID = Bundle(url: Paths.installed)?.bundleIdentifier, Registration.selectMode(bundleID: bundleID) else {
            view.switchNote.stringValue = failed
            return
        }
        view.switchNote.stringValue = ""
        DispatchQueue.main.asyncAfter(deadline: .now() + switchCheckDelay) { [weak self] in
            if !Registration.isCurrentMode(bundleID: bundleID) { self?.view.switchNote.stringValue = failed }
        }
    }
}

// Section 1.4: exactly `--render-steps <folder>` draws the screens and exits before anything else.
let arguments = CommandLine.arguments
if arguments.count == 3, arguments[1] == "--render-steps" {
    _ = NSApplication.shared
    exit(MainActor.assumeIsolated { renderSteps(into: URL(fileURLWithPath: arguments[2])) })
}

let app = NSApplication.shared
let controller = MainActor.assumeIsolated { Controller() }
app.delegate = controller
app.setActivationPolicy(.regular)
app.run()
