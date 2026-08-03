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

public struct ConversationCreateRequest: Codable, Equatable, Sendable {
  public let title: String

  public init(title: String = "") {
    self.title = title
  }
}

public struct Conversation: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let title: String
  public let summary: String
  public let summaryThroughOrdinal: Int
  public let messageCount: Int
  public let createdAt: String
  public let updatedAt: String

  enum CodingKeys: String, CodingKey {
    case id, title, summary
    case workspaceID = "workspace_id"
    case summaryThroughOrdinal = "summary_through_ordinal"
    case messageCount = "message_count"
    case createdAt = "created_at"
    case updatedAt = "updated_at"
  }
}

public struct ConversationMessage: Codable, Identifiable, Equatable, Sendable {
  public let id: String
  public let workspaceID: String
  public let conversationID: String
  public let turnID: String
  public let ordinal: Int
  public let role: String
  public let kind: String
  public let content: String
  public let provider: String?
  public let model: String?
  public let attemptID: String?
  public let knowledgeDraftID: String?
  public let verificationStatus: String?
  public let methodKeys: [String]
  public let createdAt: String

  enum CodingKeys: String, CodingKey {
    case id, ordinal, role, kind, content, provider, model
    case workspaceID = "workspace_id"
    case conversationID = "conversation_id"
    case turnID = "turn_id"
    case attemptID = "attempt_id"
    case knowledgeDraftID = "knowledge_draft_id"
    case verificationStatus = "verification_status"
    case methodKeys = "method_keys"
    case createdAt = "created_at"
  }
}

public struct ConversationTurnRequest: Codable, Equatable, Sendable {
  public let message: String
  public let turnID: String
  public let tags: [String]
  public let topK: Int
  public let mathTarget: SolveMathTargetRequest?
  public let maxOutputTokens: Int

  enum CodingKeys: String, CodingKey {
    case message, tags
    case turnID = "turn_id"
    case topK = "top_k"
    case mathTarget = "math_target"
    case maxOutputTokens = "max_output_tokens"
  }

  public init(
    message: String,
    turnID: String = UUID().uuidString,
    tags: [String] = [],
    topK: Int = 5,
    mathTarget: SolveMathTargetRequest? = nil,
    maxOutputTokens: Int = 3_000
  ) {
    self.message = message
    self.turnID = turnID
    self.tags = tags
    self.topK = topK
    self.mathTarget = mathTarget
    self.maxOutputTokens = maxOutputTokens
  }
}

public enum ImportReviewPolicy: String, Codable, CaseIterable, Identifiable, Sendable {
  case pending
  case preserve

  public var id: String { rawValue }
}

public enum ImportExtractorPolicy: String, Codable, CaseIterable, Identifiable, Sendable {
  case rules
  case configured

  public var id: String { rawValue }
}

public struct BulkExampleImportRequest: Codable, Equatable, Sendable {
  public let content: String
  public let fileFormat: String
  public let reviewPolicy: ImportReviewPolicy
  public let extractorPolicy: ImportExtractorPolicy
  public let commit: Bool
  public let sourceName: String

  enum CodingKeys: String, CodingKey {
    case content, commit
    case fileFormat = "file_format"
    case reviewPolicy = "review_policy"
    case extractorPolicy = "extractor_policy"
    case sourceName = "source_name"
  }

  public init(
    content: String,
    fileFormat: String = "auto",
    reviewPolicy: ImportReviewPolicy = .pending,
    extractorPolicy: ImportExtractorPolicy = .rules,
    commit: Bool = false,
    sourceName: String
  ) {
    self.content = content
    self.fileFormat = fileFormat
    self.reviewPolicy = reviewPolicy
    self.extractorPolicy = extractorPolicy
    self.commit = commit
    self.sourceName = sourceName
  }
}

public struct BulkImportItemResult: Codable, Equatable, Identifiable, Sendable {
  public var id: Int { index }

  public let index: Int
  public let status: String
  public let problemPreview: String
  public let fingerprint: String?
  public let verification: VerificationReport?
  public let methodKeys: [String]
  public let exampleID: String?
  public let errors: [String]

  enum CodingKeys: String, CodingKey {
    case index, status, fingerprint, verification, errors
    case problemPreview = "problem_preview"
    case methodKeys = "method_keys"
    case exampleID = "example_id"
  }
}

public struct BulkExampleImportResult: Codable, Equatable, Sendable {
  public let sourceName: String
  public let detectedFormat: String
  public let commitRequested: Bool
  public let committed: Bool
  public let canCommit: Bool
  public let totalCount: Int
  public let readyCount: Int
  public let duplicateCount: Int
  public let invalidCount: Int
  public let importedCount: Int
  public let items: [BulkImportItemResult]

  enum CodingKeys: String, CodingKey {
    case items, committed
    case sourceName = "source_name"
    case detectedFormat = "detected_format"
    case commitRequested = "commit_requested"
    case canCommit = "can_commit"
    case totalCount = "total_count"
    case readyCount = "ready_count"
    case duplicateCount = "duplicate_count"
    case invalidCount = "invalid_count"
    case importedCount = "imported_count"
  }
}

public struct WorkspaceRestoreResult: Codable, Equatable, Sendable {
  public let workspace: Workspace
  public let sourceWorkspaceID: String
  public let sourceAppVersion: String
  public let archiveFormatVersion: Int
  public let restoredRecordCounts: [String: Int]

  enum CodingKeys: String, CodingKey {
    case workspace
    case sourceWorkspaceID = "source_workspace_id"
    case sourceAppVersion = "source_app_version"
    case archiveFormatVersion = "archive_format_version"
    case restoredRecordCounts = "restored_record_counts"
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
  public let assumptions: [String: [String]]
  public let point: String
  public let direction: String
  public let mode: VerificationMode
  public let remainderPower: Int?

  enum CodingKeys: String, CodingKey {
    case expression, variable, parameters, assumptions, point, direction, mode
    case remainderPower = "remainder_power"
  }

  public init(
    expression: String,
    variable: String = "x",
    parameters: [String] = [],
    assumptions: [String: [String]] = [:],
    point: String = "oo",
    direction: String = "two_sided",
    mode: VerificationMode = .asymptoticExpansion,
    remainderPower: Int? = nil
  ) {
    self.expression = expression
    self.variable = variable
    self.parameters = parameters
    self.assumptions = assumptions
    self.point = point
    self.direction = direction
    self.mode = mode
    self.remainderPower = remainderPower
  }
}

public struct MathPayloadRequest: Codable, Equatable, Sendable {
  public let expression: String
  public let expected: String
  public let variable: String
  public let parameters: [String]
  public let assumptions: [String: [String]]
  public let point: String
  public let direction: String
  public let mode: VerificationMode
  public let remainderPower: Int?

  enum CodingKeys: String, CodingKey {
    case expression, expected, variable, parameters, assumptions, point, direction, mode
    case remainderPower = "remainder_power"
  }

  public init(
    expression: String,
    expected: String,
    variable: String = "x",
    parameters: [String] = [],
    assumptions: [String: [String]] = [:],
    point: String = "oo",
    direction: String = "two_sided",
    mode: VerificationMode = .asymptoticExpansion,
    remainderPower: Int? = nil
  ) {
    self.expression = expression
    self.expected = expected
    self.variable = variable
    self.parameters = parameters
    self.assumptions = assumptions
    self.point = point
    self.direction = direction
    self.mode = mode
    self.remainderPower = remainderPower
  }
}

public struct MathTargetDraftRequest: Codable, Equatable, Sendable {
  public let problem: String

  public init(problem: String) {
    self.problem = problem
  }
}

public struct MathTargetDraftResult: Codable, Equatable, Sendable {
  public let target: SolveMathTargetRequest?
  public let status: String
  public let provider: String
  public let model: String?
  public let confidence: Double
  public let summary: String
  public let warnings: [String]
  public let fallbackUsed: Bool
  public let error: String?
  public let requiresConfirmation: Bool

  enum CodingKeys: String, CodingKey {
    case target, status, provider, model, confidence, summary, warnings, error
    case fallbackUsed = "fallback_used"
    case requiresConfirmation = "requires_confirmation"
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
  public let mathPayload: MathPayloadRequest?
  public let verification: VerificationReport
  public let extraction: MethodExtractionSummary?
  public let methodDrafts: [MethodDraftPreview]
  public let status: String
  public let origin: String
  public let sourceAttemptID: String?
  public let reviewedAt: String?
  public let reviewerNote: String
  public let revision: Int
  public let createdAt: String
  public let updatedAt: String

  enum CodingKeys: String, CodingKey {
    case id, problem, solution, tags, reviewed, verification, extraction, status, origin
    case revision
    case workspaceID = "workspace_id"
    case methodHint = "method_hint"
    case problemKind = "problem_kind"
    case mathPayload = "math_payload"
    case methodDrafts = "method_drafts"
    case sourceAttemptID = "source_attempt_id"
    case reviewedAt = "reviewed_at"
    case reviewerNote = "reviewer_note"
    case createdAt = "created_at"
    case updatedAt = "updated_at"
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

public struct ConversationTurnResult: Codable, Equatable, Sendable {
  public let conversation: Conversation
  public let userMessage: ConversationMessage
  public let assistantMessage: ConversationMessage
  public let attempt: SolutionAttempt?
  public let knowledgeDraft: ProblemExample?
  public let summaryUpdated: Bool

  enum CodingKeys: String, CodingKey {
    case conversation, attempt
    case userMessage = "user_message"
    case assistantMessage = "assistant_message"
    case knowledgeDraft = "knowledge_draft"
    case summaryUpdated = "summary_updated"
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
  public let expectedRevision: Int
  public let reviewerNote: String

  enum CodingKeys: String, CodingKey {
    case decision
    case expectedRevision = "expected_revision"
    case reviewerNote = "reviewer_note"
  }

  public init(
    decision: ExampleReviewDecision,
    expectedRevision: Int,
    reviewerNote: String = ""
  ) {
    self.decision = decision
    self.expectedRevision = expectedRevision
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

public struct ExampleDraftUpdateRequest: Codable, Equatable, Sendable {
  public let expectedRevision: Int
  public let problem: String
  public let solution: String
  public let tags: [String]
  public let methodHint: String?
  public let mathPayload: MathPayloadRequest?

  enum CodingKeys: String, CodingKey {
    case problem, solution, tags
    case expectedRevision = "expected_revision"
    case methodHint = "method_hint"
    case mathPayload = "math_payload"
  }

  public init(
    expectedRevision: Int,
    problem: String,
    solution: String,
    tags: [String],
    methodHint: String?,
    mathPayload: MathPayloadRequest?
  ) {
    self.expectedRevision = expectedRevision
    self.problem = problem
    self.solution = solution
    self.tags = tags
    self.methodHint = methodHint
    self.mathPayload = mathPayload
  }
}

public struct ExampleDraftUpdateResult: Codable, Equatable, Sendable {
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
