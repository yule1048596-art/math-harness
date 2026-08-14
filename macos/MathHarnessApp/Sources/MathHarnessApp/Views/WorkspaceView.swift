import AppKit
import MathHarnessCore
import SwiftUI

struct WorkspaceView: View {
  @EnvironmentObject private var model: AppModel
  @State private var showingRename = false
  @State private var renameDraft = ""
  @State private var searchQuery = ""
  @State private var searchHits: [ConversationMessage] = []

  private var providerProfiles: [ProviderProfile] {
    AppSettings.providerSettings.profiles
  }

  /// 当前对话实际会用哪个服务；跟随全局时显示全局那个。
  private var currentModelLabel: String {
    let settings = AppSettings.providerSettings
    if let chosen = model.selectedConversation?.provider?.profileID,
      let profile = settings.profile(id: chosen)
    {
      return profile.name
    }
    if let fallback = settings.simpleProfileID,
      let profile = settings.profile(id: fallback)
    {
      return "\(profile.name)（全局）"
    }
    return "离线 SymPy"
  }

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
      // 标题就是这段对话自己的名字。工作区名归侧栏——它在这里只是重复，却把
      // 一整行挤到会话名要缩成「求 x…」的地步。
      VStack(alignment: .leading, spacing: 2) {
        Text(model.selectedConversation?.title ?? "新会话")
          .font(.headline)
          .lineLimit(1)
        Text(headerSubtitle)
          .font(.caption)
          .foregroundStyle(.secondary)
          .lineLimit(1)
      }
      .onTapGesture(count: 2) {
        guard let conversation = model.selectedConversation else { return }
        renameDraft = conversation.title
        showingRename = true
      }

      Spacer(minLength: 12)

      // 对话内切换模型：立即生效，不需要重启数学引擎。选择随这个对话持久化。
      Menu {
        Button {
          Task { await model.setConversationProvider(nil) }
        } label: {
          if model.selectedConversation?.provider == nil {
            Label("跟随全局设置", systemImage: "checkmark")
          } else {
            Text("跟随全局设置")
          }
        }
        if !providerProfiles.isEmpty {
          Divider()
          ForEach(providerProfiles) { profile in
            Button {
              Task {
                await model.setConversationProvider(
                  ConversationProvider(profileID: profile.id)
                )
              }
            } label: {
              if model.selectedConversation?.provider?.profileID == profile.id {
                Label(profile.name, systemImage: "checkmark")
              } else {
                Text(profile.name)
              }
            }
          }
        }
      } label: {
        Label(currentModelLabel, systemImage: "cpu")
          .lineLimit(1)
      }
      .menuStyle(.borderlessButton)
      .frame(maxWidth: 200)
      .help("这个对话使用的模型服务")
      .disabled(model.selectedConversation == nil || model.isSolving)

      // 待复核是唯一一个「需要你去做点什么」的计数，所以它留在这里，而且可以点开。
      // 方法卡、记忆、消息三个数是纯信息，各自的面板里都写着，这里不再重复占位。
      if !model.pendingExamples.isEmpty {
        Button {
          model.inspectorPane = .knowledge
        } label: {
          Label("\(model.pendingExamples.count) 待复核", systemImage: "tray.full")
            .font(.caption.weight(.medium))
        }
        .buttonStyle(.borderless)
        .foregroundStyle(.orange)
        .help("打开知识库，复核待确认的例题")
      }

      Button {
        model.showingConversationSearch.toggle()
      } label: {
        Label("搜索", systemImage: "magnifyingglass")
      }
      .buttonStyle(.borderless)
      .help("搜索本工作区的会话消息（⌘F）")
    }
    .padding(.horizontal, 18)
    .frame(minHeight: 54)
    .alert("重命名会话", isPresented: $showingRename) {
      TextField("标题", text: $renameDraft)
      Button("取消", role: .cancel) {}
      Button("保存") {
        let title = renameDraft
        Task { await model.renameSelectedConversation(title) }
      }
    }
    .popover(isPresented: $model.showingConversationSearch, arrowEdge: .bottom) {
      searchPanel
    }
  }

  private var searchPanel: some View {
    VStack(alignment: .leading, spacing: 8) {
      TextField("搜索本工作区的会话消息", text: $searchQuery)
        .textFieldStyle(.roundedBorder)
        .onSubmit {
          let query = searchQuery
          Task { searchHits = await model.searchConversations(query) }
        }
      if searchHits.isEmpty {
        Text(searchQuery.isEmpty ? "输入关键词后回车。" : "没有匹配的消息。")
          .font(.caption)
          .foregroundStyle(.secondary)
      } else {
        List(searchHits) { hit in
          VStack(alignment: .leading, spacing: 2) {
            Text(verbatim: hit.role == "user" ? "我" : "助手")
              .font(.caption2)
              .foregroundStyle(.secondary)
            Text(hit.content)
              .lineLimit(3)
              .font(.callout)
          }
          .contentShape(Rectangle())
          .onTapGesture {
            model.selectConversation(hit.conversationID)
            model.showingConversationSearch = false
          }
        }
        .listStyle(.plain)
        .frame(height: 260)
      }
    }
    .padding(12)
    .frame(width: 380)
  }

  private var headerSubtitle: String {
    guard let conversation = model.selectedConversation else {
      return "直接提问就会开一段新会话"
    }
    let memories = model.memories.count { $0.status == .active }
    return "\(conversation.messageCount) 条消息 · \(memories) 条记忆 · 双击可重命名"
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
            NewConversationStart()
              .frame(maxWidth: .infinity, minHeight: 300)
          } else {
            ForEach(model.messages) { message in
              ConversationMessageRow(message: message)
                .id(message.id)
            }
          }
          if !model.streamingReply.isEmpty {
            StreamingReplyRow(text: model.streamingReply)
              .id("streaming-reply")
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
      .onChange(of: model.streamingReply) {
        // 字在长，视图要跟着走，否则新内容一直落在屏幕外面。
        // 这里**不加动画**：每来一段就跑一次动画会抖。
        guard !model.streamingReply.isEmpty else { return }
        proxy.scrollTo("streaming-reply", anchor: .bottom)
      }
      .onChange(of: model.selectedConversationID) {
        if let id = model.messages.last?.id {
          proxy.scrollTo(id, anchor: .bottom)
        }
      }
    }
  }
}

/// 新会话的起点。
///
/// 以前这里只有一段说明文字，没有任何可点的东西——「任何数学领域都可以」听起来很像
/// 客套话。给四个真能点的例子，一眼看出跨度：微积分、线性代数、组合、几何证明。
private struct NewConversationStart: View {
  @EnvironmentObject private var model: AppModel

  private static let examples = [
    ("求 x^3·sin(x) 的导数", "function", "微积分"),
    ("求矩阵 [[2,1],[1,2]] 的特征值", "squareshape.split.2x2", "线性代数"),
    ("从 5 个不同的球里取 3 个，有多少种取法？", "number", "组合数学"),
    ("证明 (a+b)^2 - (a-b)^2 = 4ab", "checkmark.seal", "代数证明"),
  ]

  var body: some View {
    VStack(spacing: 16) {
      VStack(spacing: 6) {
        Image(systemName: "sparkles")
          .font(.title)
          .foregroundStyle(.tint)
        Text("这是一段新会话")
          .font(.title3.weight(.semibold))
        Text("直接用自然语言提问，任何数学领域都可以。回答里能被检验的断言会自动验一遍并标注可信度。")
          .font(.callout)
          .foregroundStyle(.secondary)
          .multilineTextAlignment(.center)
          .frame(maxWidth: 440)
      }

      LazyVGrid(
        columns: [GridItem(.adaptive(minimum: 220), spacing: 10)],
        spacing: 10
      ) {
        ForEach(Self.examples, id: \.0) { example in
          Button {
            model.suggestedPrompt = example.0
          } label: {
            VStack(alignment: .leading, spacing: 4) {
              Label(example.2, systemImage: example.1)
                .font(.caption)
                .foregroundStyle(.secondary)
              Text(example.0)
                .font(.callout)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
          }
          .buttonStyle(.plain)
        }
      }
      .frame(maxWidth: 520)
    }
    .padding(.vertical, 24)
  }
}

private struct ConversationMessageRow: View {
  @EnvironmentObject private var model: AppModel
  @State private var showingEdit = false
  @State private var editDraft = ""
  let message: ConversationMessage

  var body: some View {
    if message.role == "user" {
      HStack {
        Spacer(minLength: 100)
        UserMessageText(text: message.content)
          .padding(.horizontal, 14)
          .padding(.vertical, 10)
          .background(.tint.opacity(0.14), in: RoundedRectangle(cornerRadius: 15))
          .contextMenu {
            // 两者都以新回合追加，原始历史不覆盖——与人工纠正、方法卡版本快照
            // 保持同一条审计原则。
            Button {
              let content = message.content
              Task {
                _ = await model.sendConversationTurn(
                  message: content, tags: [], mathTarget: nil
                )
              }
            } label: {
              Label("重新生成", systemImage: "arrow.clockwise")
            }
            Button {
              editDraft = message.content
              showingEdit = true
            } label: {
              Label("编辑后重发……", systemImage: "pencil")
            }
            Divider()
            Button {
              NSPasteboard.general.clearContents()
              NSPasteboard.general.setString(message.content, forType: .string)
            } label: {
              Label("复制", systemImage: "doc.on.doc")
            }
          }
      }
      .alert("编辑后重发", isPresented: $showingEdit) {
        TextField("消息", text: $editDraft)
        Button("取消", role: .cancel) {}
        Button("发送") {
          let content = editDraft
          Task {
            _ = await model.sendConversationTurn(
              message: content, tags: [], mathTarget: nil
            )
          }
        }
      } message: {
        Text("会以新回合追加，原消息保留在历史中。")
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
            if let generationError = message.generationError {
              ConfidencePill(
                title: message.wasStoppedByUser ? "已停止" : "生成中断",
                symbol: message.wasStoppedByUser
                  ? "stop.circle" : "wifi.exclamationmark",
                tint: .orange,
                help: message.wasStoppedByUser
                  ? "你停止了这次生成。已经收到的正文保留下来，但不会被验算或写入知识库。"
                  : "这条回复没有生成完整，已保留正文，但不会被验算或写入知识库。\n\(generationError)"
              )
            }
            if let verification = message.verificationStatus {
              VerificationPill(status: verification)
            }
            // 两轴分开显示。结论对不对和推导站不站得住是两件事，合成一个标签
            // 就会把「结论对、推导错」显示成「对」——而那正是最该被看见的一种。
            if let conclusion = message.conclusionConfidence.flatMap(
              ConclusionConfidence.init(rawValue:)
            ) {
              ConfidencePill(
                title: conclusion.title,
                symbol: conclusion.symbol,
                tint: conclusionTint(conclusion),
                help: conclusion.explanation
              )
            }
            if let process = message.processConfidence.flatMap(
              ProcessConfidence.init(rawValue:)
            ), process != .stepUnchecked {
              ConfidencePill(
                title: process.title,
                symbol: process == .stepChecked
                  ? "list.bullet.indent" : "exclamationmark.triangle.fill",
                tint: process == .stepChecked ? .secondary : .orange,
                help: process.explanation
              )
            }
            if message.generationError == nil, message.verificationStatus == nil,
              message.conclusionConfidence == nil,
              message.kind == "chat"
            {
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

          MessageTextView(text: message.content)

          if !message.counterexample.isEmpty {
            // 反例是这套检查最有用的产物：用户据此才分得清「真错」还是「我少说了
            // 一个前提」。
            Label(
              "反例：\(formattedCounterexample)",
              systemImage: "exclamationmark.magnifyingglass"
            )
            .font(.caption)
            .foregroundStyle(.orange)
            .textSelection(.enabled)
          }

          if !message.checkedClaims.isEmpty {
            // 抽错题的风险始终存在——系统可能验了一个你没问的命题。做法是让它
            // 看得见，而不是发送前拦一道确认。
            DisclosureGroup {
              VStack(alignment: .leading, spacing: 3) {
                ForEach(message.checkedClaims, id: \.self) { claim in
                  Text(claim)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                }
              }
              .frame(maxWidth: .infinity, alignment: .leading)
              .padding(.top, 4)
            } label: {
              Text("检查了 \(message.checkedClaims.count) 条断言")
                .font(.caption)
            }
            .foregroundStyle(.secondary)
          }

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
        .contextMenu {
          // 助手消息以前一个菜单项都没有：想复制一段答案只能手动划选。
          Button {
            copyToPasteboard(message.content)
          } label: {
            Label("复制回答", systemImage: "doc.on.doc")
          }
          if !message.counterexample.isEmpty {
            Button {
              copyToPasteboard(formattedCounterexample)
            } label: {
              Label("复制反例", systemImage: "exclamationmark.magnifyingglass")
            }
          }
          if let question = precedingQuestion {
            Divider()
            Button {
              Task {
                _ = await model.sendConversationTurn(
                  message: question, tags: [], mathTarget: nil
                )
              }
            } label: {
              Label("重新生成", systemImage: "arrow.clockwise")
            }
            .disabled(model.isSolving)
          }
        }

        Spacer(minLength: 52)
      }
    }
  }

  /// 这条回答对应的提问。重新生成是以新回合追加，原始历史不覆盖。
  private var precedingQuestion: String? {
    guard let index = model.messages.firstIndex(where: { $0.id == message.id }) else {
      return nil
    }
    return model.messages[..<index].last { $0.role == "user" }?.content
  }

  private func copyToPasteboard(_ value: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(value, forType: .string)
  }

  private var methodLabels: [String] {
    message.methodKeys.map { key in
      model.methods.first(where: { $0.key == key })?.name ?? key
    }
  }

  private var formattedCounterexample: String {
    message.counterexample
      .sorted { $0.key < $1.key }
      .map { "\($0.key) = \($0.value)" }
      .joined(separator: "，")
  }

  private func conclusionTint(_ level: ConclusionConfidence) -> Color {
    switch level {
    case .proofVerified, .verified: .green
    case .numericallyChecked, .crossChecked: .blue
    case .peerReviewed: .teal
    case .unchecked: .secondary
    case .refuted: .red
    }
  }
}

/// 正在流式到达的回复。
///
/// **刻意不带可信度徽章。** 检查要看完整的推导，逐步检查在只有半条推导时给出的判断
/// 没有意义；徽章一边流一边变，用户会看到「先说对、又说错」。这里只说明它还没查。
private struct StreamingReplyRow: View {
  let text: String

  var body: some View {
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
          Label("正在回答……", systemImage: "ellipsis")
            .font(.caption2.weight(.medium))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(.quaternary, in: Capsule())
          Spacer()
        }

        MessageTextView(text: text)

        Text("回答完成后会自动检查并标注可信度。")
          .font(.caption)
          .foregroundStyle(.tertiary)
      }
      .padding(14)
      .background(.background.secondary, in: RoundedRectangle(cornerRadius: 15))

      Spacer(minLength: 52)
    }
  }
}

/// 可信度徽章。悬停给出这个档位到底意味着什么——分级只有在用户看得懂时才有用。
private struct ConfidencePill: View {
  let title: String
  let symbol: String
  let tint: Color
  let help: String

  var body: some View {
    Label(title, systemImage: symbol)
      .font(.caption2.weight(.medium))
      .foregroundStyle(tint)
      .padding(.horizontal, 7)
      .padding(.vertical, 3)
      .background(tint.opacity(0.13), in: Capsule())
      .help(help)
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
  @AppStorage(AppSettingsKey.sendShortcut)
  private var sendShortcut = SendShortcut.commandReturn.rawValue
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
      // 主交互就是一个输入框。直接问，系统自己从回答里找出可检验的断言并标注
      // 可信度——不需要先选模式、也不需要先确认目标。
      //
      // 「验算求解」不再是并列的一半，而是一个可选的收紧：当你确实想指定验算目标、
      // 拿确定性的 verified 时才展开它。
      HStack(spacing: 10) {
        if composerMode == .verifiedSolve {
          Button {
            composerMode = .chat
            resetTargetFields()
          } label: {
            Label("返回提问", systemImage: "chevron.left")
              .font(.caption)
          }
          .buttonStyle(.borderless)
          Label("候选答案由独立验证器验收", systemImage: "checkmark.shield")
            .font(.caption)
            .foregroundStyle(.secondary)
        } else {
          Label("回答会自动检查并标注可信度", systemImage: "checkmark.seal")
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        Spacer()
        if composerMode == .chat {
          Button {
            composerMode = .verifiedSolve
          } label: {
            Label("指定验算目标", systemImage: "target")
              .font(.caption)
          }
          .buttonStyle(.borderless)
          .help("需要确定性的符号验证时用它。平时直接提问即可。")
        }
      }

      if let activity = model.memoryActivityMessage {
        HStack(spacing: 6) {
          Image(systemName: "brain.head.profile")
          Text(activity)
          Spacer()
        }
        .font(.caption)
        .foregroundStyle(.secondary)
      }

      messageEditor

      if composerMode == .verifiedSolve {
        targetEditor
      }

      HStack {
        Text(statusText)
          .font(.caption)
          .foregroundStyle(.secondary)
        Spacer()
        if model.isSolving, model.stoppableTurnID != nil {
          // 停止是聊天软件的基本盘。它不取消这一回合：已经吐出来的正文照常保留成
          // 一条「已停止」的消息——不检查、不入库，和断线走同一条路。
          Button {
            Task { await model.stopCurrentTurn() }
          } label: {
            Label(
              model.isStoppingTurn ? "正在停止……" : "停止",
              systemImage: "stop.fill"
            )
          }
          .buttonStyle(.bordered)
          .tint(.red)
          .disabled(model.isStoppingTurn)
          .keyboardShortcut(.escape, modifiers: [])
          .help("停止生成，保留已经收到的正文")
        } else {
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
    .onChange(of: model.suggestedPrompt) { _, suggestion in
      guard let suggestion else { return }
      message = suggestion
      model.suggestedPrompt = nil
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

  private var messageEditor: some View {
    TextEditor(text: $message)
      .font(.body)
      .scrollContentBackground(.hidden)
      .scrollIndicators(.hidden)
      // 高度跟着内容走。以前是固定 54…110，空着也占满——小窗口下对话区被白白
      // 吃掉一大块，而那正是要读答案的地方。
      .frame(height: composerHeight)
      .padding(8)
      .background(.background, in: RoundedRectangle(cornerRadius: 10))
      .overlay {
        RoundedRectangle(cornerRadius: 10)
          .stroke(.separator, lineWidth: 1)
      }
      .overlay(alignment: .topLeading) {
        if message.isEmpty {
          Text(placeholder)
            .foregroundStyle(.tertiary)
            .padding(.horizontal, 13)
            .padding(.vertical, 12)
            .allowsHitTesting(false)
        }
      }
      .onKeyPress(keys: [.return]) { press in
        // ↩ 发送是可选的：数学问题常常要分行写，所以默认仍是 ⌘↩ 发送、↩ 换行。
        guard sendShortcut == SendShortcut.plainReturn.rawValue else {
          return .ignored
        }
        // 带修饰键的回车一律放行给输入框换行——⇧↩ 是这个模式下唯一的换行方式。
        let modifiers: EventModifiers = [.shift, .option, .control, .command]
        guard press.modifiers.isDisjoint(with: modifiers) else { return .ignored }
        guard canSubmit else { return .ignored }
        submit()
        return .handled
      }
  }

  private var placeholder: String {
    composerMode == .chat
      ? "问任何数学问题——微积分、线性代数、几何、组合、证明……"
      : "输入需要独立验算的数学问题……"
  }

  /// 输入框高度：按行数长，到六行封顶再滚动。
  private var composerHeight: CGFloat {
    let lines = message.reduce(into: 1) { count, character in
      if character.isNewline { count += 1 }
    }
    // 长行也要占位，否则粘一整段进来仍然只显示一行。
    let wrapped = max(lines, min(6, message.count / 46 + 1))
    // 每行 20 再加一段余量：给得不够时 TextEditor 会认为内容装不下，右边挂出一条
    // 滚动条——空输入框上挂着滚动条很难看。
    return CGFloat(min(max(wrapped, 2), 6)) * 20 + 14
  }

  private var canSubmit: Bool {
    !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      && !model.isSolving
      && !model.isDraftingTarget
  }

  private var statusText: String {
    if model.isDraftingTarget { return "正在整理可验证目标……" }
    if model.isStoppingTurn { return "正在停止，保留已经收到的正文……" }
    if model.isSolving {
      return composerMode == .chat
        ? "正在结合会话记忆生成回复……"
        : "正在检索、求解并独立验证……"
    }
    return (SendShortcut(rawValue: sendShortcut) ?? .commandReturn).hint
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
