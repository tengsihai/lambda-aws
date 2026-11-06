"""日志初始化。

Lambda 运行时的默认日志配置不带结构化字段，这里统一为可检索的单行格式。
"""

from __future__ import annotations

import logging
import os

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging() -> None:
    """初始化根日志记录器（幂等，可在同一容器内重复调用）。

    这里刻意只读 ``LOG_LEVEL`` 环境变量、不依赖 :func:`common.config.get_settings`：
    如果配置本身有缺失，也要能先打出日志再失败。
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # 运行时可能已经挂了 handler，重复添加会导致日志重复输出
    if root.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)
