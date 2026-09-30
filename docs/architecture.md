# 架构

## 分层

```
clients/            对外接口
├── facade.py       Client / AsyncClient：门面 + 能力路由
├── mobile.py       MobileClient / AsyncMobileClient：移动端接口
└── web.py          WebClient / AsyncWebClient：网页端接口
        │
parsing/            纯函数：响应 → 模型（无 I/O，同步异步共用）
├── mobile.py       JSON（解密后）→ Book / Chapter / Listing / CommentFeed / Account
└── web.py          HTML → Listing（自研 HTMLParser 结构化提取）
        │
transport/          HTTP
├── response.py     HttpRequest / Reply 统一表示
├── backends.py     curl-cffi（默认）与 httpx 的同步/异步后端
├── retry.py        RetryPlanner：重试与换端点的**纯逻辑**决策
└── session.py      HttpSession / AsyncHttpSession：后端 + 决策器 + 响应分诊
        │
endpoints.py        端点池：内置 → 端点源 → 发布页 → 用户配置，带 TTL 缓存
crypto.py           请求签名、载荷封解、端点源解密
imaging.py          分块还原、格式识别、编码、PDF 合成
exporting.py        下载编排：并发取图 → 还原 → 按格式交付
credentials.py      会话文件读写（加密 + 钥匙串）
settings.py         配置
```

依赖方向自上而下，没有反向依赖；`parsing` 与 `retry` 不含 I/O，因此可以完全离线测试。

## 两种客户端实现

| | `MobileClient` | `WebClient` |
| --- | --- | --- |
| 协议 | 移动端接口：请求头签名 + 响应 AES 解密 | 网页端：HTML 页面 |
| 字段完整度 | 高（结构化 JSON：标签、章节、相关推荐、评论…） | 列表级（车号、标题、作者、分类页的标签） |
| 搜索维度 | 站内/作品/作者/标签/登场角色 + 排序 + 时间范围 + 大分类 | 同上 + **副分类** |
| 适用网络 | 一般都可访问 | 需要能通过站点的反爬验证；不同线路的可用性差别很大 |
| 详情/评论 | 支持 | 不支持（由移动端覆盖） |

门面只在**一个**能力上做路由：`sub_genre`。其余情况一律走移动端，
因为它的字段更全、网络要求更低。`auto_route=False` 可以关掉路由，此时传入
`sub_genre` 会直接报错而不是被静默忽略。

## 重试与多端点

`RetryPlanner` 只做决策，不做 I/O：它按配置生成「第几次尝试 → 哪个端点 → 等多久」
的序列，`HttpSession` 负责执行与登记失败。两种模式：

- `RETRY_FIRST`：逐端点推进，每个端点连续尝试 `retry_times + 1` 次再换下一个；
- `ROTATE_FIRST`：按轮推进，每轮把全部端点各试一次。

退避只与「这是第几次尝试」有关，因此同一份配置在任何调用下都产生同样的节奏
（抖动除外，抖动使用可注入的随机源，测试里固定种子）。

`HttpSession` 的分诊规则：

| 情况 | 处理 |
| --- | --- |
| 反爬验证页 / 地区封锁页 | `ChallengeBlocked` / `RegionBlocked`：**这条线路不可用，立刻换下一条**（不在同一线上重试、也不等待）；全部线路都如此才抛异常 |
| 配置里列为临时的状态码 | `ResponseInvalid`，可重试 |
| 其余 4xx/5xx | `BadStatus`，立即失败 |
| 链路异常 | `NetworkIssue`，可重试 |
| 内容校验失败（自定义 validator） | 抛 `ResponseInvalid` 可重试，抛其它异常立即失败 |
| 全部尝试失败 | `RequestFailed`，`failures` 里有每次尝试的端点、序号与异常 |

图片走同一套机制，区别是端点池换成 CDN 列表、URL 用 `{endpoint}` 占位符按端点重建。

会话还会**记住线路**：上一次成功的端点下次优先，最近失败的端点排到最后。网页端各线路的
可用性差异很大（有的不可达、有的被验证页拦截），没有这个记忆的话每个请求都要重新踩一遍。
显式传入 `endpoints=` 的调用（例如图片的 CDN 轮换）不受记忆影响，顺序完全由调用方决定。

## 端点发现

```
内置线路表 ──┐
端点源(JSON) ─┼─→ 合并（先到先得）──→ TTL 缓存(<缓存目录>/endpoints.json) ──→ 会话端点池
发布页(HTML) ─┘
```

任何一步失败都只是降级：记一条 warning，继续用下一优先级。缓存损坏同样只当作没有缓存。
用户显式配置的端点始终优先于发现结果。

## 会话与凭据

```
login() ──→ AVS 写入 Cookie ──→ LoginSession 快照 ──→ Fernet 加密 ──→ session.json
                                                    密钥 ──→ 操作系统钥匙串
```

钥匙串不可用（无桌面会话、未安装 `keyring`）时降级为 `0600` 权限的明文文件并给出
warning；文件损坏、版本不符、密钥更换、缺少密钥都只当作「没有登录」，绝不让客户端
构造失败。写盘用「临时文件 + 原子替换」。

## 为什么解析层是纯函数

同一份解析逻辑被三种调用方式复用：同步客户端、异步客户端、离线测试。
把 I/O 挡在外面后：

- 同步与异步的差异被压缩到「谁去发请求」，不会出现两份解析逻辑逐渐走样；
- 夹具可以直接喂进解析函数，断言模型字段，不需要起 HTTP 桩；
- 解析结果与网络无关，回归测试跑得飞快（整套离线用例不到 1 秒）。
