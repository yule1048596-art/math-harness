import Foundation

public enum APIClientError: Error, LocalizedError, Equatable, Sendable {
  case invalidResponse
  case http(status: Int, detail: String)
  case transport(String)
  case decoding(String)

  public var errorDescription: String? {
    switch self {
    case .invalidResponse:
      "本地服务返回了无法识别的响应。"
    case .http(let status, let detail):
      "本地服务错误（\(status)）：\(detail)"
    case .transport(let message):
      "无法连接本地数学服务：\(message)"
    case .decoding(let message):
      "无法读取本地服务结果：\(message)"
    }
  }
}

public actor APIClient {
  public static let workspaceArchiveMediaType =
    "application/vnd.math-harness.workspace+zip"

  private let baseURL: URL
  private let bearerToken: String?
  private let session: URLSession
  private let timeout: TimeInterval

  public init(
    baseURL: URL,
    bearerToken: String? = nil,
    session: URLSession = .shared,
    timeout: TimeInterval = 240
  ) {
    self.baseURL = baseURL
    self.bearerToken = bearerToken
    self.session = session
    self.timeout = timeout
  }

  public func health() async throws -> HealthResponse {
    try await send(path: "health", method: "GET")
  }

  public func listWorkspaces() async throws -> [Workspace] {
    try await send(path: "workspaces", method: "GET")
  }

  public func createWorkspace(_ request: WorkspaceCreateRequest) async throws -> Workspace {
    try await send(path: "workspaces", method: "POST", body: request)
  }

  public func createConversation(
    workspaceID: String,
    request: ConversationCreateRequest = ConversationCreateRequest()
  ) async throws -> Conversation {
    try await send(
      path: "workspaces/\(workspaceID)/conversations",
      method: "POST",
      body: request
    )
  }

  public func listConversations(workspaceID: String) async throws -> [Conversation] {
    try await send(
      path: "workspaces/\(workspaceID)/conversations",
      method: "GET"
    )
  }

  public func listConversationMessages(
    workspaceID: String,
    conversationID: String
  ) async throws -> [ConversationMessage] {
    try await send(
      path: "workspaces/\(workspaceID)/conversations/\(conversationID)/messages",
      method: "GET"
    )
  }

  public func sendConversationTurn(
    workspaceID: String,
    conversationID: String,
    request: ConversationTurnRequest
  ) async throws -> ConversationTurnResult {
    try await send(
      path: "workspaces/\(workspaceID)/conversations/\(conversationID)/turns",
      method: "POST",
      body: request
    )
  }

  public func listMemories(
    workspaceID: String,
    query: String? = nil,
    kind: MemoryKind? = nil,
    status: MemoryStatus = .active
  ) async throws -> [MemoryItem] {
    var components = URLComponents()
    components.queryItems = [
      query.map { URLQueryItem(name: "q", value: $0) },
      kind.map { URLQueryItem(name: "kind", value: $0.rawValue) },
      URLQueryItem(name: "status", value: status.rawValue),
      URLQueryItem(name: "limit", value: "500"),
    ].compactMap { $0 }
    let queryString = components.percentEncodedQuery.map { "?\($0)" } ?? ""
    return try await send(
      path: "workspaces/\(workspaceID)/memories\(queryString)",
      method: "GET"
    )
  }

  public func createMemory(
    workspaceID: String,
    request: MemoryCreateRequest
  ) async throws -> MemoryItem {
    try await send(
      path: "workspaces/\(workspaceID)/memories",
      method: "POST",
      body: request
    )
  }

  public func updateMemory(
    workspaceID: String,
    memoryID: String,
    request: MemoryUpdateRequest
  ) async throws -> MemoryItem {
    try await send(
      path: "workspaces/\(workspaceID)/memories/\(memoryID)",
      method: "PATCH",
      body: request
    )
  }

  public func archiveMemory(
    workspaceID: String,
    memoryID: String
  ) async throws -> MemoryItem {
    try await send(
      path: "workspaces/\(workspaceID)/memories/\(memoryID)",
      method: "DELETE"
    )
  }

  public func getMemorySettings(workspaceID: String) async throws -> MemorySettings {
    try await send(
      path: "workspaces/\(workspaceID)/memory-settings",
      method: "GET"
    )
  }

  public func updateMemorySettings(
    workspaceID: String,
    enabled: Bool
  ) async throws -> MemorySettings {
    try await send(
      path: "workspaces/\(workspaceID)/memory-settings",
      method: "PUT",
      body: MemorySettingsUpdateRequest(automaticExtractionEnabled: enabled)
    )
  }

  public func getMemoryHealth(workspaceID: String) async throws -> MemoryHealth {
    try await send(
      path: "workspaces/\(workspaceID)/memory-health",
      method: "GET"
    )
  }

  public func getMemoryJob(
    workspaceID: String,
    jobID: String
  ) async throws -> MemoryExtractionJob {
    try await send(
      path: "workspaces/\(workspaceID)/memory-jobs/\(jobID)",
      method: "GET"
    )
  }

  public func enqueueMemoryExtraction(
    workspaceID: String,
    conversationID: String
  ) async throws -> MemoryExtractionJob {
    try await send(
      path: "workspaces/\(workspaceID)/conversations/\(conversationID)/memory-extractions",
      method: "POST"
    )
  }

  public func backfillMemories(workspaceID: String) async throws -> MemoryBackfillResult {
    try await send(
      path: "workspaces/\(workspaceID)/memory-backfills",
      method: "POST"
    )
  }

  public func bulkImportExamples(
    workspaceID: String,
    request: BulkExampleImportRequest
  ) async throws -> BulkExampleImportResult {
    try await send(
      path: "workspaces/\(workspaceID)/example-imports",
      method: "POST",
      body: request
    )
  }

  public func exportWorkspaceBackup(workspaceID: String) async throws -> Data {
    try await sendRaw(
      path: "workspaces/\(workspaceID)/backup",
      method: "GET",
      body: nil,
      accept: Self.workspaceArchiveMediaType,
      contentType: nil
    )
  }

  public func restoreWorkspaceBackup(_ archive: Data) async throws -> WorkspaceRestoreResult {
    let data = try await sendRaw(
      path: "workspace-restores",
      method: "POST",
      body: archive,
      accept: "application/json",
      contentType: Self.workspaceArchiveMediaType
    )
    do {
      return try JSONDecoder().decode(WorkspaceRestoreResult.self, from: data)
    } catch {
      throw APIClientError.decoding(error.localizedDescription)
    }
  }

  public func listAttempts(workspaceID: String) async throws -> [SolutionAttempt] {
    try await send(
      path: "workspaces/\(workspaceID)/attempts",
      method: "GET"
    )
  }

  public func listExamples(workspaceID: String) async throws -> [ProblemExample] {
    try await send(
      path: "workspaces/\(workspaceID)/examples",
      method: "GET"
    )
  }

  public func draftMathTarget(
    workspaceID: String,
    problem: String
  ) async throws -> MathTargetDraftResult {
    try await send(
      path: "workspaces/\(workspaceID)/math-target-drafts",
      method: "POST",
      body: MathTargetDraftRequest(problem: problem)
    )
  }

  public func updateExampleDraft(
    workspaceID: String,
    exampleID: String,
    request: ExampleDraftUpdateRequest
  ) async throws -> ExampleDraftUpdateResult {
    try await send(
      path: "workspaces/\(workspaceID)/examples/\(exampleID)",
      method: "PATCH",
      body: request
    )
  }

  public func reviewExample(
    workspaceID: String,
    exampleID: String,
    decision: ExampleReviewDecision,
    expectedRevision: Int,
    reviewerNote: String = ""
  ) async throws -> ExampleReviewResult {
    try await send(
      path: "workspaces/\(workspaceID)/examples/\(exampleID)/review",
      method: "POST",
      body: ExampleReviewRequest(
        decision: decision,
        expectedRevision: expectedRevision,
        reviewerNote: reviewerNote
      )
    )
  }

  public func solve(workspaceID: String, request: SolveRequest) async throws -> SolutionAttempt {
    try await send(
      path: "workspaces/\(workspaceID)/solve",
      method: "POST",
      body: request
    )
  }

  public func listMethods(workspaceID: String) async throws -> [MethodCard] {
    try await send(
      path: "workspaces/\(workspaceID)/methods",
      method: "GET"
    )
  }

  public func updateMethodStatus(
    workspaceID: String,
    methodID: String,
    status: String
  ) async throws -> MethodCard {
    try await send(
      path: "workspaces/\(workspaceID)/methods/\(methodID)",
      method: "PATCH",
      body: MethodStatusRequest(status: status)
    )
  }

  private func send<Response: Decodable & Sendable>(
    path: String,
    method: String
  ) async throws -> Response {
    try await sendData(path: path, method: method, body: nil)
  }

  private func send<Response: Decodable & Sendable, Body: Encodable & Sendable>(
    path: String,
    method: String,
    body: Body
  ) async throws -> Response {
    let encoder = JSONEncoder()
    do {
      return try await sendData(
        path: path,
        method: method,
        body: encoder.encode(body)
      )
    } catch let error as APIClientError {
      throw error
    } catch {
      throw APIClientError.transport(error.localizedDescription)
    }
  }

  private func sendData<Response: Decodable & Sendable>(
    path: String,
    method: String,
    body: Data?
  ) async throws -> Response {
    let data = try await sendRaw(
      path: path,
      method: method,
      body: body,
      accept: "application/json",
      contentType: body == nil ? nil : "application/json"
    )

    do {
      return try JSONDecoder().decode(Response.self, from: data)
    } catch {
      throw APIClientError.decoding(error.localizedDescription)
    }
  }

  private func sendRaw(
    path: String,
    method: String,
    body: Data?,
    accept: String,
    contentType: String?
  ) async throws -> Data {
    let url = requestURL(for: path)
    var request = URLRequest(url: url, timeoutInterval: timeout)
    request.httpMethod = method
    request.httpBody = body
    request.setValue(accept, forHTTPHeaderField: "Accept")
    if let contentType {
      request.setValue(contentType, forHTTPHeaderField: "Content-Type")
    }
    if let bearerToken {
      request.setValue("Bearer \(bearerToken)", forHTTPHeaderField: "Authorization")
    }

    let data: Data
    let response: URLResponse
    do {
      (data, response) = try await session.data(for: request)
    } catch {
      throw APIClientError.transport(error.localizedDescription)
    }

    guard let httpResponse = response as? HTTPURLResponse else {
      throw APIClientError.invalidResponse
    }
    guard (200...299).contains(httpResponse.statusCode) else {
      let envelope = try? JSONDecoder().decode(APIErrorEnvelope.self, from: data)
      let detail = envelope?.detail ?? String(data: data, encoding: .utf8) ?? "未知错误"
      throw APIClientError.http(status: httpResponse.statusCode, detail: detail)
    }

    return data
  }

  private func requestURL(for path: String) -> URL {
    let parts = path.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
    let url = baseURL.appendingPathComponent(String(parts[0]))
    guard parts.count == 2 else { return url }
    var components = URLComponents(url: url, resolvingAgainstBaseURL: false)
    components?.percentEncodedQuery = String(parts[1])
    return components?.url ?? url
  }
}
