"""Partial source evidence, never a complete move legality verdict."""


def describe_move_sources(species, level, moves, profile):
    entries = profile.get("level_up_learnsets", {}).get(str(species))
    result = []
    for slot, move in enumerate(moves):
        if not move:
            continue
        levels = sorted(set(n for ident, n in (entries or []) if ident == move))
        machines = [
            i
            for i in profile.get("tm_compatibility", {}).get(str(species), [])
            if profile.get("tm_moves", [])[i] == move
        ]
        labels = [
            f"TM{i + 1:03d}"
            if i < profile.get("tm_count", 120)
            else f"HM{i - profile.get('tm_count', 120) + 1:02d}"
            for i in machines
        ]
        if entries is None:
            status = "unverified"
            text = "该物种的等级学习表尚未核对"
        elif 0 in levels:
            status = "evolution"
            text = "列入本物种的进化招式表"
        elif any(n <= level for n in levels):
            status = "level"
            text = "列入等级学习表，当前等级已达到门槛"
        elif machines:
            status = "machine"
            text = "本物种支持此招式学习器"
        elif levels:
            status = "higher-level"
            text = f"等级学习表要求 {min(levels)} 级；其他途径未核对"
        else:
            status = "other-unverified"
            text = "未找到等级/学习器来源；遗传、教学、进化前等途径未核对"
        if labels:
            text += "；学习器：" + "、".join(labels)
        result.append(
            {
                "slot": slot + 1,
                "move": move,
                "levels": levels,
                "machines": labels,
                "status": status,
                "text": text,
            }
        )
    return result
