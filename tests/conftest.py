"""pytest 共享夹具。

测试全部在内存中完成：数据库访问与 Secrets Manager 均被替换为假实现，
因此既不需要网络，也不会触碰任何真实凭据。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# 把 src/ 加入导入路径，使 `common`、`create` 等包可被直接导入。
# 必须在导入任何业务模块之前执行。
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from helpers import TEST_ENV  # noqa: E402  —— 依赖上面的 sys.path 注入


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """为每个测试注入干净的环境变量并清理各类进程级缓存。"""
    # 先清掉可能存在的真实变量，保证测试之间互不影响
    for key in list(os.environ):
        if key.startswith(("DB_", "LOG_LEVEL")):
            monkeypatch.delenv(key, raising=False)

    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)

    # 配置与密钥都带进程级缓存，每个测试开始前必须重置
    from common import db
    from common.config import get_settings
    from common.secrets import reset_cache

    get_settings.cache_clear()
    reset_cache()
    db.close_connection()

    yield

    get_settings.cache_clear()
    reset_cache()
    db.close_connection()
