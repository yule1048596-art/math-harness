import Foundation

/// 把一个工作区的知识库导出到 vault。
///
/// 这一层只做编排：拿到卡片和例题、渲染文档、逐份问 `VaultWriter` 该怎么写、把结果凑成
/// 一批交给它的事务。真正的判断（正文归谁、要不要另存、能不能写）都在 Core 里，而且
/// 有断言钉着。
public struct KnowledgeExporter {
  public struct Summary: Equatable, Sendable {
    public var created = 0
    public var updated = 0
    public var skipped = 0
    /// 用户改过正文、这次只刷新了 frontmatter 的文件数。
    public var preserved = 0
    /// 另存了新初稿、等着合并的文件数。
    public var pendingMerge: [String] = []
    public var failures: [String] = []
    public var status: VaultWriter.TransactionStatus = .applied

    public var describesChange: Bool {
      created > 0 || updated > 0 || preserved > 0
    }
  }

  public let root: URL

  public init(root: URL) {
    self.root = root
  }

  /// 导出一个工作区。
  ///
  /// 撞名直接失败并报出候选——**撞名不猜**。加个后缀的话，哪个文件对应哪个工作区只有
  /// 我们自己知道，而这些文件是要给人读的。
  public func export(
    workspaceName: String,
    otherWorkspaceNames: [String],
    methods: [MethodCard],
    examples: [ProblemExample],
    pendingExamples: [ProblemExample] = [],
    staleSignatureCount: Int = 0,
    allWorkspaces: [(name: String, methodCount: Int?, pendingCount: Int)] = []
  ) -> Summary {
    var summary = Summary()
    let collisions = KnowledgeExport.folderCollisions(
      [workspaceName] + otherWorkspaceNames
    )
    if let clash = collisions.first(where: { $0.contains(workspaceName) }) {
      summary.status = .preflightFailed
      summary.failures.append(
        "这些工作区的名字会落到同一个文件夹，请先改名再导出：\(clash.joined(separator: "、"))"
      )
      return summary
    }

    let folder = KnowledgeExport.sanitizedComponent(workspaceName, fallback: "工作区")
    let methodsByKey = Dictionary(
      methods.map { ($0.key, $0) }, uniquingKeysWith: { first, _ in first }
    )
    var documents: [KnowledgeExport.Document] = []
    for method in methods {
      let sources = examples.filter { example in
        example.methodDrafts.contains { $0.key == method.key }
      }
      documents.append(
        KnowledgeExport.methodDocument(
          method, workspaceFolder: folder, sources: sources
        )
      )
    }
    for example in examples {
      let linked = example.methodDrafts.compactMap { methodsByKey[$0.key] }
      documents.append(
        KnowledgeExport.exampleDocument(
          example, workspaceFolder: folder, methods: linked
        )
      )
    }

    // 索引最后算：它要报告「有几处新初稿等着合并」，而那要等前面的决策都做完。
    // 所以这里分两轮——先处理知识文档，再把索引追加进同一批事务。
    var writes: [VaultWriter.FileWrite] = []
    for document in documents {
      guard
        let url = VaultLayout.destination(root: root, relativePath: document.relativePath),
        VaultLayout.isInsideOurFolder(url, root: root)
      else {
        // 越界一律拒绝。到这一步还越界，说明名字里有我们没料到的东西——报出来，
        // 不要「清洗后继续」。
        summary.failures.append("路径越出 math-harness 文件夹：\(document.relativePath)")
        continue
      }
      let existingData = try? Data(contentsOf: url)
      let existingText = existingData.flatMap { String(data: $0, encoding: .utf8) }
      if existingData != nil, existingText == nil {
        // 文件在那儿但读不回来（编码坏了、被别的东西占着）。**当成用户的**，不碰。
        summary.failures.append("\(document.relativePath)：现有文件无法读取，已跳过")
        continue
      }
      let expected = existingData.map(VaultWriter.digest(of:))

      switch VaultWriter.decide(document: document, existingText: existingText) {
      case .create(let text):
        writes.append(
          VaultWriter.FileWrite(url: url, text: text, expectedDigest: nil)
        )
        summary.created += 1
      case .unchanged:
        summary.skipped += 1
      case .replace(let text):
        writes.append(
          VaultWriter.FileWrite(url: url, text: text, expectedDigest: expected)
        )
        summary.updated += 1
      case .keepUserBody(let text, let sidecar):
        writes.append(
          VaultWriter.FileWrite(url: url, text: text, expectedDigest: expected)
        )
        summary.preserved += 1
        if let sidecar {
          let sidecarPath = document.relativePath.replacingOccurrences(
            of: ".md", with: VaultLayout.pendingUpdateSuffix
          )
          if let sidecarURL = VaultLayout.destination(
            root: root, relativePath: sidecarPath
          ) {
            let sidecarExisting = try? Data(contentsOf: sidecarURL)
            writes.append(
              VaultWriter.FileWrite(
                url: sidecarURL,
                text: sidecar,
                expectedDigest: sidecarExisting.map(VaultWriter.digest(of:))
              )
            )
            summary.pendingMerge.append(sidecarPath)
          }
        }
      }
    }

    let indexes = [
      KnowledgeExport.workspaceIndexDocument(
        workspaceName: workspaceName,
        workspaceFolder: folder,
        methods: methods,
        pendingExamples: pendingExamples,
        staleSignatureCount: staleSignatureCount,
        pendingMerges: summary.pendingMerge
      ),
      KnowledgeExport.rootIndexDocument(
        workspaces: allWorkspaces.map {
          (
            name: $0.name,
            folder: KnowledgeExport.sanitizedComponent($0.name, fallback: "工作区"),
            methodCount: $0.methodCount,
            pendingCount: $0.pendingCount
          )
        }
      ),
    ]
    for index in indexes {
      guard
        let url = VaultLayout.destination(root: root, relativePath: index.relativePath),
        VaultLayout.isInsideOurFolder(url, root: root)
      else {
        summary.failures.append("索引路径越界：\(index.relativePath)")
        continue
      }
      let existingData = try? Data(contentsOf: url)
      let existingText = existingData.flatMap { String(data: $0, encoding: .utf8) }
      switch VaultWriter.decide(document: index, existingText: existingText) {
      case .create(let text):
        writes.append(VaultWriter.FileWrite(url: url, text: text, expectedDigest: nil))
        summary.created += 1
      case .unchanged:
        summary.skipped += 1
      case .replace(let text):
        writes.append(
          VaultWriter.FileWrite(
            url: url, text: text, expectedDigest: existingData.map(VaultWriter.digest(of:))
          )
        )
        summary.updated += 1
      case .keepUserBody(let text, _):
        // 索引也照保护规则走。用户在这里写了东西，代价是这一页从此不再更新——那是
        // 看得见、删掉文件就能恢复的；静默覆盖不是。旁路文件对索引没有意义，不生成。
        writes.append(
          VaultWriter.FileWrite(
            url: url, text: text, expectedDigest: existingData.map(VaultWriter.digest(of:))
          )
        )
        summary.preserved += 1
      }
    }

    guard summary.failures.isEmpty else {
      summary.status = .preflightFailed
      return summary
    }

    let result = VaultWriter.apply(writes)
    summary.status = result.status
    summary.failures.append(contentsOf: result.failures)
    if !result.ok {
      // 没写成就不能报「创建了几份」——那会让用户以为文件已经在 vault 里了。
      summary.created = 0
      summary.updated = 0
      summary.preserved = 0
      summary.pendingMerge = []
    }
    return summary
  }
}
