# 发布 dumplingsAI 到 PyPI

本仓库使用 **GitHub Actions + PyPI Trusted Publishing** 实现全自动发布：
只需 `git push` 一个 tag，剩下的（build → publish → 创建 GitHub Release）全部由 CI 完成。

工作流文件：[`.github/workflows/python-publish.yml`](.github/workflows/python-publish.yml)

---

## 首次发布流程

```bash
# 1. 确认 main 分支上的版本号和 CHANGELOG 已同步更新
# 2. 编辑子模块 Dumplings/pyproject.toml，把 version 字段改成要发布的版本
# 3. 在 CHANGELOG.md 里把 [Unreleased] 段合并到新版本段
# 4. 提交并推送到 main（CI 会跑测试 + lint）
git add Dumplings/pyproject.toml Dumplings/CHANGELOG.md
git commit -m "chore: bump version to 0.2.1"
git push origin main

# 5. 在子模块里打 tag 并推送 —— 触发自动发布
cd Dumplings
git tag v0.2.1
git push origin v0.2.1
```

push tag 后，GitHub Actions 会自动：
1. 在 `ubuntu-latest` 上用 `uv` 构建 sdist 和 wheel
2. 通过 **Trusted Publishing (OIDC)** 发布到 PyPI（无需 API token）
3. 创建一个 GitHub Release，附带构建产物和自动生成的 release notes

可以在 https://github.com/Secret-Dumplings/dumplingsAI/actions 看到进度。

---

## 日常发布

### 正式版

```bash
cd Dumplings
# 1. 修改 pyproject.toml 的 version 字段
# 2. 更新 CHANGELOG.md
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to 0.X.Y"
git push origin main
git tag v0.X.Y
git push origin v0.X.Y
```

### 预发布版本（rc / post）

workflow 已支持以下 tag 格式：

| Tag | 说明 | PyPI 上对应的版本 |
|---|---|---|
| `v0.2.1` | 正式版 | `0.2.1` |
| `v0.2.1rc1` | 预发布 | `0.2.1rc1` |
| `v0.2.1.post1` | 后置修订 | `0.2.1.post1` |

---

## 手动验证打包（不发 PyPI）

有时候只想验证打包过程能否跑通，可以手动触发 workflow：

1. 进入 https://github.com/Secret-Dumplings/dumplingsAI/actions/workflows/python-publish.yml
2. 点击 **Run workflow**
3. **dry_run** 默认勾上 —— 此时只会 build + 上传 artifact，不会发 PyPI、不会创建 Release
4. 如果想用手动模式真正发到 PyPI，把 dry_run 取消勾选即可（不推荐，仅在 tag 触发失败时备用）

---

## 并发保护

workflow 使用 `concurrency: group: publish-${{ github.ref }}` 保证同一个 tag 不会并发跑两次；
如果误打了同一个 tag 两次，第二次会等第一次跑完才执行（不会丢失）。

---

## 常见问题

### 1. Trusted Publishing 失败：`403 Forbidden`
- 检查 PyPI 上登记的 Repository name 是否是 `dumplingsAI`（不是 `Dumplings` 或其他）
- 检查 Workflow filename 是否精确等于 `python-publish.yml`
- 检查 Owner 是否是 `Secret-Dumplings`（不是组织下的其他 team）

### 2. tag 推上去但 workflow 没跑
- 检查 tag 格式是否匹配 `v\d+.\d+.\d+`（带 `v` 前缀）
- 查看 https://github.com/Secret-Dumplings/dumplingsAI/actions 页面的 "Active runs" / "All runs"

### 3. 想撤回某个发布版本
PyPI 不允许删除已发布的版本，只能 **yank**（隐藏但保留）：
```bash
pip install pypi-attestations twine
twine upload --repository pypi dist/*  # 重新上传覆盖
# 或者在 PyPI 网页上手动 yank
```
**预防胜于治疗**：tag 一旦 push，发布就是即时的，请在打 tag 前充分测试。

---

## 版本号规范

遵循 [PEP 440](https://peps.python.org/pep-0440/) + [Semantic Versioning](https://semver.org/)：

- `MAJOR.MINOR.PATCH`（如 `0.2.1`）
- `0.x.y` 阶段：API 可能有 breaking changes，每次 MINOR 升级需阅读 CHANGELOG
- `1.0.0` 及以后：MAJOR 升级代表 breaking change