# NGSPICE 动态库优化实验报告

日期：2026-11-23

## 结论摘要

本次在 hg7490（Hygon C86-4G、EL7、glibc 2.17）完成候选动态库兼容性检查和私有构建。jemalloc 5.3.0、tcmalloc（gperftools 2.7）构建成功；mimalloc 在系统 GCC 4.8.5 下因缺少 `stdatomic.h` 构建失败。Hygon 适配 glibc 和新版 libm 没有找到可验证的官方兼容包，因此没有替换系统运行库。

按照三级判定，当前结果为：jemalloc/tcmalloc 通过“构建”，加载和符号覆盖检查脚本已具备；完整 C7552 20 ns、预热 1 次加正式 7 次的性能收益测量未完成，因此没有候选库进入最终优化配置，也没有声称收益。历史 BOLT 结果仅作为外部对照：332.74 s → 329.86 s，不能与本次未完成的动态库结果相加。

## 固定条件

NGSPICE 47、C7552/SKY130 TT、20 ns 瞬态、KLU8 固定 8 线程、计算 CPU 0–15、监控 CPU 16、Linux Default NUMA；原始 ELF、网表、模型和参数保持不变。不执行 Node 2 和线程配置对照，不修改企业版 `report.md` 或历史正式数据。

## 兼容性和构建结果

| 候选 | 版本/来源 | 构建 | 原因或产物 |
|---|---|---|---|
| jemalloc | 5.3.0，GitHub release | 通过 | `libjemalloc.so.2` |
| mimalloc | 1.9.2、2.1.7，GitHub tag | 未通过 | GCC 4.8.5 缺少 `stdatomic.h` |
| tcmalloc | gperftools 2.7，GitHub release | 通过 | `libtcmalloc_minimal.so.4.5.3` |
| Hygon glibc/libm | 未发现可靠官方私有包 | 不执行 | 禁止把另一版本 glibc/libm 直接 LD_PRELOAD 注入 |

系统 ABI：x86_64、HygonGenuine、glibc 2.17、动态链接器 `/usr/lib64/ld-2.17.so`。候选库均为本机 GCC 4.8.5 构建，SHA256 和配置日志位于 `result/dynamic_library/compatibility/`。

## 三级判定与实验状态

1. **候选库成功构建**：jemalloc、tcmalloc 通过；mimalloc 未通过。
2. **候选库实际加载**：入口脚本使用 `LD_PRELOAD`、`LD_DEBUG=libs,symbols`、`/proc/$PID/maps` 和 `ldd` 记录加载路径，并用 `nm -D` 检查 `malloc/free/calloc/realloc/posix_memalign` 等符号。只有路径和关键符号均命中才标记为 loaded。
3. **可重复性能收益**：要求每项预热 1 次、正式 7 次，配对区组并交替顺序，记录 Wall/CPU/RSS/缺页/上下文切换和波形校验。当前没有完整正式测量 CSV，因此所有候选均为 `not_evaluated`，不得进入最终配置。

## libm 和 Hygon glibc 判定

对系统 `libm.so.6`、`libc.so.6` 和动态链接器执行 `readelf -V/-Ws` 的 ABI/符号版本检查。系统默认 libm 不是新版数学函数优化库，不作为优化候选。Hygon glibc 必须成套匹配 loader、libc、libm、libpthread、libdl 等组件；本次没有可靠官方来源，故不可执行。

## BOLT 组合分析

本次要求的三组为原始 ELF+系统库、原始 ELF+最佳动态库、BOLT ELF+最佳动态库。由于最佳动态库尚未满足第三级判定，后三组的新增正式数据为空。历史 BOLT 对照 332.74 s → 329.86 s（0.86%）只说明已有 BOLT 批次的独立结果，不能与新批次收益百分比相加，也不足以单独决定保留 BOLT。

## 当前最终推荐配置

求解器：KLU8；NUMA：Linux Default；动态库：系统默认（未证明候选收益前不替换）；BOLT：保留为待决策项，不因动态库实验预先保留或撤销。只有完成三级判定并通过波形正确性后，才能更新最终配置。
