import Foundation
import MathHarnessCore
import Security

enum KeychainStoreError: Error, LocalizedError {
  case unexpectedStatus(OSStatus)
  case invalidData

  var errorDescription: String? {
    switch self {
    case .unexpectedStatus(let status):
      let message = SecCopyErrorMessageString(status, nil) as String? ?? "未知错误"
      return "无法访问钥匙串：\(message)"
    case .invalidData:
      return "钥匙串中的密钥不是有效文本。"
    }
  }
}

enum KeychainStore {
  private static let service = "com.yule.mathharness"
  /// v0.11 及更早版本使用的固定账户。保留是为了迁移，不再写入。
  static let legacyMiMoAccount = "mimo-api-key"

  static func readAPIKey(account: String) throws -> String? {
    var query = baseQuery(account: account)
    query[kSecReturnData as String] = true
    query[kSecMatchLimit as String] = kSecMatchLimitOne

    var result: CFTypeRef?
    let status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecItemNotFound {
      return nil
    }
    guard status == errSecSuccess else {
      throw KeychainStoreError.unexpectedStatus(status)
    }
    guard
      let data = result as? Data,
      let value = String(data: data, encoding: .utf8)
    else {
      throw KeychainStoreError.invalidData
    }
    return value
  }

  static func saveAPIKey(_ value: String, account: String) throws {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty {
      try deleteAPIKey(account: account)
      return
    }

    let data = Data(trimmed.utf8)
    let query = baseQuery(account: account)
    let status = SecItemUpdate(
      query as CFDictionary,
      [kSecValueData as String: data] as CFDictionary
    )
    if status == errSecItemNotFound {
      var item = query
      item[kSecValueData as String] = data
      let addStatus = SecItemAdd(item as CFDictionary, nil)
      guard addStatus == errSecSuccess else {
        throw KeychainStoreError.unexpectedStatus(addStatus)
      }
      return
    }
    guard status == errSecSuccess else {
      throw KeychainStoreError.unexpectedStatus(status)
    }
  }

  static func deleteAPIKey(account: String) throws {
    let status = SecItemDelete(baseQuery(account: account) as CFDictionary)
    guard status == errSecSuccess || status == errSecItemNotFound else {
      throw KeychainStoreError.unexpectedStatus(status)
    }
  }

  static func readAPIKey(forProfile profileID: String) throws -> String? {
    try readAPIKey(account: ProviderEnvironment.keychainAccount(for: profileID))
  }

  static func saveAPIKey(_ value: String, forProfile profileID: String) throws {
    try saveAPIKey(value, account: ProviderEnvironment.keychainAccount(for: profileID))
  }

  static func deleteAPIKey(forProfile profileID: String) throws {
    try deleteAPIKey(account: ProviderEnvironment.keychainAccount(for: profileID))
  }

  /// 把 v0.11 的固定 MiMo 密钥迁移到新的按档案存储。
  ///
  /// **旧条目刻意保留**：万一迁移后用户回退到旧版本，密钥还在原处。等下一个版本再删。
  /// 密钥丢了要重去控制台取，这是升级里最容易激怒人的失败。
  @discardableResult
  static func migrateLegacyMiMoKey(toProfile profileID: String) throws -> Bool {
    guard let legacy = try readAPIKey(account: legacyMiMoAccount), !legacy.isEmpty else {
      return false
    }
    let destination = ProviderEnvironment.keychainAccount(for: profileID)
    if let existing = try readAPIKey(account: destination), !existing.isEmpty {
      return false
    }
    try saveAPIKey(legacy, account: destination)
    return true
  }

  private static func baseQuery(account: String) -> [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
  }
}
