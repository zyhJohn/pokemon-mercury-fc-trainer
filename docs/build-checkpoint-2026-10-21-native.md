# 0.2.19-dev 三版安全补丁检查包

2026-10-21，从独立干净工作区的源码 `d0e00894d4d863b01fe8196f455a3fd714987c68` 执行标准 `python tools/build_release.py`，`build-info.json` 确认 `source_dirty=false`。原工作区的README与两份UI重构资料保持原状，未混入构建。源码及公开核验记录推送原仓库 `codex/builtin-editor-compat`；正式发行仍为v0.2.15，本轮没有新建正式Release。

本轮新增1.0/1.2/1.33安全内置修改器完整结构契约，保留原三版本与原1.33内置UPS变体；详情见[真实核心核验](builtin-editor-native-audit-2026-10-21.md)。三版实际mGBA Libretro0.11核心正常打开伤药菜单、添加5→6、正常保存、另一进程仅加载电池重载后仍为6；未将此结果扩大为0.10.5取指、外置野生控制实际并用或所有字段验收。

源码最终完整578项无跳过通过（88.003秒）；干净构建再次578项无跳过通过（77.758秒）。源码及EXE自检、中文空格路径独立解压自检、ZIP成员允许清单/CRC/逐文件SHA通过。主控另外复查打包成员不含ROM、存档、UPS或核心DLL，全部文件SHA与build-info一致。三版实际目标的新版核验CLI均通过契约复现、离线/在线识别及Lua/TCP合成RAM/cart0的写入/读回/恢复、缓存CRC下FD00/FE00/payload改动拒绝；这一层不冒充真实核心并用。

| 本地产物 | 字节数 | SHA-256 |
| --- | --- | --- |
| MercuryTrainer-0.2.19-dev-portable.zip | 12,920,030 | db9c133ec0ba5641bb0dbe6133dd62794e1b33f45281f50600522928c5b9139b |
| MercuryBuiltin-safe-0.2.19-dev-patches.zip | 49,381 | 8eb48d208b08019af65ad77b359bcdb339a9cead2faecdd0be47e31f53daf6cf |

EXE SHA-256：`07b385318d6319c1777a4e752a56b4dd716e19ceee61b0f1cefb4df0c2d782cb`。构建时间：`2026-10-21T21:21:12.049070+08:00`。文件保存在原项目忽略的 `dist/`；`build-info-0.2.19-dev.json` 保存完整构建身份。二进制不提交Git。

补丁ZIP内只有三份24,049字节UPS、`checksums.json`和使用说明；核验全部输入/输出摘要、正反向恢复及ZIP CRC通过。每份完整安全UPS直接用于对应精确原版ROM副本，不叠加到已打补丁文件，不强制忽略校验；先备份自己的ROM和完整存档。原1.33 UPS及用户原ROM/存档未改变。

使用便携包时完整解压并重载包内 `mercury_bridge.lua`。当前检查包新增识别范围见核验文档；原19项中的喷雾/劲敌/玩家联动、通用空槽创建及UI重构等未在本轮实现，继续依照[后续计划](next-stage-plan.md)推进。
