"""Held-item form rules verified against Mercury's actual ROM, not a general dex."""

ARCEUS_SPECIES = (546, *range(720, 736), 834)
SILVALLY_SPECIES = (990, *range(1048, 1065))
GIRATINA_SPECIES = (540, 718)
GENESECT_SPECIES = (702, *range(747, 751))
DIALGA_SPECIES = (536, 919)
PALKIA_SPECIES = (537, 920)
HELD_FORM_FAMILIES = (
    ARCEUS_SPECIES,
    SILVALLY_SPECIES,
    GIRATINA_SPECIES,
    GENESECT_SPECIES,
    DIALGA_SPECIES,
    PALKIA_SPECIES,
)


def held_form_family(species):
    return next((family for family in HELD_FORM_FAMILIES if species in family), None)


def held_form_species(species, item, ability, profile):
    if held_form_family(species) is None:
        return species
    metadata = profile["items"].get(str(item))
    if metadata is None:
        raise ValueError("携带道具不在已核对表中，无法推算形态")
    rules = profile["held_forms"]
    effect, parameter = metadata["hold_effect"], metadata["hold_parameter"]
    if species in ARCEUS_SPECIES:
        if ability != 180:
            return species
        if effect == 72 or (effect == 130 and item not in rules["special_z_crystals"]):
            return rules["arceus_types"][parameter] or 546
        return 546
    if species in SILVALLY_SPECIES:
        if ability == 167 and effect == 87:
            return rules["silvally_types"][parameter] or 990
        return 990
    if species in GIRATINA_SPECIES:
        return 718 if effect == 90 else 540
    if species in DIALGA_SPECIES:
        return 919 if effect == 88 else 536
    if species in PALKIA_SPECIES:
        return 920 if effect == 89 else 537
    if effect == 86:
        return {10: 748, 11: 750, 13: 747, 15: 749}.get(parameter, 702)
    return 702


def resolve_held_form(original_species, species, old_item, item, ability, profile):
    if held_form_family(species) is None or (
        species == original_species and item == old_item
    ):
        return species
    target = held_form_species(species, item, ability, profile)
    if species != original_species and species != target:
        raise ValueError("所选形态与携带道具不一致，请同步选择对应携带道具")
    return target
