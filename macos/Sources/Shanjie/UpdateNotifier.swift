import AppKit
import os
import ShanjieKit
import UserNotifications

/// The system notification for a newer version (docs/spec/install-and-update.md §3.8): the native notification center, posted by
/// this process when its background check finds a newer version (`Updates.notify`, decided by `Updates.shouldNotify`). The
/// permission is asked the first time there is something to notify and never again after it was refused; a click starts the
/// same update flow as the input menu item (`Updates.install`). The decisions are `Updates`'s; this class only talks to the
/// notification center.
@MainActor
final class UpdateNotifier: NSObject, UNUserNotificationCenterDelegate {
    private static let log = Logger(subsystem: "com.nyanako.inputmethod.shanjie", category: "notify")
    /// One identifier: each day's notification replaces the previous one, so Notification Center keeps a single entry.
    nonisolated static let identifier = "update-available"

    private let updates: Updates
    private let center = UNUserNotificationCenter.current()

    /// Sets itself as the center's delegate at once: `runServer` builds it before `app.run()`, so a click that started this
    /// process (it was not running) still reaches `didReceive`.
    init(updates: Updates) {
        self.updates = updates
        super.init()
        center.delegate = self
        updates.notify = { [weak self] version in self?.post(version) }
        updates.refreshPermission = { [weak self] in self?.refreshPermission() }
        updates.openNotificationSettings = { [weak self] in self?.openSettings() }
        updates.clearNotification = { [weak self] in
            self?.center.removeDeliveredNotifications(withIdentifiers: [Self.identifier])
        }
        refreshPermission()
    }

    /// Not yet decided: ask, and post only if allowed. Allowed: post. Denied or anything else: nothing, not even a log of the
    /// version (the input menu item stays).
    private func post(_ version: String) {
        center.getNotificationSettings { settings in
            let status = settings.authorizationStatus
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self.post(version, status: status) }
            }
        }
    }

    private func post(_ version: String, status: UNAuthorizationStatus) {
        switch status {
        case .notDetermined:
            center.requestAuthorization(options: [.alert, .sound]) { granted, _ in
                DispatchQueue.main.async {
                    MainActor.assumeIsolated {
                        if granted { self.add(version) }
                        self.refreshPermission()
                    }
                }
            }
        case .authorized, .provisional, .ephemeral:
            add(version)
        default:
            break
        }
    }

    private func add(_ version: String) {
        let content = UNMutableNotificationContent()
        content.title = UpdateText.notificationTitle(version)
        content.body = UpdateText.notificationBody
        center.add(UNNotificationRequest(identifier: Self.identifier, content: content, trigger: nil)) { error in
            if error != nil { Self.log.error("posting the update notification failed") }
        }
    }

    /// Denied, or allowed with both banners and Notification Center turned off in System Settings, reads `.denied` (nothing
    /// would show); the 關於 page then offers 打開通知設定.
    private func refreshPermission() {
        center.getNotificationSettings { settings in
            let p: NotifyPermission
            switch settings.authorizationStatus {
            case .notDetermined: p = .notDetermined
            case .authorized, .provisional, .ephemeral:
                p = settings.alertSetting == .disabled && settings.notificationCenterSetting == .disabled ? .denied : .allowed
            default: p = .denied
            }
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self.updates.notifyPermission = p }
            }
        }
    }

    /// System Settings at this app's notification settings. The settings window floats above other windows, so it closes first,
    /// as before 善解更新 opens (docs/spec/install-and-update.md §3.6).
    private func openSettings() {
        updates.closeSettings()
        let id = Bundle.main.bundleIdentifier ?? ""
        let pane = "x-apple.systempreferences:com.apple.Notifications-Settings.extension"
        guard let url = URL(string: id.isEmpty ? pane : "\(pane)?id=\(id)") else { return }
        if !NSWorkspace.shared.open(url) { Self.log.error("opening the notification settings failed") }
    }

    /// The input method has no foreground; the banner shows as for any app.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                            withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list])
    }

    /// A click on the update notification: the same as the input menu item 有新版本 X，按此更新… (a silent check, then 善解更新).
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                            withCompletionHandler completionHandler: @escaping () -> Void) {
        let isUpdate = response.notification.request.identifier == Self.identifier
            && response.actionIdentifier == UNNotificationDefaultActionIdentifier
        DispatchQueue.main.async {
            MainActor.assumeIsolated {
                if isUpdate { self.updates.install() }
                completionHandler()
            }
        }
    }
}
