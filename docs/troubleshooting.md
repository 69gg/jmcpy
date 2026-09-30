# 疑难排查

先打开日志，绝大多数降级行为都会以 warning 的形式出现：

```python
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
```

## 请求全部失败（`RequestFailed`）

异常里带着每一次尝试：

```python
try:
    client.get_book(1)
except RequestFailed as exc:
    for item in exc.failures:
        print(item.endpoint, item.attempt, item.error)
```

常见原因：

| 现象 | 处理 |
| --- | --- |
| 所有端点都是超时 | 检查网络或设代理 `JMCPY_PROXY`；也可以调大 `timeout` |
| 部分端点连不上 | 正常现象，重试会自动换下一个端点 |
| 端点全部失效 | `client.refresh_endpoints(refresh=True)` 重新发现线路 |
| 内网/离线环境 | 写死可用端点并关掉自动更新：`Settings(mobile_endpoints=("...",), auto_update_endpoints=False)` |

## `ChallengeBlocked`

撞上了站点的反爬验证页（"Just a moment..."）。**被拦的只是那一条线路**：SDK 会立刻换下一条
域名继续，只有所有线路都被拦时才会把这个异常抛出来（异常信息里带着最后一条线路的地址）。

- 用代理换出口 IP；
- 网页端可以显式配置在你能访问的域名上，跳过自动发现：

```python
from jmcpy import Settings, WebClient

WebClient(Settings(web_endpoints=("可用域名",)))
```

- 站点要求浏览器验证时，可以自己注入浏览器里已通过验证的 Cookie：

```python
client.set_cookies({"cf_clearance": "从浏览器里复制"})
```

- 如果只是想要搜索/详情/评论，改用移动端接口即可（门面默认就走移动端，
  只有副分类搜索会落到网页端）。

## `RegionBlocked`

当前出口 IP 的地区被拒绝访问。换网络或使用代理。

## `403` / `403` 但内容是 `Restricted Access!`

同上；如果响应体是短错误页，SDK 会把它识别成地区封锁而不是普通状态码错误。

## 图片下不下来 / 下载下来是花的

- **花屏**：说明分块还原没生效。正常流程会自动取阈值，若 `/chapter_view_template`
  被中间层改写导致取不到，SDK 会退回默认阈值 220980。可以直接指定阈值验证：

  ```python
  from dataclasses import replace

  fixed = replace(chapter, scramble_id=268850)
  client.download(fixed, output=ExportFormat.PATH, dest="./out")
  ```

- **动图**：GIF 不做还原，这是服务端行为（原图未切块）。
- **空响应**：SDK 会自动带时间戳重试一次；仍失败会记进 `failures`。

## 会话相关

| 现象 | 说明 |
| --- | --- |
| 日志出现「钥匙串不可用…明文文件保存」 | 当前环境没有可用的系统钥匙串（常见于无桌面会话的服务器）。装 `jmcpy[keyring]` 或设置 `use_keyring=False` 明确接受明文；也可以干脆不保存会话（`login(..., remember=False)`） |
| 换了机器后登录态没了 | 主密钥在旧机器的钥匙串里，无法解密，属预期；重新登录即可 |
| `CredentialError: 会话文件写入失败` | 检查 `<配置目录>` 是否可写；可以用 `JMCPY_HOME` 指到有写权限的位置 |
| 不想自动恢复登录态 | `Settings(restore_session=False)` |

## 端点自动更新失败

日志里会有「端点源不可用」「发布页不可用」。这只是降级——继续使用内置线路表或上一次
的缓存，不影响请求。要完全掌控线路：

```python
Settings(mobile_endpoints=(...), cdn_endpoints=(...), auto_update_endpoints=False)
```

## 想确认到底连的是哪个端点

```python
with Client() as client:
    client.search("关键词")
    print(client.endpoints)  # 当前端点池
    print(client.mobile.version)  # 当前接口版本（可能被 /setting 更新过）
```

## 参数报错

- `InvalidArgument: 无法从 'xxx' 解析出车号`：车号只接受 `int`、`"1114751"`、
  `"JM1114751"`、`/album/1114751`、`/photo/1114751`、`?id=1114751` 这几种写法。
- `ConfigurationError: 副分类必须配合大分类一起使用`：`sub_genre` 必须与 `genre` 同时给。
- `ConfigurationError: 副分类搜索只有网页端接口支持，而当前已关闭自动路由`：
  打开 `auto_route`，或改用 `client.web.search(...)`。
- `ConfigurationError: path 输出需要指定 dest`：`PATH` 与 `PDF` 必须显式给保存目录。
- `ConfigurationError: 没有可用的网页端域名`：显式配置 `web_endpoints`，
  或在能访问网页端的网络环境里重试。

## 网页端为什么拿不到作者、标签？

网页端的列表解析只依赖页面上最稳定的结构（指向本子的链接 + 标题），其余字段没有建模，
因为页面结构可能随时变化。需要完整字段时用移动端实现：

```python
book = client.get_book(1114751)  # 门面默认就是移动端
print(book.authors, book.tags, book.views)
```
