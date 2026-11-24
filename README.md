# 51295903059_刘昱垚 · NGSPICE 多层次性能优化

## 研究报告

项目根目录中的 [研究报告.pdf](研究报告.pdf) 是本项目配套的研究报告。报告用于说明 NGSPICE 动态库优化实验背景、实验方法、性能结果和分析结论；可与本仓库中的 `result/`、`docs/analysis/` 及原始实验记录交叉复核。

## 一键执行：从环境检查到最终结果

解压后进入包含 `run.sh` 的包目录。在 solegpu1或者其他服务器（使用 Node 1 计算、本地内存 Node 1、远程诊断内存 Node 0）执行下面**一条命令**，即可后台运行全部流程：

```bash
nohup bash run.sh full "$HOME/ngspice-runs/formal-001" \
  --node 1 --numa --remote-node 0 \
  > "$HOME/ngspice-formal-001.log" 2>&1 < /dev/null &
```

流程顺序：环境与 NUMA/CPU 检查 → 实际 perf 采样读写检查 → TLC/Z3 正负对照 → NGSPICE 源码编译安装 → KLU/Sparse 短测 → 两种求解器独立 Profile、FDATA、BOLT 重写 → 正式求解器/BOLT 对照 → NUMA 对照与远程诊断 → 达到门槛才执行的组合实验 → 独立最终对照 → 波形验证、统计与最终结果生成。正式测量使用 C7552、20 ns、8 线程；每个对照另有预热和七个正式区组。

### 执行前注意事项

- **先进入包目录**再运行上面的相对路径命令。其他服务器应按实际拓扑修改 `--node` 与 `--remote-node`，计算节点至少允许使用 16 个逻辑 CPU，远程内存节点必须存在且与计算节点不同。CPU 和内存绑定权限也必须允许。
- **输出目录必须不存在**。重复运行时将 `formal-001` 换成新编号，并相应修改日志名；不会覆盖历史数据或从中断处自动续跑。构建输出路径须为不含空白的 ASCII 路径；HOME 不满足时改用持久数据盘的 ASCII 路径。不要在运行过程中移动包。
- **系统依赖和权限必须满足**：Python 3.11+、编译工具、Java、numactl、sysstat、兼容 perf 和采样权限。入口会尝试 perf/Z3 兼容回退，但不会自动 sudo、安装系统软件或修改内核权限。缺依赖时会停止并记录解决方法；安装命令见后文。
- 每个参与分配内存的节点至少需要 2 GiB 空闲加可回收内存；输出盘至少空闲 8 GiB，正式运行推荐预留 20 GiB 或更多。共享服务器需选择空闲资源，自动检测不保证 CPU 独占。
- 含 NUMA 对照时从 **Default 内存策略**的 shell 启动，不要再套外层 `numactl --membind/--interleave`。本命令由脚本分别控制各实验的内存策略。
- 完整执行可能持续数小时或更久。`nohup` 允许断开终端；出现错误或单次运行超时会明确失败。组合实验只有达到预设门槛才执行，按设计跳过会记录原因。不同机器、新旧批次及不同配置的数据不可合并统计。

只有一个可用内存节点，或本次排除 NUMA 对照时，使用以下单命令，固定在所选节点生成其余正式结果：

```bash
nohup bash run.sh full "$HOME/ngspice-runs/formal-fixed-001" \
  --node 1 --skip-numa \
  > "$HOME/ngspice-formal-fixed-001.log" 2>&1 < /dev/null &
```

### 查看进度、成功状态和最终文件

```bash
# 查看主日志；按 Ctrl+C 只退出日志查看，不会停止后台实验
tail -f "$HOME/ngspice-formal-001.log"

# 执行完毕后检查两层状态
cat "$HOME/ngspice-runs/formal-001/status.json"
cat "$HOME/ngspice-runs/formal-001/experiment/status.json"
```

外层状态同时为 `"status": "SUCCEEDED"`、`"mode": "full"` 才表示本次请求的完整流程成功结束；仅有 `check` 或 `smoke` 成功不算 full 通过。成功不保证性能一定提高，应如实查看新批次统计。

最终报告为 `formal-001/experiment/report/experiment.md`；逐次数据和配对统计在 `formal-001/experiment/intermediate/measurements.csv`、`pairs.csv`；原始仿真/波形/遥测在 `formal-001/experiment/result/`，Profile、FDATA 与重写二进制在 `formal-001/experiment/profile/`。环境与节点选择记录为 `formal-001/preflight.json`。失败时先保留主日志、外层状态和 preflight.json，再按后文故障回传说明处理。

本包统一使用 C7552/SKY130。运行入口会先检查环境、选择并校验 NUMA 节点、执行实际 perf 采样读写与 TLC/Z3 正负对照，通过后才构建 NGSPICE 和执行实验。路径由脚本自身位置解析，不依赖 `/home/lyy`、当前工作目录或 hg7490 的 CPU 编号。

已归档数据和新执行数据严格分开。历史最终 KLU8 + BOLT + Default 七对均值 332.74 → 329.86 s（0.86%）仅属于原服务器的原批次，不能当作新服务器上的性能保证。

## 在未知服务器上解压、检查、短测和正式执行

### 1. 解压

```bash
mkdir -p "$HOME/tmp/ngspice"
tar -xzf /path/to/51295903059_刘昱垚.tar.gz -C "$HOME/tmp/ngspice"
cd "$HOME/tmp/ngspice/51295903059_刘昱垚"
```

若同时拿到 `.sha256` 文件，可在压缩包所在目录执行 `sha256sum -c 51295903059_刘昱垚.tar.gz.sha256`。包目录可以搬迁，也可以包含中文或空格。构建输出目录要求使用**不含空白字符的 ASCII 路径**，避免上游 Autotools/Make 的路径限制；若账户 HOME 含中文，应显式指定 ASCII 数据盘路径。不要在运行过程中移动包或输出目录。

### 2. 先执行环境检查

```bash
bash run.sh check /tmp/ngspice-runs/check-001 --node auto
```

`auto` 从当前进程有权限使用的节点中，选择至少有 16 个逻辑 CPU、2 GiB 空闲加可回收内存且该内存最多的节点。它不检测或保证 CPU 独占。输出会显示节点编号、实际 CPU 集和控制 CPU。共享服务器优先由你指定空闲节点：

```bash
bash run.sh check /tmp/ngspice-runs/check-node1 --node 1
```

检查系统架构、Python、必需命令、CPU/核心/SMT 与内存权限、磁盘、编译器/OpenMP、BOLT 版本、实际 cpu-clock:u 采样和读取、Z3 与 Java/TLC。检查也会真正执行 TLC/Z3 正负对照。结果见 `preflight.json`、`formal-check.log`、`local-verification.*/` 和 `status.json`。

### 3. 在同一节点执行短测

假设预检确定使用 Node 1：

```bash
bash run.sh smoke /tmp/ngspice-runs/smoke-001 --node 1
```

兼容原调用方式：

```bash
bash run-on-node.sh 1 smoke /tmp/ngspice-runs/smoke-002
```

自动从包内源码构建安装 NGSPICE 47；C7552 0.1 ns、8 线程下分别执行 KLU/Sparse 原版、独立 Profile → FDATA → BOLT 重写，以及两种重写版的波形比较。CPU 和内存均固定在检测到的 Node 1。短测不运行正式计时或 NUMA 对照。

### 4. 在新服务器执行正式实验

输出目录必须是新的、可写的 ASCII 路径，短测通过后固定同一节点和系统条件。建议正式数据放在持久数据盘，避免 /tmp 被自动清理：

```bash
mkdir -p "$HOME/ngspice-runs"
nohup bash run.sh full "$HOME/ngspice-runs/formal-001" --node 1 \
    > "$HOME/ngspice-runs/formal-001.log" 2>&1 < /dev/null &
echo $!
```

此默认模式固定一个节点，**不执行 NUMA 对照**。执行环境检查、形式化正负对照、源码构建、短测、KLU8/Sparse8 各自 20 ns Profile 与 BOLT 重写、原版求解器七区组对照、两种求解器各自七区组 BOLT 对照、独立最终七区组，以及统计与报告。每组对照另有一次预热，正式计时与采样分开。原有 KLU8/KLU16 历史数据保留，当前自动流程不生成新的线程数对照。

需要完整 NUMA Default/本地/远程对照时，**显式加 `--numa`**：

```bash
nohup bash run.sh full "$HOME/ngspice-runs/formal-numa-001" \
    --node 1 --numa --remote-node 0 \
    > "$HOME/ngspice-runs/formal-numa-001.log" 2>&1 < /dev/null &
echo $!
```

例如 solegpu1 只有 Node 0/1，就使用本地 1、远程 0；不要求存在 Node 2。远程节点也可写 `--remote-node auto`。NUMA 模式需要两个允许分配内存的节点，从 Default 内存策略的 shell 启动。该模式保持计算 CPU 集不变，比较 Default 与本地内存，远程内存仅作三次诊断；达到预定义门槛后才执行 BOLT × NUMA 组合。所选节点和页面统计写入新批次配置，不冒充 hg7490 的 Node 0/2。

只有一个可用节点、内存绑定被限制、或远程节点内存不足时，`--numa` 会明确失败。改用 `--skip-numa` 即可执行固定节点模式；不会悄悄跳过后宣称 NUMA 实验通过。`bash run.sh` 默认执行固定节点 full 并自动选节点，默认输出在 `~/.cache/ngspice-runs/`；在共享服务器上推荐始终明确 `--node` 和输出目录。

### 5. 判断是否通过及故障回传

```bash
tail -f "$HOME/ngspice-runs/formal-001.log"
cat "$HOME/ngspice-runs/formal-001/status.json"
cat "$HOME/ngspice-runs/formal-001/experiment/status.json"
```

外层 `status.json` 只有在环境检查、形式化检查和本次请求的实验全部成功时才为 `SUCCEEDED`。`check` 的成功仅表示环境/形式化检查成功，`smoke` 的成功仅表示短测成功，都不能代替 full 的成功。失败时保留状态、`preflight.json`、主日志和 `formal-check.log`；若已开始实验，还需 `experiment/status.json`、相关 `console.log` 或 `experiment/log/` 构建日志。输出目录已有内容时直接拒绝，使用新目录重试，不覆盖原始数据。

## 自动处理与需要手动处理的环境差异

| 项目 | 自动处理 | 无法自动解决时 |
| --- | --- | --- |
| 包路径 | 根据脚本位置定位源码、模型与工具，所有子进程参数独立传递 | 构建输出路径改用 ASCII 且不含空白字符 |
| Python | 优先包内 .venv，再尝试本机 3.12/3.11/python3 和原机兼容路径；校验 >=3.11 | `export NGSPICE_PYTHON=/absolute/path/to/python` |
| NUMA/CPU | 与在线 CPU、进程允许 CPU/内存节点取交集；按物理核心优先选择 16 个逻辑 CPU，记录控制 CPU | 申请更大作业配额或换节点；不静默减少线程或跨节点凑 CPU |
| perf | 实际记录及读取 cpu-clock:u，优先随包版，失败后尝试本机版，保存实际选择和 SHA-256 | 安装匹配当前内核的 perf，或设置 NGSPICE_PERF；管理员授权采样权限 |
| LLVM BOLT | 验证可执行及版本 22.1.8，记录 SHA-256 | 设置 NGSPICE_BOLT_DIR，目录内须同时含匹配的 llvm-bolt/perf2bolt；不静默换版本 |
| Z3 | 先尝试当前 Python 中的 4.12.2，再尝试随包版本，并真正求解 | 运行 `bash prepare-host.sh --z3` 创建本地虚拟环境 |
| Java/TLC | 真正运行正负对照；在 ASCII 临时目录执行，保留日志到用户输出目录 | 安装可运行 TLC 的 Java（原机用 Java 11）；Java 21 是否兼容以实际检查为准 |
| 系统依赖 | 逐项检测并给出日志和修复提示 | 按下面命令显式安装；入口不会自行 sudo、下载或修改内核参数 |

### 系统依赖修复

Linux x86_64；Python 3.11+；gcc/g++、make、binutils、GNU time、taskset、numactl/numastat、pidstat/mpstat、Java；兼容动态库，以及允许 cpu-clock:u 的采样权限。至少 8 GiB 空闲输出空间，full 推荐 20 GiB 或更多。至少一个允许访问、具备 16 个逻辑 CPU 和 2 GiB 空闲加可回收内存的节点。只有 16 个逻辑 CPU 可用时控制进程与仿真共享一个逻辑 CPU，这一点写入预检记录。

```bash
# Ubuntu/Debian 或 RHEL 系：显式安装系统依赖，需要管理员权限和软件源
bash prepare-host.sh --system

# 随包 Z3 不兼容当前 Python/平台时：需要 Python venv 和包下载源，不需 sudo
NGSPICE_PYTHON=/usr/bin/python3 bash prepare-host.sh --z3
```

系统安装命令不会保证为定制内核找到 perf，也不会安装任意发行版缺失的 Python 3.11。若失败，请管理员提供对应版本。`--z3` 产生 `.venv/`，入口会自动优先使用；若此前已显式设置 NGSPICE_PYTHON，请更新或取消该变量。离线服务器需提前准备相应系统软件包和 `z3-solver==4.12.2.0` 的匹配 wheel。

```bash
export NGSPICE_PYTHON=/path/to/python3.11
export NGSPICE_PERF=/path/to/host-compatible/perf
export NGSPICE_BOLT_DIR=/path/to/bolt-22.1.8/bin
bash run.sh check /tmp/ngspice-runs/recheck-001 --node 1
```

显式指定的 Python/perf/BOLT 不可用时直接报错，不擅自替换。采样权限、容器 NUMA 限制、磁盘配额和 CPU 独占需服务器管理员或调度系统解决；入口不会修改 perf_event_paranoid 或关闭隔离策略。不能在不同机器间直接比较绝对耗时、混合 ELF/Profile 或合并统计新旧批次。

## 包含的内容与结果路径

| 目录 | 内容 |
| --- | --- |
| code/core/ | NGSPICE 核心运行框架、求解器、BOLT、NUMA 入口和共享模块 |
| code/dynamic_library/ | jemalloc/tcmalloc 动态库检查、LD_PRELOAD 和 A/B/C/D 实验入口 |
| source/ | NGSPICE 47 源码、BOLT 22.1.8、perf、TLC/Z3 及私有依赖产物；不放实验 Python/Shell 入口 |
| environment/ | 输入和模型闭包、历史配置；当前配置以每次输出的 preflight.json 和 experiment/config/experiment.json 为准 |
| raw/ | C7552 历史原始归档、原版与重写 ELF、Profile、计时、波形、遥测及 SHA-256 |
| intermediate/ | 历史标准化逐次数据、配对及统计 CSV、指纹 |
| result/ | 历史报告/结果图/形式化证据和此前 Node 1 短测摘要 |
| 每次新输出目录 | preflight.json、status.json、formal-check.log、local-verification.*/ 和 experiment/ |

`experiment/` 内：`src/build/install` 为本机构建，`test/` 为短测，`profile/` 为 perf.data、FDATA、匹配 BOLT ELF 和日志，`result/` 为正式逐次运行与统计，`intermediate/` 为测量和配对 CSV，`report/experiment.md` 为新批次结果摘要。正式 Profile 与计时相互独立；99 Hz cpu-clock:u 是软件时钟采样，不能当作 IPC、Cache miss 或带宽指标。

其他命令：`bash run.sh install /tmp/ngspice-runs/install-001 --node 1`（预检后只安装）；`bash run.sh archive-analysis`（只分析随包历史归档）。可执行 `sha256sum -c REPRODUCTION-SHA256SUMS` 核验运行代码、工具及输入。

## 验证范围

先前在 hg7490 Node 1 完成的 0.1 ns 短测覆盖源码构建、两种求解器、独立采样/FDATA/BOLT 和波形比较，摘要在 `result/automation-smoke-node1/`。新增入口的预检、搬迁和失败分支证据另见 `result/portability-checks/`；不将入口修改后的环境检查冒充重新完成了所有正式实验。异机 full 成功仍须以该机实际输出为准。

## 动态库实验

动态库实验脚本位于 `code/dynamic_library/`，私有 jemalloc/tcmalloc 位于 `source/dependencies/allocators/`，正式 20 ns 数据位于 `result/dynamic_library/20ns/measurements/`。A/B/C/D 四组分别为系统 glibc、jemalloc、tcmalloc、BOLT+tcmalloc；本次整理不重新执行正式仿真。运行前先执行 `bash code/dynamic_library/check_compatibility.sh`，正式启动由用户显式执行 `python3 -u code/dynamic_library/run_20ns.py`。

TLC 检查抽象执行协议；Z3 检查已归档事实约束的可满足性，并分别运行故意违反约束的负例。它们不证明新二进制语义等价或 NGSPICE 任意电路的数值正确性。新批次波形校验是独立测试，正式对照每次保留输入、ELF 指纹与配置。
