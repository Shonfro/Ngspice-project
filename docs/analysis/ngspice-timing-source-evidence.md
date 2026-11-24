# NGSPICE 阶段计时实现审计

本审计读取随包 `source/ngspice-47.tar.gz` 到临时目录进行检索，未修改交付工程中的源码归档。

关键实现位置：

- `src/spicelib/analysis/cktsopt.c` 将 `time`、`loadtime`、`factortime`、`solvetime`、`trantime`、`tranloadtime`、`transolvetime` 等资源名映射到统计字段。
- `src/frontend/resource.c` 的 `com_rusage()` / `printres()` 输出这些字段；`space` 和 `faults` 则分别读取 RSS/`getrusage`。
- `src/misc/misc_time.c` 的 `seconds()` 在 `USE_OMP` 构建下使用 `omp_get_wtime()`，否则根据构建宏可能使用单调时钟、gettimeofday、times 或 getrusage；仅凭源代码归档不能证明历史二进制最终选择了哪一个宏分支。
- `src/frontend/dotcards.c` 调用 `rusage all`，因此正式 console 中的阶段表来自 NGSPICE 内部资源统计，而不是外部 Wall Time 拆分。

含义边界：阶段字段是 NGSPICE 统计器中的累计阶段值；Matrix Load 与 Transient Load 数值高度相关，不能把二者的节省时间相加。Matrix factor/solve 同样属于分析流程的组成部分，表格用于对照，不构成互斥阶段的严格 Wall 分解。阶段均值可以用于定位相关变化，但不能单独建立分配器与性能变化之间的因果关系。
