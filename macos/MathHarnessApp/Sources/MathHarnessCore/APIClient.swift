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
    let url = baseURL.appendingPathComponent(path)
    var request = URLRequest(url: url, timeoutInterval: timeout)
    request.httpMethod = method
    request.httpBody = body
    request.setValue("application/json", forHTTPHeaderField: "Accept")
    if body != nil {
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
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

    do {
      return try JSONDecoder().decode(Response.self, from: data)
    } catch {
      throw APIClientError.decoding(error.localizedDescription)
    }
  }
}
