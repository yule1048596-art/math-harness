import Foundation

/// v0.11 单一 provider 设置迁移所用的 UserDefaults 键。
///
/// 迁移事务放在 Core 中，既能由 App 注入 Keychain 操作，也能在没有完整 Xcode 测试
/// 运行库的机器上通过 CoreChecks 模拟失败与重试。
public struct LegacyProviderSettingsMigrationKeys: Sendable {
  public let providerSettings: String
  public let completionMarker: String
  public let legacyProvider: String
  public let legacyBaseURL: String
  public let legacyModel: String

  public init(
    providerSettings: String,
    completionMarker: String,
    legacyProvider: String,
    legacyBaseURL: String,
    legacyModel: String
  ) {
    self.providerSettings = providerSettings
    self.completionMarker = completionMarker
    self.legacyProvider = legacyProvider
    self.legacyBaseURL = legacyBaseURL
    self.legacyModel = legacyModel
  }
}

public enum LegacyProviderSettingsMigrator {
  /// 原子化迁移旧设置。任何可失败步骤出错时都不提交设置或完成标记，下一次可重试。
  @discardableResult
  public static func migrate(
    defaults: UserDefaults,
    keys: LegacyProviderSettingsMigrationKeys,
    preset: ProviderPreset,
    legacyProviderID: String,
    readLegacyKey: () throws -> String?,
    migrateLegacyKey: (String) throws -> Bool
  ) -> Bool {
    guard !defaults.bool(forKey: keys.completionMarker) else {
      return false
    }

    guard defaults.data(forKey: keys.providerSettings) == nil else {
      defaults.set(true, forKey: keys.completionMarker)
      return false
    }

    do {
      let configuredProvider = defaults.string(forKey: keys.legacyProvider)
      let legacyKey = try readLegacyKey()
      guard configuredProvider == legacyProviderID || (legacyKey?.isEmpty == false) else {
        defaults.set(true, forKey: keys.completionMarker)
        return false
      }

      var profile = preset.makeProfile()
      if let baseURL = defaults.string(forKey: keys.legacyBaseURL), !baseURL.isEmpty {
        profile.baseURL = baseURL
      }
      if let model = defaults.string(forKey: keys.legacyModel), !model.isEmpty {
        profile.defaultModel = model
      }
      let settings = ProviderSettings(
        profiles: [profile],
        useSimpleMode: true,
        simpleProfileID: profile.id
      )

      // 编码和 Keychain 复制都完成后才提交 UserDefaults。若复制抛错，设置与完成标记
      // 都保持原样；KeychainStore 自身的“目标已存在”检查让重试保持幂等。
      let encoded = try JSONEncoder().encode(settings)
      _ = try migrateLegacyKey(profile.id)
      defaults.set(encoded, forKey: keys.providerSettings)
      defaults.set(true, forKey: keys.completionMarker)
      return true
    } catch {
      return false
    }
  }
}
