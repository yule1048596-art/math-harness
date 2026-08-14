import AppKit
import Combine
import Foundation
import MathHarnessCore

/// 右侧检查器显示哪一面板。`nil` 表示收起。
///
/// 放在模型里而不是某个视图的 `@State`：工具栏、菜单命令和消息上的「待复核」徽章都要
/// 打开它，而它们分处三棵不同的视图子树。
enum InspectorPane: String, Equatable {
  case knowledge
  case memory
}

enum BackendPhase: Equatable {
  case idle
  case starting
  case running(version: String)
  case failed(message: String)
}

/// 连接测试的结果：请求本身是否打通，以及打通后后端的判定。
enum ProbeOutcome {
  case completed(ProviderTestResult)
  case transportFailure(String)
}

@MainActor
final class AppModel: ObservableObject {
  @Published private(set) var backendPhase: BackendPhase = .idle
  @Published private(set) var workspaces: [Workspace] = []
  @Published var selectedWorkspaceID: String?
  @Published private(set) var conversations: [Conversation] = []
  @Published private(set) var selectedConversationID: String?
  @Published private(set) var messages: [ConversationMessage] = []
  /// 正在流式到达的回复。为空表示当前没有回合在流。
  ///
  /// 它是一段**没有可信度**的临时正文：检查要等整条回复吐完才跑，徽章一边流一边变
  /// 会让用户看到「先说对、又说错」。落到 `messages` 里的那一刻才带上徽章。
  @Published private(set) var streamingReply: String = ""
  /// 正在流式生成、且可以被叫停的那一回合。为 nil 表示没有可停的东西。
  ///
  /// 指定了验算目标的回合不在其列：那条路的正文由求解器和验证器一起产出，中途没有
  /// 可以停下来保留的半成品。
  @Published private(set) var stoppableTurnID: String?
  /// 已经按下停止、正在等服务端收尾。按钮据此变灰，不让人连按。
  @Published private(set) var isStoppingTurn = false
  @Published private(set) var attempts: [SolutionAttempt] = []
  @Published private(set) var examples: [ProblemExample] = []
  @Published private(set) var methods: [MethodCard] = []
  @Published private(set) var memories: [MemoryItem] = []
  @Published private(set) var memorySettings: MemorySettings?
  @Published private(set) var memoryHealth: MemoryHealth?
  @Published private(set) var memoryActivityMessage: String?
  @Published private(set) var isMemoryOperationInProgress = false
  @Published var inspectorPane: InspectorPane? = AppSettings.inspectorPane {
    didSet { AppSettings.inspectorPane = inspectorPane }
  }
  // 这两个原本是视图自己的 @State。菜单命令够不着视图状态，而「新建工作区」和
  // 「搜索」正是最该有快捷键的两件事。
  @Published var showingCreateWorkspace = false
  @Published var showingConversationSearch = false
  /// 点了示例问题之后要填进输入框的文本。输入框取走后自己清空。
  ///
  /// **只填不发。** 示例是让人看清这个软件能问什么，不是替他决定问什么——填进去还能改。
  @Published var suggestedPrompt: String?
  @Published private(set) var showsArchivedConversations = false
  @Published private(set) var isSolving = false
  @Published private(set) var isDraftingTarget = false
  @Published private(set) var isRefreshing = false
  @Published private(set) var reviewingExampleID: String?
  @Published private(set) var editingExampleID: String?
  @Published private(set) var isImportingData = false
  @Published private(set) var isBackingUp = false
  @Published private(set) var isRestoring = false
  @Published var errorMessage: String?

  private let backend = BackendProcessController()
  private var api: APIClient?
  private var memoryPollingTask: Task<Void, Never>?

  var selectedWorkspace: Workspace? {
    workspaces.first { $0.id == selectedWorkspaceID }
  }

  var selectedConversation: Conversation? {
    conversations.first { $0.id == selectedConversationID }
  }

  var isDataOperationInProgress: Bool {
    isImportingData || isBackingUp || isRestoring
  }

  var pendingExamples: [ProblemExample] {
    examples
      .filter {
        $0.status == "pending_review"
          || ($0.status == "rejected" && $0.reviewedAt == nil)
      }
      .sorted { $0.createdAt > $1.createdAt }
  }

  func isAttemptCaptured(_ attemptID: String) -> Bool {
    examples.contains { $0.sourceAttemptID == attemptID }
  }

  func showError(_ message: String) {
    errorMessage = message
  }

  func start() async {
    guard backendPhase == .idle || isFailed else { return }
    backendPhase = .starting
    errorMessage = nil
    do {
      let connection = try await backend.start()
      api = APIClient(
        baseURL: connection.baseURL,
        bearerToken: connection.token
      )
      backendPhase = .running(version: connection.version)
      await refreshWorkspaces()
    } catch {
      backendPhase = .failed(message: error.localizedDescription)
    }
  }

  func stopBackend() {
    memoryPollingTask?.cancel()
    memoryPollingTask = nil
    backend.stop()
  }

  func restartBackend() async {
    backend.stop()
    api = nil
    backendPhase = .idle
    conversations = []
    selectedConversationID = nil
    messages = []
    attempts = []
    examples = []
    methods = []
    memories = []
    memorySettings = nil
    memoryHealth = nil
    memoryActivityMessage = nil
    memoryPollingTask?.cancel()
    memoryPollingTask = nil
    await start()
  }

  /// 连接测试。密钥只随这一次请求发给本地后端，不写入任何持久存储。
  func testProvider(_ request: ProviderTestRequest) async -> ProbeOutcome {
    guard let api else {
      return .transportFailure("数学引擎尚未就绪，请稍候重试。")
    }
    do {
      return .completed(try await api.testProvider(request))
    } catch {
      return .transportFailure(error.localizedDescription)
    }
  }

  /// 切换这个对话使用的模型服务。立即生效，不重启数学引擎。
  func setConversationProvider(_ provider: ConversationProvider?) async {
    guard let api, let workspaceID = selectedWorkspaceID,
      let conversationID = selectedConversationID
    else { return }
    do {
      let updated = try await api.setConversationProvider(
        workspaceID: workspaceID,
        conversationID: conversationID,
        provider: provider
      )
      replaceConversation(updated)
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  func renameSelectedConversation(_ title: String) async {
    guard let conversationID = selectedConversationID else { return }
    await renameConversation(conversationID, title: title)
  }

  func renameConversation(_ conversationID: String, title: String) async {
    guard let api, let workspaceID = selectedWorkspaceID else { return }
    do {
      replaceConversation(
        try await api.renameConversation(
          workspaceID: workspaceID,
          conversationID: conversationID,
          title: title
        )
      )
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  func toggleConversationArchive() async {
    guard let conversation = selectedConversation else { return }
    await toggleConversationArchive(conversation)
  }

  /// 归档只改状态，不删除任何消息。
  func toggleConversationArchive(_ conversation: Conversation) async {
    guard let api, let workspaceID = selectedWorkspaceID else { return }
    let next: ConversationStatus = conversation.status == .archived ? .active : .archived
    do {
      _ = try await api.setConversationStatus(
        workspaceID: workspaceID,
        conversationID: conversation.id,
        status: next
      )
      // 归档后列表可能不再包含它，所以要重新拉取而不是原地替换；当前选中的那个被
      // 归档时还得切走，否则界面会停在一个已经不在列表里的对话上。
      conversations = try await loadConversations(workspaceID: workspaceID)
      if !conversations.contains(where: { $0.id == selectedConversationID }) {
        selectConversation(conversations.first?.id)
      }
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  /// 侧栏是否连已归档的会话一起显示。
  ///
  /// 默认不显示。但**只能归档、看不到归档**等于单程票：v0.17 之前归档过的会话在界面上
  /// 再也找不回来，尽管消息一条没少。
  func setShowsArchivedConversations(_ value: Bool) async {
    guard value != showsArchivedConversations else { return }
    showsArchivedConversations = value
    guard let workspaceID = selectedWorkspaceID else { return }
    do {
      conversations = try await loadConversations(workspaceID: workspaceID)
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  private func loadConversations(workspaceID: String) async throws -> [Conversation] {
    try await api?
      .listConversations(
        workspaceID: workspaceID,
        includeArchived: showsArchivedConversations
      )
      .sorted { $0.updatedAt > $1.updatedAt } ?? []
  }

  func searchConversations(_ query: String) async -> [ConversationMessage] {
    guard let api, let workspaceID = selectedWorkspaceID else { return [] }
    do {
      return try await api.searchConversationMessages(
        workspaceID: workspaceID,
        query: query
      )
    } catch {
      errorMessage = error.localizedDescription
      return []
    }
  }

  private func replaceConversation(_ updated: Conversation) {
    if let index = conversations.firstIndex(where: { $0.id == updated.id }) {
      conversations[index] = updated
    }
  }

  func refreshWorkspaces() async {
    guard let api else { return }
    isRefreshing = true
    defer { isRefreshing = false }
    do {
      let loaded = try await api.listWorkspaces()
      workspaces = loaded.sorted { $0.createdAt < $1.createdAt }
      if let selectedWorkspaceID,
        !workspaces.contains(where: { $0.id == selectedWorkspaceID })
      {
        self.selectedWorkspaceID = nil
      }
      if selectedWorkspaceID == nil {
        selectedWorkspaceID = workspaces.first?.id
      }
      await refreshSelectedWorkspace()
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  func selectWorkspace(_ workspaceID: String?) {
    selectedWorkspaceID = workspaceID
    selectedConversationID = nil
    messages = []
    memories = []
    memorySettings = nil
    memoryHealth = nil
    memoryActivityMessage = nil
    memoryPollingTask?.cancel()
    Task { await refreshSelectedWorkspace() }
  }

  func createWorkspace(name: String, description: String) async -> Bool {
    guard let api else { return false }
    let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmedName.isEmpty else {
      errorMessage = "工作区名称不能为空。"
      return false
    }
    do {
      let workspace = try await api.createWorkspace(
        WorkspaceCreateRequest(name: trimmedName, description: description)
      )
      workspaces.append(workspace)
      selectedWorkspaceID = workspace.id
      await refreshSelectedWorkspace()
      return true
    } catch {
      errorMessage = error.localizedDescription
      return false
    }
  }

  func importExamples(
    content: String,
    sourceName: String,
    reviewPolicy: ImportReviewPolicy,
    extractorPolicy: ImportExtractorPolicy,
    commit: Bool
  ) async -> BulkExampleImportResult? {
    guard let api, let workspaceID = selectedWorkspaceID else { return nil }
    isImportingData = true
    errorMessage = nil
    defer { isImportingData = false }
    do {
      let result = try await api.bulkImportExamples(
        workspaceID: workspaceID,
        request: BulkExampleImportRequest(
          content: content,
          reviewPolicy: reviewPolicy,
          extractorPolicy: extractorPolicy,
          commit: commit,
          sourceName: sourceName
        )
      )
      if result.committed {
        await refreshSelectedWorkspace()
      }
      return result
    } catch {
      errorMessage = error.localizedDescription
      return nil
    }
  }

  func exportWorkspaceBackup() async -> (data: Data, suggestedName: String)? {
    guard let api, let workspace = selectedWorkspace else { return nil }
    isBackingUp = true
    errorMessage = nil
    defer { isBackingUp = false }
    do {
      let data = try await api.exportWorkspaceBackup(workspaceID: workspace.id)
      let safeName = workspace.name
        .replacingOccurrences(of: "/", with: "-")
        .replacingOccurrences(of: ":", with: "-")
      return (data, "\(safeName)-Math-Harness.mathharness")
    } catch {
      errorMessage = error.localizedDescription
      return nil
    }
  }

  func restoreWorkspaceBackup(_ data: Data) async -> WorkspaceRestoreResult? {
    guard let api else { return nil }
    isRestoring = true
    errorMessage = nil
    defer { isRestoring = false }
    do {
      let result = try await api.restoreWorkspaceBackup(data)
      workspaces = try await api.listWorkspaces().sorted { $0.createdAt < $1.createdAt }
      selectedWorkspaceID = result.workspace.id
      await refreshSelectedWorkspace()
      return result
    } catch {
      errorMessage = error.localizedDescription
      return nil
    }
  }

  func createConversation() async -> Conversation? {
    guard let api, let workspaceID = selectedWorkspaceID else { return nil }
    do {
      let conversation = try await api.createConversation(workspaceID: workspaceID)
      guard workspaceID == selectedWorkspaceID else { return conversation }
      conversations.insert(conversation, at: 0)
      selectedConversationID = conversation.id
      messages = []
      return conversation
    } catch {
      errorMessage = error.localizedDescription
      return nil
    }
  }

  /// 回答完成时提醒一下。
  ///
  /// 数学问题的回答动辄几十秒，人会切走去干别的。这里刻意用 Dock 图标跳动而不是系统
  /// 通知：不需要任何权限，装了就能用，未签名的开发版也一样。
  private func notifyTurnFinishedIfNeeded() {
    guard AppSettings.notifiesWhenFinished, !NSApplication.shared.isActive else {
      return
    }
    NSApplication.shared.requestUserAttention(.informationalRequest)
  }

  /// 停止当前正在流式生成的回合。
  ///
  /// 它**不取消**那条 HTTP 流——已经收到的正文要等服务端正常收尾成一条中断消息。
  /// 直接断开的话，用户看着字出现，然后整段消失。
  func stopCurrentTurn() async {
    guard
      let api,
      let workspaceID = selectedWorkspaceID,
      let conversationID = selectedConversationID,
      let turnID = stoppableTurnID,
      !isStoppingTurn
    else { return }
    isStoppingTurn = true
    do {
      try await api.stopConversationTurn(
        workspaceID: workspaceID,
        conversationID: conversationID,
        turnID: turnID
      )
    } catch {
      isStoppingTurn = false
      errorMessage = "没能停止这次生成：\(error.localizedDescription)"
    }
  }

  func selectConversation(_ conversationID: String?) {
    guard conversationID != selectedConversationID else { return }
    selectedConversationID = conversationID
    messages = []
    Task { await refreshSelectedConversation() }
  }

  func sendConversationTurn(
    message: String,
    tags: [String],
    mathTarget: SolveMathTargetRequest?
  ) async -> Bool {
    guard let api, let workspaceID = selectedWorkspaceID else { return false }
    let trimmedMessage = message.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmedMessage.isEmpty else {
      errorMessage = "请输入消息。"
      return false
    }

    let usesStreaming = mathTarget == nil
    if usesStreaming {
      streamingReply = ""
    }
    isSolving = true
    errorMessage = nil
    defer {
      isSolving = false
      stoppableTurnID = nil
      isStoppingTurn = false
      if usesStreaming {
        streamingReply = ""
      }
    }
    do {
      let conversationID: String
      if let selectedConversationID {
        conversationID = selectedConversationID
      } else {
        let created = try await api.createConversation(workspaceID: workspaceID)
        guard workspaceID == selectedWorkspaceID else { return false }
        conversations.insert(created, at: 0)
        self.selectedConversationID = created.id
        conversationID = created.id
      }
      let turnRequest = ConversationTurnRequest(
        message: trimmedMessage,
        tags: tags,
        topK: 5,
        mathTarget: mathTarget,
        maxOutputTokens: AppSettings.maxOutputTokens
      )
      if usesStreaming {
        stoppableTurnID = turnRequest.turnID
      }
      // 指定了验算目标的回合不流式：那条路的正文由求解器和验证器一起产出，
      // 中间没有可以逐字给出的东西。
      let result: ConversationTurnResult
      if usesStreaming {
        result = try await api.streamConversationTurn(
          workspaceID: workspaceID,
          conversationID: conversationID,
          request: turnRequest
        ) { [weak self] delta in
          guard let self else { return }
          guard
            workspaceID == self.selectedWorkspaceID,
            conversationID == self.selectedConversationID
          else { return }
          self.streamingReply += delta
        }
      } else {
        result = try await api.sendConversationTurn(
          workspaceID: workspaceID,
          conversationID: conversationID,
          request: turnRequest
        )
      }
      guard
        workspaceID == selectedWorkspaceID,
        conversationID == selectedConversationID
      else { return true }

      if !messages.contains(where: { $0.id == result.userMessage.id }) {
        messages.append(result.userMessage)
      }
      if !messages.contains(where: { $0.id == result.assistantMessage.id }) {
        messages.append(result.assistantMessage)
      }
      if result.assistantMessage.generationError != nil,
        !result.assistantMessage.wasStoppedByUser
      {
        // 自己按的停止不弹错误提示：那不是出错，是他要的结果。消息上的橙色徽章
        // 已经说明了这条回复不完整。
        errorMessage =
          "模型连接中断；已保留收到的内容，但未验算或写入知识库。可以重新生成。"
      }
      messages.sort { $0.ordinal < $1.ordinal }
      notifyTurnFinishedIfNeeded()
      if let index = conversations.firstIndex(where: { $0.id == result.conversation.id }) {
        conversations[index] = result.conversation
      } else {
        conversations.append(result.conversation)
      }
      conversations.sort { $0.updatedAt > $1.updatedAt }
      if let attempt = result.attempt,
        !attempts.contains(where: { $0.id == attempt.id })
      {
        attempts.append(attempt)
        attempts.sort { $0.createdAt < $1.createdAt }
      }
      if let memoryJob = result.memoryJob {
        memoryActivityMessage = "正在后台整理长期记忆……"
        startMemoryMonitoring(workspaceID: workspaceID, jobIDs: [memoryJob.id])
      }

      if mathTarget != nil {
        do {
          async let loadedMethods = api.listMethods(workspaceID: workspaceID)
          async let loadedExamples = api.listExamples(workspaceID: workspaceID)
          let (methods, examples) = try await (loadedMethods, loadedExamples)
          guard
            workspaceID == selectedWorkspaceID,
            conversationID == selectedConversationID
          else { return true }
          self.methods = methods
          self.examples = examples
        } catch {
          guard
            workspaceID == selectedWorkspaceID,
            conversationID == selectedConversationID
          else { return true }
          errorMessage = "回复已经保存，但知识栏刷新失败：\(error.localizedDescription)"
        }
      }
      return true
    } catch {
      errorMessage = error.localizedDescription
      return false
    }
  }

  func createMemory(
    content: String,
    kind: MemoryKind,
    tags: [String],
    pinned: Bool
  ) async -> Bool {
    guard let api, let workspaceID = selectedWorkspaceID else { return false }
    isMemoryOperationInProgress = true
    defer { isMemoryOperationInProgress = false }
    do {
      _ = try await api.createMemory(
        workspaceID: workspaceID,
        request: MemoryCreateRequest(
          content: content,
          kind: kind,
          tags: tags,
          pinned: pinned
        )
      )
      await refreshMemoryState(workspaceID: workspaceID)
      return true
    } catch {
      errorMessage = error.localizedDescription
      return false
    }
  }

  func updateMemory(_ memory: MemoryItem, request: MemoryUpdateRequest) async -> Bool {
    guard let api, let workspaceID = selectedWorkspaceID else { return false }
    isMemoryOperationInProgress = true
    defer { isMemoryOperationInProgress = false }
    do {
      _ = try await api.updateMemory(
        workspaceID: workspaceID,
        memoryID: memory.id,
        request: request
      )
      await refreshMemoryState(workspaceID: workspaceID)
      return true
    } catch {
      errorMessage = error.localizedDescription
      return false
    }
  }

  func archiveMemory(_ memory: MemoryItem) async {
    guard let api, let workspaceID = selectedWorkspaceID else { return }
    isMemoryOperationInProgress = true
    defer { isMemoryOperationInProgress = false }
    do {
      _ = try await api.archiveMemory(workspaceID: workspaceID, memoryID: memory.id)
      await refreshMemoryState(workspaceID: workspaceID)
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  func setAutomaticMemoryEnabled(_ enabled: Bool) async {
    guard let api, let workspaceID = selectedWorkspaceID else { return }
    do {
      memorySettings = try await api.updateMemorySettings(
        workspaceID: workspaceID,
        enabled: enabled
      )
      memoryHealth = try await api.getMemoryHealth(workspaceID: workspaceID)
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  func backfillMemories() async -> Int? {
    guard let api, let workspaceID = selectedWorkspaceID else { return nil }
    isMemoryOperationInProgress = true
    defer { isMemoryOperationInProgress = false }
    do {
      let result = try await api.backfillMemories(workspaceID: workspaceID)
      if !result.queuedJobs.isEmpty {
        memoryActivityMessage = "正在整理 \(result.queuedJobs.count) 个历史会话……"
        startMemoryMonitoring(
          workspaceID: workspaceID,
          jobIDs: result.queuedJobs.map(\.id)
        )
      } else {
        memoryActivityMessage = "没有需要整理的历史对话。"
      }
      return result.queuedJobs.count
    } catch {
      errorMessage = error.localizedDescription
      return nil
    }
  }

  func retrySelectedConversationMemory() async {
    guard
      let api,
      let workspaceID = selectedWorkspaceID,
      let conversationID = selectedConversationID
    else { return }
    do {
      let job = try await api.enqueueMemoryExtraction(
        workspaceID: workspaceID,
        conversationID: conversationID
      )
      memoryActivityMessage = "正在重新整理当前会话……"
      startMemoryMonitoring(workspaceID: workspaceID, jobIDs: [job.id])
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  func solve(
    problem: String,
    tags: [String],
    mathTarget: SolveMathTargetRequest?
  ) async -> Bool {
    guard let api, let workspaceID = selectedWorkspaceID else { return false }
    let trimmedProblem = problem.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmedProblem.isEmpty else {
      errorMessage = "请先输入数学问题。"
      return false
    }

    isSolving = true
    errorMessage = nil
    defer { isSolving = false }
    do {
      let attempt = try await api.solve(
        workspaceID: workspaceID,
        request: SolveRequest(
          problem: trimmedProblem,
          tags: tags,
          topK: 5,
          mathTarget: mathTarget,
          maxOutputTokens: AppSettings.maxOutputTokens
        )
      )
      async let loadedMethods = api.listMethods(workspaceID: workspaceID)
      async let loadedExamples = api.listExamples(workspaceID: workspaceID)
      let (methods, examples) = try await (loadedMethods, loadedExamples)
      if workspaceID == selectedWorkspaceID {
        attempts.append(attempt)
        self.methods = methods
        self.examples = examples
      }
      return true
    } catch {
      errorMessage = error.localizedDescription
      return false
    }
  }

  func draftMathTarget(problem: String) async -> MathTargetDraftResult? {
    guard let api, let workspaceID = selectedWorkspaceID else { return nil }
    let trimmedProblem = problem.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmedProblem.isEmpty else {
      errorMessage = "请先输入数学问题。"
      return nil
    }
    isDraftingTarget = true
    errorMessage = nil
    defer { isDraftingTarget = false }
    do {
      return try await api.draftMathTarget(
        workspaceID: workspaceID,
        problem: trimmedProblem
      )
    } catch {
      errorMessage = error.localizedDescription
      return nil
    }
  }

  func updateMethod(_ method: MethodCard, status: String) async {
    guard let api, let workspaceID = selectedWorkspaceID else { return }
    do {
      let updated = try await api.updateMethodStatus(
        workspaceID: workspaceID,
        methodID: method.id,
        status: status
      )
      if let index = methods.firstIndex(where: { $0.id == updated.id }) {
        methods[index] = updated
      }
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  func reviewExample(
    _ example: ProblemExample,
    decision: ExampleReviewDecision,
    reviewerNote: String
  ) async {
    guard let api, let workspaceID = selectedWorkspaceID else { return }
    reviewingExampleID = example.id
    errorMessage = nil
    defer {
      if reviewingExampleID == example.id {
        reviewingExampleID = nil
      }
    }
    do {
      _ = try await api.reviewExample(
        workspaceID: workspaceID,
        exampleID: example.id,
        decision: decision,
        expectedRevision: example.revision,
        reviewerNote: reviewerNote
      )
      async let loadedExamples = api.listExamples(workspaceID: workspaceID)
      async let loadedMethods = api.listMethods(workspaceID: workspaceID)
      let (examples, methods) = try await (loadedExamples, loadedMethods)
      guard workspaceID == selectedWorkspaceID else { return }
      self.examples = examples
      self.methods = methods
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  func updateExampleDraft(
    _ example: ProblemExample,
    request: ExampleDraftUpdateRequest
  ) async -> Bool {
    guard let api, let workspaceID = selectedWorkspaceID else { return false }
    editingExampleID = example.id
    errorMessage = nil
    defer {
      if editingExampleID == example.id {
        editingExampleID = nil
      }
    }
    do {
      _ = try await api.updateExampleDraft(
        workspaceID: workspaceID,
        exampleID: example.id,
        request: request
      )
      async let loadedExamples = api.listExamples(workspaceID: workspaceID)
      async let loadedMethods = api.listMethods(workspaceID: workspaceID)
      let (examples, methods) = try await (loadedExamples, loadedMethods)
      guard workspaceID == selectedWorkspaceID else { return false }
      self.examples = examples
      self.methods = methods
      return true
    } catch {
      errorMessage = error.localizedDescription
      return false
    }
  }

  func refreshSelectedWorkspace() async {
    guard let api, let workspaceID = selectedWorkspaceID else {
      conversations = []
      selectedConversationID = nil
      messages = []
      attempts = []
      examples = []
      methods = []
      memories = []
      memorySettings = nil
      memoryHealth = nil
      return
    }
    do {
      async let loadedConversations = api.listConversations(
        workspaceID: workspaceID,
        includeArchived: showsArchivedConversations
      )
      async let loadedAttempts = api.listAttempts(workspaceID: workspaceID)
      async let loadedExamples = api.listExamples(workspaceID: workspaceID)
      async let loadedMethods = api.listMethods(workspaceID: workspaceID)
      let (conversations, attempts, examples, methods) = try await (
        loadedConversations,
        loadedAttempts,
        loadedExamples,
        loadedMethods
      )
      guard workspaceID == selectedWorkspaceID else { return }
      self.conversations = conversations.sorted { $0.updatedAt > $1.updatedAt }
      if let selectedConversationID,
        !conversations.contains(where: { $0.id == selectedConversationID })
      {
        self.selectedConversationID = nil
      }
      if selectedConversationID == nil {
        selectedConversationID = self.conversations.first?.id
      }
      self.attempts = attempts.sorted { $0.createdAt < $1.createdAt }
      self.examples = examples
      self.methods = methods
      await refreshMemoryState(workspaceID: workspaceID)
      await refreshSelectedConversation()
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  func refreshSelectedConversation() async {
    guard
      let api,
      let workspaceID = selectedWorkspaceID,
      let conversationID = selectedConversationID
    else {
      messages = []
      return
    }
    do {
      let loaded = try await api.listConversationMessages(
        workspaceID: workspaceID,
        conversationID: conversationID
      )
      guard
        workspaceID == selectedWorkspaceID,
        conversationID == selectedConversationID
      else { return }
      messages = loaded.sorted { $0.ordinal < $1.ordinal }
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  func refreshMemoryState() async {
    guard let workspaceID = selectedWorkspaceID else { return }
    await refreshMemoryState(workspaceID: workspaceID)
  }

  private func refreshMemoryState(workspaceID: String) async {
    guard let api else { return }
    do {
      async let active = api.listMemories(workspaceID: workspaceID, status: .active)
      async let archived = api.listMemories(workspaceID: workspaceID, status: .archived)
      async let superseded = api.listMemories(workspaceID: workspaceID, status: .superseded)
      async let settings = api.getMemorySettings(workspaceID: workspaceID)
      async let health = api.getMemoryHealth(workspaceID: workspaceID)
      let loaded = try await (active, archived, superseded, settings, health)
      guard workspaceID == selectedWorkspaceID else { return }
      memories = (loaded.0 + loaded.1 + loaded.2).sorted { lhs, rhs in
        if lhs.pinned != rhs.pinned { return lhs.pinned && !rhs.pinned }
        return lhs.updatedAt > rhs.updatedAt
      }
      memorySettings = loaded.3
      memoryHealth = loaded.4
    } catch {
      guard workspaceID == selectedWorkspaceID else { return }
      errorMessage = "记忆状态刷新失败：\(error.localizedDescription)"
    }
  }

  private func startMemoryMonitoring(workspaceID: String, jobIDs: [String] = []) {
    memoryPollingTask?.cancel()
    memoryPollingTask = Task { [weak self] in
      guard let self, let api = self.api else { return }
      let trackedJobIDs = Array(Set(jobIDs))
      for _ in 0..<180 {
        if Task.isCancelled { return }
        do {
          try await Task<Never, Never>.sleep(for: .seconds(1))
          let health = try await api.getMemoryHealth(workspaceID: workspaceID)
          guard workspaceID == self.selectedWorkspaceID else { return }
          self.memoryHealth = health
          if health.queuedCount == 0 && health.runningCount == 0 {
            var trackedJobs: [MemoryExtractionJob] = []
            for jobID in trackedJobIDs {
              trackedJobs.append(
                try await api.getMemoryJob(workspaceID: workspaceID, jobID: jobID)
              )
            }
            await self.refreshMemoryState(workspaceID: workspaceID)
            let failed = health.failedCount > 0 || trackedJobs.contains { $0.status == "failed" }
            let extractedCount = trackedJobs.reduce(0) { $0 + $1.extractedCount }
            if failed {
              self.memoryActivityMessage = "记忆整理失败，可在记忆面板中重试。"
            } else if extractedCount > 0 {
              self.memoryActivityMessage = "已记住 \(extractedCount) 条长期信息。"
            } else {
              self.memoryActivityMessage = "整理完成，未发现新的长期信息。"
            }
            return
          }
        } catch is CancellationError {
          return
        } catch {
          guard workspaceID == self.selectedWorkspaceID else { return }
          self.memoryActivityMessage = "暂时无法读取记忆任务状态。"
          return
        }
      }
      if workspaceID == self.selectedWorkspaceID {
        self.memoryActivityMessage = "记忆仍在后台处理中。"
      }
    }
  }

  private var isFailed: Bool {
    if case .failed = backendPhase { return true }
    return false
  }
}
