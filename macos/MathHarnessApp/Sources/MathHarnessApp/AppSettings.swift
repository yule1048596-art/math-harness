import Foundation
import MathHarnessCore

enum AppSettingsKey {
  static let providerSettings = "providerSettings"
  static let maxOutputTokens = "maxOutputTokens"
  static let verificationRepair = "verificationRepair"
  static let verificationFallback = "verificationFallback"
  static let appearance = "appearance"
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
