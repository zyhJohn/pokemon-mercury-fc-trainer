# 配信目录与兼容边界

本目录在 2026-10-07 从[水银百科配信页](https://sum-light.github.io/azoth-wiki/distribution/)读取到 **4** 张卡片。页面是静态 HTML，卡片内含 HOME 领取链接和 `PMH1` 手动密钥，没有独立数据接口，也未列配信日期。`distributions.json` 保存网页 SHA-256、四条完整密钥及逐条字段；日期为 `null`，不推测日期。`python tools/import_distributions.py` 可重新获取并比对，若官网卡片数量改变则先停止导入待人工核查。

四条密钥各解出 **58 字节**，恰是本版 PC 槽格式。用 V1.0 和 V1.2 ROM 配置解析，物种内部编号、等级、性别、特性、招式、OT、球种和 6V 与卡片吻合，`BoxPokemon.describe()` 无错误。因此四条作为 `mercury_fc_box58` **原生记录**提供。它们是水银 HOME 的配信记录，配信页所写的领取与 HOME 保存流程仍可使用。修改器的投放则须走自己的预览、空槽检查和事务提交。

另从 [Project Pokémon Events Gallery](https://github.com/projectpokemon/EventsGallery) 选取第三世代原版个体档案。每个源档案的 URL、SHA-256、文件长度、原始字段及转换差异在 `distributions.json`；导入器校验 SHA-256、Gen3 个体校验和、名称编码及不可存储的附加字段。解析规则对照 [PKHeX 的 PK3 字段实现](https://github.com/kwsch/PKHeX/blob/master/PKHeX.Core/PKM/PK3.cs) 和 [物种编号转换](https://github.com/kwsch/PKHeX/blob/master/PKHeX.Core/PKM/Util/Conversion/SpeciesConverter.cs)。本版映射必须同时匹配 ROM 目录的内部物种编号、全国图鉴号、招式和持物名称；不能仅以全国图鉴号作为内部编号。

| 来源 | 目录状态 | 处理 |
| --- | --- | --- |
| 水银 HOME 四条 | `verified`，原生 | 完整 58 字节记录，可投放。 |
| [10 ANIV Bulbasaur，RSEFL](https://github.com/projectpokemon/EventsGallery/blob/master/Released/Gen%203/ENG/10th%20Anniversary%20Celebration/Journey%20Across%20America/Top%2020/RSEFL%20-%2010%20ANIV%20Bulbasaur%20%2811B5%29%20%28ENG%29.pk3) | `verified`，适配，0.2.15-dev.2新增 | PID、姓名/OT及语言、物种/招式、IV/EV等已逐字段核验，两版原生压缩及取出一致。源当前PP为20/40/5/10，本版取出20/20/5/10；版本码不保留，目录明确披露。 |
| [10 ANIV Celebi，RSEFL](https://github.com/projectpokemon/EventsGallery/blob/master/Released/Gen%203/ENG/10th%20Anniversary%20Celebration/Journey%20Across%20America/Celebi/RSEFL%20-%2010%20ANIV%20Celebi%20%280BF5%29%20%28ENG%29.pk3) | `verified`，适配 | 源个体的 PID、OT ID/名、昵称、经验、物种、招式、IV/EV、球、相遇数值和亲密度映射成本版 58 字节模板；两版生成结果相同。原档案标示可用于火红/叶绿。 |
| [Berry Glitch 异色蛇纹熊，RS](https://github.com/projectpokemon/EventsGallery/blob/master/Released/Gen%203/ENG/Berry%20Glitch%20Shiny%20Zigzagoon/RS%20-%20Berry%20Glitch%20Shiny%20Zigzagoon%20%280009%29%20%28ENG%29.pk3) | `verified`，适配 | 与上例同样映射；原版持物 168 与本版目录均为枝荔果，PID 与本版普通特性槽匹配，保留异色判定。 |
| [FRLG 日文ポケパーク刺球仙人掌样本](https://github.com/projectpokemon/EventsGallery/blob/master/Released/Gen%203/JPN/%E3%83%9D%E3%82%B1%E3%83%91%E3%83%BC%E3%82%AF/Eggs%202005/JoySpot/FRLG%20-%20%E3%83%9D%E3%82%B1%E3%83%91%E3%83%BC%E3%82%AF%20Cacnea%20%28F6925C85%29%20%28JPN%29.pk3) | `pending`，资料展示 | 日文昵称假名尚无本版无损编码证据；`+76` 的值`0x80000000`为命运邂逅标志bit31，低位奖章计数为0。本版PC未证能保留该标志，禁止投放；不会擅自改名或清掉位。 |

三条可投放外部个体是**转换后的模板**，`native_format=adapted_gen3_pk3_to_mercury_fc_box58`，不是原版神秘卡片。原版 PK3 存有游戏版本、当前 PP 等本版 PC 不保存的资料；这些以 `source_fields` 与 `conversion_losses` 明示。PC 取出时游戏重新计算即时能力值及 PP。原版 Wonder Card、票券和事件脚本需要原版接收/脚本流程，不能当成水银盒子记录写入；[Events Gallery 的说明](https://github.com/projectpokemon/EventsGallery#readme) 对第三世代游戏内兑换另列专用工具。[Goppier 的第三世代发送程序](https://github.com/Goppier/GEN3PokemonDistributions)使用联机发送和接收游戏卡带，也不能据此推断本改版接收脚本兼容。这里未下载或纳入任何配信 ROM。

其他版本按资料类型判断：红宝石/蓝宝石/绿宝石的第三世代 **个体 PK3** 可以逐条映射，前述 RS 蛇纹熊已作为实例；GameCube 的 `CK3/XK3` 需另核其独有来源资料，不以 PK3 解析。第四世代及以后档案有本版记录没有的字段、形态或能力表示，不能直接导入；目前未提供可无损转换的后世代模板。目录中 `pending`/`unsupported` 只用于浏览说明，不进入可投放列表。数值结构验证不等于游戏内保存重载验收，也不证明原活动来源合法性。

本批验证命令：`python -m unittest tests.test_distributions -v`；ROM 级核验由 `python tools/verify_creation.py --help` 所列参数执行，结果须与所用 ROM SHA 一起记录。目录导入与单元测试无需游戏存档、ROM 或模拟器。
