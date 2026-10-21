# 三版安全内置修改器：真实核心与开发版契约

2026-10-21，0.2.19-dev。原版1.0/1.2/1.33及原1.33内置补丁支持保留，新增三份安全变体的结构兼容契约。安全变体修复图像/调色板空项回退和背包发放越界；1.0另有两项地址重定位。实现细节见[隔离执行研究](builtin-editor-runtime-audit-2026-10-21.md)。本轮真实核心验证的是下表安全目标，不把原UPS和安全变体混称同一文件。

## 实际通过的流程

三版均从自己的原ROM生成独立安全副本，注入用户提供的电池存档前128 KiB；不加载即时存档，不直接修改RAM。按键正常继续游戏，打开背包，使用伤药进入“悠然 beta0.52”，通过内置道具页添加一瓶伤药，内存数量5→6；退出菜单，游戏内正常保存并等待完成，导出电池存档。关闭核心后启动另一进程，冷启动相同ROM，仅加载导出的电池存档，继续游戏后数量仍为6。

| 版本 | 契约ID | 安全ROM CRC32 | 父/冷启动进程PID | 保存计数前→后 |
| --- | --- | --- | --- | --- |
| 1.0 | builtin-editor-safe-v10 | 85c03f81 | 71144 / 66856 | 190,191→192,191 |
| 1.2 | builtin-editor-safe-v12 | 0f613575 | 11356 / 50088 | 216,215→216,217 |
| 1.33 | builtin-editor-safe-v133 | 19b04315 | 3100 / 55352 | 216,215→216,217 |

三版均有真实240×160核心画面，菜单、发放成功、保存及重载画面已人工检查。每次保存前后各两个完整槽、每槽14个基础section校验均通过。1.33输入电池与1.2样本相同，因此也验证了这份旧存档的1.33继续游戏流程；不代表所有旧档迁移均通过。原ROM、UPS及存档保持不变。

本机私有证据位于忽略目录 `work/mgba-runtime-20261021/public-v10/`、`public-v12/`、`public-v133/`：`result.json`、recipe、真实帧PNG和电池副本。公开仓库仅存工具、合成测试、身份与方法，不上传游戏文件、个人画面、存档或核心DLL。报告中的 `runtime_compatibility_registered=false` 记录实验时未登记的事实，随后才接入本文契约。

## 核心与方法边界

使用官方Libretro buildbot的Windows x64 mGBA核心，实际 `retro_get_system_info` 为 **0.11-212-7a12d6d**。DLL SHA-256：`7ebe8776ff4c8a13fe279b6f87b2b97282bb0e096905da3871a425e0b8d9b22d`。前端调用真实CPU/PPU/IRQ及保存实现，不采用上一阶段的BIOS/DMA替身。核心不随修改器打包。

本机0.10.5 SDL副本未成功建立GDB监听，已关闭该独立进程；不能将0.11核心结果标为0.10.5通过。尚待：0.10.5运行期代码覆盖取指刷新、外置野生遭遇控制与内置菜单实际并用、地图/战斗全流程、内置修改器全部字段及更多保存异常场景。闪光XOR8显示、蛋编辑CreateMon路径沿用既有问题记录，不在此次默改。

128 KiB后的16字节RTC尾部未注入核心，RTC/日历不在本轮验收范围。section基础校验不覆盖本改版所有扩展寄存数据（包括伤药所在扩展字段），因此以新进程实际读取数量6作为持久化证据，不以基础校验单独推断。只读分析器不选择“最大计数”为最新槽，避免计数回绕误判。

## 完整结构检查

`rom_patch_compatibility.json` 保留原1.33补丁条目，新增三条开发检查契约。每条164段，保留对应原版全部保护几何，并覆盖完整 `08A7A000..08A80000`，包括FD00图像guard与FE00背包guard。1.0校验9,002,146字节，1.2/1.33各9,002,173字节。完整配置重提取相同，90项事务/未知图腾/野生代码守卫不变；唯一元数据变化仍是道具13使用回调，整张道具表继续保护。

实际ROM身份与原版布局身份分开；目标CRC不加入原版快速白名单。已知/未知文件仍须通过完整指纹，写前完整复验能拒绝缓存CRC下的payload或两个新guard改动。只登记这三份完整配方及结构兼容文件，不声称任意1.0x/1.1x/1.2x/后续版都已适配。

| 版本 | 安全ROM SHA-256 | 派生UPS SHA-256 |
| --- | --- | --- |
| 1.0 | 093a110d43d9adbd1aa4a8b2fcdc54dff0eebce8a343b8f54354dbdb5d540de3 | 8310111595206cc4b1ab011c6199cac0c134387658977d5af062e16374c003cc |
| 1.2 | 2803552d5e5bb37855080bdef5116fc75f8f8064ddfd7f6b6024dd5a338739d5 | 0185857d265aa00d3465a7d29fd20d078671a17d66e3b43d49c423ff6baeb5ca |
| 1.33 | 440441659ee4273a3e0a7af87207a160ee5406deceef2359c991659316bcafb5 | f91014b7347e123dff550c03ab36eb4ace632d2b1cf581197aa7b3622554a68d |

## 重现

本机选择自己的可信核心、精确原版和原1.33 UPS。`tools/verify_builtin_native.py --help` 给出所需参数；示意：

```powershell
python tools/verify_builtin_native.py --core <mgba_libretro.dll> --expected-core-version 0.11-212-7a12d6d --source <原版.gba> --template <原版1.33.gba> --ups <原1.33.ups> --save <原版.sav> --output <不存在的独立目录>
python tools/verify_builtin_patch.py <原版.gba> <原1.33.ups> --safe-template <原版1.33.gba> --lua-tcp --report <独立报告.json>
```

第一项使用 `tools/native_recipes/` 内已核验按键配方，夹具要求当前伤药数量恰为5，且菜单/地图状态匹配；不适用时应失败并检查画面。允许自定义的recipe仅支持闭合集合操作，不允许代码或RAM写入。自定义recipe成功仅证明其断言与持久化，不能自动证明经过内置菜单。第二项验证离线/在线结构及真实Lua/TCP事务，RAM/cart0仍为合成夹具，不替代前项真实核心结果。RSP客户端是另一路研究工具，本轮没有真实SDL连接成功证据。

核心接口依据：[Libretro官方头文件](https://github.com/libretro/RetroArch/blob/v1.19.1/libretro-common/include/libretro.h)、[mGBA对应核心实现](https://github.com/mgba-emu/mgba/blob/7a12d6d/src/platform/libretro/libretro.c)、[官方核心下载](https://buildbot.libretro.com/nightly/windows/x86_64/latest/mgba_libretro.dll.zip)。下载地址会更新，重现须核对报告中的实际版本和DLL摘要。

## 回归与产物

最终 `python -m unittest discover -v`：578项全部通过、无跳过（88.003秒）。涵盖三安全契约正负识别、缓存CRC下两安全洞/payload/代码/数值表破坏拒绝、RSP通信合成协议、核心适配器ABI、闭合recipe及父子进程报告、存档槽结构与校验。Ruff、关键错误检查、桥接生成同步、源码自检和diff检查通过。独立审查复现三份真实目标完整契约，并对FD00/FE00/FFFF九组篡改全部拒绝；公共三版真实核心结果亦经独立复核。

本地 `dist/builtin-editor-safe-0.2.19-dev/` 保存三份24,049字节的开发UPS、校验清单及使用说明；均已重算完整SHA、目标身份并执行双向UPS恢复。每份直接应用于对应精确原版副本，包含原内置功能和安全修复，不向已打补丁的ROM叠加。二进制不提交Git。应用检查包从独立干净源码构建，构建身份和ZIP摘要另记检查点；本轮不新建正式Release。
