# 配置

全部可调项都在 `Settings` 里。三种来源，优先级从高到低：

1. 显式传参：`Client(Settings(...))` 或 `Client({"timeout": 30})`
2. 环境变量：`JMCPY_` 前缀
3. 配置文件：默认 `<配置目录>/config.toml`
4. 出厂默认

`Client()` 不带参数时按上面 2 → 3 → 4 合并；`Settings.load()` 也一样。

## 字段一览

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `backend` | `Backend.CURL_CFFI` | 传输后端，可选 `httpx` |
| `impersonate` | `"chrome"` | curl-cffi 的浏览器指纹目标，如 `chrome131`、`safari` |
| `proxy` | `None` | 代理地址；`None` 时交给底层按环境变量处理 |
| `verify` | `True` | 是否校验 TLS 证书 |
| `timeout` | `20.0` | 接口请求超时（秒） |
| `image_timeout` | `60.0` | 图片下载超时（秒） |
| `retry_mode` | `RetryMode.RETRY_FIRST` | 重试与换端点的优先级，见下 |
| `retry_times` | `3` | 每个端点的重试次数（`ROTATE_FIRST` 下是轮数） |
| `backoff_base` | `0.5` | 退避基数（秒） |
| `backoff_max` | `8.0` | 单次退避上限（秒） |
| `backoff_jitter` | `0.3` | 退避抖动比例，0 表示不抖动 |
| `retry_status` | `{403,408,429,500,502,503,504,520,521,522,523,524}` | 视为临时异常、值得重试的状态码 |
| `mobile_endpoints` | 内置线路表 | 移动端接口端点池 |
| `cdn_endpoints` | 内置 CDN 列表 | 图片 CDN 端点池 |
| `web_endpoints` | 空 | 网页端域名池；留空时运行时从发布页发现 |
| `auto_update_endpoints` | `True` | 是否向端点源/发布页拉取最新端点 |
| `endpoint_ttl` | `43200` | 端点缓存有效期（秒） |
| `mobile_version` | `"2.1.9"` | 接口版本，参与 `tokenparam` |
| `auto_update_mobile_version` | `True` | 是否用 `/setting` 返回的版本号自动更新 |
| `user_agent` | `None` | 覆盖默认 User-Agent；`None` 表示按用途取内置值 |
| `headers` | `{}` | 追加到每个请求的请求头；名字与值只能是 latin-1 能表示的字符，非法取值会在构造请求时抛 `ConfigurationError` |
| `cookies` | `{}` | 会话初始 Cookie |
| `concurrency` | `8` | 图片下载并发数 |
| `home` | `None` | 覆盖配置/缓存根目录；`None` 表示平台标准位置 |
| `session_path` | `None` | 覆盖会话文件路径；`None` 表示 `<home>/session.json` |
| `use_keyring` | `True` | 是否用操作系统钥匙串保管会话主密钥 |
| `restore_session` | `True` | 构造客户端时是否自动恢复登录态 |
| `auto_route` | `True` | 门面是否允许把副分类搜索自动路由到网页端 |

非法取值（如 `timeout=0`、`concurrency=0`、`backoff_jitter=1`）会在构造时抛
`ConfigurationError`，不会拖到请求时才炸。

## 环境变量

| 变量 | 字段 |
| --- | --- |
| `JMCPY_BACKEND` | `backend` |
| `JMCPY_IMPERSONATE` | `impersonate` |
| `JMCPY_PROXY` | `proxy` |
| `JMCPY_VERIFY` | `verify` |
| `JMCPY_TIMEOUT` | `timeout` |
| `JMCPY_IMAGE_TIMEOUT` | `image_timeout` |
| `JMCPY_RETRY_MODE` | `retry_mode` |
| `JMCPY_RETRY_TIMES` | `retry_times` |
| `JMCPY_BACKOFF_BASE` | `backoff_base` |
| `JMCPY_BACKOFF_MAX` | `backoff_max` |
| `JMCPY_BACKOFF_JITTER` | `backoff_jitter` |
| `JMCPY_MOBILE_ENDPOINTS` | `mobile_endpoints`（逗号分隔） |
| `JMCPY_CDN_ENDPOINTS` | `cdn_endpoints`（逗号分隔） |
| `JMCPY_WEB_ENDPOINTS` | `web_endpoints`（逗号分隔） |
| `JMCPY_AUTO_UPDATE_ENDPOINTS` | `auto_update_endpoints` |
| `JMCPY_ENDPOINT_TTL` | `endpoint_ttl` |
| `JMCPY_MOBILE_VERSION` | `mobile_version` |
| `JMCPY_AUTO_UPDATE_MOBILE_VERSION` | `auto_update_mobile_version` |
| `JMCPY_USER_AGENT` | `user_agent` |
| `JMCPY_COOKIES` | `cookies`（`k=v; k2=v2`） |
| `JMCPY_CONCURRENCY` | `concurrency` |
| `JMCPY_HOME` | `home` |
| `JMCPY_SESSION_PATH` | `session_path` |
| `JMCPY_USE_KEYRING` | `use_keyring` |
| `JMCPY_RESTORE_SESSION` | `restore_session` |
| `JMCPY_AUTO_ROUTE` | `auto_route` |

布尔量接受 `1/true/yes/on`（真）与 `0/false/no/off`（假）。

## 配置文件

默认位置 `<配置目录>/config.toml`：

| 平台 | 配置目录 |
| --- | --- |
| Linux | `~/.config/jmcpy` |
| macOS | `~/Library/Application Support/jmcpy` |
| Windows | `%LOCALAPPDATA%\jmcpy` |

```toml
# 顶层直接写字段
timeout = 30
retry_mode = "rotate_first"
mobile_endpoints = ["www.cdngwc.cc", "www.cdngwk.net"]
cookies = { AVS = "你的会话令牌" }
```

也可以放在 `[jmcpy]` 表里：

```toml
[jmcpy]
timeout = 30
```

未知字段会直接报错（避免拼错配置后静默失效）：

```python
Settings.from_file("config.toml")
# ConfigurationError: 未知配置项: timout
```

## 重试与多端点

```python
from jmcpy import Client, RetryMode, Settings

# 默认：同一个端点退避重试 3 次，不行再换下一个端点
Client(Settings(retry_mode=RetryMode.RETRY_FIRST, retry_times=3, mobile_endpoints=("a", "b")))

# 更快绕开单点故障：每轮把全部端点各试一次，整轮失败后退避再轮
Client(Settings(retry_mode=RetryMode.ROTATE_FIRST, retry_times=2, mobile_endpoints=("a", "b")))
```

退避时长是 `min(backoff_max, backoff_base * 2 ** (第几次 - 1))`，再乘以
`1 ± backoff_jitter` 的随机系数。第一次尝试不等待。

想完全接管线路，就把端点池写死并关掉自动更新：

```python
Client(Settings(mobile_endpoints=("www.cdngwc.cc",), auto_update_endpoints=False))
```

## 端点自动发现

默认开启。发现顺序：

1. 内置线路表（`mobile_endpoints` / `cdn_endpoints`）
2. 端点源：服务端下发的线路表（解密后取 `jm3_Server` 与 `Server`）
3. 网页发布页：页面上列出的域名（写入 `web_endpoints`）
4. 你的显式配置始终优先

结果按 `endpoint_ttl` 缓存在 `<缓存目录>/endpoints.json`。任何一步失败都只记一条
warning 并继续用下一优先级，不影响请求。

```python
with Client() as client:
    endpoints = client.refresh_endpoints(refresh=True)  # 忽略缓存强制刷新
    print(endpoints.mobile, endpoints.web)
```

## 日志

库内使用标准 `logging`，包级 logger 挂了 `NullHandler`，默认没有任何输出：

```python
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
```

降级行为（端点源不可用、钥匙串不可用、会话文件损坏等）都以 warning 形式出现在
`jmcpy.*` 的 logger 上。
