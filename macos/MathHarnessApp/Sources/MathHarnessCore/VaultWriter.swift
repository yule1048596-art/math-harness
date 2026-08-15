import CryptoKit
import Foundation

/// 把导出文档写进 vault。
///
/// ## 这里守的是一件事：不许覆盖用户写的正文
///
/// 用户会**立刻**开始在正文里补触发信号和切换关系，而回流要等下一版。自动导出如果
/// 覆盖，他第一次写的东西就没了，**而且是静默没的**——没有报错，没有提示，下次打开
/// 才发现。所以本版在还不会读正文的时候就必须保护它。
///
/// 判据是三个机器字段（`body_owner` / `body_sha256` / `draft_sha256`），加一条兜底：
/// **任何拿不准的情况都算「用户改过」**。误覆盖和少更新的代价完全不对称。
public enum VaultWriter {

  // MARK: - 磁盘上的文件

  /// 从磁盘文件里读出写入端需要的那几个字段。
  ///
  /// 只认我们自己写的形状：以 `---` 开头、有闭合的 `---`。不符合就返回 nil，调用方
  /// 一律按「用户改过」处理——那可能是用户整篇重写了，也可能根本不是我们的文件。
  public struct ExistingDocument: Equatable, Sendable {
    public let owner: KnowledgeExport.BodyOwner
    public let bodyDigest: String
    public let draftDigest: String
    public let body: String
  }

  public static func parse(_ text: String) -> ExistingDocument? {
    let normalized = text.replacingOccurrences(of: "\r\n", with: "\n")
    guard normalized.hasPrefix("---\n") else { return nil }
    let afterOpen = normalized.dropFirst(4)
    guard let closeRange = afterOpen.range(of: "\n---\n") else { return nil }
    let header = afterOpen[afterOpen.startIndex..<closeRange.lowerBound]
    let body = String(afterOpen[closeRange.upperBound...])

    var owner: KnowledgeExport.BodyOwner?
    var bodyDigest: String?
    var draftDigest: String?
    for line in header.split(separator: "\n", omittingEmptySubsequences: false) {
      let parts = line.split(separator: ":", maxSplits: 1)
      guard parts.count == 2 else { continue }
      let key = parts[0].trimmingCharacters(in: .whitespaces)
      let value = parts[1].trimmingCharacters(in: .whitespaces)
      switch key {
      case "body_owner": owner = KnowledgeExport.BodyOwner(rawValue: value)
      case "body_sha256": bodyDigest = value
      case "draft_sha256": draftDigest = value
      default: continue
      }
    }
    guard let owner, let bodyDigest else { return nil }
    // 正文前面那一行空行是渲染时加的，读回来要去掉，否则指纹永远对不上。
    let trimmedBody = body.hasPrefix("\n") ? String(body.dropFirst()) : body
    return ExistingDocument(
      owner: owner,
      bodyDigest: bodyDigest,
      draftDigest: draftDigest ?? bodyDigest,
      body: trimmedBody
    )
  }

  // MARK: - 决策

  public enum Decision: Equatable, Sendable {
    /// 文件不存在，整篇写。
    case create(String)
    /// 内容一个字没变，跳过。
    ///
    /// 这条不只是省事：全量重写会把 vault 里所有文件的修改时间刷一遍，Obsidian 的
    /// 「最近编辑」就废了。
    case unchanged
    /// 正文还是我们上次写下去的样子，可以整篇覆盖。
    case replace(String)
    /// 用户改过正文。保留他的正文，只刷新 frontmatter；`sidecar` 非空时另存新初稿。
    case keepUserBody(text: String, sidecar: String?)
  }

  /// 决定这份文档该怎么落到磁盘上。
  ///
  /// `existingText` 为 nil 表示文件不存在；读失败、编码非法的情况**也要传 nil 之外的
  /// 东西**——调用方应当传一个读不回来的标记，见 `decide(document:existing:)` 的重载。
  public static func decide(
    document: KnowledgeExport.Document,
    existingText: String?
  ) -> Decision {
    guard let existingText else {
      return .create(document.text)
    }
    guard let existing = parse(existingText) else {
      // 认不出形状：可能被整篇重写了，也可能压根不是我们的文件。一律当用户的。
      return keepUserBody(document: document, body: existingText, lastDraft: nil)
    }
    let userTouched =
      existing.owner == .user
      || KnowledgeExport.bodyDigest(existing.body) != existing.bodyDigest
    if userTouched {
      return keepUserBody(
        document: document,
        body: existing.body,
        lastDraft: existing.draftDigest
      )
    }
    // 正文还是我们的。内容与 frontmatter 都没变就不写。
    let candidate = document.text
    if candidate == existingText { return .unchanged }
    return .replace(candidate)
  }

  private static func keepUserBody(
    document: KnowledgeExport.Document,
    body: String,
    lastDraft: String?
  ) -> Decision {
    let draftDigest = document.bodyDigest
    let text = document.rendered(
      body: body,
      owner: .user,
      draftDigest: draftDigest
    )
    // 只有初稿真的变了才另存一份。否则每次导出都会刷新一个内容相同的旁路文件，
    // 用户会以为一直有新东西要合并。
    let sidecar = lastDraft == draftDigest ? nil : document.text
    return .keepUserBody(text: text, sidecar: sidecar)
  }

  // MARK: - 事务

  public struct FileWrite: Equatable, Sendable {
    public let url: URL
    public let text: String
    /// 替换前这个文件必须仍等于它。nil 表示预期该文件不存在。
    public let expectedDigest: String?

    public init(url: URL, text: String, expectedDigest: String?) {
      self.url = url
      self.text = text
      self.expectedDigest = expectedDigest
    }
  }

  public enum TransactionStatus: String, Equatable, Sendable {
    /// 前置检查没过，一个字节都没写。
    case preflightFailed
    /// 全部写完并校验通过。
    case applied
    /// 恢复材料没准备好，所以一次替换都没开始。
    case prepareFailed
    /// 写到一半失败，已经写的全部还原。
    case rolledBack
    /// 回滚也没能全部还原。**保留现场证据，不清理。**
    case partialWrite
  }

  public struct TransactionResult: Equatable, Sendable {
    public let status: TransactionStatus
    public let written: [URL]
    public let failures: [String]
    /// `partialWrite` 时保留的恢复材料位置。别的状态下已经清掉了。
    public let recoveryDirectory: URL?

    public var ok: Bool { status == .applied }
  }

  /// 两段式写入：先在内存里把一切算完并校验，再逐个替换。
  ///
  /// **这不是原生文件系统事务，也不假装是。** 跨文件替换在 POSIX 上做不到原子；这里
  /// 提供的是完整前置检查、原始字节副本、逐文件原子替换和带校验的补偿回滚。回滚时若
  /// 某个文件已被外部改动，**拒绝覆盖它**，报 `partialWrite` 并保留恢复材料——那时候
  /// 强行还原就是在毁掉更新的内容。
  public static func apply(_ writes: [FileWrite]) -> TransactionResult {
    guard !writes.isEmpty else {
      return TransactionResult(
        status: .applied, written: [], failures: [], recoveryDirectory: nil
      )
    }
    let manager = FileManager.default
    var failures: [String] = []

    // --- preflight：全部算完、全部校验，任何一项不过就一个字节都不写 ---
    var originals: [URL: Data] = [:]
    for write in writes {
      let current = try? Data(contentsOf: write.url)
      let currentDigest = current.map(digest(of:))
      guard currentDigest == write.expectedDigest else {
        failures.append(
          "\(write.url.lastPathComponent)：文件在计划之后被改动过，本次不写入"
        )
        continue
      }
      if let current { originals[write.url] = current }
      guard write.text.data(using: .utf8) != nil else {
        failures.append("\(write.url.lastPathComponent)：内容无法编码为 UTF-8")
        continue
      }
    }
    guard failures.isEmpty else {
      return TransactionResult(
        status: .preflightFailed, written: [], failures: failures,
        recoveryDirectory: nil
      )
    }

    // --- prepare：原始字节副本。备份不成就不动手 ---
    let recovery = manager.temporaryDirectory
      .appendingPathComponent("math-harness-vault-\(UUID().uuidString)")
    do {
      try manager.createDirectory(at: recovery, withIntermediateDirectories: true)
      for (url, data) in originals {
        try data.write(to: recovery.appendingPathComponent(safeName(for: url)))
      }
    } catch {
      try? manager.removeItem(at: recovery)
      return TransactionResult(
        status: .prepareFailed,
        written: [],
        failures: ["无法准备恢复材料：\(error.localizedDescription)"],
        recoveryDirectory: nil
      )
    }

    // --- apply ---
    var written: [URL] = []
    for write in writes {
      do {
        try manager.createDirectory(
          at: write.url.deletingLastPathComponent(),
          withIntermediateDirectories: true
        )
        // 替换前**立刻**再查一次。计划到这一刻之间文件可能被 Obsidian 或用户改过；
        // 只在计划时查一次等于给自己留了一个竞态窗口。
        let current = try? Data(contentsOf: write.url)
        guard current.map(digest(of:)) == write.expectedDigest else {
          throw WriteError.raced(write.url.lastPathComponent)
        }
        try writeAtomically(write.text, to: write.url)
        written.append(write.url)
      } catch {
        failures.append("\(write.url.lastPathComponent)：\(errorText(error))")
        let restored = restore(originals: originals, written: written)
        if restored {
          try? manager.removeItem(at: recovery)
          return TransactionResult(
            status: .rolledBack, written: [], failures: failures,
            recoveryDirectory: nil
          )
        }
        failures.append("回滚未能还原全部文件，恢复材料保留在 \(recovery.path)")
        return TransactionResult(
          status: .partialWrite, written: written, failures: failures,
          recoveryDirectory: recovery
        )
      }
    }

    try? manager.removeItem(at: recovery)
    return TransactionResult(
      status: .applied, written: written, failures: [], recoveryDirectory: nil
    )
  }

  // MARK: - 内部

  private enum WriteError: Error {
    case raced(String)
  }

  private static func errorText(_ error: Error) -> String {
    if case WriteError.raced(let name) = error {
      return "\(name) 在写入前被改动，已中止"
    }
    return (error as NSError).localizedDescription
  }

  /// 同目录临时文件 + 原子替换。
  ///
  /// Obsidian 会实时索引目录：写到一半的文件会被它读进去，在图谱里留下一个畸形节点。
  private static func writeAtomically(_ text: String, to url: URL) throws {
    let manager = FileManager.default
    let directory = url.deletingLastPathComponent()
    let temporary = directory.appendingPathComponent(
      ".math-harness-\(UUID().uuidString).tmp"
    )
    try Data(text.utf8).write(to: temporary, options: .atomic)
    if manager.fileExists(atPath: url.path) {
      _ = try manager.replaceItemAt(url, withItemAt: temporary)
    } else {
      try manager.moveItem(at: temporary, to: url)
    }
    try? manager.removeItem(at: temporary)
  }

  /// 还原已经替换掉的文件。全部还原成功才返回 true。
  private static func restore(originals: [URL: Data], written: [URL]) -> Bool {
    var allRestored = true
    for url in written {
      guard let original = originals[url] else {
        // 本来不存在的文件：删掉即可。删不掉也不算灾难，但要如实报。
        allRestored =
          ((try? FileManager.default.removeItem(at: url)) != nil)
          && allRestored
        continue
      }
      do {
        try writeAtomically(String(decoding: original, as: UTF8.self), to: url)
      } catch {
        allRestored = false
      }
    }
    return allRestored
  }

  /// 一段字节的指纹。公开是为了让调用方和断言用同一份实现——两处各写一遍迟早不一致。
  public static func digest(of data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
  }

  public static func digest(ofFileAt url: URL) -> String? {
    guard let data = try? Data(contentsOf: url) else { return nil }
    return digest(of: data)
  }

  private static func safeName(for url: URL) -> String {
    let path = url.path
    return digest(of: Data(path.utf8)) + "-" + url.lastPathComponent
  }
}
