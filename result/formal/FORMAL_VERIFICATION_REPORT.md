# 独立形式化与数值验证

hg7490 原始数据审计 **SUCCEEDED**，核对 554 项条件和 99 条正式运行。其中 31 条属于当前固定 KLU8 协议。

独立阶段 **BLOCKED**。当前使用 hgserver 连接 hg7490。尚未配置与 hg7490 不同的独立验证主机。目前没有 TLC 状态数、Z3 求解结果或远端波形结果。

TLA+ 模型覆盖一个抽象区组、条件门控、运行互斥和成功记录保护。Z3 编码归档值与预期约束，另用注入的矛盾作负对照。数值复现要求两份精确 ELF 各执行三次，并与归档三路保存波形精确对照。以上三类检查不能证明 NGSPICE 数值算法的普遍正确性。独立服务器不纳入性能统计。

[本机数据审计](../../result/formal/local-data-audit.json) · [本机待传验证包](../../result/formal/independent-verification.tar.gz) · [复现命令](../final/Reproduction_Guide.md)
