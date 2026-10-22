# 0.2.20-dev 异常记录检查包

从独立干净工作区源码 `32bb41f40c94f5da367595e17ab03652960e66e3` 执行标准 `python tools/build_release.py`，`build-info.json` 确认 `source_dirty=false`。工作分支 `codex/builtin-editor-compat` 推送原仓库；正式发行仍为v0.2.15，本轮没有新建正式Release。用户未提交的README和两份UI重构资料保留原状，未混入构建。

本轮新增水银专属异常/坏蛋只读诊断、原槽保留、队伍/PC单条删除及条件恢复，详情见[原生证据与功能边界](abnormal-records-2026-10-22.md)。水银不使用火箭队的单体checksum、80字节记录或地址。队伍删除/恢复同回调比较普通地图回调、ScriptContext2未锁定、战斗位、完整六槽和数量；PC只清目标58字节。

源码最终 `python -m unittest discover -v`：622项全部通过、无跳过（92.010秒）。干净标准构建再次622项通过（82.428秒），EXE自检、中文空格路径独立解压自检、ZIP成员允许清单/CRC/逐文件SHA全部通过。主控另外检查ZIP不含ROM、UPS、存档、即时存档或核心DLL，包含本轮核验文档。新模块/工具Ruff、整仓关键错误、桥接同步、源码自检及diff检查通过。独立审查发现的局部补丁绕过和UI错误覆盖两项P2均已修复并回归。

- ZIP：`MercuryTrainer-0.2.20-dev-portable.zip`，12,948,857字节。
- ZIP SHA-256：`eb688a03ce1b536119d6939df627084ccd9b4f3afb0a88ce5e1a0931a25723fc`。
- EXE SHA-256：`8d7e5133a6827f6aac920621046e64429d95a5566a607268f9fe9f05b26a8b90`。
- 构建时间（上海）：`2026-10-22T19:11:59.605832+08:00`。

检查包保存原项目忽略的 `dist/`，完整构建身份另存 `build-info-0.2.20-dev.json`。源码、测试、工具和公开工作记录提交Git；游戏文件、存档、私人诊断及二进制包不入库。使用时完整解压并重载包内 `mercury_bridge.lua`。

真实核心0.11的三版地图/Start菜单锁观察、原ROM隔离getter及PC解压矩阵、真实Lua/TCP合成RAM事务分别提供证据。**实际mGBA异常删除后的游戏保存、关闭重开尚未验收**，不能把上述证据扩大为全部保存场景通过。程序删除前提醒完整 `.sav` 备份，并为事务生成备份；结果不确定时保留原错误/路径、失效旧目标，不自动重放或恢复。
