# 离线搜索原型

压缩搜索在验证今天一局完整解后，已移至正式源码 [CompactSolver.cs](../../src/sheep_helper/native/CompactSolver.cs)。此目录保留研究说明，不维护第二份算法副本。

正式程序采用原算法与多前缀压缩搜索并行，首个完整解胜出。先前仅提高部分进度的版本未被采用；历史负面结果保留在实验记录中。

请通过 `tools/benchmark_compact_solver.py` 调用，不能直接信任底层输出。Python 包装器会拒绝不支持的规模及不一致的进度／完整性结果。原型同时允许移出与复活，仅适用于普通静态牌面。

实验细节与负面结果：[2026-11-18 对照记录](../../docs/solver-optimization-2026-11-18.md)。
