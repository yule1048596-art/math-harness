import Combine
import Foundation
import MathHarnessCore

enum BackendPhase: Equatable {
  case idle
  case starting
  case running(version: String)
  case failed(message: String)
}

@MainActor
final class AppModel: ObservableObject {
  @Published private(set) var backendPhase: BackendPhase = .idle
  @Published private(set) var workspaces: [Workspace] = []
  @Published var selectedWorkspaceID: String?
  @Published private(set) var attempts: [SolutionAttempt] = []
  @Published private(set) var examples: [ProblemExample] = []
  @Published private(set) var methods: [MethodCard] = []
  @Published private(set) var isSolving = false
  @Published private(set) var isRefreshing = false
  @Published private(set) var reviewingExampleID: String?
  @Published var errorMessage: String?

  private let backend = BackendProcessController()
  private var api: APIClient?

  var selectedWorkspace: Workspace? {
    workspaces.first { $0.id == selectedWorkspaceID }
  }

  var pendingExamples: [ProblemExample] {
    examples
      .filter { $0.status == "pending_review" }
      .sorted { $0.createdAt > $1.createdAt }
  }

  func isAttemptCaptured(_ attemptID: String) -> Bool {
    examples.contains { $0.sourceAttemptID == attemptID }
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
    backend.stop()
  }

  func restartBackend() async {
    backend.stop()
    api = nil
    backendPhase = .idle
    attempts = []
    examples = []
    methods = []
    await start()
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

  func refreshSelectedWorkspace() async {
    guard let api, let workspaceID = selectedWorkspaceID else {
      attempts = []
      examples = []
      methods = []
      return
    }
    do {
      async let loadedAttempts = api.listAttempts(workspaceID: workspaceID)
      async let loadedExamples = api.listExamples(workspaceID: workspaceID)
      async let loadedMethods = api.listMethods(workspaceID: workspaceID)
      let (attempts, examples, methods) = try await (
        loadedAttempts,
        loadedExamples,
        loadedMethods
      )
      guard workspaceID == selectedWorkspaceID else { return }
      self.attempts = attempts.sorted { $0.createdAt < $1.createdAt }
      self.examples = examples
      self.methods = methods
    } catch {
      errorMessage = error.localizedDescription
    }
  }

  private var isFailed: Bool {
    if case .failed = backendPhase { return true }
    return false
  }
}
