# 发布流程

发布由 [.github/workflows/publish.yml](../.github/workflows/publish.yml) 完成：**推送 `v` 开头的标签**时触发。

## 版本号约定

版本号只在 [pyproject.toml](../pyproject.toml) 的 `[project].version` 里写一次。
打标签前先改版本号，标签名必须是 `v` + 该版本号（`0.1.0` → `v0.1.0`），
否则工作流会直接失败，不会发布。

```bash
# 1. 改 pyproject.toml 的 version，并在 CHANGELOG.md 顶部补上对应条目
# 2. 提交
git commit -am "chore: 发布 v0.2.0"
git push origin main

# 3. 打标签并推送（这一步才会触发发布）
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

## 工作流做了什么

| 步骤 | 说明 |
| --- | --- |
| 确认标签打在 main 上 | 标签指向的提交必须是 `main` 的祖先，否则拒绝发布（防止从随手的分支提交发版） |
| 校验版本号 | 标签去掉 `v` 后必须等于 `pyproject.toml` 里的 `version` |
| 跑离线测试 | `uv run --frozen pytest -q`，测试不过就不发布 |
| 构建 | `uv build`（wheel + sdist） |
| 发布到 PyPI | `uv publish --trusted-publishing always --check-url …`；`--check-url` 会跳过已存在的文件，重跑失败的工作流不会卡在「文件已存在」 |
| 创建 GitHub Release | `gh release create --generate-notes`，附带构建产物 |

## 需要配置什么

### 1. PyPI 受信发布（推荐，无需任何密钥）

工作流用 OIDC 向 PyPI 换取**短期**上传凭据，仓库里不保存令牌。

首次发布（项目在 PyPI 上还不存在）用 **pending publisher**：

1. 登录 PyPI → Account settings → **Publishing** → *Add a pending publisher*；
2. 按下表填（**区分大小写，且必须完全一致**）：

   | 字段 | 值 |
   | --- | --- |
   | PyPI Project Name | `jmcpy` |
   | Owner | `69gg` |
   | Repository name | `jmcpy` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. 保存。下一次推 `v*` 标签时，这个仓库的该工作流就能直接发布，并自动把项目创建出来。

项目已经在 PyPI 上之后，改在项目的 **Manage → Publishing** 里添加同样的受信发布者（首次发布后 pending publisher 会自动转成正式配置）。

### 2. GitHub 侧

- **Environment**：工作流里写了 `environment: pypi`。首次运行时 GitHub 会自动创建同名环境，不需要手动建。
  想加人工审批或限制分支，在 Settings → Environments → `pypi` 里配（配了之后发布前会等待审批）。
- **权限**：工作流已声明所需权限（`id-token: write` 用于换 PyPI 凭据，`contents: write` 用于建 Release），
  仓库的默认 GITHUB_TOKEN 权限保持只读即可，不需要改。
- 不需要在仓库里配置任何 Secret。

### 3. 不想用受信发布？（可选方案）

改用 PyPI API token 的话：

1. PyPI → Account settings → API tokens → 创建项目范围的 token；
2. 仓库 Settings → Secrets and variables → Actions → 新建 `PYPI_API_TOKEN`；
3. 把工作流里的发布步骤改成：

   ```yaml
   - name: 发布到 PyPI
     run: uv publish --token "${{ secrets.PYPI_API_TOKEN }}"
   ```

   同时可以把 `environment: pypi` 与 `id-token: write` 去掉。

## 验证

- 冒烟验证（不真正发布）：把发布步骤临时换成 `uv publish --dry-run`，
  或本地跑 `uv publish --dry-run --check-url https://pypi.org/simple/jmcpy/`。
- 发布后确认：<https://pypi.org/project/jmcpy/>，
  以及 `uv tool run --from jmcpy python -c "import jmcpy; print(jmcpy.__version__)"`。
- 工作流失败的常见原因：
  - 标签与版本号不一致 → 按上面的约定改；
  - 标签所指提交不在 `main` 上 → 把提交合并进 main 后重新打标签；
  - PyPI 报 `invalid-publisher` → 受信发布者配置里的 owner/repo/工作流文件名/环境名与实际不符。
