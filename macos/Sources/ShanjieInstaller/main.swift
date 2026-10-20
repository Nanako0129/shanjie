import AppKit
import CoreText
import MachO
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
    /// The name before s3b section 13 (the two-mode versions), which install-ime.sh upgrades.
    static let legacy = URL(fileURLWithPath: home + "/Library/Input Methods/shanjie.app")
    /// system-install.md: the copy for every account on this Mac.
    static let system = URL(fileURLWithPath: RootStage.systemFolder + "/" + RootStage.appName)
    static let bundledVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
    static let zip = Bundle.main.url(forResource: "shanjie-\(bundledVersion)", withExtension: "zip")
    static let script = Bundle.main.url(forResource: "install-ime", withExtension: "sh")
    static let licenses = Bundle.main.resourceURL?.appendingPathComponent("LICENSES")

    /// system-install.md section 2.3 step 6: the CFBundleVersion the root stage accepts, compiled
    /// into this executable (the `__TEXT,__shanjie_ver` section scripts/build-installer.sh links in),
    /// not read from Info.plist. nil in a build without it, which then cannot install for this Mac.
    static let expectedBundleVersion: String? = {
        var size: UInt = 0
        let header = #dsohandle.assumingMemoryBound(to: mach_header_64.self)
        guard let bytes = getsectiondata(header, "__TEXT", "__shanjie_ver", &size), size > 0 else { return nil }
        let text = String(decoding: UnsafeBufferPointer(start: bytes, count: Int(size)), as: UTF8.self)
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }()

    /// Read from the Info.plist on disk each time: `Bundle(url:)` caches, and these copies change
    /// while the installer runs (the root stage, a removal in Terminal before going back).
    static func info(_ app: URL) -> [String: Any]? {
        NSDictionary(contentsOf: app.appendingPathComponent("Contents/Info.plist")) as? [String: Any]
    }

    static func version(_ app: URL) -> String? { info(app)?["CFBundleShortVersionString"] as? String }

    static func installedVersion() -> String? { version(installed) }

    /// The /Library copy's version: nil when there is none, "" when its version cannot be read.
    static func systemVersion() -> String? {
        // attributesOfItem does not follow a final symlink: a dangling one counts as a copy too
        // (install-ime.sh's `-e || -L`), so "My Account Only" lands on the blocked screen.
        guard (try? FileManager.default.attributesOfItem(atPath: system.path)) != nil else { return nil }
        return version(system) ?? ""
    }

    /// Whether the account layer holds the shipping-ID copy, under either name.
    static func hasAccountCopy() -> Bool {
        [installed, legacy].contains { info($0)?["CFBundleIdentifier"] as? String == InstallerPlan.shippingBundleID }
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

let minimalEnvironment = ["HOME": Paths.home, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"]

/// Extracts the bundled zip without quarantine into a new folder of this installer's own under
/// NSTemporaryDirectory(); the caller removes it.
func extractBundled() -> Result<URL, Failure> {
    guard let zip = Paths.zip else { return .failure(Screens.incomplete) }
    let tmp = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("shanjie-installer-\(UUID().uuidString)")
    do { try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true) } catch {
        return .failure(Failure(message: L("無法建立暫存資料夾。", "Cannot create a temporary folder.")))
    }
    let unzip = run("/usr/bin/ditto", ["-x", "-k", "--noqtn", zip.path, tmp.path], environment: minimalEnvironment)
    guard unzip.status == 0 else {
        try? FileManager.default.removeItem(at: tmp)
        return .failure(Failure(message: L("解開內附的輸入法時失敗了。", "Extracting the bundled input method failed."), detail: unzip.stderr))
    }
    return .success(tmp)
}

/// Runs install-ime.sh on the extracted bundle with SHANJIE_INSTALL_SKIP_REGISTER=1 and no
/// inherited environment (s3c section 2.2).
func copyFiles() -> Failure? {
    guard let script = Paths.script else { return Screens.incomplete }
    let tmp: URL
    switch extractBundled() {
    case .failure(let failure): return failure
    case .success(let url): tmp = url
    }
    defer { try? FileManager.default.removeItem(at: tmp) }
    var env = minimalEnvironment
    env["SHANJIE_INSTALL_SKIP_REGISTER"] = "1"
    let app = tmp.appendingPathComponent(RootStage.appName).path
    let result = run("/bin/bash", [script.path, app], environment: env)
    guard result.status == 0 else {
        return Failure(message: L("把輸入法複製到你的帳號時失敗了。", "Copying the input method into your account failed."), detail: result.stderr)
    }
    return nil
}

/// system-install.md section 2.4 items 1 to 3, as this user, after the root stage (or instead of it,
/// for `moveToSystem`): install-ime.sh's no-copy mode moves the account-layer copy to
/// .shanjie-previous, runs lsregister -f on the /Library copy and ends the old processes of this
/// account in both locations.
func userStage() -> Failure? {
    guard let script = Paths.script else { return Screens.incomplete }
    var env = minimalEnvironment
    env["SHANJIE_INSTALL_NO_COPY"] = "1"
    env["SHANJIE_INSTALL_SKIP_REGISTER"] = "1"
    let result = run("/bin/bash", [script.path, Paths.system.path], environment: env)
    guard result.status == 0 else {
        return Failure(message: L("把你帳號裡的那份移開時失敗了。", "Moving the copy in your account aside failed."), detail: result.stderr)
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
    // Spacing values are first-round choices (no reference screen); the user judges them in the
    // render-steps rounds. Each one is named for what it separates.
    /// Between the step indicator's items.
    static let indicatorSpacing: CGFloat = 18
    /// Between a dot and its label.
    static let dotLabelSpacing: CGFloat = 6
    /// Dot ring: inset from the bounds and line width, so the 1.5 pt ring stays inside the 8 pt box.
    static let dotRingInset: CGFloat = 0.75
    static let dotRingWidth: CGFloat = 1.5
    /// Between the seal and the title.
    static let headerSpacing: CGFloat = 14
    /// Between the screen's rows.
    static let rowSpacing: CGFloat = 14
    /// Extra room under the step indicator, to separate it from the content.
    static let afterIndicatorSpacing: CGFloat = 24
    /// Between the disclosure triangle and its label (the system's own gap is wider than looks right).
    static let disclosureSpacing: CGFloat = 2
    /// Top and bottom of the content column.
    static let verticalInset: CGFloat = 20
    /// The error detail box: about six lines of the 11 pt monospaced text.
    static let detailHeight: CGFloat = 90
    // system-install.md section 2.5, the two options of the welcome screen. First-round values, no
    // reference screen; the maintainer judges them in the render-steps rounds.
    /// Between the two option boxes.
    static let choiceSpacing: CGFloat = 12
    /// Inside an option box, around its content.
    static let choiceInset: CGFloat = 12
    /// Between the rows inside an option box.
    static let choiceRowSpacing: CGFloat = 8
    /// Corner radius of an option box's outline.
    static let choiceRadius: CGFloat = 8
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
        spacing = Look.indicatorSpacing
        var items: [NSView] = []
        for title in titles {
            let dot = DotView()
            let label = NSTextField(labelWithString: title)
            label.font = Look.smallFont
            let item = NSStackView(views: [dot, label])
            item.spacing = Look.dotLabelSpacing
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
            let circle = NSBezierPath(ovalIn: bounds.insetBy(dx: Look.dotRingInset, dy: Look.dotRingInset))
            switch state {
            case .current: Look.seal.setFill(); circle.fill()
            case .done: NSColor.tertiaryLabelColor.setFill(); circle.fill()
            case .todo: circle.lineWidth = Look.dotRingWidth; NSColor.tertiaryLabelColor.setStroke(); circle.stroke()
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
    /// system-install.md section 2.5: the welcome screen's two options, side by side.
    var choices = false
    /// The label of the button that copies `detail` (default: copy the error details).
    var copyLabel: String?
}

enum Screens {
    static let license = L("授權：程式 Apache-2.0；詞庫 MIT（小麥注音）；語言模型與擴充詞庫 CC BY-SA 4.0。",
                           "License: program Apache-2.0; lexicon MIT (McBopomofo); language model and added words CC BY-SA 4.0.")
    static let incomplete = Failure(message: L("安裝程式不完整：找不到內附的輸入法。請重新下載安裝程式。",
                                               "The installer is incomplete: the bundled input method is missing. Download the installer again."))

    // system-install.md section 2.5: the two options' texts, word for word.
    static let thisMacTitle = L("安裝到這台 Mac", "Install for This Mac")
    static let thisMacText = L("這台 Mac 的每個帳號都能用。有程式開著安全輸入時（密碼管理器、終端機的「加密的鍵盤輸入」），善解照常能用。",
                               "Every account on this Mac can use it. Keeps working while another app holds Secure Keyboard Entry.")
    static let thisMacCost = L("安裝和每次更新都要輸入一次管理員密碼。", "Installing and every update ask for an administrator password once.")
    static let accountTitle = L("只裝在我的帳號", "My Account Only")
    static let accountText = L("不需要密碼，一鍵更新。有程式開著安全輸入時，系統會暫時停用善解。",
                               "No password, one-click updates. The system disables Shanjie while another app holds Secure Keyboard Entry.")
    static let accountCost = L("其他帳號看不到善解。", "Other accounts do not see Shanjie.")

    /// `system`: the /Library copy's version (nil: none); `account`: the account copy's, when there is one.
    static func welcome(system: String?, account: String?, bundled: String) -> Screen {
        var lines = [L("開源的 macOS 注音輸入法。", "An open-source Zhuyin input method for macOS.")]
        // "" is a copy whose version cannot be read (or only the legacy shanjie.app in the account).
        if let system {
            lines.append(system.isEmpty ? L("已裝給這台 Mac，版本不明。", "Installed for this Mac, version unknown.")
                                        : L("已裝給這台 Mac，版本 \(system)。", "Installed for this Mac, version \(system)."))
        }
        if let account {
            lines.append(account.isEmpty ? L("已裝在你的帳號，版本不明。", "Installed for your account, version unknown.")
                                         : L("已裝在你的帳號，版本 \(account)。", "Installed for your account, version \(account)."))
        }
        // system-install.md section 2.5 first, then app-sandbox.md section 2.5's rollback note.
        let details = L("「這台 Mac」把善解放進系統的輸入法資料夾，由系統保護檔案；之後更新時，善解更新會跳出系統的密碼框，文字是「善解更新 wants permission to update 善解輸入法.」，只在你按下關於頁的「更新」之後出現。選「這台 Mac」時，有程式開著安全輸入的期間，你打的字會經過善解（和平常一樣：學習暫停、不讀前文、不記錄）；終端機開著「加密的鍵盤輸入」時，sudo 的密碼提示也一樣會經過善解。「只有我的帳號」就是之前的安裝方式。兩種之間可以重新打開安裝程式切換，學習資料與設定不受影響（container 依 bundle ID）。不是管理員的帳號，請選「只有我的帳號」，或請管理員安裝。\n執行中的舊版會被結束，上一版保留在同一個資料夾的 .shanjie-previous。\n退回 0.4.x 以前（沒有沙盒）的版本時，舊版看不到學習資料與設定，它們已搬進 ~/Library/Containers/com.nyanako.inputmethod.shanjie；退回期間學到的，再升級時不會搬過去。搬回的方法：https://github.com/Nanako0129/shanjie/blob/main/docs/sandbox-restore.md \n",
                        "This Mac puts Shanjie in the system's input methods folder, where the system protects its files. Later updates show the system's password dialog, reading \"善解更新 wants permission to update 善解輸入法.\", and only after you choose Update on the About page. With This Mac, while another app holds Secure Keyboard Entry, what you type goes through Shanjie (as always: learning paused, no reading of earlier text, nothing recorded); with Secure Keyboard Entry on in Terminal, the sudo password prompt goes through Shanjie too. My Account Only is the way it was installed before. Open the installer again to switch between the two; learned words and settings stay (the container follows the bundle ID). On an account that is not an administrator, choose My Account Only, or ask an administrator to install it.\nA running older copy is stopped; the previous version is kept in the same folder as .shanjie-previous.\nGoing back to a version without the sandbox (0.4.x or earlier), it sees no learned words or settings: they moved into ~/Library/Containers/com.nyanako.inputmethod.shanjie. What it learns is not moved on the next upgrade. To move the data back: https://github.com/Nanako0129/shanjie/blob/main/docs/sandbox-restore.md \n") + license
        // No primary button: Return chooses neither option (section 2.5).
        return Screen(step: 0, title: L("善解輸入法 ", "Shanjie ") + bundled, body: lines.joined(separator: "\n"),
                      seal: true, details: details, close: L("取消", "Cancel"), choices: true)
    }

    /// The same version is installed (s3c-installer.md section 2.1): enable, or reinstall.
    static func same(canReinstall: Bool) -> Screen {
        Screen(step: 0, title: L("這個版本已經裝好了", "This version is already installed"),
               body: L("按「啟用」讓系統開始使用它；從系統設定加入後回到這裡，也是按這個。\n「重新安裝」會把目前這份當成上一版保留，取代原本保留的上一版。",
                       "Choose Enable to let the system use it, also after adding it in System Settings.\nReinstall keeps the current copy as the previous version, replacing the one kept before."),
               seal: true, primary: L("啟用", "Enable"), secondary: canReinstall ? L("重新安裝", "Reinstall") : nil, close: L("取消", "Cancel"))
    }

    /// A newer version is installed where the user chose (the old enableNewer welcome line).
    static func newer(installed: String, bundled: String) -> Screen {
        Screen(step: 0, title: L("已經裝了更新的版本", "A newer version is installed"),
               body: L("已經裝了更新的版本（\(installed)），不會降版；內附的 \(bundled) 沒有安裝。按「啟用」讓系統開始使用已裝好的那一份。",
                       "A newer version (\(installed)) is installed; this installer does not downgrade, and the bundled \(bundled) was not installed. Choose Enable to use the installed one."),
               seal: true, primary: L("啟用", "Enable"), close: L("取消", "Cancel"))
    }

    /// Never downgrade: "This Mac" while the account holds a newer version. Nothing changed.
    static func accountNewer(installed: String, bundled: String) -> Screen {
        Screen(step: 0, title: L("你的帳號裡是更新的版本", "Your account has a newer version"),
               body: L("你的帳號裡已經裝了 \(installed)，比這份安裝程式內附的 \(bundled) 新。裝給這台 Mac 會換成舊版，所以沒有安裝，什麼都沒有改。要裝給這台 Mac，請用 \(installed) 的安裝程式。",
                       "Your account already has \(installed), newer than the \(bundled) in this installer. Installing for this Mac would replace it with an older version, so nothing was installed or changed. To install for this Mac, use the installer of \(installed)."),
               seal: true, secondary: L("返回", "Back"), close: L("完成", "Done"))
    }

    /// system-install.md section 2.2: "My Account Only" while this Mac has a copy. Nothing changed.
    static func blocked() -> Screen {
        Screen(step: 0, title: L("這台 Mac 已經裝了善解", "Shanjie is already installed for this Mac"),
               body: L("善解已經裝在 /Library/Input Methods，給這台 Mac 的每個帳號用，所以不能再只裝在你的帳號。什麼都沒有改。\n要改成只裝在你的帳號，請管理員在「終端機」執行下面的指令移除，再重新打開這個安裝程式。",
                       "Shanjie is installed in /Library/Input Methods for every account on this Mac, so it cannot also be installed for your account only. Nothing was changed.\nTo install it for your account only instead, have an administrator run the command below in Terminal, then open this installer again."),
               seal: true, detail: RootStage.removalCommand, secondary: L("返回", "Back"), close: L("完成", "Done"),
               copyLabel: L("拷貝指令", "Copy Command"))
    }

    /// system-install.md section 2.4: after a location change. One button; never logs the user out.
    static let logOutSentence = L("請登出再登入一次，善解才會在所有 App 裡正常。登入後輸入法選單裡如果沒有善解，請重新打開這個安裝程式。",
                                  "Log out and back in once, so that Shanjie works in every app. If Shanjie is not in the input menu after you log back in, open this installer again.")

    static func logOut() -> Screen {
        Screen(step: 3, title: L("✓ 已經裝好了，請登出再登入", "✓ Installed: log out and back in"), body: logOutSentence, primary: L("好", "OK"))
    }

    /// The two steps in the Keyboard pane, shared by every screen that asks the user to add Shanjie. The labels are the
    /// zh_TW and en strings of KeyboardSettings.appex's Localizable.loctable (文字輸入, 輸入方式, 編輯⋯), read 2026-10-21.
    static let pressPlus = L("在「文字輸入」的「輸入方式」按「編輯⋯」，再按「+」。", "Under Text Input, next to Input Sources, choose Edit…, then press +.")
    static let chooseShanjie = L("選「繁體中文」裡的「善解輸入法」，按「加入」。", "Choose Shanjie under Chinese (Traditional) and press Add.")

    /// system-install.md section 2.4: after a first "This Mac" install, the system may open System Settings by itself;
    /// the input method is added only after the log out. Step 2, like `activate`: activation is still to do.
    static func logOutThenAdd() -> Screen {
        Screen(step: 2, title: L("✓ 已經裝好了，請登出再登入，再加入善解", "✓ Installed: log out and back in, then add Shanjie"),
               body: L("1. 請登出再登入一次。\n2. 登入後到「系統設定 → 鍵盤」，", "1. Log out and back in once.\n2. Then open System Settings > Keyboard. ")
                   + pressPlus + "\n3. " + chooseShanjie + "\n\n"
                   + L("系統設定如果現在自己打開了，可以先關掉，登入後再加。登入後如果找不到善解，請重新打開這個安裝程式。",
                       "If System Settings opened by itself just now, you can close it and add Shanjie after you log back in. If you cannot find Shanjie then, open this installer again."),
               primary: L("好", "OK"))
    }

    static func working(_ status: String) -> Screen {
        Screen(step: 1, title: L("正在安裝", "Installing"), body: "", status: status, busy: true)
    }

    static let copying = L("正在複製…", "Copying…")
    /// system-install.md section 2.4: the system may ask whether the installer may enable Shanjie.
    static let enabling = L("正在請系統啟用…如果系統問要不要允許，請按『允許』。", "Asking the system to turn it on… If it asks, choose Allow.")
    static let movingAccountCopy = L("正在把你帳號裡的那份移到 .shanjie-previous…", "Moving the copy in your account to .shanjie-previous…")
    static let loading = L("正在請系統改用新的那份…", "Asking the system to use the new copy…")
    static let password = L("請在系統的視窗輸入管理員密碼。", "Enter an administrator password in the system's dialog.")
    static let waiting = L("正在等系統啟用。如果系統跳出視窗，請允許「善解輸入法」。",
                           "Waiting for the system to turn it on. If it asks, allow Shanjie.")

    static func failed(_ failure: Failure) -> Screen {
        Screen(step: 1, title: L("沒有裝好", "Not installed"), body: failure.message, detail: failure.detail, close: L("完成", "Done"))
    }

    /// The failure screen after a location change, with the log-out sentence (section 2.4).
    static func failedLogOut(_ failure: Failure) -> Screen {
        var screen = failed(failure)
        screen.body += "\n\n" + logOutSentence
        return screen
    }

    /// installer-v2.md section 9.2 item 3: the system adds a new input method only when the user
    /// adds it in System Settings. `note` is the line after a recheck that found nothing.
    static func activate(note: String? = nil) -> Screen {
        Screen(step: 2, title: L("還差一步：到系統設定加入", "One more step: add it in System Settings"),
               body: L("系統要在「輸入方式」裡加入，才會開始使用善解輸入法。\n\n1. 按「打開輸入方式設定」。\n2. ",
                       "The system uses Shanjie only after you add it under Input Sources.\n\n1. Choose Open Input Sources Settings.\n2. ")
                   + pressPlus + "\n3. " + chooseShanjie + (note.map { "\n\n" + $0 } ?? ""),
               primary: L("打開輸入方式設定", "Open Input Sources Settings"), secondary: L("重新檢查", "Check Again"),
               close: L("完成", "Done"))
    }

    static let notAcceptedMessage = L("系統沒有接受這個輸入法。可以到「系統設定 → 鍵盤」手動加入：", "The system did not accept the input method. Add it in System Settings > Keyboard: ")
        + pressPlus + chooseShanjie

    /// The failure screen after a location change (section 2.4): what failed, never "add it in
    /// System Settings" (that comes only after the log out).
    static let registrationFailedMessage = L("系統沒有登記這個輸入法。", "The system did not register the input method.")
    static let enableFailedMessage = L("系統沒有啟用這個輸入法。", "The system did not turn on the input method.")

    static let notDetected = L("還沒偵測到，請照上面三步加入。", "Not detected yet. Please add it with the three steps above.")

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

/// A push button Tab always reaches, whatever the system's keyboard navigation setting: the two
/// options of the welcome screen have no default button, so the keyboard needs another way to them.
final class FocusableButton: NSButton {
    override var canBecomeKeyView: Bool { true }
}

/// One option of the welcome screen: its content inside a rounded outline in the separator colour.
final class ChoiceBox: NSView {
    init(content: NSView) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        content.translatesAutoresizingMaskIntoConstraints = false
        addSubview(content)
        NSLayoutConstraint.activate([
            content.leadingAnchor.constraint(equalTo: leadingAnchor), content.trailingAnchor.constraint(equalTo: trailingAnchor),
            content.topAnchor.constraint(equalTo: topAnchor), content.bottomAnchor.constraint(equalTo: bottomAnchor),
        ])
    }

    required init?(coder: NSCoder) { nil }

    override func draw(_ dirtyRect: NSRect) {
        let outline = NSBezierPath(roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5), xRadius: Look.choiceRadius, yRadius: Look.choiceRadius)
        NSColor.separatorColor.setStroke()
        outline.stroke()
    }
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
    let thisMacButton = FocusableButton(title: Screens.thisMacTitle, target: nil, action: nil)
    let accountButton = FocusableButton(title: Screens.accountTitle, target: nil, action: nil)
    private let choiceRow: NSStackView
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
        tryRow = NSStackView(views: [tryField, switchButton, switchNote])
        // Two boxes of the same width side by side, each its text, its cost and its button.
        let boxWidth = (width - Look.choiceSpacing) / 2
        let boxes = [(Screens.thisMacText, Screens.thisMacCost, thisMacButton), (Screens.accountText, Screens.accountCost, accountButton)]
            .map { text, cost, button -> ChoiceBox in
                let textLabel = NSTextField(wrappingLabelWithString: text)
                let costLabel = NSTextField(wrappingLabelWithString: cost)
                textLabel.font = Look.bodyFont
                costLabel.font = Look.smallFont
                costLabel.textColor = .secondaryLabelColor
                for label in [textLabel, costLabel] { label.preferredMaxLayoutWidth = boxWidth - 2 * Look.choiceInset }
                button.bezelStyle = .push
                button.keyEquivalent = ""
                let column = NSStackView(views: [textLabel, costLabel, NSView(), button])
                column.orientation = .vertical
                column.alignment = .leading
                column.spacing = Look.choiceRowSpacing
                column.edgeInsets = NSEdgeInsets(top: Look.choiceInset, left: Look.choiceInset, bottom: Look.choiceInset, right: Look.choiceInset)
                let box = ChoiceBox(content: column)
                box.widthAnchor.constraint(equalToConstant: boxWidth).isActive = true
                return box
            }
        choiceRow = NSStackView(views: boxes)
        choiceRow.spacing = Look.choiceSpacing
        choiceRow.distribution = .fillEqually
        choiceRow.alignment = .top
        super.init(frame: frame)
        // The same height for both boxes, so the two buttons line up.
        boxes[0].heightAnchor.constraint(equalTo: boxes[1].heightAnchor).isActive = true

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
        toggle.spacing = Look.disclosureSpacing
        for column in [detailRow, detailsBody, detailsRow, tryRow] {
            column.orientation = .vertical
            column.alignment = .leading
        }

        tryField.font = Look.tryFont
        tryField.placeholderString = L("在這裡打字", "Type here")
        for button in [primary, secondary, close, copyDetail, licenses, switchButton] { button.bezelStyle = .push }
        licenses.controlSize = .small

        let header = NSStackView(views: [seal, title])
        header.spacing = Look.headerSpacing
        let buttons = NSStackView(views: [NSView(), close, secondary, primary])
        let stack = NSStackView(views: [indicator, header, body, choiceRow, statusRow, detailRow, detailsRow, tryRow, NSView(), buttons])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = Look.rowSpacing
        stack.setCustomSpacing(Look.afterIndicatorSpacing, after: indicator)
        stack.detachesHiddenViews = true
        stack.edgeInsets = NSEdgeInsets(top: Look.verticalInset, left: Look.margin, bottom: Look.verticalInset, right: Look.margin)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: leadingAnchor), stack.trailingAnchor.constraint(equalTo: trailingAnchor),
            stack.topAnchor.constraint(equalTo: topAnchor), stack.bottomAnchor.constraint(equalTo: bottomAnchor),
            buttons.widthAnchor.constraint(equalToConstant: width),
            detailScroll.widthAnchor.constraint(equalToConstant: width),
            detailScroll.heightAnchor.constraint(equalToConstant: Look.detailHeight),
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

    @objc func toggleDetails() {
        detailsBody.isHidden = disclosure.state != .on
        fitWindow()
    }

    /// The height the shown screen needs, never below the first-round window size (the welcome
    /// screen with its two options and the details open is taller than the other screens).
    var fittingHeight: CGFloat {
        layoutSubtreeIfNeeded()
        return max(Look.windowSize.height, fittingSize.height)
    }

    /// Grows or shrinks the window to `fittingHeight`, keeping its top edge where it is.
    private func fitWindow() {
        guard let window else { return }
        let content = NSRect(origin: .zero, size: NSSize(width: Look.windowSize.width, height: fittingHeight))
        var frame = window.frameRect(forContentRect: content)
        frame.origin = NSPoint(x: window.frame.minX, y: window.frame.maxY - frame.height)
        window.setFrame(frame, display: true)
    }

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
        choiceRow.isHidden = !screen.choices
        copyDetail.title = screen.copyLabel ?? L("拷貝錯誤細節", "Copy Details")
        // Return is the default key everywhere except the try page, where it belongs to the field,
        // and the welcome screen, where neither option is the default (system-install.md section 2.5).
        primary.keyEquivalent = screen.tryIt || screen.choices ? "" : "\r"
        for (button, label) in [(primary, screen.primary), (secondary, screen.secondary), (close, screen.close)] {
            button.title = label ?? ""
            button.isHidden = label == nil
            button.isEnabled = !screen.busy
        }
        fitWindow()
    }
}

// MARK: - Rendering for review (installer-v2 section 1.4)

/// The review screens with made-up versions and error text, for screenshots only.
func reviewScreens() -> [(String, Screen)] {
    [("1-welcome", Screens.welcome(system: nil, account: nil, bundled: "0.5.0")),
     ("2-welcome-this-mac", Screens.welcome(system: "0.5.0", account: nil, bundled: "0.5.0")),
     ("3-welcome-both", Screens.welcome(system: "0.4.9", account: "0.4.7", bundled: "0.5.0")),
     ("4-same", Screens.same(canReinstall: true)),
     ("5-blocked", Screens.blocked()),
     ("6-installing", Screens.working(Screens.copying)),
     ("7-password", Screens.working(Screens.password)),
     ("8-failed", Screens.failed(Failure(message: L("把輸入法複製到你的帳號時失敗了。", "Copying the input method into your account failed."),
                                         detail: "error: cp: ~/Library/Input Methods/.shanjie-staging-AbC123/善解輸入法.app: No space left on device"))),
     ("9-enabling", Screens.working(Screens.enabling)),
     ("10-activate", Screens.activate()),
     ("11-try", Screens.tryIt(note: nil)),
     ("12-log-out", Screens.logOut()),
     ("12b-log-out-then-add", Screens.logOutThenAdd()),
     ("13-failed-log-out", Screens.failedLogOut(Failure(message: Screens.enableFailedMessage))),
     ("14-newer", Screens.newer(installed: "0.6.0", bundled: "0.5.0")),
     ("15-account-newer", Screens.accountNewer(installed: "0.6.0", bundled: "0.5.0"))]
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
            guard let appearance = NSAppearance(named: appearanceName) else { return 1 }
            let view = StepView(frame: NSRect(origin: .zero, size: Look.windowSize))
            view.appearance = appearance
            view.show(screen)
            if screen.details != nil {  // the review shows the details expanded
                view.disclosure.state = .on
                view.toggleDetails()
            }
            // The window grows to fit its screen (StepView.fitWindow); the picture does the same.
            let size = NSSize(width: Look.windowSize.width, height: view.fittingHeight)
            view.setFrameSize(size)
            view.layoutSubtreeIfNeeded()
            guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width * 2),
                                                pixelsHigh: Int(size.height * 2), bitsPerSample: 8,
                                                samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                                colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { return 1 }
            bitmap.size = size
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
    /// AppleScript's "User canceled." (the password dialog's Cancel).
    private let userCanceled = -128

    private var window: NSWindow!
    private let view = StepView(frame: NSRect(origin: .zero, size: Look.windowSize))
    private var isBusy = false
    /// system-install.md section 2.2: the inputs read at the welcome screen, before any copy.
    private var systemVersion: String?
    private var accountCopy = false
    private var accountVersion: String?
    private var listedBeforeCopy = false
    private var decision = InstallDecision(action: .account(.install))
    /// The copy this run enables: /Library's for "This Mac", the account's otherwise.
    private var target = Paths.installed
    /// app-sandbox.md section 2.7: the installed copy's bundle ID and its enabled input modes, read
    /// once at the start, before anything is swapped.
    private var startBundleID: String?
    private var startModes: Set<String> = []
    private var primaryAction: () -> Void = {}
    private var secondaryAction: () -> Void = {}

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = NSWindow(contentRect: NSRect(origin: .zero, size: Look.windowSize),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = L("安裝善解輸入法", "Shanjie Installer")
        window.delegate = self
        window.contentView = view
        window.autorecalculatesKeyViewLoop = true
        for (button, selector) in [(view.primary, #selector(primaryPressed)), (view.secondary, #selector(secondaryPressed)),
                                   (view.close, #selector(closePressed)), (view.licenses, #selector(licensesPressed)),
                                   (view.copyDetail, #selector(copyDetailPressed)), (view.switchButton, #selector(switchPressed)),
                                   (view.thisMacButton, #selector(thisMacPressed)), (view.accountButton, #selector(accountPressed))] {
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

    /// Reads the state once per welcome screen. The welcome screen is shown at launch, after a
    /// cancelled password dialog and from the blocked screen; each time nothing has been copied in
    /// this run yet, so `listedBeforeCopy` is always a read from before any copy (section 2.2).
    private func showWelcome() {
        systemVersion = Paths.systemVersion()
        accountCopy = Paths.hasAccountCopy()
        accountVersion = Paths.installedVersion()
        listedBeforeCopy = Registration.modeListed(bundleID: InstallerPlan.shippingBundleID)
        let start = [Paths.installed, Paths.legacy].lazy.compactMap { Paths.info($0)?["CFBundleIdentifier"] as? String }.first
        startBundleID = start
        startModes = start.map(Registration.enabledModeIDs(bundleID:)) ?? []
        show(Screens.welcome(system: systemVersion, account: accountCopy ? (accountVersion ?? "") : nil, bundled: Paths.bundledVersion))
    }

    @objc private func thisMacPressed() { choose(.thisMac) }
    @objc private func accountPressed() { choose(.accountOnly) }

    private func choose(_ choice: InstallChoice) {
        decision = InstallDecision.decide(choice: choice, system: systemVersion, accountCopy: accountCopy, account: accountVersion,
                                          bundled: Paths.bundledVersion, listedBeforeCopy: listedBeforeCopy)
        target = choice == .thisMac ? Paths.system : Paths.installed
        let canCopy = Paths.zip != nil && Paths.script != nil && !Paths.bundledVersion.isEmpty
        switch decision.action {
        case .blocked:
            show(Screens.blocked(), secondary: { [weak self] in self?.showWelcome() })
        case .copyToSystem:
            installForThisMac()
        case .moveToSystem:
            // /Library already holds this version or a newer one: only the user stage.
            guard Paths.script != nil else { return fail(Screens.incomplete) }
            userStageThenEnable()
        case .enableSystemSame:
            show(Screens.same(canReinstall: canCopy), primary: { [weak self] in self?.enable(afterCopy: false) },
                 secondary: { [weak self] in
                     self?.decision = InstallDecision.reinstallSystem
                     self?.installForThisMac()
                 })
        case .enableSystemNewer:
            show(Screens.newer(installed: systemVersion ?? "", bundled: Paths.bundledVersion), primary: { [weak self] in self?.enable(afterCopy: false) })
        case .account(.enableNewer):
            show(Screens.newer(installed: accountVersion ?? "", bundled: Paths.bundledVersion), primary: { [weak self] in self?.enable(afterCopy: false) })
        case .accountNewer:
            show(Screens.accountNewer(installed: accountVersion ?? "", bundled: Paths.bundledVersion),
                 secondary: { [weak self] in self?.showWelcome() })
        case .account(.enableSame):
            show(Screens.same(canReinstall: canCopy), primary: { [weak self] in self?.enable(afterCopy: false) },
                 secondary: { [weak self] in self?.copyThenEnable() })
        case .account(.install), .account(.update):
            guard canCopy else { return fail(Screens.incomplete) }
            copyThenEnable()
        }
    }

    /// The ETen carry-over (app-sandbox.md section 2.7) with the state read at the start, before
    /// `swap`, off the main thread; then `done` on the main thread with swap's result.
    private func carryOverThen<T>(_ swap: @escaping () -> T, done: @escaping (T) -> Void) {
        let (bundleID, modes) = (startBundleID, startModes)
        // The controller lives as long as the app (it is the application delegate).
        DispatchQueue.global(qos: .userInitiated).async {
            let result = InstallerFlow.copy(bundleID: bundleID, modes: modes,
                                            layoutOnDisk: { InstallerFlow.layoutOnDisk(bundleID: $0, home: Paths.home) },
                                            swap: swap)
            DispatchQueue.main.async { MainActor.assumeIsolated { done(result) } }
        }
    }

    private func copyThenEnable() {
        show(Screens.working(Screens.copying))
        // The fullFlow call after enabling stays; it does not rewrite a chosen layout.
        carryOverThen(copyFiles) { failure in
            if let failure { self.fail(failure) } else { self.enable(afterCopy: true) }
        }
    }

    /// system-install.md section 2.3: extract and check the signature in this process (an early,
    /// friendly failure only), off the main thread; then the root stage through the system's
    /// password dialog on the main thread.
    private func installForThisMac() {
        guard Paths.zip != nil, Paths.script != nil else { return fail(Screens.incomplete) }
        show(Screens.working(Screens.copying))
        DispatchQueue.global(qos: .userInitiated).async {
            let prepared: Result<URL, Failure> = extractBundled().flatMap { tmp in
                guard RootStage.satisfies(bundle: tmp.appendingPathComponent(RootStage.appName), requirement: RootStage.requirement) else {
                    try? FileManager.default.removeItem(at: tmp)
                    return .failure(Failure(message: L("內附的輸入法簽章不對，沒有安裝。請重新下載安裝程式。",
                                                       "The bundled input method's signature is not valid; nothing was installed. Download the installer again.")))
                }
                return .success(tmp)
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    switch prepared {
                    case .failure(let failure): self.fail(failure)
                    case .success(let tmp): self.rootStage(tmp)
                    }
                }
            }
        }
    }

    private func rootStage(_ tmp: URL) {
        let remove = { try? FileManager.default.removeItem(at: tmp) }
        let source: String
        do {
            // A build without the version section has no constant; the builder refuses it as a bad version.
            source = try RootStage.appleScript(source: tmp.path, .production(version: Paths.expectedBundleVersion ?? ""))
        } catch RootStage.BuildError.badVersion {
            remove()
            return fail(Failure(message: L("這份安裝程式建置時沒有寫入正確的版本，不能裝給這台 Mac，什麼都沒有改。請重新下載安裝程式。",
                                           "This installer was built without a valid version, so it cannot install for this Mac; nothing was changed. Download the installer again.")))
        } catch {
            remove()
            return fail(Failure(message: L("暫存資料夾的路徑不是預期的樣子，沒有安裝。", "The temporary folder's path is not as expected; nothing was installed."),
                                detail: tmp.path))
        }
        show(Screens.working(Screens.password))
        // On the main thread (section 2.3), after this screen has been drawn.
        DispatchQueue.main.async {
            var error: NSDictionary?
            _ = NSAppleScript(source: source)?.executeAndReturnError(&error)
            remove()
            if let error {
                if (error[NSAppleScript.errorNumber] as? Int) == self.userCanceled { return self.showWelcome() }
                return self.fail(Failure(message: L("裝到這台 Mac 時失敗了，原本裝著的沒有變動。", "Installing for this Mac failed; the installed copy was not changed."),
                                         detail: error[NSAppleScript.errorMessage] as? String))
            }
            // The ETen carry-over only now (code review: before the dialog, Cancel would leave it
            // written), and still before the swap that matters: the sandboxed /Library copy cannot
            // run yet, since the old processes are ended only in the user stage.
            self.userStageThenEnable()
        }
    }

    /// Section 2.4 items 1 to 3 (with the ETen carry-over first), then registration.
    private func userStageThenEnable() {
        show(Screens.working(decision.movesAccountCopy ? Screens.movingAccountCopy : Screens.loading))
        carryOverThen(userStage) { failure in
            if let failure { self.fail(failure) } else { self.enable(afterCopy: true) }
        }
    }


    /// s3c section 2.3: against the installed copy, with its own bundle ID and preference domain.
    /// Installer-v2 section 9.2 item 2: only after a copy can the system's "not accepted" be a
    /// moment that passes, so only then is it polled; without a copy it goes straight to activate.
    /// System-install.md section 2.4: after a location change (`decision.moved`) registration is
    /// forced and the log-out screen follows.
    private func enable(afterCopy: Bool) {
        show(Screens.working(Screens.enabling))
        guard let bundleID = Paths.info(target)?["CFBundleIdentifier"] as? String,
              let defaults = UserDefaults(suiteName: bundleID) else {
            fail(Failure(message: L("找不到已安裝的善解輸入法。", "The installed input method was not found.")))
            return
        }
        let moved = decision.moved
        InstallerFlow.logRegistration(moved: moved)
        let result = Registration.run(bundleURL: target, bundleID: bundleID, defaults: defaults, moved: moved)
        InstallerFlow.logOutcome(result.outcome)
        switch InstallerFlow.next(after: result.outcome, moved: moved, firstInstall: decision.firstInstall) {
        case .tryIt: showTry(note: result.legacyDisableFailed ? Screens.legacyNote : nil)
        case .activate: showActivate()
        case .waiting: if afterCopy { poll(bundleID: bundleID) } else { showActivate() }
        case .failed: fail(Failure(message: Screens.notAcceptedMessage))
        case .logOut: show(Screens.logOut(), primary: { NSApp.terminate(nil) })
        case .logOutThenAdd: show(Screens.logOutThenAdd(), primary: { NSApp.terminate(nil) })
        case .failedLogOut:
            let message = result.outcome == .registrationFailed ? Screens.registrationFailedMessage : Screens.enableFailedMessage
            show(Screens.failedLogOut(Failure(message: message)))
        }
    }

    private func showActivate(note: String? = nil) {
        show(Screens.activate(note: note),
             primary: { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.Keyboard-Settings.extension")!) },
             secondary: { [weak self] in self?.recheck() })
    }

    /// Installer-v2 section 9.2 item 3: reads whether the system accepted it first, and only an
    /// accepted input method goes on to `Registration.run` (which then skips, or cleans up the
    /// modes of earlier versions). Not accepted never reaches a TIS call that changes anything,
    /// whatever `run` would have decided.
    private func recheck() {
        guard let bundleID = Paths.info(target)?["CFBundleIdentifier"] as? String,
              let defaults = UserDefaults(suiteName: bundleID) else {
            fail(Failure(message: L("找不到已安裝的善解輸入法。", "The installed input method was not found.")))
            return
        }
        guard Registration.isAccepted(bundleID: bundleID) else {
            showActivate(note: Screens.notDetected)
            return
        }
        let result = Registration.run(bundleURL: target, bundleID: bundleID, defaults: defaults)
        switch InstallerFlow.next(after: result.outcome) {
        case .tryIt: showTry(note: result.legacyDisableFailed ? Screens.legacyNote : nil)
        case .activate, .waiting, .logOut, .logOutThenAdd, .failedLogOut: showActivate(note: Screens.notDetected)
        case .failed: fail(Failure(message: L("系統沒有接受這個輸入法。", "The system did not accept the input method.")))
        }
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
                        Registration.run(bundleURL: self.target, bundleID: bundleID, defaults: $0)
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
        guard let bundleID = Paths.info(target)?["CFBundleIdentifier"] as? String, Registration.selectMode(bundleID: bundleID) else {
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
