# 内存地址定位工作流详解

## 1. mGBA GDB 桩（RSP 协议）

- 启动：`mGBA.exe -g game.gba`（端口 2345）；`-t 存档.ss1` 直接进游戏内状态。
- 单连接：断开后需重启 mGBA。
- 读内存：`$m<addr>,<len>#<ck>` → `$<hex>#<ck>`；写内存：`$M<addr>,<len>:<hex>#<ck>` → `$OK#...`。
- `<ck>` = 载荷字节和 & 0xFF；单次读取建议 ≤ 0x80 字节分块。
- 复用 `scripts/gdbmem.py` 中的 `GDBClient`（read/write/r8/r16/r32/w8/w16/w32）。

## 2. 用已知值搜索定位地址

1. 启动 mGBA + 载入即时存档（游戏内状态）。
2. 用 `GDBClient` 分块 dump 全 WRAM `0x02000000~0x0203FFFF`（256KB）到文件。
3. 以用户提供的游戏内已知值为特征搜索：
   - 金钱：`value.to_bytes(4,'little')`。
   - 训练师名：Gen III 字符编码（A=0xBB…Z=0xD4，a=0xD5…z=0xEE，0=0xA1…9=0xAA，0xFF 结尾）。
   - 第一格道具：`[ID u16][数量 u16]`。
4. 唯一命中即真实地址；多处命中则结合 ROM 引用次数、周边结构判定。

## 3. 结构基址动态定位

- 读 `gSaveBlock1Ptr`(`0x03005008`) → SaveBlock1 基址；金钱 = 基址 + `0x290`。
- 比硬编码更抗改版，是修改器推荐的定位方式。

## 4. 验证固定地址

- 在 ROM 中搜索该地址的小端 4 字节，出现多次即固定地址（如队伍 907 次、背包 14 次）。
- 0 次则可能是「基址+偏移」访问（如金钱经 `gSaveBlock1Ptr` 指针），需用指针动态定位。

## 5. 金手指输出

- GameShark v3 格式：`82AAAAAA VVVV`（16 位写）。u32 拆两行：低 16 位 + 高 16 位。
- 例：金钱 999999=`0x000F423F` → `820257BC 423F` + `820257BE 000F`。
- CodeBreaker 加密码（如穿墙 `509197D3...`）绑定原版 ROM 校验和，改版必失效，改版请只用 GameShark v3 地址码。

## 6. 图形修改器模板（Python Tkinter）

三层结构（单文件即可）：
- `GDBClient`：GDB RSP 读写（复用 scripts/gdbmem.py）。
- `Trainer`：数据访问层（locate/get_money/set_money/get_party/get_bag/set_bag_item…）。
- `App`：Tkinter 界面（Notebook 标签页 + Combobox 物种下拉 + Treeview 背包列表）。

要点：
- 用系统 Python 3.11（有 Tkinter）；精简版 3.13 无 Tkinter。
- 物种下拉加载 `names.json`（从 PokemonMemHack 的 UTF-16 数据文件解析）。
- 背包用 Treeview + 「写入选中/清空选中」。
- 改等级后需「存电脑再取出」刷新能力值，否则可能死机。
