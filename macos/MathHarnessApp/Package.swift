// swift-tools-version: 6.0

import PackageDescription

let package = Package(
  name: "MathHarnessApp",
  platforms: [
    .macOS(.v14)
  ],
  products: [
    .library(name: "MathHarnessCore", targets: ["MathHarnessCore"]),
    .executable(name: "MathHarnessApp", targets: ["MathHarnessApp"]),
    .executable(name: "MathHarnessCoreChecks", targets: ["MathHarnessCoreChecks"]),
  ],
  targets: [
    .target(name: "MathHarnessCore"),
    .executableTarget(
      name: "MathHarnessApp",
      dependencies: ["MathHarnessCore"],
      linkerSettings: [
        .linkedFramework("AppKit"),
        .linkedFramework("Security"),
      ]
    ),
    .executableTarget(
      name: "MathHarnessCoreChecks",
      dependencies: ["MathHarnessCore"]
    ),
  ]
)
