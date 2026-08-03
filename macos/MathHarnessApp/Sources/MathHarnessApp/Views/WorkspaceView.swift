import MathHarnessCore
import SwiftUI

struct WorkspaceView: View {
  @EnvironmentObject private var model: AppModel

  var body: some View {
    VStack(spacing: 0) {
      workspaceHeader
      Divider()
      ConversationTimeline()
      Divider()
      ConversationComposer()
    }
    .navigationTitle(model.selectedWorkspace?.name ?? "Math Harness")
  }

  private var workspaceHeader: some View {
    HStack(spacing: 12) {
      VStack(alignment: .leading, spacing: 3) {
        Text(model.selectedWorkspace?.name ?? "")
          .font(.headline)
        if let description = model.selectedWorkspace?.description,
          !description.isEmpty
        {
          Text(description)
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(1)
        }
      }

      Spacer(minLength: 12)

      Menu {
        if model.conversations.isEmpty {
          Text("还没有会话")
        } else {
          ForEach(model.conversations) { conversation in
            Button {
              model.selectConversation(conversation.id)
            } label: {
              if conversation.id == model.selectedConversationID {
                Label(conversation.title, systemImage: "checkmark")
              } else {
                Text(conversation.title)
              }
            }
          }
          Divider()
        }
        Button {
          Task { _ = await model.createConversation() }
        } label: {
          Label("新建会话", systemImage: "square.and.pencil")
        }
      } label: {
        Label(
          model.selectedConversation?.title ?? "新会话",
          systemImage: "bubble.left.and.bubble.right"
        )
        .lineLimit(1)
      }
      .menuStyle(.borderlessButton)
      .frame(maxWidth: 220)
      .disabled(model.isSolving)

      if !model.pendingExamples.isEmpty {
        Label("\(model.pendingExamples.count) 待复核", systemImage: "tray.full")
          .font(.caption)
          .foregroundStyle(.orange)
      }
      Label("\(model.methods.count) 张方法卡", systemImage: "books.vertical")
        .font(.caption)
        .foregroundStyle(.secondary)
      Label("\(model.messages.count) 条消息", systemImage: "text.bubble")
        .font(.caption)
        .foregroundStyle(.secondary)
    }
    .padding(.horizontal, 18)
    .frame(minHeight: 58)
  }
}

private struct ConversationTimeline: View {
  @EnvironmentObject private var model: AppModel

  var body: some View {
    ScrollViewReader { proxy in
      ScrollView {
        LazyVStack(spacing: 18) {
          if model.selectedConversation == nil {
            ContentUnavailableView {
              Label("开始一段数学对话", systemImage: "bubble.left.and.bubble.right")
            } description: {
              Text("每个工作区保存独立的会话、摘要、例题和方法卡。")
            } actions: {
              Button("新建会话") {
                Task { _ = await model.createConversation() }
              }
              .buttonStyle(.borderedProminent)
            }
            .frame(maxWidth: .infinity, minHeight: 320)
          } else if model.messages.isEmpty {
            ContentUnavailableView {
              Label("这是一段新会话", systemImage: "sparkles")
            } description: {
              Text("可以自然聊天，也可以切换到“验算求解”生成可复核的数学知识草稿。")
            }
            .frame(maxWidth: .infinity, minHeight: 320)
          } else {
            ForEach(model.messages) { message in
              ConversationMessageRow(message: message)
                .id(message.id)
            }
          }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 22)
        .frame(maxWidth: 900)
        .frame(maxWidth: .infinity)
      }
      .onChange(of: model.messages.count) {
        if let id = model.messages.last?.id {
          withAnimation { proxy.scrollTo(id, anchor: .bottom) }
        }
      }
      .onChange(of: model.selectedConversationID) {
        if let id = model.messages.last?.id {
          proxy.scrollTo(id, anchor: .bottom)
        }
      }
    }
  }
}

private struct ConversationMessageRow: View {
  @EnvironmentObject private var model: AppModel
  let message: ConversationMessage

  var body: some View {
    if message.role == "user" {
      HStack {
        Spacer(minLength: 100)
        Text(message.content)
          .textSelection(.enabled)
          .padding(.horizontal, 14)
          .padding(.vertical, 10)
          .background(.tint.opacity(0.14), in: RoundedRectangle(cornerRadius: 15))
      }
    } else {
      HStack(alignment: .top, spacing: 10) {
        Image(systemName: "function")
          .font(.headline)
          .foregroundStyle(.tint)
          .frame(width: 31, height: 31)
          .background(.tint.opacity(0.10), in: Circle())

        VStack(alignment: .leading, spacing: 10) {
          HStack(spacing: 8) {
            Text("Math Harness")
              .font(.subheadline.weight(.semibold))
            if let verification = message.verificationStatus {
              VerificationPill(status: verification)
            } else if message.kind == "chat" {
              Text("对话")
                .font(.caption2.weight(.medium))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 7)
                .padding(.vertical, 3)
                .background(.quaternary, in: Capsule())
            }
            Spacer()
            Text(message.model ?? message.provider ?? "本地")
              .font(.caption2)
              .foregroundStyle(.tertiary)
          }

          Text(message.content)
            .textSelection(.enabled)
            .lineSpacing(3)

          if message.knowledgeDraftID != nil || !message.methodKeys.isEmpty {
            Divider()
            VStack(alignment: .leading, spacing: 4) {
              if message.knowledgeDraftID != nil {
                Label("已生成待复核知识草稿", systemImage: "tray.and.arrow.down.fill")
                  .foregroundStyle(.orange)
              }
              if !message.methodKeys.isEmpty {
                Label(
                  "检索方法：\(methodLabels.joined(separator: "、"))",
                  systemImage: "books.vertical"
                )
              }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
          }
        }
        .padding(14)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 15))

        Spacer(minLength: 52)
      }
    }
  }

  private var methodLabels: [String] {
    message.methodKeys.map { key in
      model.methods.first(where: { $0.key == key })?.name ?? key
    }
  }
}

private struct VerificationPill: View {
  let status: String

  var body: some View {
    Label(label, systemImage: icon)
      .font(.caption2.weight(.medium))
      .foregroundStyle(color)
      .padding(.horizontal, 7)
      .padding(.vertical, 3)
      .background(color.opacity(0.12), in: Capsule())
  }

  private var label: String {
    switch status {
    case "verified": "已验证"
    case "rejected": "未通过"
    default: "待复核"
    }
  }

  private var icon: String {
    status == "verified" ? "checkmark.seal.fill" : "exclamationmark.triangle.fill"
  }

  private var color: Color {
    status == "verified" ? .green : .orange
  }
}

private enum ComposerMode: String, CaseIterable, Identifiable {
  case chat
  case verifiedSolve

  var id: String { rawValue }

  var title: String {
    switch self {
    case .chat: "普通聊天"
    case .verifiedSolve: "验算求解"
    }
  }
}

private struct ConversationComposer: View {
  @EnvironmentObject private var model: AppModel
  @State private var composerMode: ComposerMode = .chat
  @State private var message = ""
  @State private var tags = ""
  @State private var showingTarget = true
  @State private var expression = ""
  @State private var variable = "x"
  @State private var parameters = ""
  @State private var assumptions = ""
  @State private var point = "oo"
  @State private var direction = "two_sided"
  @State private var verificationMode: VerificationMode = .asymptoticExpansion
  @State private var remainderPower = "2"
  @State private var targetDraftSummary: String?
  @State private var targetDraftWarnings: [String] = []
  @State private var draftedMessage: String?
  @State private var awaitingTargetConfirmation = false

  var body: some View {
    VStack(spacing: 10) {
      HStack {
        Picker("发送模式", selection: $composerMode) {
          ForEach(ComposerMode.allCases) { mode in
            Text(mode.title).tag(mode)
          }
        }
        .pickerStyle(.segmented)
        .frame(width: 250)
        Spacer()
        if composerMode == .chat {
          Label("使用会话记忆与已晋级方法", systemImage: "brain.head.profile")
            .font(.caption)
            .foregroundStyle(.secondary)
        } else {
          Label("候选答案由独立验证器验收", systemImage: "checkmark.shield")
            .font(.caption)
            .foregroundStyle(.secondary)
        }
      }

      TextEditor(text: $message)
        .font(.body)
        .scrollContentBackground(.hidden)
        .frame(minHeight: 54, maxHeight: 110)
        .padding(8)
        .background(.background, in: RoundedRectangle(cornerRadius: 10))
        .overlay {
          RoundedRectangle(cornerRadius: 10)
            .stroke(.separator, lineWidth: 1)
        }
        .overlay(alignment: .topLeading) {
          if message.isEmpty {
            Text(
              composerMode == .chat
                ? "输入消息，可以继续追问上下文……"
                : "输入需要独立验算的数学问题……"
            )
            .foregroundStyle(.tertiary)
            .padding(.horizontal, 13)
            .padding(.vertical, 16)
            .allowsHitTesting(false)
          }
        }

      if composerMode == .verifiedSolve {
        targetEditor
      }

      HStack {
        Text(statusText)
          .font(.caption)
          .foregroundStyle(.secondary)
        Spacer()
        Button {
          submit()
        } label: {
          if model.isSolving || model.isDraftingTarget {
            ProgressView()
              .controlSize(.small)
              .frame(width: 62)
          } else {
            Label(buttonTitle, systemImage: buttonIcon)
          }
        }
        .keyboardShortcut(.return, modifiers: [.command])
        .buttonStyle(.borderedProminent)
        .disabled(
          message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || model.isSolving
            || model.isDraftingTarget
        )
      }
    }
    .padding(.horizontal, 18)
    .padding(.vertical, 12)
    .background(.regularMaterial)
    .onChange(of: message) { _, _ in
      invalidateTargetDraft()
    }
    .onChange(of: composerMode) { _, _ in
      resetTargetFields()
    }
    .onChange(of: model.selectedConversationID) { oldValue, _ in
      if oldValue == nil, model.isSolving {
        return
      }
      composerMode = .chat
      message = ""
      resetTargetFields()
    }
  }

  private var targetEditor: some View {
    DisclosureGroup("可验证数学目标", isExpanded: $showingTarget) {
      VStack(spacing: 8) {
        HStack {
          Button {
            requestTargetDraft()
          } label: {
            if model.isDraftingTarget {
              ProgressView().controlSize(.mini)
            } else {
              Label("自动整理", systemImage: "wand.and.stars")
            }
          }
          .buttonStyle(.bordered)
          .disabled(
            message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
              || model.isDraftingTarget || model.isSolving
          )
          TextField("表达式，例如 sqrt(x**2+x)-x", text: $expression)
            .font(.body.monospaced())
        }
        HStack {
          TextField("变量", text: $variable)
            .frame(width: 72)
          TextField("参数（逗号分隔）", text: $parameters)
            .frame(minWidth: 130)
          TextField("趋近点", text: $point)
            .frame(width: 90)
          Picker("方向", selection: $direction) {
            Text("双侧").tag("two_sided")
            Text("左侧").tag("left")
            Text("右侧").tag("right")
          }
          .frame(width: 150)
        }
        HStack {
          Picker("验算模式", selection: $verificationMode) {
            ForEach(VerificationMode.allCases) { item in
              Text(item.displayName).tag(item)
            }
          }
          .frame(maxWidth: 240)
          if verificationMode == .asymptoticExpansion {
            TextField("余项阶数", text: $remainderPower)
              .frame(width: 110)
          }
          TextField("标签（逗号分隔）", text: $tags)
          Spacer()
        }
        TextField("假设，例如 a:positive; n:integer（可选）", text: $assumptions)
        if let targetDraftSummary {
          VStack(alignment: .leading, spacing: 3) {
            Label(
              awaitingTargetConfirmation
                ? "\(targetDraftSummary) 请检查后确认。"
                : targetDraftSummary,
              systemImage: awaitingTargetConfirmation ? "checkmark.bubble" : "info.bubble"
            )
            ForEach(targetDraftWarnings, id: \.self) { warning in
              Text("• \(warning)")
            }
          }
          .frame(maxWidth: .infinity, alignment: .leading)
          .foregroundStyle(awaitingTargetConfirmation ? .blue : .orange)
        }
        HStack {
          Image(systemName: "checkmark.shield")
          Text("自动整理只生成建议稿；目标由你确认后才用于验算和知识草稿。")
          Spacer()
        }
        .font(.caption)
        .foregroundStyle(.secondary)
      }
      .padding(.top, 6)
    }
    .font(.caption)
  }

  private var statusText: String {
    if model.isDraftingTarget { return "正在整理可验证目标……" }
    if model.isSolving {
      return composerMode == .chat
        ? "正在结合会话记忆生成回复……"
        : "正在检索、求解并独立验证……"
    }
    return "⌘↩ 发送"
  }

  private var buttonTitle: String {
    if composerMode == .chat { return "发送" }
    if awaitingTargetConfirmation { return "确认并验算" }
    if isUnstructuredContinuation { return "作为聊天发送" }
    if !expression.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
      return "验算求解"
    }
    return "整理目标"
  }

  private var buttonIcon: String {
    if composerMode == .chat || isUnstructuredContinuation {
      return "arrow.up.circle.fill"
    }
    if awaitingTargetConfirmation
      || !expression.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    {
      return "checkmark.shield.fill"
    }
    return "wand.and.stars"
  }

  private var isUnstructuredContinuation: Bool {
    let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
    return draftedMessage == trimmed
      && expression.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      && targetDraftSummary != nil
  }

  private func submit() {
    let submittedMessage = message.trimmingCharacters(in: .whitespacesAndNewlines)
    if composerMode == .chat {
      send(submittedMessage, target: nil, tags: [])
      return
    }

    let trimmedExpression = expression.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmedExpression.isEmpty, draftedMessage != submittedMessage {
      requestTargetDraft()
      return
    }

    let target: SolveMathTargetRequest?
    if trimmedExpression.isEmpty {
      target = nil
    } else {
      let trimmedVariable = variable.trimmingCharacters(in: .whitespacesAndNewlines)
      guard !trimmedVariable.isEmpty else {
        model.errorMessage = "数学目标的变量不能为空。"
        return
      }
      let power: Int?
      if verificationMode == .asymptoticExpansion {
        guard let parsed = Int(remainderPower), (1...50).contains(parsed) else {
          model.errorMessage = "余项阶数必须是 1 到 50 之间的整数。"
          return
        }
        power = parsed
      } else {
        power = nil
      }
      let normalizedParameters = parseCommaSeparated(parameters)
      guard
        let parsedAssumptions = parseAssumptions(
          assumptions,
          allowedSymbols: Set([trimmedVariable] + normalizedParameters)
        )
      else { return }
      target = SolveMathTargetRequest(
        expression: trimmedExpression,
        variable: trimmedVariable,
        parameters: normalizedParameters,
        assumptions: parsedAssumptions,
        point: point.trimmingCharacters(in: .whitespacesAndNewlines),
        direction: direction,
        mode: verificationMode,
        remainderPower: power
      )
    }
    send(submittedMessage, target: target, tags: parseCommaSeparated(tags))
  }

  private func send(
    _ submittedMessage: String,
    target: SolveMathTargetRequest?,
    tags: [String]
  ) {
    Task {
      if await model.sendConversationTurn(
        message: submittedMessage,
        tags: tags,
        mathTarget: target
      ) {
        if message.trimmingCharacters(in: .whitespacesAndNewlines) == submittedMessage {
          message = ""
          resetTargetFields()
        }
      }
    }
  }

  private func requestTargetDraft() {
    let submittedMessage = message.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !submittedMessage.isEmpty else {
      model.errorMessage = "请先输入数学问题。"
      return
    }
    Task {
      guard let result = await model.draftMathTarget(problem: submittedMessage) else {
        return
      }
      guard message.trimmingCharacters(in: .whitespacesAndNewlines) == submittedMessage else {
        return
      }
      draftedMessage = submittedMessage
      targetDraftSummary = result.summary
      targetDraftWarnings = result.warnings
      guard let target = result.target else {
        awaitingTargetConfirmation = false
        showingTarget = true
        return
      }
      applyTargetDraft(target)
      awaitingTargetConfirmation = true
      showingTarget = true
    }
  }

  private func applyTargetDraft(_ target: SolveMathTargetRequest) {
    expression = target.expression
    variable = target.variable
    parameters = target.parameters.joined(separator: ", ")
    assumptions = target.assumptions
      .keys.sorted()
      .map { key in "\(key):\(target.assumptions[key, default: []].joined(separator: ","))" }
      .joined(separator: "; ")
    point = target.point
    direction = target.direction
    verificationMode = target.mode
    remainderPower = target.remainderPower.map(String.init) ?? ""
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

  private func invalidateTargetDraft() {
    if awaitingTargetConfirmation {
      resetTargetFields()
      return
    }
    draftedMessage = nil
    awaitingTargetConfirmation = false
    targetDraftSummary = nil
    targetDraftWarnings = []
  }

  private func resetTargetFields() {
    expression = ""
    variable = "x"
    parameters = ""
    assumptions = ""
    point = "oo"
    direction = "two_sided"
    verificationMode = .asymptoticExpansion
    remainderPower = "2"
    draftedMessage = nil
    awaitingTargetConfirmation = false
    targetDraftSummary = nil
    targetDraftWarnings = []
  }
}
