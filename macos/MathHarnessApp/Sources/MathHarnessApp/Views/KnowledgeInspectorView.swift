import MathHarnessCore
import SwiftUI

private enum KnowledgePane: String, CaseIterable, Identifiable {
  case review = "待复核"
  case methods = "方法卡"

  var id: String { rawValue }
}

struct KnowledgeInspectorView: View {
  @EnvironmentObject private var model: AppModel
  @State private var query = ""
  @State private var pane: KnowledgePane = .review

  private var filteredMethods: [MethodCard] {
    let normalized = normalizedQuery
    guard !normalized.isEmpty else { return model.methods }
    return model.methods.filter {
      $0.name.lowercased().contains(normalized)
        || $0.key.lowercased().contains(normalized)
        || $0.tags.contains(where: { $0.lowercased().contains(normalized) })
    }
  }

  private var filteredExamples: [ProblemExample] {
    let normalized = normalizedQuery
    guard !normalized.isEmpty else { return model.pendingExamples }
    return model.pendingExamples.filter {
      $0.problem.lowercased().contains(normalized)
        || $0.solution.lowercased().contains(normalized)
        || $0.tags.contains(where: { $0.lowercased().contains(normalized) })
        || ($0.extraction?.extractedMethodKeys.contains {
          $0.lowercased().contains(normalized)
        } ?? false)
    }
  }

  private var normalizedQuery: String {
    query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
  }

  var body: some View {
    VStack(spacing: 0) {
      HStack {
        VStack(alignment: .leading, spacing: 2) {
          Text("知识库")
            .font(.headline)
          Text("\(model.pendingExamples.count) 待复核 · \(model.methods.count) 张方法卡")
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        Spacer()
      }
      .padding(14)

      Picker("知识视图", selection: $pane) {
        ForEach(KnowledgePane.allCases) { item in
          Text(item.rawValue).tag(item)
        }
      }
      .pickerStyle(.segmented)
      .labelsHidden()
      .padding(.horizontal, 12)
      .padding(.bottom, 10)

      TextField(pane == .review ? "搜索待复核例题" : "搜索方法", text: $query)
        .textFieldStyle(.roundedBorder)
        .padding(.horizontal, 12)
        .padding(.bottom, 10)

      Divider()

      switch pane {
      case .review:
        reviewQueue
      case .methods:
        methodLibrary
      }
    }
  }

  @ViewBuilder
  private var reviewQueue: some View {
    if filteredExamples.isEmpty {
      ContentUnavailableView {
        Label(
          normalizedQuery.isEmpty ? "没有待复核草稿" : "没有匹配的草稿",
          systemImage: "checkmark.seal"
        )
      } description: {
        Text("每次成功求解都会自动进入这里；只有验证通过并经你确认后才会晋级。")
      }
    } else {
      ScrollView {
        LazyVStack(spacing: 10) {
          ForEach(filteredExamples) { example in
            KnowledgeDraftCard(example: example)
          }
        }
        .padding(12)
      }
    }
  }

  @ViewBuilder
  private var methodLibrary: some View {
    if filteredMethods.isEmpty {
      ContentUnavailableView {
        Label("还没有方法卡", systemImage: "books.vertical")
      } description: {
        Text("复核已验证例题后，方法会出现在这里。")
      }
    } else {
      ScrollView {
        LazyVStack(spacing: 10) {
          if model.staleSignatureCount > 0 {
            staleSignatureNotice
          }
          if let problem = model.exportProblem {
            exportProblemNotice(problem)
          }
          ForEach(filteredMethods) { method in
            MethodCardView(method: method)
          }
        }
        .padding(12)
      }
    }
  }

  /// 自动导出出的问题。
  ///
  /// **不弹窗**——它是背景动作，弹窗会打断正在进行的对话。但也不能不说：导出失败而
  /// 用户不知道，他会以为 vault 里的东西是最新的。
  private func exportProblemNotice(_ problem: String) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      Label("导出到 Vault 未完成", systemImage: "externaldrive.badge.exclamationmark")
        .font(.callout.weight(.medium))
      Text(problem)
        .font(.caption)
        .foregroundStyle(.secondary)
        .textSelection(.enabled)
      HStack {
        Spacer()
        Button("知道了") { model.exportProblem = nil }
        Button {
          Task { await model.exportToVault() }
        } label: {
          if model.isExportingToVault {
            ProgressView().controlSize(.small)
          } else {
            Text("重试")
          }
        }
        .disabled(model.isExportingToVault)
      }
    }
    .padding(10)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
  }

  /// 结构检索这一路对某些卡片是关着的——这件事必须说出来。
  ///
  /// 它们的签名来自旧版本的特征提取器，和现在算出来的查询特征不在同一个空间里。跨版本
  /// 比出来的相似度没有意义，所以系统选择不比；代价是这些卡片暂时只靠词面被找到，
  /// **而且不会有任何报错**。不显示的话，用户只会觉得「最近检索变差了」。
  private var staleSignatureNotice: some View {
    VStack(alignment: .leading, spacing: 6) {
      Label(
        "\(model.staleSignatureCount) 张方法卡的结构特征待重建",
        systemImage: "arrow.triangle.2.circlepath"
      )
      .font(.callout.weight(.medium))
      Text("它们的结构特征是旧版本算出来的，暂时只靠词面参与检索。重建从来源例题重算，不改卡片内容。")
        .font(.caption)
        .foregroundStyle(.secondary)
      HStack {
        Spacer()
        Button {
          Task { await model.rebuildMethodSignatures() }
        } label: {
          if model.isRebuildingSignatures {
            ProgressView().controlSize(.small)
          } else {
            Text("重建")
          }
        }
        .disabled(model.isRebuildingSignatures)
      }
    }
    .padding(10)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
  }
}

private struct KnowledgeDraftCard: View {
  @EnvironmentObject private var model: AppModel
  let example: ProblemExample
  @State private var expanded = false
  @State private var reviewerNote = ""
  @State private var confirmingRejection = false
  @State private var isEditing = false
  @State private var draftProblem: String
  @State private var draftSolution: String
  @State private var draftTags: String
  @State private var draftMethodHint: String
  @State private var draftExpression: String
  @State private var draftExpected: String
  @State private var draftVariable: String
  @State private var draftParameters: String
  @State private var draftAssumptions: String
  @State private var draftPoint: String
  @State private var draftDirection: String
  @State private var draftMode: VerificationMode
  @State private var draftRemainderPower: String

  init(example: ProblemExample) {
    self.example = example
    _draftProblem = State(initialValue: example.problem)
    _draftSolution = State(initialValue: example.solution)
    _draftTags = State(initialValue: example.tags.joined(separator: ", "))
    _draftMethodHint = State(initialValue: example.methodHint ?? "")
    _draftExpression = State(initialValue: example.mathPayload?.expression ?? "")
    _draftExpected = State(initialValue: example.mathPayload?.expected ?? "")
    _draftVariable = State(initialValue: example.mathPayload?.variable ?? "x")
    _draftParameters = State(
      initialValue: example.mathPayload?.parameters.joined(separator: ", ") ?? ""
    )
    _draftAssumptions = State(
      initialValue: Self.renderAssumptions(example.mathPayload?.assumptions ?? [:])
    )
    _draftPoint = State(initialValue: example.mathPayload?.point ?? "oo")
    _draftDirection = State(initialValue: example.mathPayload?.direction ?? "two_sided")
    _draftMode = State(initialValue: example.mathPayload?.mode ?? .asymptoticExpansion)
    _draftRemainderPower = State(
      initialValue: example.mathPayload?.remainderPower.map(String.init) ?? "2"
    )
  }

  private var canApprove: Bool {
    example.verification.status == "verified"
  }

  private var isReviewing: Bool {
    model.reviewingExampleID == example.id
  }

  private var isSaving: Bool {
    model.editingExampleID == example.id
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .top, spacing: 8) {
        VStack(alignment: .leading, spacing: 4) {
          if isEditing {
            TextField("题目", text: $draftProblem, axis: .vertical)
              .textFieldStyle(.roundedBorder)
              .lineLimit(2...6)
          } else {
            Text(example.problem)
              .font(.subheadline.weight(.semibold))
              .lineLimit(expanded ? nil : 3)
              .textSelection(.enabled)
          }
          Label(
            "\(example.origin == "conversation" ? "对话自动记忆" : "手动录入") · 修订 \(example.revision)",
            systemImage: example.origin == "conversation"
              ? "bubble.left.and.exclamationmark.bubble.right"
              : "square.and.pencil"
          )
          .font(.caption2)
          .foregroundStyle(.secondary)
        }
        Spacer()
        StatusBadge(status: example.verification.status)
      }

      if isEditing {
        VStack(alignment: .leading, spacing: 5) {
          Text("解答内容")
            .font(.caption2.weight(.semibold))
          TextEditor(text: $draftSolution)
            .font(.caption)
            .scrollContentBackground(.hidden)
            .frame(minHeight: 120, maxHeight: 220)
            .padding(6)
            .background(.background, in: RoundedRectangle(cornerRadius: 7))
            .overlay {
              RoundedRectangle(cornerRadius: 7)
                .stroke(.separator, lineWidth: 0.5)
            }
        }
      } else {
        Text(example.solution)
          .font(.caption)
          .foregroundStyle(.secondary)
          .lineLimit(expanded ? nil : 5)
          .textSelection(.enabled)
      }

      if isEditing {
        draftEditor
      }

      if isEditing {
        Label(
          "保存后会根据新题目与新解答重新提炼方法，当前方法预览不会被沿用。",
          systemImage: "arrow.triangle.2.circlepath"
        )
        .font(.caption2)
        .foregroundStyle(.secondary)
      } else if !example.methodDrafts.isEmpty {
        VStack(alignment: .leading, spacing: 3) {
          Text("准备写入的方法")
            .font(.caption2.weight(.semibold))
          ForEach(example.methodDrafts, id: \.key) { draft in
            VStack(alignment: .leading, spacing: 3) {
              Text(draft.name)
                .font(.caption.weight(.medium))
              Text(draft.key)
                .font(.caption2.monospaced())
                .foregroundStyle(.secondary)
              Text(draft.goal)
                .font(.caption2)
                .foregroundStyle(.secondary)
              if expanded, !draft.procedure.isEmpty {
                ForEach(Array(draft.procedure.enumerated()), id: \.offset) { index, step in
                  Text("\(index + 1). \(step)")
                    .font(.caption2)
                    .textSelection(.enabled)
                }
              }
            }
            .padding(7)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 7))
          }
        }
      } else {
        Label("已保留为例题，尚未提炼出明确方法", systemImage: "doc.text.magnifyingglass")
          .font(.caption2)
          .foregroundStyle(.secondary)
      }

      HStack(alignment: .top, spacing: 7) {
        Image(
          systemName: isEditing
            ? "arrow.triangle.2.circlepath"
            : canApprove ? "checkmark.shield.fill" : "lock.trianglebadge.exclamationmark"
        )
        .foregroundStyle(isEditing ? .blue : canApprove ? .green : .orange)
        Text(verificationGuidance)
          .font(.caption2)
          .foregroundStyle(.secondary)
      }

      if expanded, !isEditing {
        TextField("复核意见（可选）", text: $reviewerNote, axis: .vertical)
          .textFieldStyle(.roundedBorder)
          .lineLimit(1...3)
      }

      HStack {
        if isEditing {
          Button("取消编辑") {
            restoreDraftFields()
            isEditing = false
          }
          .buttonStyle(.plain)
          Spacer()
          Button("保存并重新校验") { saveDraft() }
            .buttonStyle(.borderedProminent)
            .disabled(isSaving)
        } else {
          Button(expanded ? "收起" : "展开复核") { expanded.toggle() }
            .buttonStyle(.plain)
          Button("编辑草稿") {
            expanded = true
            isEditing = true
          }
          .buttonStyle(.plain)
          Spacer()
          if expanded {
            Button("驳回", role: .destructive) {
              confirmingRejection = true
            }
            .disabled(isReviewing)
            Button("确认晋级") {
              Task {
                await model.reviewExample(
                  example,
                  decision: .approve,
                  reviewerNote: reviewerNote
                )
              }
            }
            .buttonStyle(.borderedProminent)
            .disabled(!canApprove || isReviewing)
          }
        }
        if isReviewing || isSaving {
          ProgressView()
            .controlSize(.small)
        }
      }
      .font(.caption)
    }
    .padding(12)
    .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
    .overlay {
      RoundedRectangle(cornerRadius: 10)
        .stroke(.separator.opacity(0.5), lineWidth: 0.5)
    }
    .confirmationDialog(
      "驳回这条知识草稿？",
      isPresented: $confirmingRejection,
      titleVisibility: .visible
    ) {
      Button("驳回草稿", role: .destructive) {
        Task {
          await model.reviewExample(
            example,
            decision: .reject,
            reviewerNote: reviewerNote
          )
        }
      }
      Button("取消", role: .cancel) {}
    } message: {
      Text("草稿会保留在历史中，但不会进入正式方法库。")
    }
  }

  private var verificationGuidance: String {
    if isEditing {
      return "保存会建立旧版本快照，并对新内容重新执行数学验证与方法提炼。"
    }
    switch example.verification.status {
    case "verified":
      return "独立数学验证已通过；确认题目、解答与方法无误后可晋级。"
    case "rejected":
      return "当前数学目标验证未通过；请编辑草稿并修正表达式、答案或条件。"
    default:
      return "缺少可验证数学目标；可编辑补全后重新校验，当前不能晋级。"
    }
  }

  private var draftEditor: some View {
    VStack(alignment: .leading, spacing: 7) {
      HStack {
        TextField("标签（逗号分隔）", text: $draftTags)
        TextField("方法提示（可选）", text: $draftMethodHint)
      }
      Divider()
      Text("可验证数学目标（表达式与期望答案同时留空可移除）")
        .font(.caption2.weight(.semibold))
      HStack {
        TextField("原表达式", text: $draftExpression)
          .font(.caption.monospaced())
        TextField("期望答案", text: $draftExpected)
          .font(.caption.monospaced())
      }
      HStack {
        TextField("变量", text: $draftVariable)
          .frame(width: 70)
        TextField("参数（逗号分隔）", text: $draftParameters)
        TextField("趋近点", text: $draftPoint)
          .frame(width: 85)
        Picker("方向", selection: $draftDirection) {
          Text("双侧").tag("two_sided")
          Text("左侧").tag("left")
          Text("右侧").tag("right")
        }
        .frame(width: 140)
      }
      HStack {
        Picker("验算模式", selection: $draftMode) {
          ForEach(VerificationMode.allCases) { item in
            Text(item.displayName).tag(item)
          }
        }
        if draftMode == .asymptoticExpansion {
          TextField("余项阶数", text: $draftRemainderPower)
            .frame(width: 95)
        }
        TextField("假设，如 a:positive; n:integer", text: $draftAssumptions)
      }
    }
    .font(.caption)
    .padding(9)
    .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
  }

  private func saveDraft() {
    let problem = draftProblem.trimmingCharacters(in: .whitespacesAndNewlines)
    let solution = draftSolution.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !problem.isEmpty, !solution.isEmpty else {
      model.errorMessage = "题目和解答内容不能为空。"
      return
    }
    let expression = draftExpression.trimmingCharacters(in: .whitespacesAndNewlines)
    let expected = draftExpected.trimmingCharacters(in: .whitespacesAndNewlines)
    let mathPayload: MathPayloadRequest?
    if expression.isEmpty, expected.isEmpty {
      mathPayload = nil
    } else {
      guard let parsed = buildMathPayload() else { return }
      mathPayload = parsed
    }
    let request = ExampleDraftUpdateRequest(
      expectedRevision: example.revision,
      problem: problem,
      solution: solution,
      tags: parseCommaSeparated(draftTags),
      methodHint: draftMethodHint.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
      mathPayload: mathPayload
    )
    Task {
      if await model.updateExampleDraft(example, request: request) {
        isEditing = false
      }
    }
  }

  private func buildMathPayload() -> MathPayloadRequest? {
    let expression = draftExpression.trimmingCharacters(in: .whitespacesAndNewlines)
    let expected = draftExpected.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !expression.isEmpty, !expected.isEmpty else {
      model.errorMessage = "原表达式与期望答案必须同时填写或同时留空。"
      return nil
    }
    let variable = draftVariable.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !variable.isEmpty else {
      model.errorMessage = "数学目标的变量不能为空。"
      return nil
    }
    let parameters = parseCommaSeparated(draftParameters)
    guard
      let assumptions = parseAssumptions(
        draftAssumptions,
        allowedSymbols: Set([variable] + parameters)
      )
    else {
      return nil
    }
    let remainderPower: Int?
    if draftMode == .asymptoticExpansion {
      guard let parsed = Int(draftRemainderPower), (1...50).contains(parsed) else {
        model.errorMessage = "余项阶数必须是 1 到 50 之间的整数。"
        return nil
      }
      remainderPower = parsed
    } else {
      remainderPower = nil
    }
    return MathPayloadRequest(
      expression: expression,
      expected: expected,
      variable: variable,
      parameters: parameters,
      assumptions: assumptions,
      point: draftPoint.trimmingCharacters(in: .whitespacesAndNewlines),
      direction: draftDirection,
      mode: draftMode,
      remainderPower: remainderPower
    )
  }

  private func restoreDraftFields() {
    draftProblem = example.problem
    draftSolution = example.solution
    draftTags = example.tags.joined(separator: ", ")
    draftMethodHint = example.methodHint ?? ""
    draftExpression = example.mathPayload?.expression ?? ""
    draftExpected = example.mathPayload?.expected ?? ""
    draftVariable = example.mathPayload?.variable ?? "x"
    draftParameters = example.mathPayload?.parameters.joined(separator: ", ") ?? ""
    draftAssumptions = Self.renderAssumptions(example.mathPayload?.assumptions ?? [:])
    draftPoint = example.mathPayload?.point ?? "oo"
    draftDirection = example.mathPayload?.direction ?? "two_sided"
    draftMode = example.mathPayload?.mode ?? .asymptoticExpansion
    draftRemainderPower = example.mathPayload?.remainderPower.map(String.init) ?? "2"
  }

  private func parseCommaSeparated(_ value: String) -> [String] {
    var seen = Set<String>()
    return
      value
      .split(separator: ",")
      .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
      .filter { !$0.isEmpty && seen.insert($0).inserted }
  }

  private func parseAssumptions(
    _ value: String,
    allowedSymbols: Set<String>
  ) -> [String: [String]]? {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty { return [:] }
    let allowedProperties: Set<String> = [
      "real", "positive", "negative", "nonzero", "integer",
      "nonnegative", "nonpositive",
    ]
    var result: [String: [String]] = [:]
    for rawClause in trimmed.split(separator: ";") {
      let parts = rawClause.split(separator: ":", maxSplits: 1)
      guard parts.count == 2 else {
        model.errorMessage = "假设格式应为 a:positive; n:integer。"
        return nil
      }
      let symbol = parts[0].trimmingCharacters(in: .whitespacesAndNewlines)
      guard allowedSymbols.contains(symbol) else {
        model.errorMessage = "假设中的符号 \(symbol) 必须先声明为变量或参数。"
        return nil
      }
      let properties = parts[1]
        .split(separator: ",")
        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
        .filter { !$0.isEmpty }
      guard !properties.isEmpty, properties.allSatisfy(allowedProperties.contains) else {
        model.errorMessage = "假设属性只支持 positive、integer、nonzero 等受限值。"
        return nil
      }
      result[symbol] = Array(Set(properties)).sorted()
    }
    return result
  }

  private static func renderAssumptions(_ assumptions: [String: [String]]) -> String {
    assumptions.keys.sorted()
      .map { key in "\(key):\(assumptions[key, default: []].joined(separator: ","))" }
      .joined(separator: "; ")
  }
}

extension String {
  fileprivate var nilIfEmpty: String? { isEmpty ? nil : self }
}

private struct MethodCardView: View {
  @EnvironmentObject private var model: AppModel
  let method: MethodCard
  @State private var expanded = false

  var body: some View {
    VStack(alignment: .leading, spacing: 9) {
      HStack(alignment: .top) {
        VStack(alignment: .leading, spacing: 2) {
          Text(method.name)
            .font(.subheadline.weight(.semibold))
          Text(method.key)
            .font(.caption2.monospaced())
            .foregroundStyle(.secondary)
        }
        Spacer()
        StatusBadge(status: method.status)
      }

      Text(method.goal)
        .font(.caption)
        .foregroundStyle(.secondary)
        .lineLimit(expanded ? nil : 2)

      HStack(spacing: 12) {
        Label("\(method.successCount)", systemImage: "checkmark")
        Label("\(method.failureCount)", systemImage: "xmark")
        Text("v\(method.version)")
        Spacer()
        Button(expanded ? "收起" : "详情") { expanded.toggle() }
          .buttonStyle(.plain)
      }
      .font(.caption2)
      .foregroundStyle(.secondary)

      if expanded {
        if !method.procedure.isEmpty {
          Divider()
          VStack(alignment: .leading, spacing: 5) {
            Text("步骤")
              .font(.caption.weight(.semibold))
            ForEach(Array(method.procedure.enumerated()), id: \.offset) { index, step in
              Text("\(index + 1). \(step)")
                .font(.caption)
                .textSelection(.enabled)
            }
          }
        }

        HStack {
          if method.status == "pending_review" {
            Label("请从对应例题完成复核", systemImage: "arrow.turn.up.right")
              .font(.caption2)
              .foregroundStyle(.secondary)
          }
          Spacer()
          if method.status != "deprecated" {
            Button("废弃") {
              Task { await model.updateMethod(method, status: "deprecated") }
            }
            .controlSize(.small)
          }
        }
      }
    }
    .padding(12)
    .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
    .overlay {
      RoundedRectangle(cornerRadius: 10)
        .stroke(.separator.opacity(0.5), lineWidth: 0.5)
    }
  }
}

struct StatusBadge: View {
  let status: String

  var body: some View {
    Text(label)
      .font(.caption2.weight(.medium))
      .foregroundStyle(color)
      .padding(.horizontal, 7)
      .padding(.vertical, 3)
      .background(color.opacity(0.12), in: Capsule())
  }

  private var label: String {
    switch status {
    case "verified": "已验证"
    case "needs_review": "待复核"
    case "rejected": "未通过"
    case "generation_failed": "生成失败"
    case "promoted": "已晋级"
    case "pending_review": "待审"
    case "deprecated": "已废弃"
    default: status
    }
  }

  private var color: Color {
    switch status {
    case "verified": .green
    case "promoted": .blue
    case "needs_review", "pending_review": .orange
    case "rejected", "generation_failed", "deprecated": .red
    default: .secondary
    }
  }
}
