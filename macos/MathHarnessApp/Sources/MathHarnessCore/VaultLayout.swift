import Foundation

/// vault 里属于我们的那块地方。
///
/// **只碰 `math-harness/` 这一个子目录。** 用户的 vault 里有他自己经年累月的笔记，
/// 我们写进去的每一个字节都必须落在这个文件夹以内——这条边界不能靠调用方自觉，
/// 要在拼路径的地方就挡住。
public enum VaultLayout {
  /// 我们在 vault 根目录下的文件夹名。
  public static let rootFolderName = "math-harness"

  /// 索引文件名。
  public static let indexFileName = "MATH-HARNESS.md"
  public static let workspaceIndexFileName = "索引.md"

  /// 用户改过正文之后，新初稿写到这个后缀的旁路文件里，等 v0.21 做合并。
  public static let pendingUpdateSuffix = ".updated.md"

  /// 把一条相对路径落到 vault 根目录下。
  ///
  /// 越界一律返回 nil，不做「清洗后继续」：`..`、绝对路径、盘符、空段都可能来自
  /// 一个被改过的工作区名，而写到 vault 之外就是在动用户别的笔记。**拒绝比修正安全**
  /// ——修正之后写到哪里只有我们自己知道。
  public static func destination(root: URL, relativePath: String) -> URL? {
    let components = relativePath.split(separator: "/", omittingEmptySubsequences: false)
    guard !components.isEmpty else { return nil }
    for component in components {
      let piece = String(component)
      if piece.isEmpty || piece == "." || piece == ".." { return nil }
      if piece.hasPrefix("/") || piece.contains(":") { return nil }
      if piece.hasPrefix(".") { return nil }
    }
    var url = root.appendingPathComponent(rootFolderName, isDirectory: true)
    for component in components.dropLast() {
      url = url.appendingPathComponent(String(component), isDirectory: true)
    }
    return url.appendingPathComponent(String(components[components.count - 1]))
  }

  /// 这个 URL 是不是落在我们自己的文件夹里。写之前再验一次。
  ///
  /// 用 `standardizedFileURL` 化掉 `..` 和符号链接式的写法之后再比前缀——直接比字符串
  /// 会被 `/vault/math-harness/../别处` 骗过去。
  public static func isInsideOurFolder(_ url: URL, root: URL) -> Bool {
    let ours =
      root
      .standardizedFileURL
      .appendingPathComponent(rootFolderName, isDirectory: true)
      .standardizedFileURL
    let target = url.standardizedFileURL
    let oursPath = ours.path.hasSuffix("/") ? ours.path : ours.path + "/"
    return target.path.hasPrefix(oursPath)
  }
}
