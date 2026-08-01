import Foundation
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
  private static let mimoAccount = "mimo-api-key"

  static func readMiMoAPIKey() throws -> String? {
    var query = baseQuery
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

  static func saveMiMoAPIKey(_ value: String) throws {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty {
      let status = SecItemDelete(baseQuery as CFDictionary)
      guard status == errSecSuccess || status == errSecItemNotFound else {
        throw KeychainStoreError.unexpectedStatus(status)
      }
      return
    }

    let data = Data(trimmed.utf8)
    let status = SecItemUpdate(
      baseQuery as CFDictionary,
      [kSecValueData as String: data] as CFDictionary
    )
    if status == errSecItemNotFound {
      var item = baseQuery
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

  private static var baseQuery: [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: mimoAccount,
    ]
  }
}
