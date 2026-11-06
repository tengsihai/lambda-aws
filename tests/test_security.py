"""安全约束测试：确保仓库里不存在任何写死的密钥。

这条约束对应项目的核心要求——数据库口令只能来自 Secrets Manager，
代码、配置、工作流文件中都不允许出现明文凭据。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 需要扫描的目录
SCAN_DIRS = [PROJECT_ROOT / "src", PROJECT_ROOT / "tests", PROJECT_ROOT / ".github"]
SCAN_FILES = [
    PROJECT_ROOT / "template.yaml",
    PROJECT_ROOT / "pyproject.toml",
    PROJECT_ROOT / "requirements-dev.txt",
    PROJECT_ROOT / "schema.sql",
]

# 明显不该出现在仓库里的凭据形态
FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS AccessKey ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS SecretAccessKey", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"][^'\"]{20,}")),
    ("私钥块", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
    ("硬编码口令赋值", re.compile(r"(?i)(?:password|passwd|pwd)\s*=\s*['\"][^'\"]{4,}['\"]")),
]

# 允许出现的例外：占位符、环境变量读取、字段名判断等
ALLOWED_SNIPPETS = [
    "os.environ.get",  # 从环境变量读取
    'credentials.get("password")',  # 从密钥字典取值
    '"password"',  # 字段名
    "'password'",
    "DB_PASSWORD",  # 被明确禁止使用的环境变量名，出现在测试断言里
    "MYSQL_PASSWORD",
    "AWS_SECRET_ACCESS_KEY",
    "password=",  # 函数关键字参数，如 pymysql.connect(password=password)
]


def _iter_source_files() -> list[Path]:
    """收集所有需要扫描的文本文件。"""
    files: list[Path] = []
    for directory in SCAN_DIRS:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".yml", ".yaml", ".toml", ".txt"}:
                files.append(path)
    files.extend(path for path in SCAN_FILES if path.exists())
    return files


def _is_allowed(line: str) -> bool:
    """判断该行是否属于允许的例外。"""
    return any(snippet in line for snippet in ALLOWED_SNIPPETS)


def test_scan_covers_source_files() -> None:
    """确保扫描范围没有因为路径变动而失效。"""
    files = _iter_source_files()
    assert len(files) > 10
    assert any(path.name == "handler.py" for path in files)


@pytest.mark.parametrize(
    "file_path", _iter_source_files(), ids=lambda p: str(p.relative_to(PROJECT_ROOT))
)
def test_no_hardcoded_secret_in_file(file_path: Path) -> None:
    """逐文件扫描，发现疑似凭据即失败。"""
    content = file_path.read_text(encoding="utf-8")

    violations: list[str] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        if _is_allowed(line):
            continue
        for label, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(line):
                violations.append(
                    f"{file_path.relative_to(PROJECT_ROOT)}:{line_number} [{label}] {line.strip()}"
                )

    assert not violations, "发现疑似硬编码密钥：\n" + "\n".join(violations)


def test_workflow_uses_oidc_role() -> None:
    """部署必须走 OIDC 临时凭证，不允许配置静态 AccessKey。"""
    workflow = PROJECT_ROOT / ".github" / "workflows" / "ci-cd.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "id-token: write" in content, "缺少 OIDC 所需的 id-token 权限"
    assert "role-to-assume" in content, "未使用 IAM 角色切换"
    assert "aws-access-key-id" not in content.lower(), "不允许在工作流中写入静态 AccessKey"


def test_template_references_secret_arn_only() -> None:
    """CloudFormation 模板只应引用密钥 ARN，不应出现任何口令字段。"""
    content = (PROJECT_ROOT / "template.yaml").read_text(encoding="utf-8")

    assert "DbSecretArn" in content
    # 模板里不应出现用于承载口令的变量名
    for forbidden in ("DB_PASSWORD", "DbPassword", "MasterUserPassword"):
        assert forbidden not in content, f"模板中不应出现 {forbidden}"
