import AppKit
import SwiftUI

@main
struct MathHarnessDesktopApp: App {
  @StateObject private var model = AppModel()

  var body: some Scene {
    WindowGroup {
      RootView()
        .environmentObject(model)
        .task { await model.start() }
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
    }
  }
}
