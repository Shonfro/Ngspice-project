# NGSPICE 多层次性能优化报告

批次 **klu8-20261115-051336-94122**，状态 **completed**，阶段 **—**。

C7552/SKY130 是 ISCAS85 晶体管级负载。20 ns 瞬态窗口沿用 v4，并保留三路指定节点电压。计时指标为单次仿真的端到端 Wall Time。新实验固定 KLU8 和 8 个配置线程，CPU 0–15 执行仿真，CPU 16 运行控制器。

## 环境

hg7490，Hygon 7490。拓扑、Linux 版本、cgroup、NUMA balancing、内存策略和 ELF／FDATA／Profile 指纹见[审计目录](../../test/numa/klu8-20261115-051336-94122/)。[编译参数](../../../eda/bolt/src/ngspice/configure-v2.args)。

## 求解器与线程

同一网表、20 ns、8 线程时，已归档 KLU 原版平均 335.40 s，Sparse 1.3 原版 653.85 s。这是先后运行的两组样本，未按求解器交替配对。全窗口保存波形通过校验，最大绝对差 2.96e-10 V。根据这组结果保留 KLU8。历史 KLU16 的线程收益为 3.86%，本轮不纳入 NUMA 或最终部署。[归档](../../result/solver-20261115-000316-20800/)

## 二进制布局

复用原版 ELF 的 99 Hz cpu-clock:u 训练，perf2bolt 生成 FDATA 后由 LLVM Upstream BOLT 22.1.8 重排。Profile 不含硬件 PMU 或 LBR 指标。KLU8 两批 BOLT 分别平均缩短 0.98% 和 1.10%。[七对复测](../numa/klu8-bolt-followup-analysis.md) · [Profile 和重写记录](../../profile/20261114-055440-34321/c7552/klu8/)

## NUMA 与组合

[默认、本地及远程诊断](../numa/numa-analysis.md)。

2×2 跳过。Local policy did not pass the paired performance and placement criteria。

## 最终独立验证


批次 **klu8-20261115-051336-94122**，状态 **completed**，阶段 **—**。

最佳内存策略：**default**。对照 Original KLU8 + Default 与对应的 BOLT + 最佳策略，独立预热 1 对、正式 7 对。

| 对照 | 原版或基准 (s) | 试验组 (s) | 加速比 | 耗时缩短 | 更快次数 | 配对节省 95% CI (s) |
|---|---:|---:|---:|---:|---:|---:|
| Original + Default → BOLT + Best NUMA | 332.74 | 329.86 | 1.0087× | 0.86% | 7/7 | [+1.53, +4.22] |

逐对节省 (s)：+0.23、+2.22、+2.26、+3.39、+4.63、+3.42、+3.99。平均 +2.88 s，配对标准差 1.45 s。基准与试验中位数分别为 332.91 和 329.69 s。

逐对相对收益为 +0.07%, +0.67%, +0.68%, +1.02%, +1.39%, +1.03%, +1.20%。几何平均配对加速比 **1.0087×**。14 次正式运行均通过保存信号的精确波形比较，最大绝对电压差 0 V。

[最终计时图](final-time.svg)

[原始计时与校验](../../result/final-validation/klu8-20261115-051336-94122/)
[原版与 BOLT 布局热图](klu8-layout-heatmap.svg) · [热图源数据](klu8-layout-heatmap.csv)。两侧使用同一组原版样本权重，按匹配函数的入口页映射。


[复现 README](../../README.md)。样本量为七个配对，置信区间基于配对差值的 Student t。结论适用于该主机、网表和配置。

