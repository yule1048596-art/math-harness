import Foundation

public struct HealthResponse: Codable, Equatable, Sendable {
  public let status: String
  public let version: String

  public init(status: String, version: String) {
    self.status = status
    self.version = version
  }
}

public struct Workspace: Codable, Identifiable, Hashable, Sendable {
  public let id: String
  public let name: String
  public let description: String
  public let createdAt: String

  enum CodingKeys: String, CodingKey {
    case id, name, description
    case createdAt = "created_at"
  }

  public init(id: String, name: String, description: String, createdAt: String) {
    self.id = id
    self.name = name
    self.description = description
    self.createdAt = createdAt
  }
}

public struct WorkspaceCreateRequest: Codable, Equatable, Sendable {
  public let name: String
  public let description: String

  public init(name: String, description: String) {
    self.name = name
    self.description = description
  }
}

public enum VerificationMode: String, Codable, CaseIterable, Identifiable, Sendable {
  case asymptoticExpansion = "asymptotic_expansion"
  case asymptoticEquivalence = "asymptotic_equivalence"
  case exactEquivalence = "exact_equivalence"
  case limit

  public var id: String { rawValue }

  public var displayName: String {
    switch self {
    case .asymptoticExpansion: "渐进展开"
    case .asymptoticEquivalence: "渐进等价"
    case .exactEquivalence: "精确等价"
    case .limit: "极限"
    }
  }
}

public struct SolveMathTargetRequest: Codable, Equatable, Sendable {
  public let expression: String
  public let variable: String
  public let parameters: [String]
  public let point: String
  public let direction: String
  public let mode: VerificationMode
  public let remainderPower: Int?

  enum CodingKeys: String, CodingKey {
    case expression, variable, parameters, point, direction, mode
    case remainderPower = "remainder_power"
  }

  public init(
    expression: String,
    variable: String = "x",
    parameters: [String] = [],
    point: String = "oo",
    direction: String = "two_sided",
    mode: VerificationMode = .asymptoticExpansion,
    remainderPower: Int? = nil
  ) {
    self.expression = expression
    self.variable = variable
    self.parameters = parameters
    self.point = point
    self.direction = direction
    self.mode = mode
    self.remainderPower = remainderPower
  }
}

public struct SolveRequest: Codable, Equatable, Sendable {
  public let problem: String
  public let tags: [String]
  public let topK: Int
  public let mathTarget: SolveMathTargetRequest?
  public let maxOutputTokens: Int

  enum CodingKeys: String, CodingKey {
    case problem, tags
    case topK = "top_k"
    case mathTarget = "math_target"
    case maxOutputTokens = "max_output_tokens"
  }

  public init(
    problem: String,
    tags: [String] = [],
    topK: Int = 5,
    mathTarget: SolveMathTargetRequest? = nil,
    maxOutputTokens: Int = 3_000
  ) {
    self.problem = problem
    self.tags = tags
    self.topK = topK
    self.mathTarget = mathTarget
    self.maxOutputTokens = maxOutputTokens
  }
}

public struct CandidateStep: Codable, Equatable, Sendable {
  public let explanation: String
  public let expression: String?
}

public struct CandidateSolution: Codable, Equatable, Sendable {
  public let answerKind: String
  public let answerText: String
  public let answerExpression: String?
  public let steps: [CandidateStep]
  public let usedMethodKeys: [String]
  public let assumptions: [String]
  public let confidence: Double

  enum CodingKeys: String, CodingKey {
    case steps, assumptions, confidence
    case answerKind = "answer_kind"
    case answerText = "answer_text"
    case answerExpression = "answer_expression"
    case usedMethodKeys = "used_method_keys"
  }
}

public struct VerificationReport: Codable, Equatable, Sendable {
  public let status: String
  public let summary: String
  public let checks: [String]
  public let computed: [String: String]
  public let error: String?
}

public struct MethodExtractionSummary: Codable, Equatable, Sendable {
  public let provider: String
  public let model: String?
  public let status: String
  public let extractedMethodKeys: [String]

  enum CodingKeys: String, CodingKey {
    case provider, model, status
    case extractedMethodKeys = "extracted_method_keys"
  }
}

public struct MethodDraftPreview: Codable, Equatable, Sendable {
  public let key: String
  public let name: String
  public let goal: String
  public let applicableWhen: [String]
  public let procedure: [String]
  public let failureModes: [String]
  public let tags: [String]

  enum CodingKeys: String, CodingKey {
    case key, name, goal, procedure, tags
    case applicableWhen = "applicable_when"
    case failureModes = "failure_modes"
  }
}

public struct ProblemExample: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let problem: String
  public let solution: String
  public let tags: [String]
  public let methodHint: String?
  public let reviewed: Bool
  public let problemKind: String
  public let verification: VerificationReport
  public let extraction: MethodExtractionSummary?
  public let methodDrafts: [MethodDraftPreview]
  public let status: String
  public let origin: String
  public let sourceAttemptID: String?
  public let reviewedAt: String?
  public let reviewerNote: String
  public let createdAt: String

  enum CodingKeys: String, CodingKey {
    case id, problem, solution, tags, reviewed, verification, extraction, status, origin
    case workspaceID = "workspace_id"
    case methodHint = "method_hint"
    case problemKind = "problem_kind"
    case methodDrafts = "method_drafts"
    case sourceAttemptID = "source_attempt_id"
    case reviewedAt = "reviewed_at"
    case reviewerNote = "reviewer_note"
    case createdAt = "created_at"
  }
}

public struct MethodCard: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let key: String
  public let name: String
  public let goal: String
  public let applicableWhen: [String]
  public let procedure: [String]
  public let failureModes: [String]
  public let tags: [String]
  public let status: String
  public let version: Int
  public let successCount: Int
  public let failureCount: Int

  enum CodingKeys: String, CodingKey {
    case id, key, name, goal, procedure, tags, status, version
    case workspaceID = "workspace_id"
    case applicableWhen = "applicable_when"
    case failureModes = "failure_modes"
    case successCount = "success_count"
    case failureCount = "failure_count"
  }
}

public struct MethodMatch: Codable, Equatable, Sendable {
  public let method: MethodCard
  public let score: Double
  public let reasons: [String]
}

public struct SolutionGenerationTrace: Codable, Equatable, Sendable {
  public let provider: String
  public let model: String?
  public let fallbackUsed: Bool
  public let verificationFallbackUsed: Bool
  public let correctionAttempted: Bool
  public let correctionSucceeded: Bool
  public let durationMS: Int
  public let error: String?

  enum CodingKeys: String, CodingKey {
    case provider, model, error
    case fallbackUsed = "fallback_used"
    case verificationFallbackUsed = "verification_fallback_used"
    case correctionAttempted = "correction_attempted"
    case correctionSucceeded = "correction_succeeded"
    case durationMS = "duration_ms"
  }
}

public struct SolutionAttempt: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let problem: String
  public let tags: [String]
  public let problemKind: String
  public let recommendedMethods: [MethodMatch]
  public let candidate: CandidateSolution?
  public let generation: SolutionGenerationTrace
  public let verification: VerificationReport
  public let status: String
  public let feedbackMethodKeys: [String]
  public let correctionOf: String?
  public let createdAt: String

  enum CodingKeys: String, CodingKey {
    case id, problem, tags, candidate, generation, verification, status
    case workspaceID = "workspace_id"
    case problemKind = "problem_kind"
    case recommendedMethods = "recommended_methods"
    case feedbackMethodKeys = "feedback_method_keys"
    case correctionOf = "correction_of"
    case createdAt = "created_at"
  }
}

public struct MethodStatusRequest: Codable, Equatable, Sendable {
  public let status: String

  public init(status: String) {
    self.status = status
  }
}

public enum ExampleReviewDecision: String, Codable, Sendable {
  case approve
  case reject
}

public struct ExampleReviewRequest: Codable, Equatable, Sendable {
  public let decision: ExampleReviewDecision
  public let reviewerNote: String

  enum CodingKeys: String, CodingKey {
    case decision
    case reviewerNote = "reviewer_note"
  }

  public init(decision: ExampleReviewDecision, reviewerNote: String = "") {
    self.decision = decision
    self.reviewerNote = reviewerNote
  }
}

public struct ExampleReviewResult: Codable, Equatable, Sendable {
  public let example: ProblemExample
  public let learnedMethods: [MethodCard]

  enum CodingKeys: String, CodingKey {
    case example
    case learnedMethods = "learned_methods"
  }
}

public struct BackendReady: Codable, Equatable, Sendable {
  public let baseURL: String
  public let pid: Int
  public let port: Int
  public let version: String

  enum CodingKeys: String, CodingKey {
    case pid, port, version
    case baseURL = "base_url"
  }
}

struct APIErrorEnvelope: Codable, Sendable {
  let detail: String
}
