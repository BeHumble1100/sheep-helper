# Sheep Helper

《羊了个羊：星球》的 Windows 通关指引。助手在本机恢复大世界和每日关卡的棋盘，搜索并回放校验完整路线，再用准心标记下一张牌。可以手动跟随指引，也可以在对齐后开启自动点击。

## 快速开始

桌面模式需要 Windows、Python 3.10+，以及可用的微信抓包环境。实时抓包依赖本机受信任的 mitmproxy 证书、PATH 中的 `mitmdump`，以及 Clash 系统代理 `127.0.0.1:7890` 或 `127.0.0.1:7897`。

在仓库根目录运行：

```powershell
python -m pip install -e ".[capture]"
sheep-helper guide
```

如果已经在本机打包，也可以双击 `dist\SheepHelper.exe`。

1. 点「开始抓包」，等面板显示就绪后，在微信小程序中开启一局新的大世界或每日关卡。
2. 自行通过第一关，到达第二关后在助手里确认。助手会尝试自动对齐并显示下一张牌。
3. 核对准心和牌型后，按提示手动点牌，或开启「自动点击」。道具和复活仍需手动完成并确认，随后可按指引取回移出的牌。
4. 如果点错、换局或标记偏移，点「暂停」结束本局指引。

## 面板设置与排查

| 情况 | 操作 |
| --- | --- |
| 求解超时 | 在「求解时限」调整预算并点「应用」。初次默认 20 秒，设置会保存并在下次求解生效。牌局仍一致时，可用「载入最近抓包」重试一小时内完整保存的对局。 |
| 没出现开局记录 | 确认抓包已就绪，再完全关闭并重新打开微信小程序，以建立新连接。抓包期间助手会临时切换系统代理，结束后恢复原配置。 |
| 准心偏移 | 保持游戏窗口可见，点击「重新自动对齐」。出现橙色预览时，可展开「校准微调」，调整中心和牌间距后点击「应用微调位置」。 |
| 希望保留鼠标控制 | 对齐后勾选「后台模拟点击」，再开启自动点击。游戏窗口需要保持未最小化。 |
| 路线或画面失配 | 点击「暂停」清除本局指引，再从当前游戏状态判断是否需要开新局。 |

自动点击默认关闭。助手会等待画面变化确认出牌，确认失败时停止。它只展示通过完整回放校验的路线，搜索超时表示当前预算内未找到完整解。

## 离线求解

离线模式只读取本地棋盘与遮挡关系文件。仓库中的样例可直接用于验证：

```powershell
python -m pip install -e .
sheep-helper solve `
  --cards captured-layouts/64510ca5ca10058da0ca36f5d02fe9bf.cards.json `
  --coverage captured-layouts/64510ca5ca10058da0ca36f5d02fe9bf.coverage.json `
  --output captured-layouts/main.solution.json `
  --time-limit 120
```

使用 `sheep-helper solve --help` 查看算法和道具选项。桌面求解默认采用混合搜索；内置的压缩求解后端可用时，两种搜索会并行运行，并采用首个通过回放校验的结果。算法细节与实验结果见 [求解器说明](SOLVER.md) 和 [优化记录](docs/solver-optimization-2026-11-18.md)。

正式地图流程使用对应对局的地图资产和牌面数据。实时抓包记录保存在本地，插件会剔除账号标识与会话凭据。通用 HAR 的 `inspect` 和 `export` 用于检查协议结构。使用 `sheep-helper prepare-session --help` 查看离线地图准备参数。

## 开发与打包

```powershell
python -m unittest discover -s tests -q
python -m pip install -e ".[capture]"
python -m pip install "pyinstaller>=6,<7"
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_exe.ps1
```

构建产物为 `dist\SheepHelper.exe`，运行时仍依赖前述 Windows 抓包环境。历史改动见 [CHANGELOG](CHANGELOG.md)。
