import Foundation

/// 一个 OpenAI 兼容服务端点。**不含密钥**——密钥只存 Keychain，配置本身可以安全落
/// UserDefaults 并作为 JSON 传给后端。
///
/// JSON 键名必须与 Python 侧 `provider_config.ProviderProfile` 逐字一致，两边各有
/// 断言钉住。
public struct ProviderProfile: Codable, Identifiable, Hashable, Sendable {
  public var id: String
  public var name: String
  public var baseURL: String
  public var defaultModel: String
  /// MiMo 只支持 JSON Object 模式，OpenAI 支持严格 Schema。选错会让结构化输出
  /// 在运行时才失败，所以这是必须暴露给用户的配置，不是内部细节。
  public var structuredOutputMode: StructuredOutputMode
  public var timeoutSeconds: Double
  public var maxOutputTokens: Int

  enum CodingKeys: String, CodingKey {
    case id
    case name
    case baseURL = "base_url"
    case defaultModel = "default_model"
    case structuredOutputMode = "structured_output_mode"
    case timeoutSeconds = "timeout_seconds"
    case maxOutputTokens = "max_output_tokens"
  }

  public init(
    id: String = UUID().uuidString,
    name: String,
    baseURL: String,
    defaultModel: String,
    structuredOutputMode: StructuredOutputMode = .jsonSchema,
    timeoutSeconds: Double = 60,
    maxOutputTokens: Int = 3_000
  ) {
    self.id = id
    self.name = name
    self.baseURL = baseURL
    self.defaultModel = defaultModel
    self.structuredOutputMode = structuredOutputMode
    self.timeoutSeconds = timeoutSeconds
    self.maxOutputTokens = maxOutputTokens
  }
}

public enum StructuredOutputMode: String, Codable, CaseIterable, Sendable {
  case jsonSchema = "json_schema"
  case jsonObject = "json_object"

  public var displayName: String {
    switch self {
    case .jsonSchema: "严格 Schema（推荐）"
    case .jsonObject: "JSON Object（兼容模式）"
    }
  }

  public var explanation: String {
    switch self {
    case .jsonSchema:
      "服务端保证输出符合结构定义。OpenAI 及多数新版兼容服务支持。"
    case .jsonObject:
      "只保证输出是 JSON，不保证符合业务结构；本地校验失败时会重试一次。"
    }
  }
}

/// 常见厂商预设。绝大多数厂商都提供 OpenAI 兼容端点，所以一套通用配置即可覆盖；
/// 预设只是把 Base URL 和默认模型填好，省去查文档。
public enum ProviderPreset: String, CaseIterable, Identifiable, Sendable {
  case mimo
  case openai
  case deepseek
  case moonshot
  case zhipu
  case qwen
  case openrouter
  case siliconflow
  case ollama
  case custom

  public var id: String { rawValue }

  public var displayName: String {
    switch self {
    case .mimo: "小米 MiMo"
    case .openai: "OpenAI"
    case .deepseek: "DeepSeek"
    case .moonshot: "Kimi（Moonshot）"
    case .zhipu: "智谱 GLM"
    case .qwen: "通义千问"
    case .openrouter: "OpenRouter"
    case .siliconflow: "SiliconFlow"
    case .ollama: "Ollama（本地）"
    case .custom: "自定义"
    }
  }

  public var baseURL: String {
    switch self {
    case .mimo: "https://api.xiaomimimo.com/v1"
    case .openai: "https://api.openai.com/v1"
    case .deepseek: "https://api.deepseek.com/v1"
    case .moonshot: "https://api.moonshot.cn/v1"
    case .zhipu: "https://open.bigmodel.cn/api/paas/v4"
    case .qwen: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    case .openrouter: "https://openrouter.ai/api/v1"
    case .siliconflow: "https://api.siliconflow.cn/v1"
    case .ollama: "http://127.0.0.1:11434/v1"
    case .custom: ""
    }
  }

  public var defaultModel: String {
    switch self {
    case .mimo: "mimo-v2.5-pro"
    case .openai: "gpt-5.6-sol"
    case .deepseek: "deepseek-chat"
    case .moonshot: "kimi-k2-turbo-preview"
    case .zhipu: "glm-4.6"
    case .qwen: "qwen-max"
    case .openrouter: "openai/gpt-5.6-sol"
    case .siliconflow: "deepseek-ai/DeepSeek-V3"
    case .ollama: "qwen3"
    case .custom: ""
    }
  }

  /// MiMo 的 Responses API 只保证 JSON Object；自建 Ollama 同样不保证严格 Schema。
  public var structuredOutputMode: StructuredOutputMode {
    switch self {
    case .mimo, .ollama, .qwen: .jsonObject
    default: .jsonSchema
    }
  }

  /// 本地服务不需要密钥。
  public var requiresAPIKey: Bool { self != .ollama }

  public func makeProfile(id: String = UUID().uuidString) -> ProviderProfile {
    ProviderProfile(
      id: id,
      name: displayName,
      baseURL: baseURL,
      defaultModel: defaultModel,
      structuredOutputMode: structuredOutputMode
    )
  }
}

/// 后端的模型角色。分开配置的主要价值：记忆提取和目标整理是高频低难度调用，
/// 配便宜模型能明显省钱；求解配强模型。
public enum ModelRole: String, Codable, CaseIterable, Identifiable, Sendable {
  case conversation
  case solver
  case targetDrafter = "target_drafter"
  case methodExtractor = "method_extractor"
  case memoryExtractor = "memory_extractor"
  case claimDrafter = "claim_drafter"
  case reviewer

  public var id: String { rawValue }

  public var displayName: String {
    switch self {
    case .conversation: "对话"
    case .solver: "求解"
    case .targetDrafter: "目标整理"
    case .methodExtractor: "方法提炼"
    case .memoryExtractor: "记忆提取"
    case .claimDrafter: "抽断言"
    case .reviewer: "异模型复核"
    }
  }

  public var explanation: String {
    switch self {
    case .conversation: "普通聊天的每一个回合。"
    case .solver: "生成候选解。答案仍由本地 SymPy 独立验收。"
    case .targetDrafter: "把自然语言题目整理成可验证目标，供你确认。"
    case .methodExtractor: "从已验证解答中提炼方法卡。"
    case .memoryExtractor: "从对话中整理用户画像、学习目标和讲解偏好。"
    case .claimDrafter:
      "回答里没有等式时，把题面和答案拼成一条可检验的断言。它只提出，判定仍归 SymPy，"
        + "而且只在规则版抽不出来时才调用。"
    case .reviewer:
      "换一个模型复核答案。必须绑到与「对话」「求解」不同的档案——同模型自查是负收益，"
        + "相同时这一层会自动跳过。"
    }
  }

  /// 求解与方法提炼有零成本的离线实现（SymPy / 规则），可以完全不接模型。
  public var supportsOffline: Bool {
    switch self {
    case .solver, .methodExtractor, .targetDrafter, .memoryExtractor, .conversation,
      .claimDrafter:
      true
    // 复核没有离线实现：本地 SymPy 已经在别的层跑过了，再「离线复核」一遍是空转。
    case .reviewer: false
    }
  }

  /// 高频低难度、适合配便宜模型的角色。
  public var isHighFrequencyCheap: Bool {
    self == .memoryExtractor || self == .targetDrafter
  }
}

/// 某个角色使用哪个档案、哪个模型。
public struct RoleBinding: Codable, Hashable, Sendable {
  /// 档案 ID，或 `RoleBinding.offlineProfile` 表示走零成本离线路径。
  public var profile: String
  /// 留空表示使用档案的默认模型。
  public var model: String?
  public var reasoningEffort: String

  public static let offlineProfile = "offline"

  enum CodingKeys: String, CodingKey {
    case profile
    case model
    case reasoningEffort = "reasoning_effort"
  }

  public init(profile: String, model: String? = nil, reasoningEffort: String = "medium") {
    self.profile = profile
    self.model = model
    self.reasoningEffort = reasoningEffort
  }

  public static var offline: RoleBinding { RoleBinding(profile: offlineProfile) }

  public var isOffline: Bool {
    profile.lowercased() == Self.offlineProfile
  }
}

/// 完整的 provider 设置：档案列表 + 角色绑定。
public struct ProviderSettings: Codable, Hashable, Sendable {
  public var profiles: [ProviderProfile]
  public var roles: [String: RoleBinding]
  /// 简单档：一个档案跑全部五个角色。关闭后可逐角色指定。
  public var useSimpleMode: Bool
  public var simpleProfileID: String?

  public init(
    profiles: [ProviderProfile] = [],
    roles: [String: RoleBinding] = [:],
    useSimpleMode: Bool = true,
    simpleProfileID: String? = nil
  ) {
    self.profiles = profiles
    self.roles = roles
    self.useSimpleMode = useSimpleMode
    self.simpleProfileID = simpleProfileID
  }

  /// 展开成后端需要的五角色绑定。
  ///
  /// 简单档下所有角色指向同一档案；没有可用档案时全部退回离线，这样即使设置为空
  /// App 也能正常离线工作。
  public func resolvedRoles() -> [String: RoleBinding] {
    if useSimpleMode {
      guard let id = simpleProfileID, profiles.contains(where: { $0.id == id }) else {
        return Dictionary(uniqueKeysWithValues: ModelRole.allCases.map { ($0.rawValue, .offline) })
      }
      return Dictionary(
        uniqueKeysWithValues: ModelRole.allCases.map { ($0.rawValue, RoleBinding(profile: id)) }
      )
    }
    var resolved: [String: RoleBinding] = [:]
    for role in ModelRole.allCases {
      let binding = roles[role.rawValue] ?? .offline
      // 指向已删除档案的绑定退回离线，而不是让后端拿着无效 ID 起不来。
      if binding.isOffline || profiles.contains(where: { $0.id == binding.profile }) {
        resolved[role.rawValue] = binding
      } else {
        resolved[role.rawValue] = .offline
      }
    }
    return resolved
  }

  public func profile(id: String) -> ProviderProfile? {
    profiles.first { $0.id == id }
  }
}

public enum ProviderEnvironment {
  public static let profilesKey = "MATH_HARNESS_PROVIDERS"
  public static let rolesKey = "MATH_HARNESS_ROLES"
  public static let keyPrefix = "MATH_HARNESS_PROVIDER_KEY__"

  /// 档案 ID 对应的密钥环境变量名。
  ///
  /// 规则必须与 Python 侧 `provider_config.key_env_name` 逐字一致，否则后端读不到
  /// 密钥。两边各有断言。
  public static func keyEnvName(for profileID: String) -> String {
    // 必须限定 ASCII：Swift 的 isLetter 是 Unicode 感知的，中文字符会被判为字母，
    // 而 Python 侧的 [^A-Za-z0-9] 会把它替换成下划线。不限定就会两边不一致。
    let sanitized = profileID.map { character -> String in
      let isASCIIAlphanumeric =
        character.isASCII && (character.isLetter || character.isNumber)
      return isASCIIAlphanumeric ? String(character) : "_"
    }
    .joined()
    .uppercased()
    return keyPrefix + sanitized
  }

  /// Keychain 中该档案密钥的账户名。
  public static func keychainAccount(for profileID: String) -> String {
    "provider-key-\(profileID)"
  }

  public static func encodedProfiles(_ profiles: [ProviderProfile]) throws -> String {
    let data = try JSONEncoder().encode(profiles)
    return String(decoding: data, as: UTF8.self)
  }

  public static func encodedRoles(_ roles: [String: RoleBinding]) throws -> String {
    let data = try JSONEncoder().encode(roles)
    return String(decoding: data, as: UTF8.self)
  }
}
