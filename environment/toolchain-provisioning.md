# 工具提供方式

| 工具 | 包内内容 | 执行方式 |
|---|---|---|
| LLVM BOLT 22.1.8 | source/toolchain/llvm-bolt；perf2bolt 为同程序入口链接 | 直接使用，无需构建完整 LLVM |
| Linux perf | source/toolchain/perf | 直接使用，需与主机内核及动态库兼容；NGSPICE_PERF 可替换 |
| NGSPICE 47 | source/ngspice-47.tar.gz | code/core/install.sh 自动解压、配置、编译、安装到新输出目录 |
| C7552 / SKY130 | environment/inputs/c7552 | 递归依赖已随包，运行时单独布置 |
| TLC 1.7.4 | source/toolchain/formal/tla2tools.jar | 使用主机 Java；入口执行 TLC 正负对照确认兼容 |
| Z3 4.12.2 | source/toolchain/formal/python | 本机匹配的 Python 包；其他平台需匹配安装 |

主机前置条件：Linux x86_64、Python 3.11+、gcc/g++、make、readelf、taskset、numactl/numastat、pidstat/mpstat、Java；内核允许 cpu-clock:u 采样。包内工具使用系统动态库，不是无依赖容器。

此前在 hg7490 NUMA Node 1 做过短测，CPU 16–31，控制进程 CPU 144，内存绑定 Node 1。0.1 ns 短测验证工具链和数值输出，不产生或替代 20 ns 正式性能结论。

新版入口的节点选择、perf 回退和依赖修复命令以根目录 README.md 为准；不要求未知服务器存在 Node 2。
