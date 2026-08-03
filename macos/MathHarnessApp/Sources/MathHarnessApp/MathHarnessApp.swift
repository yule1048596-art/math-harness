import AppKit
import SwiftUI

@main
struct MathHarnessDesktopApp: App {
  @StateObject private var model = AppModel()
  @AppStorage(AppSettingsKey.appearance) private var appearance = AppAppearance.system
    .rawValue

  private var colorScheme: ColorScheme? {
    switch AppAppearance(rawValue: appearance) ?? .system {
    case .system: nil
    case .light: .light
    case .dark: .dark
    }
  }

  var body: some Scene {
    WindowGroup {
      RootView()
        .environmentObject(model)
        .preferredColorScheme(colorScheme)
        .task {
          // 迁移必须先于后端启动：BackendProcessController 读的是迁移后的档案。
          AppSettings.migrateLegacySettingsIfNeeded()
          await model.start()
        }
        .onReceive(
          NotificationCenter.default.publisher(
            for: NSApplication.willTerminateNotification
          )
        ) { _ in
          model.stopBackend()
        }
    }
    .defaultSize(width: 1_180, height: 760)
    .windowToolbarStyle(.unifiedCompact)

    Settings {
      SettingsView()
        .environmentObject(model)
        .preferredColorScheme(colorScheme)
    }
  }
}
