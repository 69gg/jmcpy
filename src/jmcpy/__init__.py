"""禁漫天堂 Python SDK。

本包提供服务端接口的封装：搜索、评论、分类与排行、章节详情、图片下载解码。

库内只使用标准 :mod:`logging`，并在包级 logger 上挂了 ``NullHandler``：
默认不产生任何输出，需要排查问题时由调用方自行配置 ``logging`` 即可。
"""

from __future__ import annotations

import logging

from ._version import __version__

logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["__version__"]
