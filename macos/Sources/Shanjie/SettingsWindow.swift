import AppKit
import ShanjieKit
import SwiftUI
import UniformTypeIdentifiers

/// The settings window (docs/spec/macos-shell.md §9.1, layout and size from docs/spec/macos-shell.md §9.2): one
/// NSWindow with a SwiftUI sidebar of pages and the selected page's grouped Form, in the input method's own process. It
/// has no text field, so it needs no Edit menu and the input method never types into its own window. Brought forward like
/// `AlertDialogs`: remember the frontmost app, `NSApp.activate()`, `makeKeyAndOrderFront`; on close the focus goes back.
@MainActor
final class SettingsWindowController: NSObject, NSWindowDelegate {
    private let shell: Shell
    /// Built on the first `show()`, so a user who never opens the window pays nothing.
    private var model: SettingsModel?
    private var window: NSWindow?
    private var previous: NSRunningApplication?
    /// The 個別設定的 App names and icons, kept across window updates (docs/spec/macos-shell.md §9.3).
    private let appLabels = AppLabels()
    /// The selected sidebar page; `show(page:)` sets it (docs/spec/english-mixed.md §10.3 opens the 選字 page).
    private let navigation = SettingsNavigation()

    init(shell: Shell) {
        self.shell = shell
        super.init()
        // A Dock, resolution or display change while the window is open shrinks the usable area: refit and clamp again
        // (docs/spec/macos-shell.md §9.2, any screen).
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.refit() }
        }
    }

    func show(page: SettingsPage? = nil) {
        if let page { navigation.page = page }
        if let front = NSWorkspace.shared.frontmostApplication, front != NSRunningApplication.current {
            previous = front
        }
        let m = model ?? SettingsModel(shell: shell)
        model = m
        m.refreshExternal()
        let w = window ?? makeWindow(m)
        window = w
        let screen = Self.screenUnderMouse()
        fit(w, on: screen)
        center(w, on: screen)
        NSApp.activate()
        w.makeKeyAndOrderFront(nil)
        // activate() is only a request (cooperative activation, macOS 14+); on the user's machine it was
        // refused and the window stayed behind the frontmost app (device check). Like vChewing,
        // put it in front regardless; a click on it then activates the input method.
        w.orderFrontRegardless()
    }

    /// Before 善解更新 opens (docs/spec/install-and-update.md §3.4, §3.6): this window floats above it (`level`). Nothing when closed.
    /// `previous` is dropped first, so `windowWillClose` re-activates no other app right before the helper asks to come to
    /// the front (under cooperative activation that app could keep the focus).
    func close() {
        previous = nil
        window?.close()
    }

    /// Sizes the window (docs/spec/macos-shell.md §9.2): one fixed height, `SettingsLayout.frameHeight`, clamped to the usable
    /// height of `screen`, keeping the top edge where it is (`show()` centres it afterwards, `refit()` clamps it). Nothing
    /// is measured: pages taller than the window scroll. With `.fullSizeContentView` the content rect is the whole frame,
    /// so this height is the frame's.
    private func fit(_ w: NSWindow, on screen: NSScreen?) {
        let top = w.frame.maxY
        let visible = screen?.visibleFrame.height ?? .greatestFiniteMagnitude
        w.setContentSize(NSSize(width: SettingsForm.width, height: SettingsLayout.frameHeight(visibleHeight: visible)))
        w.setFrameTopLeftPoint(NSPoint(x: w.frame.minX, y: top))
    }

    /// The screen changed (Dock, resolution, text size, display removed) while the window is open: recompute the height,
    /// then clamp the frame back into the screen's usable area (docs/spec/macos-shell.md §9.2), so a window dragged down
    /// to the Dock moves up instead of sitting under it.
    private func refit() {
        guard let window, window.isVisible else { return }
        let screen = window.screen ?? NSScreen.main
        fit(window, on: screen)
        if let area = screen?.visibleFrame {
            window.setFrameOrigin(settingsFrame(window.frame, clampedInto: area).origin)
        }
    }

    /// The window's background material (Liquid Glass was too see-through; a flatter glass).
    /// `.popover` was picked by eye on the system's list, not measured; a device check passed.
    static let backgroundMaterial: NSVisualEffectView.Material = .popover
    /// Above ordinary app windows even when the input method is not active, as AlertDialogs does with .modalPanel (docs/spec/macos-shell.md
    /// §9.1, device check).
    static let level: NSWindow.Level = .floating

    /// The glass runs under the title bar, as in Syrtis's settings window (TokenBar SettingsWindowController.swift):
    /// with the window's background clear, a title bar outside the glass was see-through. The form starts this far down. The mask here deliberately leaves out the window's
    /// `.fullSizeContentView`: with it the content rect is the whole frame and this comes out 0. 32 pt on the
    /// machine (review probe); a toolbar or another title bar style would need this changed.
    static let titleBarHeight = NSWindow.frameRect(forContentRect: NSRect(x: 0, y: 0, width: 100, height: 100), styleMask: [.titled, .closable]).height - 100

    private func makeWindow(_ model: SettingsModel) -> NSWindow {
        // The system's frosted material behind the content, never around it (a wrapped view gets vibrancy
        // and washed-out text; Syrtis, PR 491); the SwiftUI form sits on top with its own background hidden.
        // Not Liquid Glass (NSGlassEffectView): on device it was too see-through for a settings window, and the
        // glass was made flatter. The tint preview keeps the real
        // candidate glass; whether its depth on this backdrop matches the bar over an app was not measured.
        let glass = NSVisualEffectView()
        glass.material = Self.backgroundMaterial
        glass.blendingMode = .behindWindow
        glass.state = .active
        let host = NSHostingView(rootView: SettingsForm(model: model, appLabels: appLabels, navigation: navigation))
        host.safeAreaRegions = []  // placed below the title bar by its top constraint, so no second inset
        host.sizingOptions = []  // the window's size comes from fit() alone (docs/spec/macos-shell.md §9.2), not the content's
        let container = NSView()
        let layers: [(NSView, CGFloat)] = [(glass, 0), (host, Self.titleBarHeight)]
        for (v, top) in layers {
            v.translatesAutoresizingMaskIntoConstraints = false
            container.addSubview(v)
            NSLayoutConstraint.activate([
                v.leadingAnchor.constraint(equalTo: container.leadingAnchor), v.trailingAnchor.constraint(equalTo: container.trailingAnchor),
                v.topAnchor.constraint(equalTo: container.topAnchor, constant: top), v.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            ])
        }
        let w = NSWindow(contentRect: .zero, styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: false)
        w.titlebarAppearsTransparent = true
        w.contentView = container
        w.title = "善解設定"
        w.isOpaque = false
        w.backgroundColor = .clear
        w.isReleasedWhenClosed = false
        w.level = Self.level
        w.delegate = self
        return w
    }

    /// The screen the mouse is on: the user just clicked the menu there, and `NSScreen.main` is the screen of the key
    /// window, which an input method's process does not have.
    private static func screenUnderMouse() -> NSScreen? {
        let mouse = NSEvent.mouseLocation
        return NSScreen.screens.first(where: { NSMouseInRect(mouse, $0.frame, false) }) ?? NSScreen.main
    }

    /// Centred on `screen`'s usable area. Done after the size is set (`center()` before layout put the window at the
    /// top, device check); the size is capped to that area, so the whole window is inside it.
    private func center(_ w: NSWindow, on screen: NSScreen?) {
        guard let area = screen?.visibleFrame else { return }
        w.setFrameOrigin(NSPoint(x: area.midX - w.frame.width / 2, y: area.midY - w.frame.height / 2))
    }

    /// The secure-input and cannot-save rows have no notification, so they are re-read each time the window is key.
    func windowDidBecomeKey(_ notification: Notification) {
        model?.refreshExternal()
    }

    func windowWillClose(_ notification: Notification) {
        // Only while we are still the active app: otherwise the user has moved on and this would steal focus.
        if NSApp.isActive { previous?.activate() }
        previous = nil
    }
}

/// The sidebar pages (docs/spec/macos-shell.md §9.2), in sidebar order.
enum SettingsPage: CaseIterable, Hashable {
    /// 關於 last (docs/spec/install-and-update.md §3.4–§3.6).
    case general, candidates, appearance, memory, diagnostics, about

    var title: String {
        switch self {
        case .general: "一般"
        case .candidates: "選字"
        case .appearance: "外觀"
        case .memory: "選字記憶"
        case .diagnostics: "診斷"
        case .about: "關於"
        }
    }

    /// SF Symbols picked by name from the system catalogue, not measured against a reference (first device round).
    var symbol: String {
        switch self {
        case .general: "gearshape"
        case .candidates: "character.cursor.ibeam"
        case .appearance: "paintpalette"
        case .memory: "brain"
        case .diagnostics: "stethoscope"
        case .about: "info.circle"
        }
    }
}

/// The window's content (docs/spec/macos-shell.md §9.2): the pages in a sidebar, the selected page's form beside it. The
/// selection is view state in the window's one hosting view, so reopening the window shows the last page in this run.
/// A plain fixed-width sidebar, not NavigationSplitView: that one's sidebar can be collapsed, and this window has no
/// toolbar or View menu to bring it back (checked: canCollapse true, no toolbar).
@MainActor
final class SettingsNavigation: ObservableObject {
    @Published var page: SettingsPage = .general
}

struct SettingsForm: View {
    /// The sidebar column; a round number for the short labels, not measured against System Settings (first device round).
    static let sidebarWidth: CGFloat = 160
    static var width: CGFloat { sidebarWidth + SettingsPageForm.formWidth }
    @ObservedObject var model: SettingsModel
    let appLabels: AppLabels
    @ObservedObject var navigation: SettingsNavigation

    var body: some View {
        HStack(spacing: 0) {
            // A click on the sidebar's empty space or a ⌘-click deselects; keep the page instead of showing none.
            List(SettingsPage.allCases, id: \.self, selection: Binding(get: { navigation.page }, set: { if let p = $0 { navigation.page = p } })) { p in
                Label(p.title, systemImage: p.symbol)
            }
            .listStyle(.sidebar)
            // Hidden like the form's, so the window's glass shows through both columns: with the list's own sidebar
            // background the two read as a different tint and opacity (seen in screenshots; checked in a
            // VM, the device check is docs/spec/install-and-update.md §10.4).
            .scrollContentBackground(.hidden)
            .frame(width: Self.sidebarWidth)
            // A fresh form per page, so a page opens at its top rather than at the previous page's scroll offset.
            SettingsPageForm(page: navigation.page, model: model, appLabels: appLabels)
                .id(navigation.page)
        }
    }
}

/// One page's controls, system controls only; each control writes through `SettingsModel`. The sections are the single
/// form's (docs/spec/macos-shell.md §9.3), grouped by page.
struct SettingsPageForm: View {
    /// Chosen to fit the controls and their labels on one line each; not measured against any reference.
    static let formWidth: CGFloat = 420
    /// Icon-to-slider gap, Syrtis's value (TokenBar GlassTintControl.swift `HStack(spacing: 8)`); not measured.
    private static let tintIconSpacing: CGFloat = 8
    /// The app icons in the 個別設定的 App list: one line of body text; not measured against any reference.
    private static let appIconSize: CGFloat = 16
    /// The four pair checkboxes, in the order of the bits of `shanjie_engine_set_fuzzy` (docs/spec/decoder.md §6.2).
    private static let fuzzyPairs = ["ㄣ／ㄥ", "ㄓ／ㄗ", "ㄔ／ㄘ", "ㄕ／ㄙ"]
    let page: SettingsPage
    @ObservedObject var model: SettingsModel
    let appLabels: AppLabels

    var body: some View {
        Form {
            switch page {
            case .general:
                Section("鍵盤") {
                    Picker("鍵盤排列", selection: Binding(get: { model.layout }, set: { model.selectLayout($0) })) {
                        Text("標準").tag(InputMode.standard)
                        Text("倚天").tag(InputMode.eten)
                    }
                }
                Section("聊天／書面") {
                    Picker("切換方式", selection: Binding(get: { model.profileMode }, set: { model.setProfileMode($0) })) {
                        Text("依 App 自動").tag(ProfileMode.auto)
                        Text("一律聊天").tag(ProfileMode.chat)
                        Text("一律書面").tag(ProfileMode.formal)
                    }
                    Text("依 App 自動：聊天 App（Discord、LINE、訊息等）用聊天設定，其他 App 用書面設定。")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("個別設定的 App")
                    if model.appProfiles.isEmpty {
                        Text("在 App 裡打開善解選單，勾選「\(MenuEntry.Text.appChat)」，就會列在這裡。")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        ForEach(appLabels.sorted(model.appProfiles), id: \.row.bundle) { item in
                            HStack {
                                Image(nsImage: item.label.icon).resizable().frame(width: Self.appIconSize, height: Self.appIconSize)
                                    .accessibilityHidden(true)
                                Text(item.label.name)
                                Spacer()
                                // Dimmed outside 依 App 自動, where the choice is kept but not used; removing stays possible.
                                Text(item.row.chat ? "聊天" : "書面")
                                    .foregroundStyle(model.profileMode == .auto ? .secondary : .tertiary)
                                Button { model.removeAppProfile(item.row.bundle) } label: { Image(systemName: "minus.circle") }
                                    .buttonStyle(.borderless)
                                    .accessibilityLabel("移除")
                            }
                        }
                    }
                }
            case .candidates:
                Section("選字") {
                    Toggle(MenuEntry.Text.prediction, isOn: Binding(get: { model.prediction }, set: { model.setPrediction($0) }))
                    // docs/spec/prediction.md §9.4: right under 即時預測, greyed (setting kept) while it is off; not in the menu.
                    Toggle(MenuEntry.Text.browserFirstSyllable,
                           isOn: Binding(get: { model.browserFirstSyllable }, set: { model.setBrowserFirstSyllable($0) }))
                        .disabled(!model.prediction)
                    Text(MenuEntry.Text.browserFirstSyllableNote)
                        .font(.caption).foregroundStyle(.secondary)
                    // Greyed while the prediction row is off, like the menu item (docs/spec/prediction.md §6).
                    if model.showsAbbreviation {
                        Toggle(MenuEntry.Text.abbreviation, isOn: Binding(get: { model.abbreviation }, set: { model.setAbbreviation($0) }))
                            .disabled(!model.prediction)
                    }
                    // Same place as in the menu (after 即時預測 and 可省略韻母 when shown); greyed (setting kept) when the layout is Eten, like the menu item (docs/spec/english-mixed.md §1.1, §11).
                    Toggle(MenuEntry.Text.english, isOn: Binding(get: { model.english }, set: { model.setEnglish($0) }))
                        .disabled(!model.englishEnabled)
                    // docs/spec/english-mixed.md §1.1: one line under the toggle, as the 聊天／書面 note above is written.
                    Text(MenuEntry.Text.englishDigitsNote)
                        .font(.caption).foregroundStyle(.secondary)
                    // docs/spec/english-mixed.md §1.1, §3.2: before the key-order choice; greyed unless key-order is enabled and on, since (b) needs it on.
                    Toggle(MenuEntry.Text.englishReversalGuard, isOn: Binding(get: { model.englishReversalGuard }, set: { model.setEnglishReversalGuard($0) }))
                        .disabled(!model.englishReversalGuardEnabled)
                    Text(MenuEntry.Text.englishReversalGuardNote)
                        .font(.caption).foregroundStyle(.secondary)
                    // docs/spec/english-mixed.md §11 with docs/spec/english-mixed.md §11: under the toggle, with a line of what it costs; greyed on the
                    // toggle's condition and while the toggle is off.
                    Toggle(MenuEntry.Text.englishKeyOrder, isOn: Binding(get: { model.englishKeyOrder }, set: { model.setEnglishKeyOrder($0) }))
                        .disabled(!model.englishKeyOrderEnabled)
                    Text(MenuEntry.Text.englishKeyOrderNote)
                        .font(.caption).foregroundStyle(.secondary)
                    Toggle(MenuEntry.Text.englishOverwrite, isOn: Binding(get: { model.englishOverwrite }, set: { model.setEnglishOverwrite($0) }))
                        .disabled(!model.englishOverwriteEnabled)
                    Text(MenuEntry.Text.englishOverwriteNote)
                        .font(.caption).foregroundStyle(.secondary)
                    // docs/spec/keys-and-composition.md §9.1: always enabled (it works with the English mixing off too: then the composition is sent first).
                    Toggle(MenuEntry.Text.shiftToggle, isOn: Binding(get: { model.shiftToggle }, set: { model.setShiftToggle($0) }))
                    Text(MenuEntry.Text.shiftToggleNote)
                        .font(.caption).foregroundStyle(.secondary)
                    // docs/spec/english-mixed.md §9, §11: always enabled, independent of the 中英混打 switch.
                    Toggle(MenuEntry.Text.spacing, isOn: Binding(get: { model.spacing }, set: { model.setSpacing($0) }))
                    Text(MenuEntry.Text.spacingNote)
                        .font(.caption).foregroundStyle(.secondary)
                    Toggle(MenuEntry.Text.demote, isOn: Binding(get: { model.demote }, set: { model.setDemote($0) }))
                    // Right after the toggle that greys it. Greyed while 避免把敏感字詞排在前面 is off or the small model did not load,
                    // like the menu item: the core makes no rerank job then (docs/spec/semantic-rerank.md §8.2, §8.3).
                    Toggle(MenuEntry.Text.rerank, isOn: Binding(get: { model.rerank }, set: { model.setRerank($0) }))
                        .disabled(!model.rerankEnabled)
                    // Rerank strength (docs/spec/semantic-rerank.md §8.2): always the three choices, greyed with the toggle,
                    // or when the loaded model has only the standard set.
                    Picker("強度", selection: Binding(get: { model.rerankLevel }, set: { model.setRerankLevel($0) })) {
                        Text("保守").tag(RerankLevel.conservative)
                        Text("標準").tag(RerankLevel.standard)
                        Text("積極").tag(RerankLevel.aggressive)
                    }
                    .pickerStyle(.segmented)
                    .disabled(!model.rerankLevelEnabled)
                }
                // docs/spec/lexicon-and-packs.md §4.2, §4.3: every pack switch lives here (the input menu only names the loaded packs); new packs join this section.
                Section("詞包") {
                    Toggle(MenuEntry.Text.acgPack, isOn: Binding(get: { model.acgPack }, set: { model.setAcgPack($0) }))
                    if let line = model.acgDataLine {
                        Text(line).font(.caption).foregroundStyle(.secondary)
                    }
                    // Next to the other word pack; off until chosen (docs/spec/lexicon-and-packs.md §7.2, §4.1, §4.2).
                    Toggle(MenuEntry.Text.quotePack, isOn: Binding(get: { model.quotePack }, set: { model.setQuotePack($0) }))
                    Toggle(MenuEntry.Text.itPack, isOn: Binding(get: { model.itPack }, set: { model.setItPack($0) }))
                    Toggle(MenuEntry.Text.beautyPack, isOn: Binding(get: { model.beautyPack }, set: { model.setBeautyPack($0) }))
                }
                // docs/spec/decoder.md §6.1: all off by default. The adjacent-key checkbox is greyed (value kept) in the Eten layout, where
                // the core does nothing with it.
                Section("模糊輸入") {
                    ForEach(0..<Self.fuzzyPairs.count, id: \.self) { bit in
                        Toggle(Self.fuzzyPairs[bit], isOn: Binding(get: { model.fuzzyFlags >> UInt32(bit) & 1 == 1 }, set: { model.setFuzzy(bit: bit, $0) }))
                    }
                    Toggle("鄰鍵打錯", isOn: Binding(get: { model.fuzzyFlags >> 4 & 1 == 1 }, set: { model.setFuzzy(bit: 4, $0) }))
                        .disabled(!model.fuzzyAdjacentEnabled)
                    Text("鄰鍵打錯：打出詞庫沒有的讀音時，改用鍵盤上左右相鄰的鍵來猜；只適用標準鍵盤排列。開著時 go、cp、cup 這類字會被收成中文，不會變英文；要英文就按住 Shift 打，或把這個關掉。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            case .appearance:
                Section("外觀") {
                    // Before the glass tint (docs/spec/macos-shell.md §8.6.1). A candidate window already open keeps its orientation, so the next one uses the new setting. (The core also turns a prediction row on screen, but a click in this window is inferred, not measured, to commit the composition and hide the panel first.)
                    Picker("候選窗方向", selection: Binding(get: { model.candidateVertical }, set: { model.setCandidateVertical($0) })) {
                        Text("橫排").tag(false)
                        Text("直排").tag(true)
                    }
                    .pickerStyle(.segmented)
                    // The end icons are Syrtis's (TokenBar GlassTintControl.swift): outline at the clear end, filled at the deep
                    // end, in an HStack beside the slider. As the Slider's own value labels inside a grouped Form both came out
                    // filled on device. The slider keeps the regular size (Syrtis's is .small), as checked on
                    // device. The icons are decoration, hidden from VoiceOver; the slider carries the label.
                    LabeledContent("候選窗玻璃深淺") {
                        HStack(spacing: Self.tintIconSpacing) {
                            Image(systemName: "rectangle.on.rectangle")
                                .foregroundStyle(.secondary)
                                .accessibilityHidden(true)
                            Slider(value: Binding(get: { model.glassTint }, set: { model.setGlassTint($0) }), in: 0...1)
                                .accessibilityLabel("候選窗玻璃深淺")
                            Image(systemName: "rectangle.fill.on.rectangle.fill")
                                .foregroundStyle(.secondary)
                                .accessibilityHidden(true)
                        }
                    }
                    // The real bar's glass, cells and tint decision (the depth over an app while typing was not compared).
                    HStack {
                        Spacer()
                        TintPreview(value: model.glassTint)
                        Spacer()
                    }
                }
            case .memory:
                Section("選字記憶") {
                    if model.pausedSecure { Text(MenuEntry.Text.pausedSecure).foregroundStyle(.secondary) }
                    if model.unavailable { Text(MenuEntry.Text.unavailable).foregroundStyle(.secondary) }
                    Toggle(MenuEntry.Text.excludeBackup, isOn: Binding(get: { model.backupExcluded }, set: { model.setBackupExcluded($0) }))
                    Button(MenuEntry.Text.clear) { model.clear() }
                }
            case .diagnostics:
                Section("診斷") {
                    Toggle("記錄偶發問題的診斷資料", isOn: Binding(get: { model.diagLog }, set: { model.setDiagLog($0) }))
                    Text("只存在這台 Mac 的記憶體裡，不會自動送出。會記下放開 Shift 時算不算單按（按著 Shift 打字時不記）、切換中英、換 App 的時間與 App 的識別碼（例如 com.apple.TextEdit），不記你打的字。紀錄留到輸入法結束，可能涵蓋好幾天；安全輸入時不記 Shift，切換中英照記。")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("複製診斷紀錄…") { model.openDiagPreview() }
                        .disabled(model.diagLogEmpty)
                }
            case .about:
                if let updates = model.updates {
                    // docs/spec/install-and-update.md §3.8: the user may have just changed it in System Settings.
                    AboutSections(updates: updates).onAppear { updates.refreshPermission() }
                }
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(.hidden)  // the window's glass shows through
        .frame(width: Self.formWidth)
        // docs/spec/macos-shell.md §13.3: the whole text first; only 複製 puts it, unchanged, on the pasteboard.
        .sheet(isPresented: Binding(get: { model.diagPreview != nil }, set: { if !$0 { model.closeDiagPreview() } })) {
            VStack(alignment: .leading) {
                ScrollView {
                    Text(model.diagPreview ?? "").font(.system(.caption, design: .monospaced))  // not selectable: ⌘C would skip the copy below
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(width: Self.diagPreviewSize.width, height: Self.diagPreviewSize.height)
                HStack {
                    Spacer()
                    Button("取消", role: .cancel) { model.closeDiagPreview() }.keyboardShortcut(.cancelAction)
                    Button("複製") { model.copyDiagPreview(to: NSPasteboard.general) }
                    .keyboardShortcut(.defaultAction)
                }
            }
            .padding()
        }
    }
    /// The preview's text area: wide enough for a line of the log in the caption monospaced font; not measured against a reference.
    private static let diagPreviewSize = CGSize(width: 460, height: 320)
}

/// The 關於 page (docs/spec/install-and-update.md §3.4–§3.6): version, the update controls, the site and the licence line. The
/// decisions behind it (what the version reads, when a check runs, what the result says) are `Updates`, in ShanjieKit.
private struct AboutSections: View {
    /// The site's front page, also the cask's homepage (packaging/Casks/shanjie.rb).
    private static let site = URL(string: "https://shanjie.nyanako.com/")!
    /// The bundled models (docs/spec/models.md §1), read once; empty (no section) when models.tsv is missing or unreadable.
    private static let models = ModelList.load(from: Bundle.main.url(forResource: "models", withExtension: "tsv"))
    @ObservedObject var updates: Updates

    var body: some View {
        Section("善解輸入法") {
            LabeledContent("版本", value: updates.versionText)
        }
        if !Self.models.isEmpty {
            Section("模型") {
                ForEach(Self.models, id: \.file) { LabeledContent($0.name, value: $0.code) }
            }
        }
        Section("更新") {
            Toggle("自動檢查更新", isOn: Binding(get: { updates.autoCheck }, set: { updates.setAutoCheck($0) }))
            Text(UpdateText.autoCheckNote).font(.caption).foregroundStyle(.secondary)
            // docs/spec/install-and-update.md §3.8: off with 自動檢查更新 (no background check, so no notification).
            Toggle(UpdateText.notifyToggle, isOn: Binding(get: { updates.notifyEnabled }, set: { updates.setNotify($0) }))
                .disabled(!updates.autoCheck)
            Text(UpdateText.notifyNote).font(.caption).foregroundStyle(.secondary)
            if updates.showsPermissionLine {
                HStack {
                    Text(UpdateText.permissionOff).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button(UpdateText.openNotificationSettings) { updates.openNotificationSettings() }
                }
            }
            Text(UpdateText.location(bundleURL: Bundle.main.bundleURL)).font(.caption).foregroundStyle(.secondary)
            LabeledContent("上次檢查") {
                Text(status).foregroundStyle(.secondary)
            }
            HStack {
                Button("立即檢查") { updates.checkNow() }
                    .disabled(updates.checking)
                if let v = updates.available {
                    Button(UpdateText.install(v)) { updates.install() }
                        .disabled(updates.checking)
                }
            }
        }
        Section {
            Link("shanjie.nyanako.com", destination: Self.site)
            Text("程式以 Apache-2.0 授權釋出，內含的 Sparkle 是 MIT；詞庫與模型的授權與出處在 App 的 Resources/LICENSES。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    /// 「日期 時間・結果」, 檢查中… while a check runs, — before any check.
    private var status: String {
        if updates.checking { return UpdateText.checking }
        guard let date = updates.lastCheck else { return "—" }
        let when = date.formatted(date: .numeric, time: .shortened)
        return UpdateText.result(updates.result, available: updates.available).map { "\(when)・\($0)" } ?? when
    }
}

/// The name and icon of one app in the 個別設定的 App list (docs/spec/macos-shell.md §9.3), looked up here so `SettingsModel` and
/// the tests never touch NSWorkspace. An app that is gone shows its bundle ID and the generic app icon. This code logs nothing;
/// whether the system's LaunchServices lookup logs the queried bundle ID is unverified (to be checked on a device; docs/spec/macos-shell.md §6.1).
struct AppLabel {
    let name: String
    let icon: NSImage

    init(bundle: String) {
        if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundle) {
            name = FileManager.default.displayName(atPath: url.path)
            icon = NSWorkspace.shared.icon(forFile: url.path)
        } else {
            name = bundle
            icon = NSWorkspace.shared.icon(for: .applicationBundle)
        }
    }
}

/// `AppLabel`s by bundle ID, so the list looks an app up once rather than on every redraw (a glass tint drag redraws the whole
/// form). Only bundle IDs not seen yet are looked up; ones no longer listed are forgotten, so a removed and re-added app is
/// looked up again.
@MainActor
final class AppLabels {
    private var byBundle: [String: AppLabel] = [:]

    /// The rows by name, then bundle ID, so equal names keep a fixed order.
    func sorted(_ rows: [AppProfileRow]) -> [(row: AppProfileRow, label: AppLabel)] {
        var kept: [String: AppLabel] = [:]
        let items = rows.map { row in
            let label = byBundle[row.bundle] ?? AppLabel(bundle: row.bundle)
            kept[row.bundle] = label
            return (row: row, label: label)
        }
        byBundle = kept
        return items.sorted {
            let order = $0.label.name.localizedStandardCompare($1.label.name)
            return order == .orderedSame ? $0.row.bundle < $1.row.bundle : order == .orderedAscending
        }
    }
}

/// `SampleCandidateBar` in SwiftUI; the slider value goes in on every update, so it follows a drag.
private struct TintPreview: NSViewRepresentable {
    let value: Double

    func makeNSView(context: Context) -> SampleCandidateBar { SampleCandidateBar() }
    func updateNSView(_ bar: SampleCandidateBar, context: Context) { bar.setTint(value) }
}
