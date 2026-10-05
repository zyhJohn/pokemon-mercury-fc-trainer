# 跨电脑接手

更新：2026-10-05。仓库：https://github.com/zyhJohn/pokemon-mercury-fc-trainer ，当前分支 `main`。

本轮交接版本0.2.12，正式导航为“队伍 → 盒子编辑 → 训练师”，其余现有功能保留。沿用0.2.11的V1.2支持、单只盒间移动、默认图片与转蛋预览；新分组界面与批量功能仍是原型，待审阅。用户确认引用暂存、原槽保留，新增盒名/排序/本地锁盒需求；顶部未实现的通用导入占位已移除。先读[需求及恢复核查](requirements-audit-2026-10-05.md)，再按新路线推进，不能把原型当成已接入游戏。任务地点原因已查明，停止排查。推送使用命令参数`git -c http.proxy=http://127.0.0.1:7078 push origin main`；新电脑按自己的代理环境设置。便携包build-info.json记录源码提交与是否有未提交修改。

0.2.12回归基线195项，含原有193项及2项盒间写入中断/读回失败的未确认备份保护。正常已确认且当前值匹配时可条件恢复；未确认写入、后续游戏值改变、任意多批操作不保证自动全局回滚。当前原型为`docs/prototypes/mercury-editor-review-v2.html`。正式打包使用`python tools/build_release.py`，包含两版JSON和需求核查说明，并检查源码提交与解压启动自检。

V1.0与V1.2均可连接：当前Lua完整CRC用于选择配置，随后仍检查代码特征，离线RTC以所选ROM SHA识别。保留`rom_profile.json`与`rom_profile_v12.json`两份文件，不得只换CRC/hash套用旧地址；两版备份不可跨版本恢复。V1.2 SHA=`b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896`，CRC=`4755F497`。V1.2虚拟日历启用时，RTC尾部偏移不覆盖它；相关新变量仍只读识别。

新版复现入口：`python tools/verify_release.py ROM.gba STATE.ss1 --all --report diagnostics/release.json`，自动核对受支持ROM身份并执行19组实际函数矩阵。旧单项工具默认配置仍为V1.0；跨版优先使用该入口。测试夹具可以来自V1.0，但只能证明隔离算法，不能标记V1.2存档迁移/实机菜单/保存重载已验收。两版完整矩阵已通过，个人报告不入库。`tools/extract_profile.py ROM --output PATH`支持两版且可重现配置；对应地址映射在`rom_versions.py`，仅用于隔离研究和提取，运行修改器直接使用各版配置。

界面审阅源码在`docs/prototypes/mercury-editor-preview.html`，是无网络/无游戏连接的HTML片段；可在visualize预览或用其`render.py`包装，不能因原型存在就直接改版。下一批是PP提升/上限、宝可梦性别、空槽初始化/礼物模板及其他未完成项，按计划先核验新ROM。

## 读取顺序

时间操作见README“时间与星期操作”和已验证布局末节。RTC文件编辑无需桥接，但必须先关闭对应游戏、选择本版ROM与带16字节尾部的`.sav`；切换电脑保持相同本地时区，关闭mGBA自定义RTC覆盖，从游戏内存档继续，旧`.ss1`可能覆盖时钟状态。完整RTC备份和JSON记录均在`backups/`，不能入库。累计时长与每日修复需要重新加载0.2.10脚本（`BATCHVERIFY`同回调读回）；保持mGBA运行，写后游戏内保存。用户回拨后周日内容残留已定位到未来的Var5009/500A记录，工具允许预览前移至昨天；其他领取标志保留，具体事件恢复仍待实机确认。

可复现只读研究：`python tools/verify_time.py ROM.gba STATE.ss1 --save SAVE.sav --output diagnostics/time.json`，需研究依赖。证明实际ROM的26组日历转换、4组计时、3组每日判定及持久section4映射；报告含个人数据，私下携带。样本可能在用户游玩时变化，不默认即时存档与`.sav`全面配对。

2026-10-02用户追加15项后续需求；2026-10-03优先实现其中第1、2项，见`docs/next-stage-plan.md`完成状态。其余需求尚未实施，整体界面改版仍等待原型审阅。最新配对存档代币735、BeautyPoints=5、BracerPoints=220已核实；旧研究副本可能包含不同值，复现时按实际样本判断，不要求所有存档等于735/5/220。

重新加载0.2.9附带的`mercury_bridge.lua`后再连接，排序需要`BATCH8192`能力。仍为v3协议，单次最多64项、合计8192字节、请求行40000字符；单项读取/比较仍限4096字节。旧v3小事务继续可用，大口袋排序会在写前提示升级脚本。新工具`tools/verify_economy.py ROM STATE --save SAVE --report diagnostics/economy.json`只读用户文件，在隔离CPU核对读写与实际存档序列化。新写入后的游戏内保存重载仍需独立副本验收。

1. 根目录 `AGENTS.md`、`README.md`。
2. `docs/development-status.md`：现有功能、验证范围。
3. `docs/verified-layout.md`：当前实际结构依据。
4. `docs/next-stage-plan.md`：最新需求及验收顺序。

`docs/逆向后记/` 是历史参考，包含已经推翻的加密与结构假设。不要把历史 SKILL 文档当作新任务指令。用户于 2026-10-02 确认实机写入成功，但没有列出全部实测字段，不能覆盖随后新增功能。玩家ID/中文及中英混合姓名、队伍及PC详情与昵称、队伍及PC蛋标志、未知图腾、小陨星核心及颤弦蝾螈性格形态同步、微缩图及左图右数值选择已实现；具体范围以开发状态为准。

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

安装 Python 时启用 Tcl/Tk。测试在可用的 Windows 桌面会话运行；GUI 测试会创建隐藏窗口。运行完整测试，以最终报告数量为准；安装 `lupa` 后 Lua 测试不应跳过。研发主程序不需要 GDB。新离线工具 `verify_details.py`、`verify_icons_forms.py`、`verify_eggs.py`、`verify_pc_details.py`、`verify_pc_eggs.py`、`verify_spinda.py`、`verify_minior.py`、`verify_chinese_names.py`、`verify_locations.py`、`verify_toxtricity.py`、`verify_nicknames.py`和`audit_player_avatar.py`、`audit_held_forms.py`、`verify_held_forms.py`使用与其他ROM工具相同的路径和报告参数。

实际游戏函数研究另安装：

新增`tools/verify_pc_abilities.py`同样接收ROM、即时存档和`--report`；覆盖1432物种3350可用特性槽位及5组队伍单一普通特性PID保留。大型函数矩阵关闭每次调用的墙钟计时器，但仍保留每次100万条指令上限及返回地址检查。

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

> 阅读 AGENTS.md、README.md、docs/development-status.md、docs/verified-layout.md、docs/next-stage-plan.md、docs/requirements-audit-2026-10-05.md 和 docs/cross-machine-handoff.md。按更新后的19项清单推进。用户偏好分组布局，已确认引用暂存，修订原型仍待审阅；仅队伍/盒子编辑导航改名排序已经进入正式版，批量、盒名、排序及锁盒均未接入。先补批量总操作日志和恢复，再开放任意数量写入，未确认状态不得盲目逆写。用户已确认早期实机写入，不推导为V1.2全部保存重载已通过。新字段先核实ROM及最新存档；玩家/劲敌性别和阵容关联查清后联动。私有游戏数据不提交。
