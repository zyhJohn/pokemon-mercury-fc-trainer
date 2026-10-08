# 第三世代待核个体审计（2026-10-08）

结论：日文 FRLG ポケパーク Cacnea 样本继续保持 `pending`，没有可无损投放的 58 字节模板。另抽查两条英文 10 ANIV 个体：妙蛙种子（11B5）符合现行适配规则，可作为**转换模板候选**；急冻鸟（4A78）与本版特性选择规则冲突，不能在现行规则下接入。本审计只针对这三个固定源文件，未进行活动来源合法性鉴定或游戏内保存重载验收。

## 可复跑材料与方法

- 只读脚本：[tools/audit_distribution_pending.py](../tools/audit_distribution_pending.py)。运行 `python tools/audit_distribution_pending.py` 会从 [Project Pokémon EventsGallery](https://github.com/projectpokemon/EventsGallery) 下载这三个 `.pk3`，逐条校验固定 SHA-256、原版个体校验和、字段及两套本地 ROM 配置，并输出 JSON，不改目录、ROM、存档或模拟器。原仓库的 [README](https://github.com/projectpokemon/EventsGallery#readme) 将个体档案与游戏内 Mystery Gift 兑换区分。
- 如需复跑 ROM 原生压缩/解压探针，在上述命令加 `--rom-v10 <V1.0.gba> --rom-v12 <V1.2.gba> --state-v10 <V1.0.ss1> --state-v12 <V1.2.ss1>`。脚本先校验 ROM SHA，再在隔离 CPU 内调用两版 ROM 的 80→58 压缩与 58→100 解压/取出函数，不写入实际 mGBA 会话或任何存档。`ss1` 仅提供内存初态；本次所用文件位于用户机器的 `D:\mGBA-0.10.5-win64`，文件不入库。
- 本次实际 ROM SHA-256：V1.0 `628607dcbeac3ab471310d5472c8fbd0df250745230207c488f66adbf1a43821`；V1.2 `b98d9701f4b567810c70221564c348f4482791c614c3f1bb282e96678b7a0896`。这与两份配置及 [pokemon_creation_layout.json](../pokemon_creation_layout.json) 所绑定的版本相同。
- 字段解释取自 [PKHeX `PK3.cs` 的姓名/语言字段](https://github.com/kwsch/PKHeX/blob/master/PKHeX.Core/PKM/PK3.cs#L1089-L1113)、[origins 与特性位](https://github.com/kwsch/PKHeX/blob/master/PKHeX.Core/PKM/PK3.cs#L1211-L1261)、[命运邂逅位](https://github.com/kwsch/PKHeX/blob/master/PKHeX.Core/PKM/PK3.cs#L1292-L1299)。其中 offset `0x4C` 的最高位是 `FatefulEncounter`，低位才是奖章信息；不能把 `0x80000000` 说成“有奖章”。

## 日文 FRLG Cacnea：阻断证据

[源档案](https://github.com/projectpokemon/EventsGallery/blob/master/Released/Gen%203/JPN/%E3%83%9D%E3%82%B1%E3%83%91%E3%83%BC%E3%82%AF/Eggs%202005/JoySpot/FRLG%20-%20%E3%83%9D%E3%82%B1%E3%83%91%E3%83%BC%E3%82%AF%20Cacnea%20%28F6925C85%29%20%28JPN%29.pk3)为 100 字节，SHA-256 `b1e5ab448c072d8ec7368fb0c69a0d8d97811ae41594139264b6d9d0dfb57a6a`，原版校验和 `0x4165` 正确。源内部物种 344 / 全国图鉴 331，本版两套目录均匹配；招式 ID 40、43、71、227，持物 0，PID `0x6D58E860`，OTID `0x2AEF0D18`。这些数值本身不是阻断项。

阻断在姓名及事件标志。源语言码 1（日文），昵称前五个原始字节为 `5b9a6851ff`；[本版姓名编码器](../name_codec.py) 无法把这组字节解为同一姓名，现行创建器只写已验证的语言码 2。OT 原始字节 `bdc2bbccffff00` 若误用本版字库会读成 `CHAR`，这不能证明日文源 OT 就是 `CHAR`。不可用改英文/中文名替代来声称无损。

源 ribbon word 为 `0x80000000`：bit31 的 `FatefulEncounter=true`，bit0–30 为零。因此**没有源奖章**，但有命运邂逅标志。实测把原源个体 offset 76 的该位单独翻转，再分别经过两版 ROM 原生压缩，两次得到的 PC58 完全相同（两版输出 SHA-256 均为 `bad092e1e308eba4b33a5e6c745bf59a59e6cd0731e79251e0ebe14d367916ab`）。本版 PC58 不能保留这个来源标志，[本地创建器](../pokemon_creation.py) 的复制范围也跳过源 76–79 字节。100 字节源还含队伍即时状态，取出时会重建。故此样本继续只做资料展示，不设投放模板。

## 英文个体抽查

| 个体 | 源档案与 SHA-256 | 结果 |
| --- | --- | --- |
| [10 ANIV 妙蛙种子 11B5](https://github.com/projectpokemon/EventsGallery/blob/master/Released/Gen%203/ENG/10th%20Anniversary%20Celebration/Journey%20Across%20America/Top%2020/RSEFL%20-%2010%20ANIV%20Bulbasaur%20%2811B5%29%20%28ENG%29.pk3) | `9206d065784efdbe538ffdd44950342f1f1e9eea3baeb26a09b9f4d5236a492d`，80 字节，校验和 `0x9887` | **可适配候选**。源内部编号/图鉴号 1/1，招式 230/74/235/76，持物 0；语言 2、姓名 `BULBASAUR`/`10 ANIV` 可编码；奖章、命运邂逅、标记和华丽大赛/病毒位均零。当前 [`parse_pk3`](../tools/import_distributions.py) 无需放宽规则即通过。 |
| [10 ANIV 急冻鸟 4A78](https://github.com/projectpokemon/EventsGallery/blob/master/Released/Gen%203/ENG/10th%20Anniversary%20Celebration/Journey%20Across%20America/Top%2020/RSEFL%20-%2010%20ANIV%20Articuno%20%284A78%29%20%28ENG%29.pk3) | `841e8f501fc8d8d5c8bb741ab4deb9feafb4974174fc896097219c579dae9675`，80 字节 | **不接入**。源特性位 0 与本改版该物种双普通特性时使用 PID 奇偶的规则冲突，两版创建器均拒绝。不能为了凑模板改 PID 或特性位。 |

妙蛙种子可供主实现者接入的审计结果：模板 SHA-256 `b4a569af9b5ce409c410823e726f4ad6e4a788acc22da413deda5e92de31cc34`，58 字节十六进制为：

```text
b6526bac0a000000bccfc6bcbbcdbbcfccff026aa2a100bbc8c3d0000100000080430500004604e628b10e1300000000000000ff4680df045c35
```

本地忽略目录的 [canonical row](../diagnostics/distribution-bulbasaur-candidate-2026-10-08.json) 由现有 `parse_pk3` 直接生成。源 PID `0xAC6B52B6`、完整 OTID `10`、等级 70、昵称/OT、语言码 2、经验 344960、四招、PP 提升、六项 IV/EV、持物、亲密度、球、相遇地点/等级及 OT 性别逐字段回比模板相同。两版 `BoxPokemon.describe()` 无结构错误且模板字节相同；两版原生 80→58 压缩逐字节等于模板，两版原生 58→100 解压及取出逐字节等于 `box_to_party_pokemon()`，队伍即时能力值均为 `(164, 84, 74, 84, 99, 114)`。来源**游戏版本码** 2 不进入本版 PC；源当前 PP `[20, 40, 5, 10]` 在本版取出时重算为 `[20, 20, 5, 10]`，第二招（ID 74）的 PP 随本版招式数据变化，须在目录中明示转换损失。队伍即时能力值也由取出流程重建。这是**转换个体模板**，不是神秘卡片，也不证明实机保存重载与活动合法性。
