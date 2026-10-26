# 2026-10-26 AppWorld 无 Docker 验收

**Bubblewrap + 管道 RPC 已在 22 节点通过单任务工程验收；Docker 权限不再阻塞当前实验路线。**

## 1. 运行身份

| 项目 | 实际记录 |
|---|---|
| 最终 run ID | `rootless-p0-final-20261026-133633` |
| 运行代码 commit | `32404c4a2873dc22e5f83c6ae6f3837acd80c5ad` |
| 完成时间 | 2026-10-26 13:37:21 UTC，即北京时间 21:37:21 |
| 节点与账号 | `ssh -p 22 lyf@211.81.55.158`，普通用户 UID 21007 |
| 环境 | Linux 5.15.0-176-generic、glibc 2.35、Python 3.12.11、AppWorld 0.1.3.post1 |
| 数据 | 官方 train 任务 `07b42fd_1`，data 0.1.0，seed 100 |
| 执行后端 | 私有 Bubblewrap 0.6.1-1ubuntu0.3 + stdin/stdout JSON RPC |
| 资源 | 模型调用 0、GPU 使用 0、Docker 使用 0、sudo 使用 0 |
| 状态 | screen 作业已结束，exit code 0 |

完整方案与重跑入口见 [无 Docker 执行方案](../无Docker执行方案.md)。本记录描述当次结果，不作为动态资源余量快照。

## 2. 验收结果

| 层次 | 结果 | 内容 |
|---|---:|---|
| 项目测试 | 31/31，本地与服务器均通过 | 接口、日志边界、原生/无 Docker 工具和失败处理 |
| 执行端隔离 | 29/29 | 文件隐藏、namespace、seccomp、资源、时间冻结、超时、API deadline、跨步状态等 |
| 整体流程 | 10/10 | 正负例、重置、奖励伪造、退出后评分、输入不变、runner 桥接、源数据不变等 |

四个案例均由独立进程调用官方 evaluate_task：

| 案例 | 官方检查通过数 | 任务 success | 解读 |
|---|---:|---|---|
| 初次空操作 no_op_before | 1/5 | false | 不做目标操作不能完成任务 |
| 官方正确解 official_solution | 5/5 | true | API 代理和官方评分链路可用 |
| 重置后空操作 reset_no_op | 1/5 | false | 前次成功没有被下一次空操作继承 |
| 奖励伪造 reward_spoof | 1/5 | false | admin/私有入口和写 reward 文件被拒，伪造输出不改变评分 |

**1/5 是同一任务的五项检查中一项通过，不是“任务成功率 20%”。** 这里只有一个任务，不能估计 benchmark 成功率。

### 现有 runner 的桥接

RootlessAppWorldEnvironment 实现现有 reset/step/verify 接口。固定官方 oracle policy 通过 core run_episode 完成两步执行，生成两条内存轨迹和两条审计记录；runner 返回 success，独立官方评分为 5/5。

这证明真实 adapter 能接入已有 runner，仍没有调用学生模型。包含官方解的轨迹仅在内存用于验收，没有写入 Git 或训练文件。

## 3. 边界与状态核对

- 执行端看不到 home/.ssh、项目、原始任务数据、controller 私有 canary 或 Docker socket；没有原生 AppWorld 包和 GPU 设备。
- 六类 namespace 与宿主不同；无有效 capabilities，no-new-privileges 和 seccomp 生效；fork、exec、socket、嵌套 user namespace 请求被拒。
- 按配置检查 512 MiB 地址空间、累计 15 秒 CPU、每步 15 秒 wall time；无限循环和模拟慢 API 超时后执行端被终止，不能续跑。
- 任务公开时间被冻结，变量跨步保留，输出截断；公开类型注解解析为代理类型。
- 所有执行端停止后才导出状态；评分前后输入文件哈希一致。
- 77 个源文件哈希未变，四次初始公开 profile 哈希一致。
- 两次空操作的官方导出状态文件总哈希相同；奖励伪造与空操作相同；直接官方解与 runner oracle 相同。

状态核对仅覆盖**已导出的文件与公开 profile**，不声称全部内存状态或数据库行为等价。执行端资源限制不能外推给 controller/world/verifier 的整个作业；当前未施加整体 cgroup 配额。

## 4. 安装与失败记录

Bubblewrap 用普通用户解包到自己的 tools 目录，权限 0755，无 setuid/setgid。Ubuntu InRelease 的 gpgv 验证通过，签名 key 为 `F6ECB3762474EDA9D21B7022871920D1991BC93C`；索引、deb 和二进制逐层核验。可复现入口重新核验现有安装成功，already_installed=true，没有覆盖安装。

| 运行 | commit | 结果与处理 |
|---|---|---|
| rootless-p0-20261026-132836 | 2f13a28 | 26 项隔离检查和空操作通过；正确解的类型注解导入失败，报告保留 |
| rootless-p0-retry-20261026-133209 | 0a21e6c | ApiCollection/Requester 映射为公开代理后，27 项隔离及四案例通过；没有挂载原生 AppWorld |
| rootless-p0-final-20261026-133633 | 32404c4 | 新增慢 API deadline 和 runner 桥接后，29 项隔离与 10 项流程全部通过 |

## 5. 证据位置

本地摘要目录：[appworld-rootless](../server_snapshots/2026-10-26/appworld-rootless/)。

- [acceptance.json](../server_snapshots/2026-10-26/appworld-rootless/acceptance.json)：四案例、运行 commit、配置与六个源码文件 SHA256。
- [isolation.json](../server_snapshots/2026-10-26/appworld-rootless/isolation.json)、[runner-bridge.json](../server_snapshots/2026-10-26/appworld-rootless/runner-bridge.json)：执行端探针与 runner 桥接。
- [status.json](../server_snapshots/2026-10-26/appworld-rootless/status.json)、[launch.json](../server_snapshots/2026-10-26/appworld-rootless/launch.json)、[project-unit-tests.txt](../server_snapshots/2026-10-26/appworld-rootless/project-unit-tests.txt)：启动、退出和项目测试。
- [evidence-index.json](../server_snapshots/2026-10-26/appworld-rootless/evidence-index.json)：16 个来源文件的路径、字节数和 SHA256，包括安装及前两轮记录。目录关闭换行转换，保留原字节。

服务器根为 `/nfs-data/sdf/lyf/rsi-long-horizon`。完整报告在 `runs/rootless/rootless-p0-final-20261026-133633/acceptance.json`，启动日志在 `logs/rootless-p0-final-20261026-133633.log`。

只归档元数据、摘要、哈希和项目测试日志；数据库、官方解源码、详细评分要求、含解答的原始轨迹不进入 Git，不导出训练。原始失败输出保留在服务器，Git 中的失败摘要不含官方解。

## 6. 结论范围

当前配置下，普通用户可不用 Docker 完成受限 Python 执行、公开 API 交互、退出后快照与独立官方评分，可以在此 adapter 上接入学生 policy。

尚无学生长程能力、B0/B1、内化或 RSI 收益证据；也未覆盖其他任务族、可写挂载、网络及额外依赖。沙箱共享宿主内核，有限检查不是安全证明。P0 的真实学生 episode 仍未完成。
