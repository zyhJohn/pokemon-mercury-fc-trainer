# 跨电脑交接 · 2026-10-08

## 当前继续开发

`0.2.15-dev.2`最终便携检查包已从干净源码构建并通过独立解压自检，构建内322项全部通过。版本ZIP、源码身份与SHA见[dev.2构建检查点](build-checkpoint-2026-10-08-dev2.md)，前一检查包不覆盖。

最新续推为`0.2.15-dev.2`，配信目录8条/7可用，新增妙蛙种子及PP差异披露，322项回归通过。四份续审工具/记录已更新原仓库审阅分支；玩家性别/来源方式/星期旗标仍按实际证据保持关闭，不能直接清领取位。下述`0.2.15-dev`段落为前一检查点，当前状态先读[本日审核](requirements-audit-2026-10-08.md)。

10月8日用户已取消额度暂停条件，继续推进喷雾/劲敌安全事务、自定义创建表单及未核验字段研究。当前已发行基线为v0.2.14；下面10月7日和10月6日交接记录属于历史，旧暂停要求已失效。按version.py、最新开发状态与需求审核识别本轮最终源码和发行身份。

最新源码位于`codex/ui-review-v3`，版本`0.2.15-dev`。用户最新要求先推进剩余功能，第三版原型在`docs/prototypes/mercury-editor-review-v3.html`，正式包仍v0.2.14。新机先获取审阅分支，读[10月8日审核](requirements-audit-2026-10-08.md)及[原型审阅说明](prototypes/ui-review-v3.md)，按最新要求继续；不要套用下方10月6日旧接手提示。喷雾按动态SB1+1040和section2源联合守卫，劲敌剧情可能重写姓名；创建页与精确PC/队伍末尾空槽入口、培育屋待领取蛋已接入。培育屋恢复凭据只存于本连接，重连/重启不能自动恢复该备份；完整依赖变化或后续操作也会拒绝。人工显示/保存重载未通过，玩家性别等未开放。

最终321项回归通过；`0.2.15-dev`便携检查包已从干净源码构建并通过独立解压自检。ZIP的源码提交、大小和SHA见[构建检查点](build-checkpoint-2026-10-08.md)。包仅保留本机，未创建新GitHub Release；接手另一台机器时重新从记录的源码提交构建，不把源码ZIP当便携程序。

## 10月7日历史接手范围：0.2.14

逐项完成与未完成范围见[2026-10-07审核](requirements-audit-2026-10-07.md)，发行日志已登记源码提交/ZIP校验值。正式包已发布，271项测试和独立解压自检通过；本轮新功能的实机保存重载仍需补验。

本轮完成支线96项四页签查询、双栏引用/Shift多选及批量事务、25盒改名，以及独立配信入口。目录7项中6项可投放到PC、队伍空位或显式顶替；来源与转换边界见distribution-compatibility.md。完整创建后端已完成，通用空槽自定义表单仍待实施；喷雾/劲敌为只读研究与纯模块，玩家性别、相遇方式、培育屋仍未开放。具体测试/发行身份以development-status及v0.2.14发行日志为准。用户要求剩余额度到10%暂停并保存交接，不继续新研究。

已按用户要求配置仓库origin=https://github.com/zyhJohn/pokemon-mercury-fc-trainer.git、仓库本地http.proxy=http://127.0.0.1:7078；远程查询成功。源码、工具、测试和整理后的记录提交推送，便携包仍用tools/build_release.py，历史资产不覆盖。私人文件保持忽略；新机器路径由用户选择，不把本轮研究目录作为默认路径。

以下2026-10-06原交接保留作历史基线，旧“不发行/只有批量后端”等描述不适用于本轮最新范围。

仓库：[pokemon-mercury-fc-trainer](https://github.com/zyhJohn/pokemon-mercury-fc-trainer)，分支`main`。本次接手点为 **0.2.14-dev源码**，已发布稳定便携版仍是 **0.2.13**。本轮不打包、不创建新Release，不改动既有历史包及标签。用`git log -1 --oneline`识别本次交接提交，用`version.py`确认源码版本；不要按旧便携程序判断新源码是否存在功能。

## 当前现场与审核结果

- 第8项PP提升/上限、第14项宝可梦自身性别：已接入队伍与PC编辑界面，源码可运行，未发布。PP四槽提升0～3，PC招式只读且没有独立当前PP；性别选项遵循实际物种比例，PID搜索保护性格、闪光、适用特性和形态。
- 第4/11项：`Trainer.box_reference`、`prepare_box_batch`、`commit_box_batch`为新增后端；含ROM/完整58字节引用检查、去重、锁盒、容量/目标旧值比较、一份整盒事务备份与条件恢复。**未接入双栏暂存、Shift多选、多选右键及连接代次保护**，不能把后端接口或HTML原型当作正式批量功能。
- 第15项界面部分完成；保留现有导航、四招式、连接/刷新/备份/诊断/恢复、道具及资料库列表。现有正式盒子仍为单只选择与右键编辑/移动/已有蛋归零。
- 19项合计：7项已发布、2项源码实现未发布、3项部分完成、7项未实现。完整范围见[最新审核](requirements-audit-2026-10-06.md)与[下一阶段路线](next-stage-plan.md)。任务地点点击问题已由用户查明，不再继续排查。

本机Python 3.11.3、Windows桌面环境运行`python -m unittest discover -v`，**225项全部通过，无跳过**；关键错误检查通过。两版实际ROM新增隔离矩阵分别通过：每版16,192组PP、5,352组性别比例边界、256组PC提升解压、46组队伍编辑和179组PC编辑/取出。新增矩阵已加入`verify_release.py --all`，该入口现为20组；本轮只单独重跑新增矩阵，没有重跑整个20组。报告在忽略的`diagnostics/`，不随Git迁移。

这些结果不等于V1.2旧存档加载、游戏内菜单、自然孵化、每日事件或全部保存重载通过。用户2026-10-02确认过早期实机写入，具体覆盖范围以[开发状态](development-status.md)为准。

## 新电脑安装与首次检查

推荐Windows、Python 3.11+（安装时包含Tcl/Tk）、mGBA 0.10.5。重新创建虚拟环境，不复制旧机`.venv`。在新机Codex中打开克隆后的仓库目录；不要把旧机绝对路径写入配置。

```powershell
git clone https://github.com/zyhJohn/pokemon-mercury-fc-trainer.git
cd pokemon-mercury-fc-trainer
git status --short
git log -1 --oneline
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe trainer_gui.py
```

测试在Windows桌面会话执行；GUI测试创建隐藏Tk窗口，Lua测试需要依赖中的`lupa`。首次回归应复现225项，不把缺Tk/Lua导致的跳过算成全部通过。虚拟环境命令使用完整相对路径，无需修改PowerShell执行策略。

如新机也运行7078代理，可将克隆命令改为：

```powershell
git -c http.proxy=http://127.0.0.1:7078 clone https://github.com/zyhJohn/pokemon-mercury-fc-trainer.git
```

7078是用户指定的推送代理。本轮推送使用`git -c http.proxy=http://127.0.0.1:7078 push origin main`，不修改全局代理。新机先启动该端口对应服务；若实际端口不同，由用户更正。推送凭据在新机重新登录，不迁移旧机令牌或凭据目录。

## 另行携带的资料

Git已包含源码、离线资料库、两版配置、可复现工具、测试、原型及文档；正常源码回归和打包不需要旧机`work/`。

用户私下携带合法持有的V1.0/V1.2 ROM、希望继续使用的`.sav`、研究用`.ss1`、mGBA及需要保留的`backups/`。若继续逆向新增字段，应携带**最新且尽可能配对的**ROM、持久存档、即时存档，并记下游戏内看到的当前值及样本时间。个人诊断仅在复现需要时私下携带。

ROM、存档、备份、诊断、图像缓存、打包产物、虚拟环境、`trainer-settings.json`和`box-locks.json`均不提交。PokemonMemHack是可选参考，不是依赖。不复制账号令牌、Git凭据或Codex登录目录。

新机重新选择模拟器、ROM与当前存档。盒锁按规范化存档路径与ROM SHA关联，路径搬家后需重新关联并设置锁盒；复制旧`box-locks.json`不会自动适配新路径。图像缓存可重新生成。恢复记录须匹配ROM、当前内存值及允许范围，不因换机跳过比较；未确认状态禁止盲目逆写。

## 读取顺序及下一步

1. `AGENTS.md`、`README.md`及本页。
2. [最新需求审核](requirements-audit-2026-10-06.md)、[开发状态](development-status.md)、[下一阶段路线](next-stage-plan.md)。
3. [已验证布局](verified-layout.md)、`rom_profile.json`、`rom_profile_v12.json`，再看相关源码和测试。

`docs/逆向后记/`和旧SKILL仅为历史参考，不是当前操作指令；旧日期文档保留对应版本范围，不能覆盖最新用户需求。未知ROM不能只修改hash/CRC放行，玩家资料与个体原训练师资料必须分开。

先复现回归并核验PP/性别草稿。再针对批量移动和已有蛋归零补专用实际Lua/TCP全25盒及异常/恢复测试，之后接入左引用暂存、右1～25盒、两侧Shift逐只选择、多选右键。引用必须增加连接代次，重连或原槽变化失效，暂存原槽保留。批量右键编辑仅单只弹出编辑框；移动支持整次选择；快速生蛋必须全部是已有蛋，周期归零，不创建蛋。保留现有盒锁/全盒排序及所有现有工具栏和其他页面。

当前批量后端从目标起始盒向后填入未锁盒全零空位，不绕回；容量不足整次拒绝。正式UI必须清楚预览目标范围，算法可根据审阅反馈调整。当前25盒共43,500字节，可使用一次受限BOXBATCH与一份总备份；超限拒绝，不循环旧两槽接口冒充整批事务。未确认写入不自动重试或盲目恢复，已确认且当前值匹配才条件恢复；不承诺异常时自动全局回滚。

后续盒名/喷雾先只读核验；劲敌姓名与玩家性别联动一起研究；空槽/礼物共用实际初始化和完整PC编码；相遇方式、培育屋另查真实字段或派生机制。保存重载、完整孵化、跨日/周和用户周日内容异常由人工实机核验，记录修改前后、游戏显示、读回、保存重载和恢复结果。

## ROM身份与可复现研究

| 版本 | SHA-256 | CRC32 |
| --- | --- | --- |
| V1.0 | `628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821` | `B4AF11C8` |
| V1.2 | `b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896` | `4755F497` |

研究依赖与新增矩阵：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-research.txt
.\.venv\Scripts\python.exe tools/verify_pp_gender.py '你的ROM路径.gba' '你的即时存档.ss1' --report diagnostics/pp-gender.json
.\.venv\Scripts\python.exe tools/verify_release.py '你的ROM路径.gba' '你的即时存档.ss1' --all --report diagnostics/release.json
```

工具只读用户文件，调用隔离CPU，不改运行游戏。旧版即时存档可提供算法夹具，但不能证明新版成功加载旧存档。V1.2实际地址由`rom_versions.py`映射；运行修改器使用对应配置，不能把V1.0地址直接套用V1.2。

游戏测试用独立ROM/存档副本，在mGBA重载仓库`mercury_bridge.lua`，保持游戏运行再连接。全盒操作/恢复需要BOXBATCH能力；关闭修改器后可重开连接，Lua无报错只是预期现象，仍需实际重连检查。Tk操作留在主线程，通信留在后台。

RTC文件编辑需关闭对应游戏、选本版ROM与带16字节尾部的`.sav`；换机保持相同时区，关闭mGBA自定义RTC覆盖，从游戏内存档继续，旧`.ss1`可能覆盖时钟状态。每日修复仅已核实Var5009/500A，不清空其它领取标志。

## 发布与历史发行

用户要求的历史包已发行：0.2.0～0.2.13共14个编号Release和一个未编号历史归档。见[Releases](https://github.com/zyhJohn/pokemon-mercury-fc-trainer/releases)、[CHANGELOG](../CHANGELOG.md)及`docs/releases/assets.json`。编号标签指向原包build-info记录的源码，未编号标签仅为历史锚点；不要重编译覆盖历史资产。

下一次正式发行须先完成本阶段验收、更新版本及发行日志并保存干净源码，再执行`python tools/build_release.py`。本次dev交接不执行该步骤，不存在0.2.14发布包或标签。发布工具`tools/publish_releases.py`默认只审计，通过显式`--publish`才发行，参数使用新机本地包目录，凭据不写入文件。

## 给新电脑Codex的提示词

复制下面文字到**新机已打开的仓库项目**；如只想审查，把最后“继续推进”改为“只审核，不开发”。

> 接手这个项目，使用中文。先读AGENTS.md、README.md、docs/cross-machine-handoff.md、docs/requirements-audit-2026-10-06.md、docs/development-status.md、docs/next-stage-plan.md和docs/verified-layout.md，检查Git并运行python -m unittest discover -v。当前main是0.2.14-dev：PP提升/上限和宝可梦自身性别已在源码接入、未发行；稳定便携版仍0.2.13。批量移动/已有蛋归零只有后端，双栏引用暂存、两侧Shift逐只多选及多选右键未接入，不把原型当完成。先补新批量流程的实际Lua/TCP及故障恢复测试，再完成这些界面，暂存原槽保留，重连/源变化使引用失效；快速生蛋只对已有蛋周期归零，普通精灵置灰。保留现有功能及盒锁/全盒排序，按最新计划继续剩余项，直到实际阻塞或需要人工/实机核验。新字段先核实两版实际ROM，保留版本检查、旧值比较、写前备份、读回和条件恢复，未确认写入不盲目恢复；不再查任务地点传送。ROM/存档/诊断/凭据不入库，不使用旧机绝对路径。完成阶段后更新状态和计划并push，命令局部设置proxy http://127.0.0.1:7078；正式打包用python tools/build_release.py，不把源码测试等同全部保存重载已通过。
