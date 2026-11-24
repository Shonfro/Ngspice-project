# 复现与核查

hg7490 的正式性能批次已经归档。在实验目录执行以下命令核查现有 checkpoint、重建标准化数据和图表、准备本机验证包。

~~~bash
bash code/pipeline/run_all.sh --config conf/final-pipeline.yaml --resume --non-interactive
bash code/pipeline/run_all.sh --status
~~~

\`--dry-run\` 只核查必要路径和磁盘空间。\`--analysis-only\` 只分析存量数据。\`--formal-only\` 生成本机待交接包，保留原始性能数据。当前统一入口把已完成批次作为 checkpoint；对于不完整的性能批次会报告阻塞，目前没有实现逐次仿真的自动补测。

当前连接别名为 hgserver，实际实验主机为 hg7490。独立阶段需有与 hg7490 不同的服务器及传输授权。通过获准渠道把 \`result/formal/independent-verification.tar.gz\` 放到该服务器，在新目录执行：

~~~bash
tar -xzf /path/to/independent-verification.tar.gz
sha256sum -c SHA256SUMS
TLC_JAR=/path/to/tla2tools.jar python3 -B verify_independent.py
~~~

系统需要 Java、TLC JAR、Z3 CLI、Python 3.10+、x86-64 及可加载精确 ELF 的动态库。验证脚本保留结果到 \`evidence/\`，包括实际主机、依赖、TLC/Z3 日志和最多六次独立波形复现。完成后回传 \`evidence/\` 用于汇总。远端数据不用于 hg7490 性能统计。

[环境与依赖](../../conf/) · [形式化验证状态](FORMAL_VERIFICATION_REPORT.md) · [标准化数据](../../result/analysis/)
