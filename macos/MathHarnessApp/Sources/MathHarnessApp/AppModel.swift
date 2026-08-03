import Combine
import Foundation
import MathHarnessCore

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
  @Published private(set) var attempts: [SolutionAttempt] = []
  @Published private(set) var examples: [ProblemExample] = []
  @Published private(set) var methods: [MethodCard] = []
  @Published private(set) var memories: [MemoryItem] = []
  @Published private(set) var memorySettings: MemorySettings?
  @Published private(set) var memoryHealth: MemoryHealth?
  @Published private(set) var memoryActivityMessage: String?
  @Published private(set) var isMemoryOperationInProgress = false
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

    isSolving = true
    errorMessage = nil
    defer { isSolving = false }
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
      let result = try await api.sendConversationTurn(
        workspaceID: workspaceID,
        conversationID: conversationID,
        request: ConversationTurnRequest(
          message: trimmedMessage,
          tags: tags,
          topK: 5,
          mathTarget: mathTarget,
          maxOutputTokens: AppSettings.maxOutputTokens
        )
      )
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
      messages.sort { $0.ordinal < $1.ordinal }
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
      async let loadedConversations = api.listConversations(workspaceID: workspaceID)
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
