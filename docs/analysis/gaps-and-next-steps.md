# 数据缺口与后续最小补采方案

## 本轮未采集

- A/B/C/D 函数级 `perf.data`、perf report、perf script 和稳定调用栈。
- malloc/free/calloc/realloc 调用次数、分配字节数、线程缓存和 allocator-specific counters。
- 每次运行可直接比较的 CPU migrations、NUMA 页面统计和系统负载快照。
- 可将旧 332.74 s 批次与新批次逐项对齐的完整 ELF、输入闭包、动态依赖和计时边界证据。

## 后续最小补采

1. 在同一固定 20 ns A/B/C/D 控制条件下，分别采集足够重复的 `perf record -g`、`perf report` 和 `perf script`，记录 ELF/DSO Build ID；仅用于机制诊断，不替换当前正式结果。
2. 使用与 glibc 2.17、jemalloc 5.3.0、gperftools 2.7 ABI 匹配的诊断接口或 LD_PRELOAD 统计 shim，记录调用次数、字节数、峰值和线程缓存；先做短时验证，再决定是否正式补采。
3. 为每次运行保存 `/proc/PID/status`、`sched`、`numa_maps`、CPU migration、频率/负载和完整动态链接符号解析快照，并对旧基线使用同一采集协议。

这些补采属于后续独立实验，本轮未执行。
