#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$(command -v python3)"
USER_ROOT="$(cd && pwd)"
LAUNCH_AGENTS_DIR="${USER_ROOT}/Library/LaunchAgents"
PLIST_PATH="${LAUNCH_AGENTS_DIR}/com.zhouxutong.solid-mechanics-jobs.plist"
LOG_DIR="${PROJECT_DIR}/logs"

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

if [[ "$PROJECT_DIR" == *"&"* || "$PYTHON_BIN" == *"&"* ]]; then
  print -u2 "项目路径含有 XML 特殊字符 &，请改用 GitHub Actions 定时更新。"
  exit 1
fi

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.zhouxutong.solid-mechanics-jobs</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>${PROJECT_DIR}/scripts/update_jobs.py</string>
    <string>--timeout</string>
    <string>12</string>
    <string>--workers</string>
    <string>8</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_DIR}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>8</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/update.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/update-error.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/com.zhouxutong.solid-mechanics-jobs"

print "已安装每日 08:30 自动更新任务："
print "$PLIST_PATH"
print "日志目录：$LOG_DIR"
