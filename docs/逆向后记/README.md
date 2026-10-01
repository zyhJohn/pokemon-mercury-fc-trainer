# 逆向后记（Reverse Engineering Notes）

本目录收录本次「宝可梦水银FC」内存逆向定位与修改器开发过程中，WorkBuddy（AI 助手）产出的完整工作流程、过程记录与可复用产物，供 GitHub 二次开发参考。

## 目录说明

| 文件 | 说明 |
| --- | --- |
| `逆向方法论-SKILL.md` | 可复用的 GBA 内存逆向「技能」定义：从「只有 ROM+存档」到「交付图形修改器」的完整流程、核心步骤与关键坑。适用于其它 GBA 改版的同类逆向。 |
| `workflow.md` | 定位内存地址的详细步骤、GDB RSP 协议说明、Tkinter 修改器模板。 |
| `gen3-structures.md` | Gen III 存档 / 队伍 / 背包 / 精灵子结构的完整偏移表（含本改版实测地址）。 |
| `gdbmem.py` | 可复用的 mGBA GDB 调试桩客户端（读/写内存，RSP 协议）。 |
| `工作日志-2026-10-01.md` | 本次开发的全过程工作日志：从金手指分析 → 失效根因定位 → 图形修改器 → 打包 → git 整理 → 无 GDB 连接，含每一步的实测地址、踩坑记录与修复方案。 |

> 说明：`逆向方法论-SKILL.md`、`workflow.md`、`gen3-structures.md`、`gdbmem.py` 来自 WorkBuddy 用户级技能 `gba-memory-hacking`，原为 AI 助手跨会话复用的方法论，现同步收录进本仓库以便随项目发布。

## 辅助逆向脚本（tools/）

项目根目录下的 `tools/` 收录了本次逆向过程中实际使用的辅助脚本：

| 脚本 | 用途 |
| --- | --- |
| `gdbmem.py` | mGBA GDB 桩客户端，读/写内存 |
| `search_mem.py` | 在 WRAM 区间按已知值特征搜索地址 |
| `search2.py` | 结构化搜索（金钱/背包等已知值定位） |
| `write_test.py` | 写入验证脚本 |
| `mock_bridge.py` | 模拟 Lua 桥接服务的测试桩，用于无 mGBA 环境端到端验证修改器协议 |

这些脚本仅依赖 Python 标准库（socket/struct/threading），无需额外安装。
