# 求解器设计

## 选择

主关是一个完全信息的单人组合搜索问题：每次只能选无遮挡牌；相同牌按客户端规则插入消除栏并三张消除；第七张未消除牌失败。它还包含一次主动移出三张、一次复活后移出三张，且两者的牌都须从三个临时槽回取。

采用“带偏好随机回放 + 去重束搜索”作为当前实现：

- 随机回放快速探索不同的解锁顺序；
- 分层束搜索保留容量安全、可补成三消、可解锁同类牌的状态；
- 转置表按 `剩余牌 + 消除栏牌面序列 + 三个临时槽牌面序列 + 道具状态` 合并等价历史；
- 每一个输出计划再由独立回放器逐步核对遮挡、插入位置、三消、七格限制和道具槽。

这是比纯贪心更稳健、又比没有可采纳估价函数的 A*/IDA* 更适合现阶段的折中。SameGame 的研究也指出，缺少可靠可采纳评估时，单人 MCTS 是有效替代方案；本实现保留随机回放作为后续接入 SP-MCTS/交叉熵调参的策略层。

参考：

- Schadd et al., [Single-player Monte-Carlo tree search for SameGame](https://www.sciencedirect.com/science/article/pii/S0950705111001869)
- Takes & Kosters, [Solving SameGame and its Chessboard Variant](https://liacs.leidenuniv.nl/~kosterswa/samegame.pdf)
- Junghanns & Schaeffer, [Sokoban: Enhancing general single-agent search methods using domain knowledge](https://www.sciencedirect.com/science/article/pii/S0004370201001096)

## 运行

```powershell
$env:PYTHONPATH = 'src'
python -m sheep_helper.cli solve `
  --cards captured-layouts/64510ca5ca10058da0ca36f5d02fe9bf.cards.json `
  --coverage captured-layouts/64510ca5ca10058da0ca36f5d02fe9bf.coverage.json `
  --output captured-layouts/main.solution.json `
  --time-limit 180
```

该命令只读取本地地图文件，绝不操作游戏窗口、发送游戏请求或使用账号会话数据。

## 当前验证

- 18 张解析样本：生成完整计划，并回放到 `remaining=0`、消除栏为空、临时槽为空；
- 临时移出区：测试覆盖前三张的槽位分配与回取；
- 主关：180 秒内探索 13,604,081 个候选状态，未发现可回放的完整路线。因此没有输出声称可通关的主关计划。
- 主关 SP-MCTS 首轮：60 秒完成 12,893 次精确状态模拟，同样未发现可回放路线。
