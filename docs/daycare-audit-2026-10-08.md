# 培育屋待领取蛋：双版 ROM 有界核查

日期：2026-10-08。仅适用于 V1.0 SHA-256 `628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821` 和 V1.2 SHA-256 `b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896`。研究工具为 [`tools/audit_daycare.py`](../tools/audit_daycare.py)，使用本机两版 ROM、`.ss1`、`.sav` 的只读输入和 Unicorn 私有 RAM；未连接或写入运行中的 mGBA。原版 [FireRed daycare.c](https://github.com/pret/pokefirered/blob/master/src/daycare.c)、[CFRU daycare.c](https://github.com/Skeli789/Complete-Fire-Red-Upgrade/blob/master/src/daycare.c) 与 [CFRU save.c](https://github.com/Skeli789/Complete-Fire-Red-Upgrade/blob/master/src/save.c) 仅用来寻找候选，下面的地址和行为均由上述精确 ROM 核对。

## 已验证字段及调用

| 对象 | V1.0 / V1.2 | 实际证据 |
| --- | --- | --- |
| SaveBlock1 指针 | `0x03005008` | 两个即时存档均指向 `0x0202552C`，但程序应每次重读指针。 |
| 培育屋 | SaveBlock1 `+0x2F80`，样本为 `0x020284AC` | 两个父母槽各 `0x8C` 字节；槽内盒装记录从 `+0` 起，累计步数 u32 在各槽 `+0x88`。第二槽从培育屋 `+0x8C` 起。 |
| 旧后代 token / 孵化计数 | 培育屋 `+0x118` u16 / `+0x11A` u8 | 计步函数仍要求 token 为 0；本版待领蛋触发不设置此 token。`+0x11A` 是队伍蛋孵化计数，不是待领标志。 |
| 待领标志 | Flag `0x266`，SaveBlock1 `+0xF2C` bit 6 | `IsEggPending` 读取 FlagGet；`TriggerPendingDaycareEgg` 调 FlagSet。隔离 CPU 从清零状态触发时，SaveBlock1 中仅此字节 bit 6 变化。 |
| 自然计步 | `0x080462C4`，外层 `0x080463B8` | 前者按两只父母是否存在增加累计步数；token 为 0、两只均存在、第二槽累计步数低字节变成 `0xFF` 才调用兼容度；随机数再与兼容度比较，成功才触发待领旗标。外层还处理队伍蛋孵化。 |
| 兼容度 | `0x0804654C` → V1.0 `0x09D1989C` / V1.2 `0x09D1BE00` | 两版实际 ROM 函数执行了空父母、单父母、同性/同只父母和皮可西+百变怪测试。 |
| 触发 | `0x080459F0` → `0x09D1962C` / `0x09D1BB90` | 入口只调用 FlagSet `0x266`，**不检查父母或兼容度**。 |
| 待领查询 | `0x080463FC` → `0x09D19640` / `0x09D1BBA4` | 返回 FlagGet `0x266`。 |
| 领取造蛋 | `0x080460D4` → `0x09D18E4C` / `0x09D1B3B0` | 通过培育屋父母生成蛋，实际输出的物种、蛋位、继承 IV 与队伍数量如下。独立的通用 `CreateEgg` 钩子在 `0x08046150`，不能代替培育屋领取。 |

两版钩子地址的 Thumb 跳转目标和计步处对 GetBoxMonData、兼容度、触发包装函数的实际 `BL` 均由工具逐项核对。Flag `0x266` 的位定位同时用实际 FlagClear、FlagSet、FlagGet 与私有 RAM 差分证实。CFRU 存档按 SaveBlock1 每 `0xFF0` 字节分段：父母结构 `+0x2F80..+0x2FCF` 位于 Flash section 3 的 `+0xFA0..+0xFEF`，其余 `+0x2FD0..+0x309A` 位于 section 4 的 `+0..+0xCA`；标志位于 section 1 `+0xF2C`。工具只选择签名、校验和、计数一致的最新这三段。游戏正常保存后才会把 RAM 变化写到 `.sav`。

## 实际样本与隔离 CPU 矩阵

V1.0 `.ss1` 有皮可西 ID 36 与百变怪 ID 132，各累计 5502 步；ROM 兼容度为 20，待领旗标为 1，旧 token 为 0。V1.2 `.ss1` 两槽为空、兼容度及待领旗标均为 0。两版最新 `.sav` 均为空父母且待领旗标 0；V1.0 即时状态与持久存档属于不同游戏时点，不能拿这组文件证明一次保存重载。V1.0 样本的父母数据仅复制到隔离 CPU 私有 RAM，以便在 V1.2 重放同一有父母情形。

两版均通过相同 7 条计步情形：无父母、仅一只、复制同一父母成不兼容配对，兼容度都是 0 且不生成待领蛋；兼容配对但步数未到、旧 token 非零、随机数未命中时亦不生成；兼容度 20、第二槽低字节从 `0xFE` 进到 `0xFF` 且固定随机种子 0 时生成待领旗标。固定种子 1 不命中。所有情形计步函数均未直接在队伍创建蛋。额外负例证实：**空父母直接调用触发函数仍会置待领旗标**，所以该函数绝不能作为裸编辑入口。

从自然计步成功状态调用两版实际 `GiveEggFromDaycare`，在隔离 CPU 中各得到 ID 173 皮宝宝，蛋标志 1，至少三项 IV 与相应父母位置一致，队伍数量由 5 变 6。领取函数本身没有清除待领旗标；清除由完整 NPC 交互中的其他步骤负责，本次没有执行完整脚本。上述结果说明领取走的是本版父母/继承代码，不能用任意 `CreateMon` 或周期归零制造“培育屋新蛋”。IV 匹配是这组固定种子的输出证据，并非对所有遗传机制（招式、道具、球、特性等）的完整验收。

## 资格模块与原生 NPC 分支

新增 [`daycare_data.py`](../daycare_data.py) 和 [`daycare_layout.json`](../daycare_layout.json)。后一文件按两版完整 SHA 分开记录各 1432 个已核验物种的两蛋群、全国图鉴映射和性别比例；蛋群从本 ROM 的 `gBaseStats` 指针 `0x080001BC` → `0x0976DFBC`、每项 28 字节的 `+20/+21` 提取，全国号逐一调用本 ROM `0x08043298`，性别边界逐一调用 `0x0803F78C`。这避免把扩展形态的内部编号误作全国图鉴号。两版数据虽在本次相同，仍分别绑定精确 ROM SHA。`tools/verify_daycare_compatibility.py` 对两版各 1432 项回读实际 ROM，性别函数按 PID 0、127、255 核 4296 次/版，并对 14 个关键配对逐父母比较 `GetBoxMonData` 字段 0/1/5/11/45、性别函数及实际 `GetDaycareCompatibilityScore`，均通过。情形包括空/单父母、同性、无性别、百变怪双只和搭无性别、不可繁殖蛋群 15、同/异全国种与相同/不同 OT、扩展形态同全国号及蛋父母。另有 5 类坏记录拒绝（未核验物种、坏蛋标记、蛋位不一致、未知姓名编码、空槽残留）及两类有效旧字段接受（头部 hasSpecies 位清零、`+28` 为 FFFF）；不能照搬原版盒装校验和限制。

模块的 `read_daycare_snapshot(memory, rom_sha256)` 只读 SaveBlock1 指针、完整 `0x11B` 字节培育屋内容、Flag 字节，返回两份 80 字节明文父母记录（各封装在 `0x8C` 槽内）、昵称/OT 名、性别、步数、兼容度与明确拒绝原因。其父母记录**不是** PC 的 58 字节压缩格式。`prepare_pending_patch(snapshot)` 仅在两只有效非蛋父母、实际算法兼容度大于 0、旧 token 为 0 且当前无待领蛋时准备 `SaveBlock1+0xF2C` 的一个字节补丁；`after = before | 0x40`，原字节其他位保持。未知 ROM、无效指针、读取中变化会拒绝。蛋父母在 ROM 原兼容函数可能仍得到正分，因此模块额外拒绝。

独立审查另以隔离 CPU 做了两版各 **1932** 组兼容度补验，全部与纯函数一致。复现构造：使用 V1.0 即时存档的一个 `0x8C` 父母槽作明文模板；每版 1432 个已核验物种逐一搭配百变怪 ID 132，再用 `random.Random(16416)` 从同一物种集合预先抽取 500 对。逐对给两个槽写物种 `+32`、随机 PID `+0` 和同/异 OTID `+4`，清头部坏蛋/蛋位及 IV 蛋位，在私有 RAM 中比较 `parse_parent_record`/`compatibility_score` 与该版实际 `0x0804654C`；该补验是审查时的内联实验，没有持久诊断文件。工具自身的正式可复跑矩阵为上述 14 个具名边界情形。

对两版实际 NPC 脚本的有界静态字节与隔离 CPU handler 已加入 `tools/audit_daycare.py`：`0x08167DD1` 的 specialvar/比较/跳转链在队伍数量 0、5 时指向 `0x08167DEB` 领取分支，数量 6 指向 `0x08167DE1` 的满队提示并结束；`0x08167E04` 为 special `0xB8`（调用原生 `GiveEggFromDaycare`），随后的 `0x08167E07` 为 `clearflag 0x266`。两版以 5 只队伍成员执行两个实际命令 handler 后均由 5 变 6、Flag 先保持 1 再变 0，培育屋完整 `0x11B` 字节未改变。拒领脚本 `0x08167DC9` 也先清 Flag `0x266`，随后运行另一 special。本核查只覆盖相关命令和分支，不等于完整 NPC 对话、动画或保存重载验收。

## 对快捷操作的结论

本轮源码已通过 `Trainer.snapshot_daycare/prepare_daycare_egg/commit_daycare_egg` 接入受保护事务和 `DaycareWindow`。同一次 `BATCHVERIFYCRC` 回调比较 ROM 身份、SaveBlock1 指针、两份完整 `0x8C` 父母槽、`+0x118` token、`+0x11A` 计数、Flag 原字节和游戏状态后写入并读回；只允许两只有效非蛋父母且兼容度大于 0、当前未待领时置位。第二父母步数 `+0x114` 位于完整槽守卫内。ROM 裸触发函数自身无资格守卫；真正领取交给游戏原生 NPC 脚本及其 `GiveEggFromDaycare`，保留父母种类、IV/招式/球/特性等继承分支。只把已有蛋亲密度/孵化周期设为 0 属于另一项功能，不能用来表示培育屋待领蛋。

新增 `tests/test_daycare_transactions.py` 7项全部通过，其中32个实际Lua/TCP子案例覆盖两版提交和恢复的回调前父母、token、计数、Flag邻位、指针、战斗及队伍数量变化；另有回执丢失后的未确认备份拒绝恢复和重复事件旧备份拒绝。恢复凭据绑定当前Trainer实例、最新确认备份的路径/文件SHA及随机身份，同时复查完整依赖。仅本连接最近已确认的培育屋写入可条件恢复一次，后续培育屋写入尝试或相关恢复尝试使旧凭据失效，重连/重启后不能自动恢复；备份仍保留供诊断。这样避免领取A后生成B，恢复A误清B的问题。界面预览明确提示此范围。

仍需实机验证完整培育屋 NPC 对话和旗标清除、队伍满时处理、父母取出或改变后的行为、两版游戏内保存/重载及条件恢复；这些未由本次 CPU 矩阵覆盖。自然孵化的完整动画和保存重载也仍需实机验收。尤其 V1.2 当前样本没有真实寄放父母，虽然该版 ROM 对隔离复制父母的函数矩阵通过，仍需该版独立游戏事件样本。

复跑：向 `python tools/audit_daycare.py` 传入 `--v10-rom/--v10-state/--v10-save` 与 `--v12-rom/--v12-state/--v12-save` 六个本地路径，可用 `--report diagnostics/daycare/audit.json` 保存忽略目录中的结果；兼容度工具传两版 ROM 与 `.ss1` 四个路径，支持 `--report diagnostics/daycare/compatibility.json`。两工具拒绝不匹配的完整 ROM SHA，没有默认私人绝对路径。此次实际输出均为 `passed: true`；新增模块单测 `python -m unittest tests.test_daycare_data -v` 为 4/4 通过。隔离 CPU 结果、源码单测及实机游戏/保存重载是不同证据层级。
