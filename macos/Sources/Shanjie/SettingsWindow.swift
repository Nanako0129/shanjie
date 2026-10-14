import AppKit
import ShanjieKit
import SwiftUI
import UniformTypeIdentifiers

/// The settings window (docs/contracts/settings-window.md section 2.1): one NSWindow with a SwiftUI
/// grouped Form, in the input method's own process. It has no text field, so it needs no Edit menu and
/// the input method never types into its own window. Brought forward like `AlertDialogs`: remember the
/// frontmost app, `NSApp.activate()`, `makeKeyAndOrderFront`; on close the focus goes back.
@MainActor
final class SettingsWindowController: NSObject, NSWindowDelegate {
    private let shell: Shell
    /// Built on the first `show()`, so a user who never opens the window pays nothing.
    private var model: SettingsModel?
    private var window: NSWindow?
    private var host: NSHostingView<SettingsForm>?
    private var previous: NSRunningApplication?
    /// The 個別設定的 App names and icons, kept across window updates (profile-choice section 2.4).
    private let appLabels = AppLabels()

    init(shell: Shell) {
        self.shell = shell
    }

    func show() {
        if let front = NSWorkspace.shared.frontmostApplication, front != NSRunningApplication.current {
            previous = front
        }
        let m = model ?? SettingsModel(shell: shell)
        model = m
        m.refreshExternal()
        let w = window ?? makeWindow(m)
        window = w
        fit(w)
        center(w)
        NSApp.activate()
        w.makeKeyAndOrderFront(nil)
        // activate() is only a request (cooperative activation, macOS 14+); on the user's machine it was
        // refused and the window stayed behind the frontmost app (device check 2026-10-10). Like vChewing,
        // put it in front regardless; a click on it then activates the input method.
        w.orderFrontRegardless()
    }

    /// Sizes the window to the form, keeping its top edge where it is (`show()` centres it afterwards).
    private func fit(_ w: NSWindow) {
        guard let host else { return }
        let top = w.frame.maxY
        let size = host.fittingSize
        w.setContentSize(NSSize(width: size.width, height: size.height + Self.titleBarHeight))
        w.setFrameTopLeftPoint(NSPoint(x: w.frame.minX, y: top))
    }

    /// Profile-choice section 2.4: the 個別設定的 App list grew or shrank while the window is open. Called one main-actor turn
    /// after SwiftUI's onChange, so the hosting view's fitting size is read after the change is laid out (inferred, not measured;
    /// the device check sees it).
    private func refit() {
        guard let window, window.isVisible else { return }
        fit(window)
    }

    /// The window's background material (user, 2026-10-10: Liquid Glass was too see-through; a flatter glass).
    /// `.popover` was picked by eye on the system's list, not measured; the user's device check passed (2026-10-10).
    static let backgroundMaterial: NSVisualEffectView.Material = .popover

    /// The glass runs under the title bar, as in Syrtis's settings window (TokenBar SettingsWindowController.swift):
    /// with the window's background clear, a title bar outside the glass was see-through (user, 2026-10-10, third
    /// visual round). The form starts this far down. The mask here deliberately leaves out the window's
    /// `.fullSizeContentView`: with it the content rect is the whole frame and this comes out 0. 32 pt on the maintainer's
    /// machine (2026-10-10 review probe); a toolbar or another title bar style would need this changed.
    static let titleBarHeight = NSWindow.frameRect(forContentRect: NSRect(x: 0, y: 0, width: 100, height: 100), styleMask: [.titled, .closable]).height - 100

    private func makeWindow(_ model: SettingsModel) -> NSWindow {
        // The system's frosted material behind the content, never around it (a wrapped view gets vibrancy
        // and washed-out text; Syrtis, PR 491); the SwiftUI form sits on top with its own background hidden.
        // Not Liquid Glass (NSGlassEffectView): on device it was too see-through for a settings window, and the
        // user asked for a flatter glass (2026-10-10, third visual round). The tint preview keeps the real
        // candidate glass; whether its depth on this backdrop matches the bar over an app was not measured.
        let glass = NSVisualEffectView()
        glass.material = Self.backgroundMaterial
        glass.blendingMode = .behindWindow
        glass.state = .active
        let host = NSHostingView(rootView: SettingsForm(model: model, appLabels: appLabels, onRowCountChange: { [weak self] in
            Task { @MainActor in self?.refit() }
        }))
        host.safeAreaRegions = []  // placed below the title bar by its top constraint, so no second inset
        self.host = host
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
        // Above ordinary app windows even when the input method is not active, as AlertDialogs does with
        // .modalPanel (contract section 2.1, device check 2026-10-10).
        w.level = .floating
        w.delegate = self
        return w
    }

    /// Centred on the screen the mouse is on: the user just clicked the menu there, and `NSScreen.main`
    /// is the screen of the key window, which an input method's process does not have. Done after the
    /// content size is set (`center()` before layout put the window at the top, device check 2026-10-10).
    private func center(_ w: NSWindow) {
        let mouse = NSEvent.mouseLocation
        guard let screen = NSScreen.screens.first(where: { NSMouseInRect(mouse, $0.frame, false) }) ?? NSScreen.main else { return }
        let area = screen.visibleFrame
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

/// System controls only. Order follows the menu (section 2.2); each control writes through `SettingsModel`.
struct SettingsForm: View {
    /// Chosen to fit the controls and their labels on one line each; not measured against any reference.
    private static let formWidth: CGFloat = 420
    /// Icon-to-slider gap, Syrtis's value (TokenBar GlassTintControl.swift `HStack(spacing: 8)`); not measured.
    private static let tintIconSpacing: CGFloat = 8
    /// The app icons in the 個別設定的 App list: one line of body text; not measured against any reference.
    private static let appIconSize: CGFloat = 16
    @ObservedObject var model: SettingsModel
    let appLabels: AppLabels
    /// The 個別設定的 App list changed length: the window refits its height.
    let onRowCountChange: () -> Void

    var body: some View {
        Form {
            Section("鍵盤") {
                Picker("鍵盤排列", selection: Binding(get: { model.layout }, set: { model.selectLayout($0) })) {
                    Text("標準").tag(InputMode.standard)
                    Text("倚天").tag(InputMode.eten)
                }
            }
            Section("選字") {
                Toggle(MenuEntry.Text.prediction, isOn: Binding(get: { model.prediction }, set: { model.setPrediction($0) }))
                // Greyed while the prediction row is off, like the menu item (v3-engine section 12.1).
                if model.showsAbbreviation {
                    Toggle(MenuEntry.Text.abbreviation, isOn: Binding(get: { model.abbreviation }, set: { model.setAbbreviation($0) }))
                        .disabled(!model.prediction)
                }
                // Same place as in the menu (after 即時預測 and 可省略韻母 when shown); greyed (setting kept) when the word list did not load
                // or the layout is Eten, like the menu item (zhen-mixed section 3.2).
                Toggle(MenuEntry.Text.english, isOn: Binding(get: { model.english }, set: { model.setEnglish($0) }))
                    .disabled(!model.englishEnabled)
                Toggle(MenuEntry.Text.demote, isOn: Binding(get: { model.demote }, set: { model.setDemote($0) }))
                // Right after the toggle that greys it. Greyed while 避免把敏感字詞排在前面 is off or the small model did not load,
                // like the menu item: the core makes no rerank job then (s7c2b section 2.3).
                Toggle(MenuEntry.Text.rerank, isOn: Binding(get: { model.rerank }, set: { model.setRerank($0) }))
                    .disabled(!model.rerankEnabled)
                // Rerank strength (docs/contracts/rerank-strength.md section 3): always the three choices, greyed with the toggle,
                // or when the loaded model has only the standard set.
                Picker("強度", selection: Binding(get: { model.rerankLevel }, set: { model.setRerankLevel($0) })) {
                    Text("保守").tag(RerankLevel.conservative)
                    Text("標準").tag(RerankLevel.standard)
                    Text("積極").tag(RerankLevel.aggressive)
                }
                .pickerStyle(.segmented)
                .disabled(!model.rerankLevelEnabled)
                Toggle(MenuEntry.Text.acgPack, isOn: Binding(get: { model.acgPack }, set: { model.setAcgPack($0) }))
                if let line = model.acgDataLine {
                    Text(line).font(.caption).foregroundStyle(.secondary)
                }
            }
            // profile-choice section 2.4, between 選字 and 外觀.
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
            Section("外觀") {
                // Before the glass tint (candidate-vertical contract section 2.1). A candidate window already open keeps its orientation, so the next one uses the new setting. (The core also turns a prediction row on screen, but a click in this window is inferred, not measured, to commit the composition and hide the panel first.)
                Picker("候選窗方向", selection: Binding(get: { model.candidateVertical }, set: { model.setCandidateVertical($0) })) {
                    Text("橫排").tag(false)
                    Text("直排").tag(true)
                }
                .pickerStyle(.segmented)
                // The end icons are Syrtis's (TokenBar GlassTintControl.swift): outline at the clear end, filled at the deep
                // end, in an HStack beside the slider. As the Slider's own value labels inside a grouped Form both came out
                // filled on device (user, 2026-10-10). The slider keeps the regular size (Syrtis's is .small), as checked on
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
            Section("選字記憶") {
                if model.pausedSecure { Text(MenuEntry.Text.pausedSecure).foregroundStyle(.secondary) }
                if model.unavailable { Text(MenuEntry.Text.unavailable).foregroundStyle(.secondary) }
                Toggle(MenuEntry.Text.excludeBackup, isOn: Binding(get: { model.backupExcluded }, set: { model.setBackupExcluded($0) }))
                Button(MenuEntry.Text.clear) { model.clear() }
            }
        }
        .onChange(of: model.appProfiles.count) { onRowCountChange() }
        .formStyle(.grouped)
        .scrollContentBackground(.hidden)  // the window's glass shows through
        .frame(width: Self.formWidth)
        .fixedSize(horizontal: false, vertical: true)
    }
}

/// The name and icon of one app in the 個別設定的 App list (profile-choice section 2.4), looked up here so `SettingsModel` and
/// the tests never touch NSWorkspace. An app that is gone shows its bundle ID and the generic app icon. This code logs nothing;
/// whether the system's LaunchServices lookup logs the queried bundle ID is unverified (contract section 3.4 checks it on device).
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
