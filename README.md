# 宝可梦水银FC 修改器（mGBA 版）

一个面向 **火红(BPRE)改版《宝可梦水银FC~致150年后的你》** 的图形化内存修改器，操作方式对齐 PokemonMemHack。
通过 **mGBA 的 Lua 内存桥接脚本**（推荐）或 **GDB 调试桩**（旧方式）读写游戏内存，无需修改 ROM 或存档文件。

> 本文档面向二次开发，包含完整的内存地址表、数据结构和逆向定位工作流。

---

## 一、项目简介

- **目标游戏**：`宝可梦水银FC~致150年后的你 Version 1.0.gba`（32MB）
- **ROM 底包**：Pokémon FireRed (US)，游戏代码 `BPRE`（`POKEMON FIRE`）
- **改版内容**：以金银(Johto)为主题，加入第四世代及之后的宝可梦/道具/技能，原创剧情
- **修改器技术栈**：Python 3 + Tkinter（GUI）↔ mGBA 内存桥接（Lua socket 服务 或 GDB 桩）

### 已实现功能

| 标签页 | 功能 | 状态 |
|---|---|---|
| 数值 | 金钱、代币 | ✅ 已验证 |
| 队伍 | 物种（下拉+中文名）、等级、HP | ✅ 已验证（物种 5/6 正确） |
| 背包 | 道具查看/修改/添加/删除（含改版扩展道具名） | ✅ 已验证 |
| 能力值 | 个体值(IV)、努力值(EV)（队伍 6 只 × 6 属性） | 🆕 待实测验证 |
| 训练师 | 主角名字、劲敌名字、性别 | 🆕 待实测验证 |

> 🆕 标记的功能基于 Bulbapedia Gen III 标准结构实现，已通过 mock 端到端验证，
> 但该改版魔改了引擎，**实际地址需在游戏内实测确认**（尤其劲敌名、性别、子结构 IV/EV）。

---

## 二、目录结构

```
pokemon-mercury-fc-trainer/
├── README.md           # 项目说明 + 技术文档（本文档）
├── LICENSE             # MIT 许可证
├── .gitignore          # Git 忽略规则（含游戏 ROM/存档）
├── trainer_gui.py      # 修改器主程序源码（Tkinter）
├── mercury_bridge.lua  # mGBA 内存桥接脚本（不用 GDB，推荐方式）
├── names.json          # 中文名称表（PokemonMemHack + azoth-wiki 道具表合并）
├── 启动修改器.bat       # 双击入口（实际逻辑在 .ps1 里）
├── 启动修改器.ps1       # 一键启动核心脚本（PowerShell，处理中文/空格路径）
├── docs/                # 逆向后记：逆向方法论 + 完整工作日志（见 docs/逆向后记/）
│   └── 逆向后记/
├── tools/               # 逆向辅助脚本（gdbmem / 地址搜索 / 写入验证 / 桥接测试桩）
└── dist/
    └── 水银FC修改器.exe  # 打包产物（被 gitignore，发布到 Releases）
```

> `docs/逆向后记/` 收录本次逆向开发的完整工作流程、过程记录与可复用方法论（原为 WorkBuddy 的
> 用户级技能 `gba-memory-hacking`，现随仓库发布），`tools/` 收录实际使用的逆向辅助脚本，
> 详见 `docs/逆向后记/README.md`。二者面向二次开发与「复现同类 GBA 改版逆向」场景。

> 说明：`启动修改器.bat` 假定本目录位于 mGBA 安装目录内（与 `mGBA.exe` 同级或子目录），
> 通过 `启动修改器.ps1` 启动 mGBA（正常模式，无需 GDB）与修改器 exe。改用 PowerShell 的原因：
> `start` 命令对含空格/中文的 ROM 路径解析不可靠（会报「系统找不到文件」）。
> 游戏 ROM / 存档（`.gba` / `.sav` / `.ss1` / `.ss2`）涉及版权与个人数据，已加入 `.gitignore`，勿上传。

依赖的 PokemonMemHack 数据文件（用于生成 `names.json`）：
`ItemNameList.txt` / `BreedNameList.txt` / `SkillNameList.txt` / `PersonalityList.txt` / `SpecNameList.txt`
（UTF-16 编码，格式：每行 `{编号}{中文}{日文}{英文}`）

---

## 三、核心成果：内存地址表

> 这是整个项目最有价值的产出，二次开发可直接复用。

### 3.1 关键地址（本改版实测）

| 名称 | 地址 | 说明 |
|---|---|---|
| `gSaveBlock1Ptr` | `0x03005008` | SaveBlock1 结构指针（IWRAM，动态定位用） |
| SaveBlock1 基址 | `0x0202552C` | 通过上述指针读出 |
| **金钱** | `0x020257BC` | = SaveBlock1 + `0x290`，u32 |
| **代币** | `0x020257C0` | = SaveBlock1 + `0x294`，u16 |
| **队伍** | `0x02024284` | 6 只 × 100 字节 |
| **队伍数量** | `0x02024029` | u8 |
| **背包道具口袋** | `0x0203BB20` | 每格 4 字节 `[道具ID u16][数量 u16]`，明文 |

### 3.2 重要结论：SaveBlock1 被改版前移

- 原版火红 SaveBlock1 基址约 `0x020255A8`，本改版为 `0x0202552C`，**前移了 0x7C 字节**。
- 因此原版火红金手指全部失效：金钱 `0x02025838` → 本改版 `0x020257BC`。
- 队伍/经验地址（`0x02024284` / `0x02023D50`）不在 SaveBlock1 内，**未受影响**。

### 3.3 队伍宝可梦结构（100 字节）

| 偏移 | 大小 | 字段 |
|---|---|---|
| 0x00 | 4 | 性格值（personality） |
| 0x04 | 4 | 原训练师 ID（OT ID，= 公开ID | 秘密ID<<16） |
| 0x08 | 10 | 昵称 |
| 0x12 | 2 | 语言 |
| 0x14 | 7 | 原训练师名 |
| 0x1C | 2 | 校验和（本改版恒为 0，即未校验） |
| 0x20 | 48 | 4 个子结构 × 12 字节（见 3.4） |
| 0x50 | 4 | 状态 |
| 0x54 | 1 | **等级** |
| 0x56 | 2 | **当前 HP** |
| 0x58 | 2 | **最大 HP** |
| 0x5A | 2 | 攻击 |
| 0x5C | 2 | 防御 |
| 0x5E | 2 | 速度 |
| 0x60 | 2 | 特攻 |
| 0x62 | 2 | 特防 |

### 3.4 子结构顺序（性格值 % 24）

48 字节数据区按 `personality % 24` 排列为 4 个子结构（Growth/Attacks/EVs/Misc）：

```
0: GAEM  1: GAME  2: GEAM  3: GEMA  4: GMAE  5: GMEA
6: AGEM  7: AGME  8: AEGM  9: AEMG 10: AMGE 11: AMEG
12: EGAM 13: EGMA 14: EAGM 15: EAMG 16: EMGA 17: EMAG
18: MGAE 19: MGEA 20: MAGE 21: MAEG 22: MEGA 23: MEAG
```

**Growth 子结构（含物种）**：

| 偏移 | 大小 | 字段 |
|---|---|---|
| 0 | 2 | **物种**（= 全国图鉴编号，1~386 原版，>386 为改版新增） |
| 2 | 2 | 携带道具 |
| 4 | 4 | 经验值（本改版布局异常，见「已知问题」） |
| 8 | 1 | PP 加成 |
| 9 | 1 | 亲密度 |

**重要**：本改版 `security key = 0`，且子结构校验和恒为 0 —— 意味着**子结构是明文存储，不需要 XOR 解密**（原版需 `OTID ⊕ personality` 解密）。

### 3.5 存档文件结构（Gen III 128KB Flash）

```
偏移        内容
0x000000    存档块 A（14 扇区 × 4096B）
0x00E000    存档块 B（14 扇区 × 4096B）
0x01C000    殿堂（Hall of Fame）
0x01E000    神秘礼物
0x01F000    战斗记录
```

- 每扇区 = 3968 字节数据 + 128 字节尾部。
- 尾部：`0xFF4` 扇区ID(u16)、`0xFF6` 校验和(u16)、`0xFF8` 签名 `0x08012025`、`0xFFC` 存档索引(u32)。
- 存档索引大的为最新存档块。
- 本改版 `security key`（section0 + `0x0AF8` / `0x0F20`）为 0，即存档明文。

---

## 四、逆向工程工作流

> 如何从「只有 ROM + 存档」到「定位出所有内存地址」，可用于其它 GBA 改版。

1. **识别 ROM 底包**：读 GBA 头 `0xA0`（游戏标题 12B）、`0xAC`（游戏代码 4B，如 `BPRE`）。
2. **解析存档**：按 Gen III 128KB 结构定位存档块、扇区、section；提取训练师名/金钱/队伍（security key=0 时明文）。
3. **启动 mGBA GDB 桩**：`mGBA.exe -g game.gba`（端口 2345），可加 `-t 存档.ss1` 快速进入游戏内状态。
4. **连接并读写内存**：用 GDB RSP 协议（见 3.6）。
5. **用已知值搜索定位**：以游戏内已知的数值（如金钱 118464、训练师名、第一格道具）为特征，
   在全 WRAM（`0x02000000~0x0203FFFF`）搜索，唯一命中即真实地址。
6. **动态定位**：通过 `gSaveBlock1Ptr` 指针 + 相对偏移定位，比硬编码地址更抗改版。
7. **固定地址验证**：在 ROM 中搜索该地址（小端 4 字节），出现多次说明是固定地址。

### 3.6 mGBA GDB 桩协议（RSP）

- 端口 `2345`，单连接（断开后需重启 mGBA）。
- 读内存：`$m<addr>,<len>#<校验和>` → 返回 `$<hex>#<校验和>`（校验和 = 载荷字节和 & 0xFF）。
- 写内存：`$M<addr>,<len>:<hex>#<校验和>` → 返回 `$OK#...`。
- 单次读取建议 ≤ 0x80 字节分块。

参考实现见 `trainer_gui.py` 中的 `GDBClient` 类。

---

## 五、使用方法

修改器支持两种连接方式，**自动尝试**（优先 TCP 桥接，失败回退 GDB）。

### 方式 A：TCP 桥接（推荐，不用 GDB、不用 cmd）

1. 正常启动 mGBA 并进入游戏（**直接双击 `mGBA.exe` 即可，无需任何参数**）。
2. 在 mGBA 菜单 `Tools → Scripting → File → Load Script` 加载一次 `mercury_bridge.lua`
   （脚本加载后常驻，直到关闭 mGBA；在 mGBA 控制台会显示 `listening on port 8888`）。
3. 双击 `水银FC修改器.exe`，点「连接」。

> 之后每次只需「双击 mGBA → 加载脚本 → 双击修改器 → 连接」，全程无命令行。

### 方式 B：GDB 桩（旧方式，兜底）

```
mGBA.exe -g "游戏.gba"     # 带 -g 参数启动
python trainer_gui.py       # 或双击 exe
```

### 一键启动

双击 `启动修改器.bat`（内部调用 `启动修改器.ps1`）会自动启动 mGBA + 修改器 exe
（mGBA 用正常模式，不用 GDB；仍需在 mGBA 里手动加载一次 `mercury_bridge.lua`）。

> 注意：源码运行需带 Tkinter 的 Python（系统 Python 3.11）；某些精简版 Python（如 3.13）无 Tkinter。exe 已内置运行时，无需 Python。

**启动后请先在 mGBA 窗口按按键进入游戏**（mGBA 启动后停在标题画面，需手动按 Start/回车进入），
进入游戏后 `gSaveBlock1Ptr` 才会被初始化，修改器「连接」后才能读到金钱/队伍等数据。

1. 点「连接」→ 状态栏变绿（显示连接方式：`TCP 桥接` 或 `GDB 桩`）。
2. 改数值/队伍/背包 → 点「写入全部」或「写入选中」。
3. 「刷新」重新读取。

### 常见问题

- **「mGBA -g 启动后无反应」**：mGBA 其实是正常启动了（窗口标题 `mGBA - POKEMON FIRE - 0.10.5`），
  只是停在标题画面等你按键进入游戏，不是卡死。
- **「一键启动无效」**：旧版 `启动修改器.bat` 用 `start` 命令启动，遇到含空格/中文的 ROM 路径会报
  「系统找不到文件」。现已改用 PowerShell 脚本，请确认 `启动修改器.bat` 与 `启动修改器.ps1` 都在同一目录。
- **修改器点「连接」失败**：优先用方式 A（加载 `mercury_bridge.lua`）；若用方式 B，需 `-g` 启动 mGBA。
  GDB 桩是单连接，一次连接失败需重启 mGBA 再连。

---

## 六、依赖与运行环境

- Windows + mGBA 0.10.5+（Lua 脚本功能，或 GDB 桩）
- **exe 版**：无需任何依赖（Python 运行时已内置）
- **源码版**：Python 3.11（含 Tkinter 8.6），仅标准库
- 打包：PyInstaller 6.x（`--onefile --windowed --add-data "names.json;."`）

---

## 七、已知问题与 TODO

- [ ] **经验值字段**：本改版魔改了 Growth 子结构的 exp 布局，读出来多为乱码（少数精灵正常），需进一步逆向。
- [ ] **队伍第 5 只精灵物种**：偶发解码异常（数据结构边缘情况），需定位子结构顺序的例外规则。
- [ ] **其它背包口袋**：仅定位了道具口袋（`0x0203BB20`），重要道具/精灵球/技能机/树果口袋待定位。
- [ ] **mGBA GDB 单连接**：修改器断开后需重启 mGBA 才能重连。
- [ ] **扩展精灵/道具名**：编号 >386（精灵）/ >376（道具）的中文名未收录，暂显示「扩展#编号」。
- [ ] **PC 盒子 / 缎带 / 个体值(IV) / 努力值(EV) / 招式**：尚未在 GUI 中提供编辑。

---

## 八、二次开发指引

### 8.1 扩展思路

- **加新字段**：在 `Trainer` 类加 `get_xxx/set_xxx`，在 `App` 类加对应标签页/控件，`_refresh`/`_write_all` 中接上即可。
- **支持其它改版/游戏**：改 `ADDR` 表 + 用「工作流」重新定位地址即可。
- **后端扩展**：当前已支持两种内存后端（`MemClient` TCP 桥接 + `GDBClient` GDB 桩）。
  如需第三种（如 ReadProcessMemory），实现相同的 `r8/r16/r32/w8/w16/w32/read/write` 接口即可无缝接入 `Trainer`。
- **做成独立 exe**：用 PyInstaller 打包 `trainer_gui.py`。

### 8.2 关键文件说明

- `trainer_gui.py`：单文件，含 `MemClient`（TCP 桥接）、`GDBClient`（GDB RSP）、`Trainer`（数据访问层）、
  `App`（GUI）四部分。`Trainer` 只依赖内存客户端的通用接口（`r8/r16/r32/w8/w16/w32/read/write`），
  两种后端可无缝切换；`App._do_connect()` 优先尝试 `MemClient`，失败回退 `GDBClient`。
- `mercury_bridge.lua`：mGBA Lua 内存桥接脚本，在 mGBA 内监听 TCP 端口（默认 8888），
  用 `emu:read8/read16/read32/write8/.../readRange` 提供内存读写服务，替代 GDB 桩（无需 `-g`、无需 cmd）。
- `names.json`：`{breeds: 物种, items: 道具, skills: 技能, pers: 性格, specs: 特性}`，键为十进制编号字符串。
- `启动修改器.bat`：双击入口，仅一行 `powershell -ExecutionPolicy Bypass -File 启动修改器.ps1`。
- `启动修改器.ps1`：核心启动逻辑。用 PowerShell `Start-Process` 启动 mGBA 与修改器 exe，
  规避 bat `start` 命令对含空格/中文路径的解析坑；脚本本身保持纯 ASCII，避免 Windows PowerShell 5.1
  按 GBK 读取无 BOM UTF-8 文件导致中文乱码。

### 8.3 内存桥接协议（mercury_bridge.lua ↔ MemClient）

纯文本协议，每条命令以换行结尾，响应以换行结尾：

| 命令 | 参数 | 响应 |
|---|---|---|
| `PING` | — | `PONG` |
| `READ <hex addr> <hex len>` | 地址、长度 | 十六进制字节串 |
| `READ8/16/32 <hex addr>` | 地址 | 十进制值 |
| `WRITE8/16/32 <hex addr> <dec val>` | 地址、值 | `OK` / `ERR` |
| `WRITE <hex addr> <hex bytes>` | 地址、字节串 | `OK` / `ERR` |

端口默认 `8888`，被占用时脚本自动 `+1` 递增；`MemClient.connect()` 会从默认端口向上扫描并握手 `PING/PONG`。

### 8.3 构建（打包 exe）

需带 Tkinter 的 Python 3.11 + PyInstaller：

```bash
# 1. 建虚拟环境并装 PyInstaller
python3.11 -m venv venv-build
venv-build/Scripts/pip install pyinstaller

# 2. 打包（--add-data 用分号分隔，Windows 专用）
venv-build/Scripts/pyinstaller --onefile --windowed --clean \
  --name MercuryTrainer --add-data "names.json;." trainer_gui.py
```

产物在 `dist/MercuryTrainer.exe`，可重命名为 `水银FC修改器.exe`。
源码中 `resource_path()` 用 `sys._MEIPASS` 定位打包内的 `names.json`，勿删。

---

## 九、免责声明

本项目仅用于学习与个人单机游戏，请勿用于商业用途或侵犯版权。
