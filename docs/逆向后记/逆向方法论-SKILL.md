---
name: gba-memory-hacking
description: GBA 改版/原版游戏的内存逆向定位与修改器开发工作流。当用户需要分析 GBA 游戏（尤其是宝可梦/火红改版）的内存地址、金手指、存档结构，或需要为 mGBA/VBA 编写/适配内存修改器、定位金钱/背包/队伍等数据地址时使用。覆盖 mGBA GDB 调试桩读写内存、Gen III 存档结构解析、用已知值搜索定位内存地址、Tkinter 图形修改器开发。触发词：GBA 金手指、内存修改器、内存地址定位、宝可梦改版、mGBA 修改器、PokemonMemHack、火红改版、存档分析。
agent_created: true
---

# GBA 内存逆向定位与修改器开发

针对 GBA 游戏（尤其宝可梦/火红改版）的完整逆向工作流：从「只有 ROM + 存档」到「定位全部关键内存地址并交付一个可用的图形修改器」。

## 何时使用

- 用户要求分析 GBA 游戏的金手指 / 内存地址 / 存档结构。
- 用户要开发或适配内存修改器（VBA→mGBA，或自建）。
- 需要定位金钱、背包、队伍、经验等数据的真实内存地址。
- 金手指失效，需要找出改版把数据移动到了哪里。

## 核心流程

按顺序执行，跳过不适用的步骤：

1. **识别 ROM 底包**：读 GBA 头 `0xA0`（标题 12B）、`0xAC`（游戏代码 4B，如 `BPRE`=火红）。改版通常保留原版引擎，原版金手指地址多为「结构偏移」问题而非完全失效。
2. **解析存档**：Gen III 128KB Flash 结构（见 `references/gen3-structures.md`），提取训练师名/金钱/队伍，作为后续内存搜索的「已知值特征」。
3. **连接 mGBA GDB 桩**：`mGBA.exe -g game.gba`（端口 2345）；用 `-t 存档.ss1` 可直接进入游戏内状态。用 `scripts/gdbmem.py` 读写内存（GDB RSP 协议）。
4. **用已知值定位**：以游戏内已知数值（金钱、训练师名、第一格道具）为特征，在全 WRAM（`0x02000000~0x0203FFFF`）搜索，唯一命中即真实地址。详见 `references/workflow.md`。
5. **动态定位优先**：通过结构指针（如 `gSaveBlock1Ptr` = `0x03005008`）+ 相对偏移定位，比硬编码地址更抗改版。
6. **验证固定地址**：在 ROM 里搜索该地址（小端 4 字节），出现多次说明是固定地址；否则是动态分配。
7. **交付**：金手指码（GameShark v3 格式 `82AAAAAA VVVV`）+ 可选的图形修改器（Python Tkinter，模板见 workflow.md）。

## 关键坑（务必注意）

- **改版可能移动整个结构体**：本案例火红改版把 SaveBlock1 前移 0x7C，导致金钱从 `0x02025838` 移到 `0x020257BC`。先测结构基址，不要盲信原版金手指。
- **security key = 0 时存档/子结构明文**：该值在 section0 + `0x0AF8`（副本 `0x0F20`）。为 0 则金钱/道具数量/精灵子结构都不需要 XOR 解密。
- **mGBA GDB 桩是单连接**：一个连接断开后需重启 mGBA 才能重连。GUI 应保持长连接，单次读内存建议 ≤ 0x80 字节分块。
- **GUI 用 Python 3.11**（含 Tkinter）；精简版 Python 3.13 常无 Tkinter。

## 参考文件

- `references/gen3-structures.md`：Gen III 存档/队伍/背包/子结构的完整偏移表（含本改版实测地址）。
- `references/workflow.md`：定位地址的详细步骤、GDB RSP 协议、Tkinter 修改器模板。
- `scripts/gdbmem.py`：可直接复用的 mGBA GDB RSP 客户端（读/写内存）。
