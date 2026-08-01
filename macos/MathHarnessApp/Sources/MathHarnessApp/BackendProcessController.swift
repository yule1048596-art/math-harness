import Foundation
import MathHarnessCore
import Security

struct BackendConnection: Sendable {
  let baseURL: URL
  let token: String?
  let version: String
}

enum BackendLaunchError: Error, LocalizedError {
  case invalidExternalURL
  case projectNotFound
  case missingMiMoAPIKey
  case randomTokenFailed(OSStatus)
  case processExited(String)
  case startupTimedOut(String)
  case invalidReadyFile

  var errorDescription: String? {
    switch self {
    case .invalidExternalURL:
      "MATH_HARNESS_BACKEND_URL 不是有效地址。"
    case .projectNotFound:
      "没有找到随 App 打包的数学服务，也没有找到开发仓库。请使用打包脚本生成完整 App。"
    case .missingMiMoAPIKey:
      "已选择小米 MiMo，但钥匙串中还没有 API Key。请先在设置中保存密钥。"
    case .randomTokenFailed(let status):
      "无法生成本地服务令牌（Security 状态 \(status)）。"
    case .processExited(let log):
      "本地数学服务启动后立即退出。\n\(log)"
    case .startupTimedOut(let log):
      "等待本地数学服务启动超时。\n\(log)"
    case .invalidReadyFile:
      "本地数学服务生成了无效的连接信息。"
    }
  }
}

@MainActor
final class BackendProcessController {
  private var process: Process?
  private var readyFile: URL?
  private var logFile: URL?
  private var logHandle: FileHandle?

  func start() async throws -> BackendConnection {
    stop()

    let environment = ProcessInfo.processInfo.environment
    if let configuredURL = environment["MATH_HARNESS_BACKEND_URL"] {
      guard let url = URL(string: configuredURL) else {
        throw BackendLaunchError.invalidExternalURL
      }
      let token = environment["MATH_HARNESS_LOCAL_TOKEN"]
      let client = APIClient(baseURL: url, bearerToken: token, timeout: 5)
      let health = try await client.health()
      return BackendConnection(baseURL: url, token: token, version: health.version)
    }

    let fileManager = FileManager.default
    let dataRoot = try applicationSupportDirectory()
    try fileManager.createDirectory(at: dataRoot, withIntermediateDirectories: true)

    let runtimeDirectory = fileManager.temporaryDirectory
      .appendingPathComponent("math-harness-\(UUID().uuidString)", isDirectory: true)
    try fileManager.createDirectory(at: runtimeDirectory, withIntermediateDirectories: true)
    let readyFile = runtimeDirectory.appendingPathComponent("ready.json")
    self.readyFile = readyFile
    var keepBackendRunning = false
    defer {
      if !keepBackendRunning {
        stop()
      }
    }

    let token = try makeRandomToken()
    let process = Process()
    let commonArguments = [
      "--data-dir", dataRoot.path,
      "--port", "0",
      "--ready-file", readyFile.path,
      "--parent-pid", String(ProcessInfo.processInfo.processIdentifier),
      "--log-level", "warning",
    ]

    if let backend = packagedBackendURL() {
      process.executableURL = backend
      process.arguments = commonArguments
      process.currentDirectoryURL = backend.deletingLastPathComponent()
    } else if let projectRoot = developmentProjectRoot() {
      process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
      process.arguments =
        [
          "uv", "run", "--project", projectRoot.path,
          "--no-editable", "math-harness-server",
        ] + commonArguments
      process.currentDirectoryURL = projectRoot
    } else {
      throw BackendLaunchError.projectNotFound
    }

    var childEnvironment = environment
    childEnvironment["MATH_HARNESS_LOCAL_TOKEN"] = token
    childEnvironment["MATH_HARNESS_DATA_DIR"] = dataRoot.path
    childEnvironment["MATH_HARNESS_METHOD_EXTRACTOR"] = "rules"
    childEnvironment["MATH_HARNESS_SOLVER"] = AppSettings.solverProvider.rawValue
    childEnvironment["MATH_HARNESS_ENV_FILE"] =
      runtimeDirectory
      .appendingPathComponent("no-local-env").path
    if AppSettings.solverProvider == .mimo {
      guard let apiKey = try KeychainStore.readMiMoAPIKey(), !apiKey.isEmpty else {
        throw BackendLaunchError.missingMiMoAPIKey
      }
      childEnvironment["MIMO_API_KEY"] = apiKey
      childEnvironment["MATH_HARNESS_MIMO_BASE_URL"] = AppSettings.mimoBaseURL
      childEnvironment["MATH_HARNESS_MIMO_MODEL"] = AppSettings.mimoModel
      childEnvironment["MATH_HARNESS_MIMO_SOLVER_REASONING_EFFORT"] = "none"
      childEnvironment["MATH_HARNESS_VERIFICATION_REPAIR"] = "true"
      childEnvironment["MATH_HARNESS_VERIFICATION_FALLBACK"] = "true"
    }
    process.environment = childEnvironment

    let logURL = try backendLogURL()
    if !fileManager.fileExists(atPath: logURL.path) {
      fileManager.createFile(atPath: logURL.path, contents: nil)
    }
    let handle = try FileHandle(forWritingTo: logURL)
    try handle.seekToEnd()
    process.standardOutput = handle
    process.standardError = handle
    self.logFile = logURL
    self.logHandle = handle
    self.process = process

    do {
      try process.run()
    } catch {
      stop()
      throw error
    }

    for _ in 0..<300 {
      if !process.isRunning {
        throw BackendLaunchError.processExited(readLogTail())
      }
      if let data = try? Data(contentsOf: readyFile),
        let ready = try? JSONDecoder().decode(BackendReady.self, from: data)
      {
        guard let baseURL = URL(string: ready.baseURL) else {
          throw BackendLaunchError.invalidReadyFile
        }
        let client = APIClient(baseURL: baseURL, bearerToken: token, timeout: 3)
        do {
          let health = try await client.health()
          keepBackendRunning = true
          return BackendConnection(
            baseURL: baseURL,
            token: token,
            version: health.version
          )
        } catch {
          // The socket is bound before Uvicorn finishes startup. Keep polling.
        }
      }
      try await Task<Never, Never>.sleep(for: .milliseconds(100))
    }

    throw BackendLaunchError.startupTimedOut(readLogTail())
  }

  func stop() {
    if let process, process.isRunning {
      process.terminate()
    }
    process = nil
    try? logHandle?.close()
    logHandle = nil
    logFile = nil
    if let readyFile {
      try? FileManager.default.removeItem(at: readyFile.deletingLastPathComponent())
    }
    readyFile = nil
  }

  private func applicationSupportDirectory() throws -> URL {
    let base = try FileManager.default.url(
      for: .applicationSupportDirectory,
      in: .userDomainMask,
      appropriateFor: nil,
      create: true
    )
    let directory = base.appendingPathComponent("Math Harness", isDirectory: true)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    return directory
  }

  private func backendLogURL() throws -> URL {
    let directory = try applicationSupportDirectory()
      .appendingPathComponent("Logs", isDirectory: true)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    return directory.appendingPathComponent("backend.log")
  }

  private func packagedBackendURL() -> URL? {
    guard let resources = Bundle.main.resourceURL else { return nil }
    let candidates = [
      resources.appendingPathComponent("backend/math-harness-server"),
      resources.appendingPathComponent("backend/math-harness-server/math-harness-server"),
    ]
    return candidates.first { FileManager.default.isExecutableFile(atPath: $0.path) }
  }

  private func developmentProjectRoot() -> URL? {
    if let configured = ProcessInfo.processInfo.environment["MATH_HARNESS_PROJECT_ROOT"] {
      let url = URL(fileURLWithPath: configured, isDirectory: true)
      if FileManager.default.fileExists(atPath: url.appendingPathComponent("pyproject.toml").path) {
        return url
      }
    }

    var candidate = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    for _ in 0..<8 {
      let manifest = candidate.appendingPathComponent("pyproject.toml")
      if FileManager.default.fileExists(atPath: manifest.path) {
        return candidate
      }
      let parent = candidate.deletingLastPathComponent()
      if parent == candidate { break }
      candidate = parent
    }
    return nil
  }

  private func makeRandomToken() throws -> String {
    var bytes = [UInt8](repeating: 0, count: 32)
    let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
    guard status == errSecSuccess else {
      throw BackendLaunchError.randomTokenFailed(status)
    }
    return Data(bytes).base64EncodedString()
  }

  private func readLogTail() -> String {
    guard
      let logFile,
      let data = try? Data(contentsOf: logFile),
      let text = String(data: data, encoding: .utf8)
    else {
      return "没有可用的后台日志。"
    }
    return String(text.suffix(4_000))
  }
}
