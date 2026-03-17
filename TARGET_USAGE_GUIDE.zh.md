# CCB 多实例 Target 模式使用指南

这份指南面向最终用户，介绍如何在同一个项目里同时启动多个 `provider@instance` target，并在运行中按 target 进行增删、探活、重建与任务路由。

## 1. target 语法

CCB 多实例模式统一使用：

```text
provider@instance
```

常见例子：

- `codex@1`
- `codex@2`
- `claude@main`
- `opencode@main`
- `cursor@exp`

说明：

- `provider` 是后端类型，例如 `codex`、`claude`、`opencode`、`gemini`、`droid`、`cursor`
- `instance` 是你给该实例起的名字，例如 `1`、`2`、`main`、`exp`
- 启动和手工路由时都建议始终显式写出 `@instance`
- 迁移窗口内，旧的 provider-only 配置仍可能被自动归一化到 `@main`，但新写法应统一使用 target 形式

## 2. anchor 规则：最后一个 target 是当前 pane

启动时，**最后一个 target 就是 anchor**，它会运行在你当前所在的 pane。

例如：

```bash
ccb codex@1 codex@2 claude@main
```

这里：

- `codex@1` 和 `codex@2` 会被放到额外 pane
- `claude@main` 是最后一个 target
- 所以 `claude@main` 就是 anchor，运行在当前 pane

再比如：

```bash
ccb codex@1 codex@2 codex@3 codex@4
```

这里 `codex@4` 是 anchor，也就是当前 pane 中真正启动的实例。

> 记忆口诀：**谁写在最后，谁占当前 pane。**

## 3. 典型启动方式

### 3.1 四个同 provider 实例

```bash
ccb codex@1 codex@2 codex@3 codex@4
```

语义：

- 同时启动四个 Codex 实例
- `codex@4` 是 anchor
- 适合把多个 Codex worker 并排开起来做并行协作

### 3.2 混合 provider 启动

```bash
ccb codex@1 codex@2 claude@main
```

语义：

- 启动两个 Codex worker
- 当前 pane 留给 `claude@main`
- 适合让 Claude 做 anchor / 主 agent，Codex 做并行子 agent

### 3.3 逗号形式

```bash
ccb codex@1,codex@2,claude@main
```

语义与空格写法相同，只是参数写成一段。

### 3.4 从配置启动

```bash
ccb
```

如果当前项目或全局 `ccb.config` 已配置 target 列表，直接 `ccb` 就会按配置启动。

## 4. 运行中增删 target

### 4.1 新增一个 target

```bash
ccb add codex@3
```

语义：

- 在当前项目的现有 CCB session 中，新增一个 `codex@3`
- 不会重启整个 session
- 适合你发现 worker 不够时临时扩容

### 4.2 移除一个 target

```bash
ccb rm codex@2
```

语义：

- 只移除指定 target
- 不影响其他 target
- 适合单独下线某个实例

注意：

- `ccb add` / `ccb rm` 是 **target 粒度** 命令
- 它们不是 provider/all 粒度命令
- live `rm` 不允许直接删除当前 anchor；想换 anchor，请重启一组新的 target 布局

## 5. kill / ping / autonew 的粒度

### 5.1 `ccb kill`

#### target 粒度

```bash
ccb kill codex@2
```

只杀掉 `codex@2`。

#### provider 粒度

```bash
ccb kill --provider codex
```

杀掉当前项目中所有 Codex target，例如 `codex@1`、`codex@2`、`codex@4`。

#### all 粒度

```bash
ccb kill
```

杀掉当前项目中所有活跃 target。

### 5.2 `ccb-ping`

#### target 粒度

```bash
ccb-ping codex@1
```

只检查一个 target。

#### provider 粒度

```bash
ccb-ping --provider codex
```

检查当前项目里全部 Codex target，并输出汇总结果。

#### all 粒度

```bash
ccb-ping
```

检查当前项目中所有 target。

### 5.3 `autonew`

`autonew` 用于重置运行中的 session / worker，上下文和控制逻辑按 target 隔离。

#### target 粒度

```bash
autonew codex@1
```

只重建 `codex@1`，不碰 `codex@2`、`claude@main` 等其他 target。

#### provider 粒度

```bash
autonew codex
```

重建当前项目中的全部 Codex target。

#### all 粒度

```bash
autonew
```

重建当前项目中的全部 target。

## 6. `ccb.config` 配置示例

CCB 会优先读取：

- `.ccb/ccb.config`（项目级）
- `~/.ccb/ccb.config`（全局）

### 6.1 纯文本配置

```text
codex@1,codex@2,claude@main
```

### 6.2 带 `cmd` pane 的纯文本配置

```text
codex@1,codex@2,claude@main,cmd
```

说明：

- `cmd` 会占用一个额外 pane
- `cmd` 不改变 anchor 规则
- 仍然是**最后一个 target**决定当前 pane 运行谁

### 6.3 JSON 配置

```json
{
  "targets": ["codex@1", "codex@2", "claude@main"],
  "cmd": {
    "enabled": true,
    "title": "CCB-Cmd",
    "start_cmd": "bash"
  },
  "flags": {
    "auto": false,
    "resume": false
  }
}
```

## 7. live control plane 优先，失败时 fallback

对运行中 session 的操作，例如：

- `ccb add codex@3`
- `ccb rm codex@2`
- 部分 target 状态刷新与查询

CCB 会优先走 **live control plane**。

这意味着：

- 如果当前 session 的 live control socket 可达，CCB 会优先通过它操作正在运行的 orchestrator
- 这样能拿到最新的活跃 target、pane 绑定和 parent / anchor 状态

如果 live control plane 不可达，CCB 会 **fallback** 到持久化状态，例如：

- persisted target session
- registry / control-plane backing file

对最终用户的理解可以简单一点：

> **优先连活的 orchestrator；连不上时，再根据落盘状态恢复和处理。**

## 8. 向指定 target 发任务

多实例模式下，**最重要的使用习惯**就是：

- 对单个实例发任务时，优先写完整 target
- 不要在多实例场景里长期依赖裸 provider 名称

### 8.1 用统一命令 `ask`

`ask` 的统一入口语法是：

```bash
ask <provider|provider@instance> [options] <message>
```

推荐写法：

```bash
CCB_CALLER=claude ask codex@2 --foreground "帮我只检查这个仓库里的测试失败原因"
CCB_CALLER=claude ask codex@2 --background "并行做一个重构草案"
```

说明：

- 在多实例场景里，推荐直接写 `ask codex@2 ...`
- **显式 target 会把请求直接路由到该 target，不会把原始消息转发给 anchor target**
- 如果你写的是裸 provider，例如 `ask codex ...`，CCB 可能会从 `CCB_TARGET` 或 `CCB_CALLER_TARGET` 推断目标实例；这对自动编排有用，但对手工调用不够直观
- 因此，**手工调用时不推荐写 `ask codex ...`，而推荐写 `ask codex@2 ...`**

### 8.2 `CCB_CALLER` 与 completion hook

`ask` 是编排入口，外部 shell 手工调用时应显式设置 `CCB_CALLER`：

```bash
CCB_CALLER=claude ask codex@2 --foreground "总结一下刚才的实现差异"
```

完成后，CCB 会根据调用链把 completion hook 回通知到对应 caller；多实例下还会带上 target 信息，例如：

- `CCB_TARGET`
- `CCB_PROVIDER`
- `CCB_INSTANCE`
- `CCB_CALLER_TARGET`

这意味着：

- 原始请求不会自动串到 anchor
- 但**任务完成后的通知**可以按 `caller` / `caller_target` 回到正确的发起端

如果你希望**既不串消息，也不回完成通知**，可以直接关闭 hook：

```bash
CCB_COMPLETION_HOOK_ENABLED=0 CCB_CALLER=claude ask codex@2 --foreground "只在 codex@2 执行，不要回通知"
```

### 8.3 `--notify` 的当前行为

```bash
CCB_CALLER=claude ask codex@2 --notify "任务完成，请继续下一步"
```

说明：

- `--notify` 适合短消息同步通知
- 当前实现下，`--notify` 还没有完全走统一 askd fire-and-forget RPC
- 必要时会回退到 legacy provider daemon 路径

### 8.4 用 provider 专用入口 `cask/gask/oask/dask/lask/uask`

如果你想直接调用某个 provider 的专用 ask 命令，target 要通过 `--target` 传入：

```bash
cask --target codex@2 "只处理 tests/ 目录"
gask --target gemini@main "给我一个替代方案"
oask --target opencode@main "做一个实现草案"
dask --target droid@main "检查 Android 构建错误"
lask --target claude@main "总结一下 worker 输出"
uask --target cursor@exp "检查 IDE 侧上下文"
```

注意：

- 这些命令不是 `cask codex@2 ...` 这种写法
- **target 必须通过 `--target` 指定**
- 统一入口 `ask` 更适合跨 provider 的统一心智；专用入口更适合你明确知道要直连哪个 provider

## 9. 查看回复与挂载状态

### 9.1 `pend` 是 provider 级视图，不是 target 级视图

统一回复查看命令仍然是：

```bash
pend codex 5
pend claude 3
```

这里的含义是：

- 查看某个 provider 最近的回复
- **不是** 查看某个精确 target 的专属回复视图

因此目前不要这样写：

```bash
pend codex@2
```

在当前 CLI 里，`pend` 仍是 **provider 粒度**，不是 **target 粒度**。

### 9.2 provider 专用 `pend`

各 provider 的专用查看命令仍然可用：

```bash
cpend 5
gpend 3
opend
lpend 3
upend 2
```

补充说明：

- `cpend` / `dpend` / `lpend` / `upend` 支持 `--raw`
- `cpend` / `gpend` / `opend` / `dpend` / `lpend` / `upend` 都支持 `--session-file`
- `opend` 当前更偏向直接读取 OpenCode 当前会话，不提供 `N` 参数
- 即便使用 provider 专用 `pend`，当前也仍然是**按 provider 解析当前可见会话**，不是统一的 target 选择器

如果你追求**绝对精确的 target 视角**，当前最稳妥的做法仍然是：

- 直接切到该 target 所在 pane 查看
- 或结合 provider 自身日志 / session 文件定位

### 9.3 `ccb-mounted`：看哪些 target 真的挂载成功

`ccb-mounted` 的语法是：

```bash
ccb-mounted [--json|--simple] [--autostart] [path]
```

常见用法：

```bash
ccb-mounted --json
ccb-mounted --simple
ccb-mounted --autostart
ccb-mounted /path/to/project
```

说明：

- 默认输出 JSON
- `--simple` 输出空格分隔的 mounted target 列表
- `--autostart` 会先尝试拉起离线 provider daemon，再做探测
- `[path]` 可以用来检查其他项目目录

JSON 输出里最重要的字段：

- `cwd`: 当前检查的项目目录
- `mounted`: 已挂载且健康的 target 列表
- `mounted_providers`: 这些 target 对应的 provider 去重列表

你可以把它理解为：

> **不只是“配置里写了什么”，而是“当前真的挂上并且探活通过了什么”。**

## 10. tmux pane 布局注意点

多实例模式下，布局有几个需要记住的点。

### 10.1 当前 pane 永远属于 anchor

也就是：

- 最后一个 target
- 运行在当前 pane
- 是你最直接交互的实例

### 10.2 额外 pane 的排布不一定和输入顺序一一对应

为了保持布局稳定，CCB 在放置非 anchor target 时，可能按内部顺序创建 pane。

因此：

- **anchor 语义严格按输入顺序决定**
- **非 anchor 的创建顺序可能做过布局调整**

如果你只关心“谁在当前 pane”，只要记住“最后一个 target 是 anchor”即可。

### 10.3 layout rule

当前 README 中的布局规则可以概括为：

- 当前 pane = 最后一个 target
- 额外 pane 按 `[cmd?, reversed targets[:-1]]` 参与布局
- 第一个额外 pane 通常先放到右上
- 后续 pane 再按左右列向下填充

因此：

- `ccb codex@1 codex@2 codex@3 codex@4` 中，`codex@4` 一定是当前 pane
- 其余实例在侧边 pane 的相对位置，应该以实际 tmux 布局为准，而不是只靠命令行顺序脑补

## 11. 推荐使用习惯

### 主 agent + worker

```bash
ccb codex@1 codex@2 claude@main
```

适合：

- `claude@main` 做主控 / anchor
- `codex@1`、`codex@2` 做并行执行

### 纯 worker 池

```bash
ccb codex@1 codex@2 codex@3 codex@4
```

适合：

- 同 provider 多 worker 并发
- 需要多个 Codex 同时处理不同子任务

### 稳定项目默认配置

把常用 target 写进 `.ccb/ccb.config`：

```text
codex@1,codex@2,claude@main,cmd
```

这样进入项目后直接执行：

```bash
ccb
```

即可恢复标准布局。

## 12. 一页速查

```bash
# 启动
ccb codex@1 codex@2 codex@3 codex@4
ccb codex@1 codex@2 claude@main

# 运行中扩容 / 缩容
ccb add codex@3
ccb rm codex@2

# kill
ccb kill codex@2
ccb kill --provider codex
ccb kill

# ping
ccb-ping codex@1
ccb-ping --provider codex
ccb-ping

# autonew
autonew codex@1
autonew codex
autonew

# 把任务发到指定 target
CCB_CALLER=claude ask codex@2 --foreground "检查失败测试"
CCB_CALLER=claude ask codex@2 --background "并行起草方案"
CCB_COMPLETION_HOOK_ENABLED=0 CCB_CALLER=claude ask codex@2 --foreground "只在 codex@2 执行"

# provider 专用 ask
cask --target codex@2 "只处理 tests/ 目录"
lask --target claude@main "总结 worker 输出"
uask --target cursor@exp "检查 IDE 上下文"

# 回复查看
pend codex 5
cpend 5
lpend 3
upend 2

# 挂载状态
ccb-mounted --json
ccb-mounted --simple
ccb-mounted --autostart
```

如果你只记住五件事：

1. **统一写 `provider@instance`**
2. **最后一个 target 是 anchor**
3. **手工发任务时优先显式写 target，不要裸写 provider**
4. **`pend` 目前还是 provider 级，不是 target 级**
5. **`ccb-mounted` 看的是“真的挂载且健康”的 target**

就足够稳定使用多实例 target 模式。
