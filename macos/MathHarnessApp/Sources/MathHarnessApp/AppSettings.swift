import Foundation
import MathHarnessCore

enum AppSettingsKey {
  static let providerSettings = "providerSettings"
  static let maxOutputTokens = "maxOutputTokens"
  static let verificationRepair = "verificationRepair"
  static let verificationFallback = "verificationFallback"
  static let appearance = "appearance"
  static let inspectorPane = "inspectorPane"
  static let sendShortcut = "sendShortcut"
  static let messageTextSize = "messageTextSize"
  static let rendersMarkdown = "rendersMarkdown"
  static let typesetsFormulas = "typesetsFormulas"
  static let notifiesWhenFinished = "notifiesWhenFinished"
  static let vaultBookmark = "vaultBookmark"
  static let vaultDisplayPath = "vaultDisplayPath"
  static let exportsAutomatically = "exportsAutomatically"
  static let didMigrateLegacyMiMo = "didMigrateLegacyMiMo"

  // v0.11 及更早的键。只在迁移时读取，不再写入。
  static let legacySolverProvider = "solverProvider"
  static let legacyMiMoBaseURL = "mimoBaseURL"
  static let legacyMiMoModel = "mimoModel"
}

enum AppAppearance: String, CaseIterable, Identifiable {
  case system
  case light
  case dark

  var id: String { rawValue }

  var displayName: String {
    switch self {
    case .system: "跟随系统"
    case .light: "浅色"
    case .dark: "深色"
    }
  }
}

/// 哪个键发送、哪个键换行。
///
/// 数学问题经常要分几行写（条件一行、问题一行），所以默认是 ⌘↩ 发送、↩ 换行。
/// 习惯了聊天软件的人会想要反过来，那就让他改。
enum SendShortcut: String, CaseIterable, Identifiable {
  case commandReturn
  case plainReturn

  var id: String { rawValue }

  var displayName: String {
    switch self {
    case .commandReturn: "⌘↩ 发送，↩ 换行"
    case .plainReturn: "↩ 发送，⇧↩ 换行"
    }
  }

  var hint: String {
    switch self {
    case .commandReturn: "⌘↩ 发送"
    case .plainReturn: "↩ 发送 · ⇧↩ 换行"
    }
  }
}

/// 对话正文的字号。数学解答又长又密，这是最常被调的一项。
enum MessageTextSize: String, CaseIterable, Identifiable {
  case small
  case medium
  case large
  case extraLarge

  var id: String { rawValue }

  var displayName: String {
    switch self {
    case .small: "紧凑"
    case .medium: "标准"
    case .large: "宽松"
    case .extraLarge: "特大"
    }
  }

  var pointSize: CGFloat {
    switch self {
    case .small: 12
    case .medium: 13
    case .large: 15
    case .extraLarge: 17
    }
  }

  var lineSpacing: CGFloat {
    switch self {
    case .small: 2
    case .medium: 3
    case .large: 4
    case .extraLarge: 5
    }
  }
}

enum AppSettings {
  // Swift 6 严格并发下不能持有 UserDefaults 静态属性（非 Sendable），每次现取。
  private static var defaults: UserDefaults { .standard }

  static var providerSettings: ProviderSettings {
    get {
      guard
        let data = defaults.data(forKey: AppSettingsKey.providerSettings),
        let decoded = try? JSONDecoder().decode(ProviderSettings.self, from: data)
      else {
        return ProviderSettings()
      }
      return decoded
    }
    set {
      guard let data = try? JSONEncoder().encode(newValue) else { return }
      defaults.set(data, forKey: AppSettingsKey.providerSettings)
    }
  }

  static var maxOutputTokens: Int {
    let value = defaults.integer(forKey: AppSettingsKey.maxOutputTokens)
    return value == 0 ? 3_000 : value
  }

  /// 验证失败后是否让模型纠正一次。直接影响调用次数与费用，所以必须可见可关。
  static var verificationRepair: Bool {
    boolean(AppSettingsKey.verificationRepair, default: true)
  }

  /// 纠正仍失败时是否用 SymPy 重新生成。
  static var verificationFallback: Bool {
    boolean(AppSettingsKey.verificationFallback, default: true)
  }

  static var appearance: AppAppearance {
    AppAppearance(rawValue: defaults.string(forKey: AppSettingsKey.appearance) ?? "")
      ?? .system
  }

  /// 右侧面板上次是开着还是收着。默认打开知识库——它是这个软件的主张所在。
  static var inspectorPane: InspectorPane? {
    get {
      guard let raw = defaults.string(forKey: AppSettingsKey.inspectorPane) else {
        return .knowledge
      }
      return InspectorPane(rawValue: raw)
    }
    set {
      defaults.set(newValue?.rawValue ?? "", forKey: AppSettingsKey.inspectorPane)
    }
  }

  static var sendShortcut: SendShortcut {
    SendShortcut(rawValue: defaults.string(forKey: AppSettingsKey.sendShortcut) ?? "")
      ?? .commandReturn
  }

  static var messageTextSize: MessageTextSize {
    MessageTextSize(
      rawValue: defaults.string(forKey: AppSettingsKey.messageTextSize) ?? ""
    ) ?? .medium
  }

  /// 是否按 Markdown 渲染回答正文。
  static var rendersMarkdown: Bool {
    boolean(AppSettingsKey.rendersMarkdown, default: true)
  }

  /// 是否把 `$...$` 里的 LaTeX 排成真正的公式。认不出来的照旧显示原文。
  static var typesetsFormulas: Bool {
    boolean(AppSettingsKey.typesetsFormulas, default: true)
  }

  /// 回答完成时，窗口不在前台就跳一下 Dock 图标。
  static var notifiesWhenFinished: Bool {
    boolean(AppSettingsKey.notifiesWhenFinished, default: true)
  }

  /// Obsidian vault 根目录的访问凭据。存 bookmark 而不是路径：路径在下次启动时可能
  /// 已经不对，而 bookmark 能跟着文件走，也能明确告诉我们「找不到了」。
  static var vaultBookmark: Data? {
    get { defaults.data(forKey: AppSettingsKey.vaultBookmark) }
    set {
      if let newValue {
        defaults.set(newValue, forKey: AppSettingsKey.vaultBookmark)
      } else {
        defaults.removeObject(forKey: AppSettingsKey.vaultBookmark)
      }
    }
  }

  /// 上次选中的路径，只用于显示。够不着的时候还能告诉用户「你选的是哪儿」。
  static var vaultDisplayPath: String {
    get { defaults.string(forKey: AppSettingsKey.vaultDisplayPath) ?? "" }
    set { defaults.set(newValue, forKey: AppSettingsKey.vaultDisplayPath) }
  }

  /// 晋级、复核之后是否自动导出。
  static var exportsAutomatically: Bool {
    boolean(AppSettingsKey.exportsAutomatically, default: true)
  }

  private static func boolean(_ key: String, default fallback: Bool) -> Bool {
    defaults.object(forKey: key) == nil ? fallback : defaults.bool(forKey: key)
  }

  /// 把 v0.11 的单一 MiMo 配置迁移成一个 provider 档案。
  ///
  /// 只在首次升级时跑一次。用户既有的 Base URL、模型和 Keychain 密钥都保留下来，
  /// 升级后不需要重新填写。
  @discardableResult
  static func migrateLegacySettingsIfNeeded(
    defaults migrationDefaults: UserDefaults = .standard,
    readLegacyKey: () throws -> String? = {
      try KeychainStore.readAPIKey(account: KeychainStore.legacyMiMoAccount)
    },
    migrateLegacyKey: (String) throws -> Bool = {
      try KeychainStore.migrateLegacyMiMoKey(toProfile: $0)
    }
  ) -> Bool {
    LegacyProviderSettingsMigrator.migrate(
      defaults: migrationDefaults,
      keys: LegacyProviderSettingsMigrationKeys(
        providerSettings: AppSettingsKey.providerSettings,
        completionMarker: AppSettingsKey.didMigrateLegacyMiMo,
        legacyProvider: AppSettingsKey.legacySolverProvider,
        legacyBaseURL: AppSettingsKey.legacyMiMoBaseURL,
        legacyModel: AppSettingsKey.legacyMiMoModel
      ),
      preset: .mimo,
      legacyProviderID: "mimo",
      readLegacyKey: readLegacyKey,
      migrateLegacyKey: migrateLegacyKey
    )
  }
}
