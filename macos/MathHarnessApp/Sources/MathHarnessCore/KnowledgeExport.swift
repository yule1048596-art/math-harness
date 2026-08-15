import CryptoKit
import Foundation

/// 把知识库渲染成 Obsidian 读得懂的 Markdown。
///
/// ## 一条分界贯穿整个文件
///
/// **frontmatter 是机器的，正文是人的。** 机器字段（键、可信度、来源、统计、版本）写在
/// frontmatter，每次导出都刷新；触发信号、失败边界、切换条件写在正文，用户改过之后
/// 导出就不再覆盖（判定见 `bodyDigest`）。
///
/// 由此推出一条不太直观的规矩：**正文里不写可信度。** 正文不会被更新，写进去的可信度
/// 迟早会和库里的真实状态对不上，那时它就是在说谎。可信度全部留在 frontmatter。
///
/// ## 服从既有笔记约定，不自创
///
/// 目标 vault 已经有一套数学笔记规范，而且有 linter 在执行。我们生成的文件必须过得了
/// 那道 lint，否则每导出一个文件就给用户添一条警告：
///
/// - 行内 `$...$`、行间 `$$...$$`，**绝不产出 `\(...\)` 或 `\[...\]`**；
/// - 反例进 `[!error]`，例子进 `[!example]`；
/// - 方法卡打 `tool_idea` 标签——那是 vault 里既有的 M2 级方法笔记约定。
///
/// 机器产出的表达式（断言、反例取值）一律用行内代码包起来。它们是解析器语法，不是排版
/// 意义上的数学；包成代码既诚实，又顺手躲开 Markdown 把 `x**2` 里的星号吃掉的问题。
public enum KnowledgeExport {

  // MARK: - 文档

  /// 一份待写入的文件。
  public struct Document: Equatable, Sendable {
    /// 相对 vault 根目录的路径，例如 `math-harness/微积分/方法卡/differentiate.md`。
    public let relativePath: String
    public let frontmatter: [FrontmatterEntry]
    public let body: String

    /// 正文的指纹。**只归一换行**，别的字节一概照原样算。
    ///
    /// 归一是必须的：Obsidian 或别的编辑器改一次换行符，逐字节比对就会认为「用户改过」，
    /// 保护于是退化成「永远不更新」。而 Markdown 的硬换行是行尾双空格，**绝不能**顺手
    /// 把尾随空格也清掉——那会改变文档语义。
    public var bodyDigest: String { KnowledgeExport.bodyDigest(body) }

    /// 完整文件内容（机器初稿）。
    public var text: String {
      rendered(body: body, owner: .machine, draftDigest: bodyDigest)
    }

    /// 按指定正文和归属渲染出完整文件。
    ///
    /// 三个机器字段固定写在最后，顺序不变——它们是写入端的判据，位置飘来飘去会让
    /// 逐字符稳定性无从谈起：
    ///
    /// - `body_owner`：正文归谁。**一旦翻成 `user` 就永不翻回**，那是保护的根。
    /// - `body_sha256`：**我们上次写下去的正文**的指纹。磁盘上的正文和它对不上，
    ///   就说明有人动过。
    /// - `draft_sha256`：上次提供的机器初稿。用它判断「这次有没有新东西可给」，
    ///   免得每次导出都重写一遍旁路文件。
    public func rendered(
      body renderedBody: String,
      owner: BodyOwner,
      draftDigest: String
    ) -> String {
      var lines = ["---"]
      for entry in frontmatter {
        lines.append(contentsOf: entry.yamlLines)
      }
      lines.append("body_owner: \(owner.rawValue)")
      lines.append("body_sha256: \(KnowledgeExport.bodyDigest(renderedBody))")
      lines.append("draft_sha256: \(draftDigest)")
      lines.append("---")
      lines.append("")
      return lines.joined(separator: "\n") + "\n" + renderedBody
    }
  }

  /// 正文归谁。
  ///
  /// 这个标记是粘的：从 `machine` 翻到 `user` 之后**永远不翻回**。没有它就没法区分
  /// 「用户没动过」和「我们上次刚把用户改的正文原样写回去」——后者的指纹当然对得上，
  /// 于是下一次导出就会理直气壮地覆盖掉用户写的东西。
  public enum BodyOwner: String, Equatable, Sendable {
    case machine
    case user
  }

  /// frontmatter 的一项。
  ///
  /// 只支持**简单标量**和**简单块列表**——块标量、嵌套映射、flow 映射、YAML anchor
  /// 一律不产出。理由是回流那一版要能安全地读回来并原样写出去；产出自己解析不回来的
  /// 东西，等于给下一版埋一个必须猜的地方。
  public struct FrontmatterEntry: Equatable, Sendable {
    public enum Value: Equatable, Sendable {
      case text(String)
      case number(Int)
      case list([String])
    }

    public let key: String
    public let value: Value

    public var yamlLines: [String] {
      switch value {
      case .text(let raw):
        return ["\(key): \(KnowledgeExport.yamlScalar(raw))"]
      case .number(let raw):
        return ["\(key): \(raw)"]
      case .list(let items):
        if items.isEmpty { return ["\(key): []"] }
        return ["\(key):"] + items.map { "  - \(KnowledgeExport.yamlScalar($0))" }
      }
    }
  }

  // MARK: - 方法卡

  /// 一张方法卡的导出文档。
  ///
  /// `sources` 是支撑它的例题，用来生成来源链接；顺序由调用方决定，这里原样使用，
  /// 以保证同样输入两次导出逐字符一致。
  public static func methodDocument(
    _ method: MethodCard,
    workspaceFolder: String,
    sources: [ProblemExample] = []
  ) -> Document {
    let slug = sanitizedComponent(method.key, fallback: "method")
    var entries: [FrontmatterEntry] = [
      FrontmatterEntry(key: "key", value: .text(method.key)),
      FrontmatterEntry(key: "title", value: .text(method.name)),
      // `tool_idea` 排在最前：vault 里靠它认出这是一张 M2 级方法笔记。
      FrontmatterEntry(
        key: "tags",
        value: .list(["tool_idea"] + method.tags.filter { $0 != "tool_idea" })
      ),
      FrontmatterEntry(key: "status", value: .text(method.status)),
    ]
    if let confidence = method.confidence {
      entries.append(FrontmatterEntry(key: "confidence", value: .text(confidence)))
    }
    entries.append(contentsOf: [
      FrontmatterEntry(key: "version", value: .number(method.version)),
      FrontmatterEntry(key: "success_count", value: .number(method.successCount)),
      FrontmatterEntry(key: "failure_count", value: .number(method.failureCount)),
      FrontmatterEntry(
        key: "feature_version",
        value: .number(method.signature["feature_version"]?.intValue ?? 0)
      ),
    ])
    if !sources.isEmpty {
      entries.append(
        FrontmatterEntry(
          key: "source_examples",
          value: .list(sources.map(\.id))
        )
      )
    }

    var body = "# \(method.name)\n"
    if !method.goal.isEmpty {
      body += "\n\(method.goal)\n"
    }
    body += section("触发信号", bullets: method.applicableWhen)
    body += section("步骤", numbered: method.procedure)
    body += section("失败边界", bullets: method.failureModes)
    // 这一节机器永远填不了：「什么时候换」是判断，不是统计。留一个空位和一句说明。
    body += "\n## 什么时候换\n\n"
    body += "<!-- 用 [[方法卡]] 写下切换关系。这一节由你维护，导出不会覆盖。 -->\n"
    if !sources.isEmpty {
      body += "\n## 来源\n\n"
      for example in sources {
        let path = "\(workspaceFolder)/例题/\(exampleSlug(example)).md"
        body += "- \(wikilink(to: path, alias: inlineSafe(example.problem, limit: 40)))\n"
      }
    }

    return Document(
      relativePath: "\(workspaceFolder)/方法卡/\(slug).md",
      frontmatter: entries,
      body: body
    )
  }

  // MARK: - 例题

  public static func exampleDocument(
    _ example: ProblemExample,
    workspaceFolder: String,
    methods: [MethodCard] = []
  ) -> Document {
    let report = example.verification
    var entries: [FrontmatterEntry] = [
      FrontmatterEntry(key: "example_id", value: .text(example.id)),
      FrontmatterEntry(
        key: "title", value: .text(inlineSafe(example.problem, limit: 60))
      ),
      FrontmatterEntry(key: "tags", value: .list(example.tags)),
      FrontmatterEntry(key: "status", value: .text(example.status)),
      FrontmatterEntry(key: "verification", value: .text(report.status)),
    ]
    if let conclusion = report.conclusionConfidence {
      entries.append(
        FrontmatterEntry(key: "conclusion_confidence", value: .text(conclusion))
      )
      if let label = proofStateLabel(conclusion) {
        entries.append(FrontmatterEntry(key: "proof_state", value: .text(label)))
      }
    }
    if let process = report.processConfidence {
      entries.append(
        FrontmatterEntry(key: "process_confidence", value: .text(process))
      )
    }
    if !methods.isEmpty {
      entries.append(
        FrontmatterEntry(key: "methods", value: .list(methods.map(\.key)))
      )
    }

    var body = "# \(inlineSafe(example.problem, limit: 60))\n"
    body += "\n## 题目\n\n\(trimmedBlock(example.problem))\n"
    body += "\n## 解答\n\n\(trimmedBlock(example.solution))\n"
    if !report.counterexample.isEmpty {
      // 反例进 `[!error]`——vault 的数学笔记规范明确要求反例不与例子共用同一种 callout。
      body += "\n> [!error] 反例\n"
      for key in report.counterexample.keys.sorted() {
        body += "> - `\(key) = \(report.counterexample[key] ?? "")`\n"
      }
    }
    if !report.checks.isEmpty {
      body += "\n## 检查了什么\n\n"
      for check in report.checks {
        body += "- `\(inlineSafe(check, limit: 200))`\n"
      }
    }
    if !methods.isEmpty {
      body += "\n## 相关方法\n\n"
      for method in methods {
        let slug = sanitizedComponent(method.key, fallback: "method")
        let path = "\(workspaceFolder)/方法卡/\(slug).md"
        body += "- \(wikilink(to: path, alias: inlineSafe(method.name, limit: 30)))\n"
      }
    }

    return Document(
      relativePath: "\(workspaceFolder)/例题/\(exampleSlug(example)).md",
      frontmatter: entries,
      body: body
    )
  }

  // MARK: - 索引

  /// 一个工作区的入口页。
  ///
  /// **刻意不写时间戳。** 索引里放「上次导出于 …」会让每次导出都改到这个文件，vault 里
  /// 的修改时间被无谓地刷一遍，Obsidian 的「最近编辑」就废了。而「上次写于何时」文件
  /// 系统本来就记着。什么都没变时**一个字节都不写**，本身就是最准的信号。
  public static func workspaceIndexDocument(
    workspaceName: String,
    workspaceFolder: String,
    methods: [MethodCard],
    pendingExamples: [ProblemExample],
    staleSignatureCount: Int,
    pendingMerges: [String]
  ) -> Document {
    let entries: [FrontmatterEntry] = [
      FrontmatterEntry(key: "title", value: .text(workspaceName)),
      FrontmatterEntry(key: "tags", value: .list(["math-harness"])),
      FrontmatterEntry(key: "method_count", value: .number(methods.count)),
      FrontmatterEntry(key: "pending_review", value: .number(pendingExamples.count)),
    ]

    var body = "# \(workspaceName)\n\n\(generatedBanner)\n"

    if staleSignatureCount > 0 {
      body += "\n> [!warning] \(staleSignatureCount) 张方法卡的结构特征待重建\n"
      body += "> 它们暂时只靠词面参与检索。在 App 的知识库面板里点「重建」。\n"
    }
    if !pendingMerges.isEmpty {
      body += "\n> [!note] 有 \(pendingMerges.count) 处新初稿等着合并\n"
      body += "> 你改过这些笔记的正文，所以导出没有覆盖它们；新初稿另存在旁边：\n"
      for path in pendingMerges {
        body += "> - `\(path)`\n"
      }
    }

    // 按可信度分组。强的排前面——「越用越强」的前提是强的那部分看得见。
    let order = [
      "proof_verified", "verified", "cross_checked", "numerically_checked",
      "peer_reviewed", "unchecked",
    ]
    let grouped = Dictionary(grouping: methods) { $0.confidence ?? "unchecked" }
    body += "\n## 方法卡\n"
    if methods.isEmpty {
      body += "\n还没有方法卡。复核已验证的例题之后它们会出现在这里。\n"
    }
    for tier in order {
      guard let cards = grouped[tier], !cards.isEmpty else { continue }
      body += "\n### \(confidenceTitle(tier))\n\n"
      for card in cards {
        let slug = sanitizedComponent(card.key, fallback: "method")
        let path = "\(workspaceFolder)/方法卡/\(slug).md"
        body += "- \(wikilink(to: path, alias: card.name))"
        if card.successCount > 0 {
          body += "（用过 \(card.successCount) 次）"
        }
        body += "\n"
      }
    }

    if !pendingExamples.isEmpty {
      body += "\n## 待复核\n\n"
      body += "这些例题还没有经你确认，**不参与检索**。\n\n"
      for example in pendingExamples {
        let path = "\(workspaceFolder)/例题/\(exampleSlug(example)).md"
        body += "- \(wikilink(to: path, alias: inlineSafe(example.problem, limit: 40)))\n"
      }
    }

    return Document(
      relativePath: "\(workspaceFolder)/\(VaultLayout.workspaceIndexFileName)",
      frontmatter: entries,
      body: body
    )
  }

  /// 总索引。
  ///
  /// `methodCount` 为 nil 表示这次没加载那个工作区的知识库，**于是不写数字**。
  /// 写个 0 上去是假的——那会让人以为那个工作区空了。不知道就别说。
  public static func rootIndexDocument(
    workspaces: [(name: String, folder: String, methodCount: Int?, pendingCount: Int)]
  ) -> Document {
    let entries: [FrontmatterEntry] = [
      FrontmatterEntry(key: "title", value: .text("Math Harness")),
      FrontmatterEntry(key: "tags", value: .list(["math-harness"])),
      FrontmatterEntry(key: "workspace_count", value: .number(workspaces.count)),
    ]
    var body = "# Math Harness 知识库\n\n\(generatedBanner)\n\n## 工作区\n\n"
    if workspaces.isEmpty {
      body += "还没有导出过任何工作区。\n"
    }
    for workspace in workspaces {
      let path = "\(workspace.folder)/\(VaultLayout.workspaceIndexFileName)"
      body += "- \(wikilink(to: path, alias: workspace.name))"
      if let count = workspace.methodCount {
        body += " — \(count) 张方法卡"
        if workspace.pendingCount > 0 {
          body += " · \(workspace.pendingCount) 条待复核"
        }
      }
      body += "\n"
    }
    return Document(
      relativePath: VaultLayout.indexFileName,
      frontmatter: entries,
      body: body
    )
  }

  /// 索引页顶部那句话。
  ///
  /// 索引是生成物，但**保护规则一视同仁**：真在这里写了东西，我们照样不覆盖。代价是
  /// 索引会停在那一刻不再更新——那是看得见、也删掉文件就能恢复的；静默丢失不是。
  /// 所以先把话说在前面。
  static let generatedBanner =
    "> [!info] 这一页由 Math Harness 生成\n"
    + "> 每次导出会重写它。想写自己的东西请写在别的笔记里——"
    + "在这里写了也不会丢，但这一页从此不再更新。\n"

  static func confidenceTitle(_ tier: String) -> String {
    switch tier {
    case "proof_verified": "形式化证明"
    case "verified": "符号验证"
    case "cross_checked": "独立重算一致"
    case "numerically_checked": "数值检验"
    case "peer_reviewed": "异模型复核"
    default: "未标注"
    }
  }

  // MARK: - 链接

  /// 指向另一份导出文档的 wikilink。
  ///
  /// **必须写全路径。** 两个工作区都可能有一张 `differentiate.md`，也各有一份
  /// `索引.md`；只写文件名的话 Obsidian 会在同名文件里任选一个，而且不报错——用户看到
  /// 的是一条指向别的工作区的链接。别名让它读起来仍然干净。
  public static func wikilink(to relativePath: String, alias: String) -> String {
    let target =
      "\(VaultLayout.rootFolderName)/"
      + (relativePath.hasSuffix(".md") ? String(relativePath.dropLast(3)) : relativePath)
    return "[[\(target)|\(alias)]]"
  }

  // MARK: - 可信度映射

  /// 我们七档，vault 的数学笔记规范四档。映射**只许降，不许升**。
  ///
  /// `verified` 的准确含义是「SymPy 判定这条等式恒成立」。把它写成整篇解答「已证明」
  /// 就多说了——过程轴可能还是 `step_unchecked`，也就是没人查过中间推导。
  ///
  /// `refuted` 不映射到任何标签：那四档全是正面状态，硬塞进去等于把「找到反例」说成
  /// 一种证明程度。反例自己进 `[!error]` 块。
  public static func proofStateLabel(_ conclusion: String) -> String? {
    switch conclusion {
    case "proof_verified", "verified": "已证明"
    case "numerically_checked", "cross_checked", "peer_reviewed", "unchecked": "待检查"
    default: nil
    }
  }

  // MARK: - 文件名

  /// 例题的文件名。日期加短 ID——**不用标题**。
  ///
  /// 标题会被改，ID 不会。按标题命名的话，用户在 App 里改一次题面，vault 里所有指向
  /// 它的链接就全断了。
  public static func exampleSlug(_ example: ProblemExample) -> String {
    let day = String(example.createdAt.prefix(10))
    let short = String(example.id.replacingOccurrences(of: "-", with: "").prefix(8))
    return sanitizedComponent("\(day)-\(short)", fallback: "example")
  }

  /// 路径里一段的安全化。
  ///
  /// 处理分隔符、控制字符、首尾空格与点（点开头在类 Unix 下是隐藏文件），并限长。
  /// 长度按字符算而不是字节：中文标题按字节很容易超，按字符 80 足够安全。
  public static func sanitizedComponent(_ raw: String, fallback: String) -> String {
    var cleaned = ""
    for character in raw {
      if character.isNewline || character.unicodeScalars.allSatisfy({ $0.value < 0x20 }) {
        cleaned.append(" ")
      } else if "/\\:*?\"<>|".contains(character) {
        cleaned.append("-")
      } else {
        cleaned.append(character)
      }
    }
    cleaned = cleaned.split(separator: " ").joined(separator: " ")
    while cleaned.hasPrefix(".") { cleaned.removeFirst() }
    while cleaned.hasSuffix(".") { cleaned.removeLast() }
    cleaned = cleaned.trimmingCharacters(in: .whitespaces)
    if cleaned.count > 80 {
      cleaned = String(cleaned.prefix(80)).trimmingCharacters(in: .whitespaces)
    }
    return cleaned.isEmpty ? fallback : cleaned
  }

  /// 规范化之后撞在一起的名字。
  ///
  /// **撞名不猜。** 两个工作区叫「微积分/上」和「微积分:上」会落到同一个文件夹，那时
  /// 正确的做法是报出来让用户改名，而不是自作主张加个后缀——加了后缀，哪个是哪个只有
  /// 我们自己知道，而这些文件是要给人读的。
  public static func folderCollisions(_ names: [String]) -> [[String]] {
    var groups: [String: [String]] = [:]
    var order: [String] = []
    for name in names {
      let key = sanitizedComponent(name, fallback: "workspace")
      if groups[key] == nil { order.append(key) }
      groups[key, default: []].append(name)
    }
    return order.compactMap { key in
      let group = groups[key] ?? []
      return group.count > 1 ? group : nil
    }
  }

  // MARK: - 内部

  /// 正文指纹。公开是为了让写入端和断言用同一份实现——两处各写一遍，迟早会不一致，
  /// 而那正好把正文保护击穿。
  public static func bodyDigest(_ body: String) -> String {
    let normalized =
      body
      .replacingOccurrences(of: "\r\n", with: "\n")
      .replacingOccurrences(of: "\r", with: "\n")
    var trimmed = Substring(normalized)
    while trimmed.hasSuffix("\n") { trimmed = trimmed.dropLast() }
    let digest = SHA256.hash(data: Data(trimmed.utf8))
    return digest.map { String(format: "%02x", $0) }.joined()
  }

  /// YAML 标量。拿不准就加引号——这里宁可难看，也不要写出一个解析回来不一样的值。
  static func yamlScalar(_ raw: String) -> String {
    let unsafe = CharacterSet(charactersIn: ":#{}[],&*!|>'\"%@`\n\r\t")
    let looksNumeric = Double(raw) != nil
    let reserved = ["true", "false", "null", "yes", "no", "on", "off", "~"]
    let needsQuotes =
      raw.isEmpty
      || raw.rangeOfCharacter(from: unsafe) != nil
      || raw != raw.trimmingCharacters(in: .whitespaces)
      || looksNumeric
      || reserved.contains(raw.lowercased())
    guard needsQuotes else { return raw }
    let escaped =
      raw
      .replacingOccurrences(of: "\\", with: "\\\\")
      .replacingOccurrences(of: "\"", with: "\\\"")
      .replacingOccurrences(of: "\n", with: " ")
      .replacingOccurrences(of: "\r", with: " ")
      .replacingOccurrences(of: "\t", with: " ")
    return "\"\(escaped)\""
  }

  /// 压成一行并限长，用于标题、链接别名和 frontmatter。
  static func inlineSafe(_ raw: String, limit: Int) -> String {
    let flattened = raw.split(whereSeparator: \.isNewline).joined(separator: " ")
    let collapsed = flattened.split(separator: " ").joined(separator: " ")
    guard collapsed.count > limit else { return collapsed }
    return String(collapsed.prefix(limit)) + "…"
  }

  static func trimmedBlock(_ raw: String) -> String {
    raw.replacingOccurrences(of: "\r\n", with: "\n")
      .trimmingCharacters(in: .whitespacesAndNewlines)
  }

  static func section(_ title: String, bullets: [String]) -> String {
    guard !bullets.isEmpty else { return "" }
    var text = "\n## \(title)\n\n"
    for item in bullets {
      text += "- \(inlineSafe(item, limit: 300))\n"
    }
    return text
  }

  static func section(_ title: String, numbered items: [String]) -> String {
    guard !items.isEmpty else { return "" }
    var text = "\n## \(title)\n\n"
    for (index, item) in items.enumerated() {
      text += "\(index + 1). \(inlineSafe(item, limit: 300))\n"
    }
    return text
  }
}
