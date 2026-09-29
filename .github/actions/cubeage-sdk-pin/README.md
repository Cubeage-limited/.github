# cubeage-sdk-pin

Release gate. Fails a check when the repository pins a refused
`cubeage-native-sdk` version. `4.2.18` and `4.2.19` are refused: they carry the
offline-first-launch V1 guest-stranding bug. `4.2.20` (tagged 2026-10-01) is the
minimum for builds with the V1 adoption.

Inputs: `deny` (default `4.2.18,4.2.19`) and `minimum` (default empty; set to
`4.2.20` to tighten later).

It scans tracked files (skipping `Library/`, `node_modules/`, `*.meta`, binary
files, files over 2 MB, and Markdown/text prose) for these pin sites:

- Unity `Packages/manifest.json` and `packages-lock.json` (`...sdk#vX.Y.Z`)
- Solar2D `cubeage_sdk_pin*.json` and `build.settings` release URLs
- SwiftPM `Package.swift` and `Package.resolved`
- Gradle/TOML coordinates (`com.cubeage...:artifact:X.Y.Z`)
- workflow refs (`CUBEAGE_SDK_REF: vX.Y.Z`, `--branch vX.Y.Z`, `repository:` + `ref:`)
- `.gitmodules` branch, or a checked-out submodule at a tag

A test that must name a refused version puts `cubeage-sdk-pin: ignore` on the
same line or the line above.

```yaml
  cubeage-sdk-pin:
    runs-on: <same runner as the other jobs>
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0
      - uses: Cubeage/.github/.github/actions/cubeage-sdk-pin@<sha>
```
