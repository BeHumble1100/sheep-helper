# 上传源码仓库

仓库保留现有 Git 历史，当前分支为 `main`。构建产物不纳入源码提交。

## 本地验证

在 Windows、Python 3.12 环境中执行：

```powershell
python -m pip install -e .
python -m unittest discover -s tests -q
git status --short
git log -5 --oneline
```

已有的 `captured-layouts` 跟踪文件仅作为离线测试夹具保留；新增抓包、证书、日志、虚拟环境、解包资源和 EXE 由 `.gitignore` 排除。
`tests/fixtures` 中的回归截图已遮去上方非测试区域。个人练习文件由本地 `.git/info/exclude` 排除，不删除原文件。

## 关联目标仓库

先确认当前项目的提交姓名与邮箱：

```powershell
git config --get user.name
git config --get user.email
```

本地尚未配置 `origin` 时，填入自己的目标仓库地址再执行：

```powershell
git remote add origin <目标仓库地址>
git remote -v
git fetch origin
```

如果远程仓库已有提交，先检查并整合远程历史；如果远程为空，可执行：

```powershell
git push -u origin main
```

日常运行只使用 `dist/SheepHelper.exe`。需要分发 EXE 时，将构建产物放在仓库的 Release 附件中，与源码提交分开。
