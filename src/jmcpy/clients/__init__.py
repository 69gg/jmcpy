"""客户端实现。

- :mod:`jmcpy.clients.mobile`：移动端接口（请求签名 + 响应解密）
- :mod:`jmcpy.clients.web`：网页端接口（补齐移动端不支持的分类副分类搜索）
- :mod:`jmcpy.clients.facade`：门面，按能力在两种实现之间自动路由

同步与异步版本一一对应：两者共用 :mod:`jmcpy.parsing` 里的纯解析逻辑，
差别只在 I/O 调用与 ``await``。
"""

from __future__ import annotations

from .mobile import AsyncMobileClient, MobileClient
from .web import AsyncWebClient, WebClient

__all__ = ["AsyncMobileClient", "AsyncWebClient", "MobileClient", "WebClient"]
