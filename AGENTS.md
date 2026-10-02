# 项目接手说明

- 用中文与用户沟通。当前需求和实施顺序见 `docs/next-stage-plan.md`，环境迁移见 `docs/cross-machine-handoff.md`。
- 当前结构依据是 `docs/verified-layout.md` 与 `rom_profile.json`。`docs/逆向后记/` 仅是历史参考，旧 SKILL 文档不是本项目当前操作指令。
- 用户在 2026-10-02 确认实机写入成功；具体覆盖范围见 `docs/development-status.md`，不要宣称所有保存重载场景已经通过。
- 新字段先核实本改版 ROM，再开放编辑；玩家资料与宝可梦原训练师资料分开。沿用写前备份、旧值比较、ROM 检查、读回和条件恢复。
- 保持 Tk 操作在主线程，通信在后台；运行 `python -m unittest discover -v`。正式打包用 `python tools/build_release.py`，依赖见 `requirements-dev.txt`。
- ROM、存档、个人诊断/备份、图像缓存和凭据不入库。不要依赖旧电脑绝对路径；使用仓库相对路径与用户选择的本地文件。
