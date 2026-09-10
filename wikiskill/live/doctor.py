"""Read-only local diagnostics with fixed messages that keep configuration values private."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from urllib.parse import urlsplit

from .config import Config


def _check(name: str, status: str, message: str, repair: str = "") -> dict:
    return {"id": name, "status": status, "message": message, "repair": repair}


def _creatable(path: Path) -> bool:
    """Check the nearest existing parent using metadata and access checks."""
    parent = path.parent
    while True:
        try:
            mode = parent.stat().st_mode
            return stat.S_ISDIR(mode) and os.access(parent, os.W_OK | os.X_OK)
        except FileNotFoundError:
            if parent.is_symlink() or parent == parent.parent:
                return False
            parent = parent.parent


def _path_check(name: str, path: Path, *, directory: bool, repair: str,
                required: bool = False, writable: bool = False) -> dict:
    try:
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            if path.is_symlink():
                return _check(name, "error", "符号链接的目标不存在。", repair)
            if required:
                return _check(name, "error", "目录或文件尚不存在。", repair)
            if writable and not _creatable(path):
                return _check(name, "error", "尚不存在，且无法在父目录中创建。", repair)
            return _check(name, "warning", "尚不存在。", repair)
        correct_type = stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)
        if not correct_type:
            return _check(name, "error", "路径类型错误，需要目录。" if directory else
                          "路径类型错误，需要普通文件。", repair)
        access = os.R_OK | (os.X_OK if directory else 0) | (os.W_OK if writable else 0)
        if not os.access(path, access):
            return _check(name, "error", "当前用户缺少所需的读取、遍历或写入权限。", repair)
        return _check(name, "ok", "存在且具备所需访问权限。")
    except (OSError, ValueError, RuntimeError):
        return _check(name, "error", "无法检查路径。", repair)


def _config_repair(error: Exception) -> str:
    """Map known validation failures to fixed advice; all values remain private."""
    hints = {
        "Unknown config fields; use the current config format":
            "config.json 只接受当前配置字段；按使用说明移除旧字段，或备份后运行 init --reset-settings。",
        "Invalid executor or API provider":
            "executor 使用 codex/api/ollama，api_provider 使用 chat_completions/gemini。",
        "api_url must be an HTTP(S) URL without credentials, query or fragment":
            "将 api_url 设为完整 HTTP(S) 地址，移除用户名、密码、查询参数及片段。",
        "API model and key must be text": "将 api_model 和 api_key 设为字符串。",
        "codex_command must contain an executable and string arguments":
            '将 codex_command 设为非空字符串数组，例如 ["codex", "app-server"]。',
        "model must be null or a nonempty Codex model name":
            "将 model 设为 null（使用 Codex 默认模型）或非空模型名称。",
    }
    for name in ("analysis_interval_minutes", "timeout_seconds", "poll_seconds"):
        hints[f"{name} must be a positive integer"] = f"将 {name} 设为正整数。"
    for name in ("auto_start",):
        hints[f"{name} must be boolean"] = f"将 {name} 设为 true 或 false。"
    for name in ("codex_home", "install_directory"):
        hints[f"{name} must be an absolute directory"] = f"将 {name} 设为绝对目录路径。"
    return hints.get(str(error), "检查 config.json 是否为有效的 UTF-8 JSON 对象，字段类型和取值是否符合当前配置；"
                     "修正后重试，或备份后运行 wikiskill-codex --root <目录> init --reset-settings。")


def _command_check(config: Config) -> dict:
    try:
        command = config.codex_command[0]
        # CodexSession launches in the data root; resolve relative executable/PATH entries there.
        if os.path.dirname(command):
            executable = Path(command)
            if not executable.is_absolute():
                executable = config.root / executable
            found = shutil.which(str(executable))
        else:
            search_path = os.pathsep.join(str(Path(entry) if os.path.isabs(entry) else config.root / entry)
                                         for entry in os.get_exec_path())
            found = shutil.which(command, path=search_path)
        if found and Path(found).is_file():
            return _check("codex_command", "ok", "已找到可执行命令；未运行命令或检查登录。")
    except (OSError, ValueError, RuntimeError):
        pass
    return _check("codex_command", "error" if config.executor == "codex" else "warning",
                  "未找到可执行命令。", "检查 codex_command 首项和 PATH；安装 Codex CLI，或填写可执行文件的绝对路径。"
                  "API 执行方式不依赖此命令。")


def diagnose(root: str | Path) -> dict:
    """Inspect the selected root without constructing a Runtime, Store or model client."""
    checks = []
    executor = None

    def report():
        return {"ok": not any(item["status"] == "error" for item in checks),
                "executor": executor, "checks": checks}

    try:
        home = Path(root).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        checks.append(_check("root", "error", "无法解析数据目录。", "为 --root 指定可访问的本地目录。"))
        return report()
    checks.append(_path_check("root", home, directory=True, required=True, writable=True,
        repair="检查 --root 目录及其权限；尚未初始化时运行 wikiskill-codex --root <目录> init。"))
    if checks[-1]["status"] == "error":
        return report()
    config_file = home / "config.json"
    config_check = _path_check("config_file", config_file, directory=False, required=True,
        repair="确认 config.json 是可读的普通文件；缺失时运行 wikiskill-codex --root <目录> init。")
    checks.append(config_check)
    checks.append(_path_check("database", home / "state.sqlite3", directory=False, writable=True,
        repair="缺失时运行 wikiskill-codex --root <目录> init；已存在时检查 state.sqlite3 的类型和读写权限。"))
    for name in ("skills", "reports", "locks"):
        checks.append(_path_check(f"data.{name}", home / name, directory=True, writable=True,
            repair=f"{name} 目录会在相关操作首次使用时创建；检查同名路径和父目录读写权限。"))
    checks.append(_path_check("data.worker.log", home / "worker.log", directory=False, writable=True,
        repair="worker.log 会在后台首次启动时创建；检查同名路径和数据目录读写权限。"))
    if config_check["status"] == "error":
        return report()
    try:
        config = Config.load(home)
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as error:
        checks.append(_check("config", "error", "无法读取配置，或配置不符合当前格式。", _config_repair(error)))
        return report()
    checks.append(_check("config", "ok", "配置符合当前格式。"))
    executor = config.executor
    try:
        private = not (config_file.stat().st_mode & 0o077)
        checks.append(_check("config_permissions", "ok" if private else "warning",
            "配置权限未开放给其他用户。" if private else "配置权限允许其他用户访问。",
            "" if private else "将数据目录中的 config.json 权限设为 600（chmod 600）。"))
    except OSError:
        checks.append(_check("config_permissions", "error", "无法读取配置权限。", "检查 config.json 是否仍存在且可访问。"))
    checks.append(_check("api_key", "ok" if config.api_key.strip() or executor in {"codex", "ollama"} else "warning",
        "已配置。" if config.api_key.strip() else "未配置。",
        "若 API 服务需要认证，请在设置页填写 api_key；免认证服务可留空。" if executor == "api" and not config.api_key.strip() else ""))
    if executor == "api":
        checks.append(_check("api_model", "ok" if config.api_model.strip() else "error",
            "已配置。" if config.api_model.strip() else "未配置。",
            "" if config.api_model.strip() else "在设置页填写 API 模型名称 api_model。"))
        try:
            urlsplit(config.api_url).port
            checks.append(_check("api_endpoint", "ok", "API 协议和地址格式有效；未验证连接和认证。"))
        except ValueError:
            checks.append(_check("api_endpoint", "error", "API 地址端口无效。",
                "将 api_url 中的端口改为 0 到 65535 的整数，或省略端口。"))
    elif executor == 'ollama':
        checks.append(_check('ollama_model', 'ok' if config.ollama_model.strip() else 'error',
            '已配置本机模型；未连接 Ollama。' if config.ollama_model.strip() else '未配置本机模型。',
            '' if config.ollama_model.strip() else '在设置页填写已经下载的 Ollama 模型名称。'))
    else:
        checks.append(_check("codex_model", "ok", "已指定模型。" if config.model else "使用 Codex 默认模型。"))
    checks.append(_command_check(config))
    codex_home = Path(config.codex_home)
    checks.append(_path_check("codex_home", codex_home, directory=True,
        repair="检查 codex_home 设置和读取权限；首次使用 Codex 后会生成该目录。"))
    for name in ("sessions", "archived_sessions"):
        checks.append(_path_check(f"codex.{name}", codex_home / name, directory=True,
            repair=f"Codex 有相应会话后会生成 {name}；已存在时检查 codex_home 和读取权限。"))
    checks.append(_path_check("install_directory", Path(config.install_directory), directory=True, writable=True,
        repair="检查 install_directory 及父目录权限；目录可在首次安装 Skill 时创建。"))
    return report()


def format_report(report: dict) -> str:
    lines = ["WikiSkill 环境诊断：" + ("通过" if report["ok"] else "存在阻止使用的问题")]
    if report["executor"]:
        lines.append("执行方式：" + report["executor"])
    for item in report["checks"]:
        lines.append(f'[{item["status"].upper()}] {item["id"]}: {item["message"]}')
        if item["repair"]:
            lines.append("  建议：" + item["repair"])
    lines.append("仅检查本地配置和文件状态；未验证登录、API 连接或数据库内容。")
    return "\n".join(lines)
