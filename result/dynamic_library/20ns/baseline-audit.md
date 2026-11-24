# 历史基准与 20 ns 动态库批次审计

## 结论

历史 KLU8 原版结果（报告记录的约 332.74 s）与本次动态库批次 A（494.317 s）不能拼接为累计收益。两批次均描述为 NGSPICE 47、C7552/SKY130 TT、20 ns、KLU8，但原始交付数据无法证明所有运行环境和测量边界逐项完全相同，因此本次完整组合收益只采用同批次 A→D 配对结果。

## 可核对指纹

| 对象 | SHA256 |
|---|---|
| 本批次原始 NGSPICE ELF | `30338761d40c04bd7158b7f20bb912b878db6210775eaf00c086a1c614cb87be` |
| 本批次历史 BOLT KLU8 ELF | `22f13df8f37a0fdb70bdd6395a6508d6e5b561fcea2540d0ee7fc8b555c94a5a` |
| jemalloc 5.3.0 | `70dd9d6232300b57b5640e15460ea4224a1543d7afb28584dc2456ee39bd961b` |
| tcmalloc gperftools 2.7 | `158b02b4f7798b4f6e8769e8b12f4a091aac4afca47fb603e1c45064875e9efc` |

## 差异边界

本次每次运行由 GNU time 覆盖 simulator launcher 到进程退出，并保留了实际命令、ELF、LD_PRELOAD、PID 观测、maps、阶段计时和波形。历史批次使用了原有 runner 和独立归档；现有材料不能证明其 CPU 频率/系统负载、动态链接依赖快照、wrapper 计时边界和输入 staging 过程与新批次逐项等价。

因此不能把 `332.74 -> 380.069` 或 `332.74 -> 494.317` 当作本次收益，也不能将历史 BOLT 0.86% 与本次 tcmalloc 21.482% 直接相加。新批次的结论仅为：同一 20 ns A/D 批次中，平均 Wall 从 494.317 s 降至 380.069 s，配对节省 114.249 s、改善 23.112%。
