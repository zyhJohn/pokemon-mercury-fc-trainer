"""Regenerate the portable Lua bridge's fixed wild-layout whitelist."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = '-- BEGIN GENERATED WILD LAYOUTS (wild_encounter_layout.json)'
END = '-- END GENERATED WILD LAYOUTS'


def generated():
    data = json.loads((ROOT / 'wild_encounter_layout.json').read_text(encoding='utf-8'))
    lines = [START, 'local wildLayouts = {']
    for entry in data['releases'].values():
        lines.append('  ["' + entry['rom_sha256'] + '"] = {')
        for key in ['generator', 'stub_address', 'base_stats_address', 'base_stats_record_size',
                    'stub_species_offset', 'stub_level_offset']:
            lines.append(f'    {key} = {entry[key]},')
        for key in ['original_generator', 'original_stub', 'hook_template', 'stub_template']:
            lines.append(f'    {key} = "{entry[key]}",')
        lines.append('    species = {' + ','.join('{'+str(a)+','+str(b)+'}' for a, b in entry['valid_species_ranges']) + '},')
        lines.append('    guards = {')
        for guard in entry['guards']:
            lines.append('      {' + str(guard['address']) + ',"' + guard['expected_hex'] + '"},')
        lines.extend(['    },', '  },'])
    return '\n'.join(lines + ['}', END])


def sync(check=False):
    path = ROOT / 'mercury_bridge.lua'
    source = path.read_text(encoding='utf-8')
    start, end = source.index(START), source.index(END) + len(END)
    result = source[:start] + generated() + source[end:]
    if check:
        if result != source:
            raise ValueError('Lua wild whitelist differs from audited layout JSON')
    else:
        path.write_text(result, encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    sync(parser.parse_args().check)
