import Foundation

enum AppInfo {
  /// 打包后取 Info.plist；从源码运行时回落到这里的常量。
  static var version: String {
    Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? fallbackVersion
  }

  static let fallbackVersion = "0.19.0"
}
