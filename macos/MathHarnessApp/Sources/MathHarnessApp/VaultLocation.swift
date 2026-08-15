import AppKit
import Foundation
import MathHarnessCore

/// 用户选的 Obsidian vault 根目录，以及跨启动继续访问它的凭据。
///
/// ## 为什么要存 bookmark 而不是路径
///
/// 一条路径在下次启动时可能已经不对：用户重命名了文件夹、把 vault 挪到了别的盘、或者
/// 把它放在 iCloud 里而这台机器上还没下载。bookmark 能跟着文件走，也能在解析时明确
/// 告诉我们「找不到了」，而一条字符串路径只会在写入时才失败。
///
/// 当前打包出来的 App **没有开沙盒**（`build_macos_app.sh` 没有把 entitlements 传给
/// `codesign`）。带 `.withSecurityScope` 的 bookmark 在非沙盒进程里也能创建，所以这里
/// 一律先按 security-scoped 走，失败再退回普通 bookmark——将来真开了沙盒，这段代码
/// 不用改，只需要补上 `com.apple.security.files.bookmarks.app-scope`。
@MainActor
final class VaultLocation: ObservableObject {
  enum Status: Equatable {
    case notConfigured
    case ready(URL)
    /// 选过，但现在够不着。`reason` 直接显示给用户。
    case unavailable(reason: String)

    var url: URL? {
      if case .ready(let url) = self { return url }
      return nil
    }
  }

  @Published private(set) var status: Status = .notConfigured

  private var scopedURL: URL?
  private var isAccessing = false

  init() {
    restore()
  }

  var isConfigured: Bool {
    if case .notConfigured = status { return false }
    return true
  }

  /// 显示给用户的位置。没配置时是空串。
  var displayPath: String {
    switch status {
    case .ready(let url): url.path
    case .unavailable: AppSettings.vaultDisplayPath
    case .notConfigured: ""
    }
  }

  // MARK: - 选择与恢复

  /// 让用户选一个 vault 根目录。返回是否选成了。
  @discardableResult
  func chooseFolder() -> Bool {
    let panel = NSOpenPanel()
    panel.title = "选择 Obsidian Vault 根目录"
    panel.message = "知识库会导出到这个目录下的 math-harness 文件夹，不会碰其它笔记。"
    panel.canChooseFiles = false
    panel.canChooseDirectories = true
    panel.allowsMultipleSelection = false
    panel.canCreateDirectories = true
    guard panel.runModal() == .OK, let url = panel.url else { return false }
    return adopt(url)
  }

  @discardableResult
  func adopt(_ url: URL) -> Bool {
    guard let data = Self.makeBookmark(for: url) else {
      status = .unavailable(reason: "无法记住这个文件夹的访问权限，请换一个位置。")
      return false
    }
    stopAccessing()
    AppSettings.vaultBookmark = data
    AppSettings.vaultDisplayPath = url.path
    scopedURL = url
    isAccessing = url.startAccessingSecurityScopedResource()
    status = .ready(url)
    return true
  }

  func forget() {
    stopAccessing()
    AppSettings.vaultBookmark = nil
    AppSettings.vaultDisplayPath = ""
    scopedURL = nil
    status = .notConfigured
  }

  /// 从存下来的 bookmark 恢复。
  ///
  /// **任何一步失败都只是「够不着」，不是崩溃，也不该清掉配置**——vault 可能只是这次
  /// 没挂上外置盘，下次就回来了。清掉的话用户还得重新找一遍。
  private func restore() {
    guard let data = AppSettings.vaultBookmark else {
      status = .notConfigured
      return
    }
    var isStale = false
    do {
      let url = try URL(
        resolvingBookmarkData: data,
        options: [.withSecurityScope],
        relativeTo: nil,
        bookmarkDataIsStale: &isStale
      )
      guard FileManager.default.fileExists(atPath: url.path) else {
        status = .unavailable(reason: "上次选择的 Vault 目录现在找不到了。")
        return
      }
      scopedURL = url
      isAccessing = url.startAccessingSecurityScopedResource()
      status = .ready(url)
      if isStale, let refreshed = Self.makeBookmark(for: url) {
        // 目录被移动过。bookmark 跟到了新位置，把它重新存一份，免得下次解析更贵。
        AppSettings.vaultBookmark = refreshed
        AppSettings.vaultDisplayPath = url.path
      }
    } catch {
      status = .unavailable(
        reason: "无法访问上次选择的 Vault 目录：\(error.localizedDescription)"
      )
    }
  }

  /// 重新试一次。用户修好了（插上盘、下载完 iCloud）之后点它。
  func retry() {
    stopAccessing()
    restore()
  }

  private func stopAccessing() {
    if isAccessing, let url = scopedURL {
      url.stopAccessingSecurityScopedResource()
    }
    isAccessing = false
  }

  private static func makeBookmark(for url: URL) -> Data? {
    // 先按 security-scoped 存；沙盒下这是唯一能跨启动用的形式。
    if let data = try? url.bookmarkData(
      options: [.withSecurityScope],
      includingResourceValuesForKeys: nil,
      relativeTo: nil
    ) {
      return data
    }
    // 退回普通 bookmark。非沙盒进程照样能用它跨启动定位目录；真开了沙盒时上面那条
    // 会成功，这条走不到。
    return try? url.bookmarkData(
      options: [],
      includingResourceValuesForKeys: nil,
      relativeTo: nil
    )
  }
}
