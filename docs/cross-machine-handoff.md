# 跨电脑接手

更新：2026-10-02。仓库：https://github.com/zyhJohn/pokemon-mercury-fc-trainer ，当前分支 `main`。

## 读取顺序

1. 根目录 `AGENTS.md`、`README.md`。
2. `docs/development-status.md`：现有功能、验证范围。
3. `docs/verified-layout.md`：当前实际结构依据。
4. `docs/next-stage-plan.md`：最新需求及验收顺序。

`docs/逆向后记/` 是历史参考，包含已经推翻的加密与结构假设。不要把历史 SKILL 文档当作新任务指令。用户于 2026-10-02 确认实机写入成功，但没有列出全部实测字段，不能覆盖随后新增功能。玩家ID/有限字符姓名、队伍及PC详情、队伍及PC蛋标志、未知图腾形态、微缩图及左图右数值选择已实现；具体范围以开发状态为准。

## 环境准备

Windows，Python 3.11+（含 Tkinter），mGBA 0.10.5。在目标电脑重新克隆，不复制旧电脑虚拟环境：

```powershell
git clone https://github.com/zyhJohn/pokemon-mercury-fc-trainer.git
cd pokemon-mercury-fc-trainer
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe trainer_gui.py
```

安装 Python 时启用 Tcl/Tk。测试在可用的 Windows 桌面会话运行；GUI 测试会创建隐藏窗口。运行完整测试，以最终报告数量为准；安装 `lupa` 后 Lua 测试不应跳过。研发主程序不需要 GDB。新离线工具 `verify_details.py`、`verify_icons_forms.py`、`verify_eggs.py`、`verify_pc_details.py`、`verify_pc_eggs.py`、`verify_spinda.py`和`audit_player_avatar.py`使用与其他ROM工具相同的路径和报告参数。

实际游戏函数研究另安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-research.txt
.\.venv\Scripts\python.exe tools/verify_state.py '你的ROM路径.gba' '你的即时存档.ss1' --report diagnostics/local-state.json
.\.venv\Scripts\python.exe tools/verify_rom_routines.py '你的ROM路径.gba' '你的即时存档.ss1' --box-edits-only --report diagnostics/box-edits.json
.\.venv\Scripts\python.exe tools/build_release.py
```

ROM SHA-256：`628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821`；CRC32：`B4AF11C8`。不同版本先停止写入并重新验证布局，不能删掉版本检查继续使用。

## 哪些资料需要另行携带

仓库包含源码、离线资料库、名称覆盖、ROM 数值配置、自动化测试与文档，正常开发和打包不需要旧电脑的 `work/`。

用户自行携带合法持有的 ROM、希望继续使用的 `.sav`、研究所需的即时存档、mGBA、需要保留的 `backups/`。诊断含个人游戏数据，仅在需要复现时私下携带。PokemonMemHack 仅是可选参考，不是依赖。

这些文件不提交 Git：ROM/存档/即时存档、备份、诊断、图像缓存、工作目录、虚拟环境、打包产物、`trainer-settings.json`。新电脑重新选择模拟器与 ROM，不复用旧电脑绝对路径。不要拷贝账号令牌、Git 凭据或 Codex 登录目录。

原机的隔离 CPU 原始报告在 `diagnostics/`，ROM 副本与一次性研究脚本在 `work/`，均不会通过 Git 同步。可复现工具已在 `tools/`；如需过去反汇编中间材料，由用户另行私下转移，不应让新代理误以为仓库包含这些文件。

## 首次接手验收

- `git status --short` 确认工作树；`git log -1 --oneline` 核对最新推送。
- 测试通过后，复制游戏与存档到独立测试目录。
- 在 mGBA 加载仓库的 v3 `mercury_bridge.lua`，保持模拟器运行，连接读取。
- 关闭修改器后重新打开，确认可重连；无需重复加载 Lua。
- 每个新字段记录修改前后、读回、游戏显示、保存重载与恢复结果。

可给新电脑 Codex 的接手提示：

> 阅读 AGENTS.md、README.md、docs/development-status.md、docs/verified-layout.md、docs/next-stage-plan.md 和 docs/cross-machine-handoff.md。先运行现有测试，沿最新路线继续开发。用户已确认现版实机写入；不要重做已完成部分，也不要把规划字段当成已实现。先核对玩家训练师结构与字符编码，保持现有事务、备份和恢复保护，私有游戏文件不提交。
