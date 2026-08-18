# 分支策略

Siming 使用轻量化的 `main + develop` 开发流程。

## 长期分支

### `main`

稳定发布线。

- 只接收已经完成发布验收的 `release/*`，以及必要的 `hotfix/*`。
- 不在 `main` 上直接进行日常功能开发。
- 任意进入 `main` 的提交都应满足正式发布质量门槛。
- 正式版本以 `vX.Y.Z` tag 标记；tag 永久保留。

### `develop`

下一版本的日常集成线。

- 普通功能、修复、重构默认从 `develop` 创建临时分支。
- 普通 PR 默认合并回 `develop`。
- Android、PC、Gateway、Agent、同步和上下文等跨端改动先在这里集成并通过 CI。

## 临时分支

### `feature/*`

新功能，例如：

```text
feature/android-world-relation-editor
feature/context-manifest-audit
```

从 `develop` 创建，完成后 PR 到 `develop`，合并后删除。

### `fix/*`

普通缺陷修复，例如：

```text
fix/android-chapter-order
fix/gateway-conflict-replay
```

从 `develop` 创建，完成后 PR 到 `develop`，合并后删除。

### `refactor/*`

非功能性重构，从 `develop` 创建并合回 `develop`，合并后删除。

### `release/X.Y.Z`

版本冻结后从 `develop` 创建。

发布分支只允许：

- 版本号更新；
- Release Notes；
- 发布门禁所需调整；
- 明确的 release blocker 修复。

完成完整 CI 与 Release Preflight 后：

```text
develop
   ↓
release/X.Y.Z
   ↓
 main
   ↓
vX.Y.Z
```

发布完成后删除 `release/X.Y.Z`，保留 tag，并将 `main` 的最终发布状态同步回 `develop`。

### `hotfix/*`

仅用于已发布版本上的紧急生产问题。

从 `main` 创建，验证后合回 `main` 并发布补丁版本；随后把同一修复同步回 `develop`。

## 合并约定

- 功能/普通修复：`feature|fix|refactor → develop`
- 版本发布：`release → main`
- 紧急修复：`hotfix → main`，随后同步 `develop`
- 临时分支合并或废弃后立即删除。
- 不保留已经合并的 `feature/*`、`fix/*`、`release/*` 等历史分支；历史由 PR、commit 和 tag 保存。

## 发布约定

正式发布前至少确认：

1. `develop` 集成 CI 全绿；
2. 从 `develop` 创建干净的 `release/X.Y.Z`；
3. 三端版本号一致；
4. Release Preflight 和完整发布门禁通过；
5. `release/X.Y.Z` 合入 `main`；
6. `vX.Y.Z` 精确指向发布提交；
7. Windows 安装包、Android APK 和 SHA-256 资产通过验证；
8. 发布后删除 release 分支，并同步 `main → develop`。
