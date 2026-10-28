#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Extract finger electrode geometry from a user-supplied piano THP configuration.

The vendor file uses named blocks and C-style numeric arrays, not standard INI.
Only the required numeric assignments are interpreted, using the Python lexer
and literal parser. Output is private calibration, not a redistributable asset.
"""
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import tokenize


def assignments(text):
    ignored = {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.COMMENT}
    tokens = [(token.type, token.string) for token in tokenize.generate_tokens(io.StringIO(text).readline)
              if token.type not in ignored]

    def value(name):
        matches = [i for i, token in enumerate(tokens[:-1])
                   if token == (tokenize.NAME, name) and tokens[i + 1] == (tokenize.OP, '=')]
        if len(matches) != 1:
            raise ValueError(f'Need exactly one numeric assignment for {name}')
        start = matches[0] + 2
        if tokens[start] == (tokenize.OP, '{'):
            end = start + 1
            while end < len(tokens) and tokens[end] != (tokenize.OP, '}'):
                end += 1
            if end == len(tokens):
                raise ValueError(f'Unclosed numeric array: {name}')
            literal = [(tokenize.OP, '[')] + tokens[start + 1:end] + [(tokenize.OP, ']')]
            result = ast.literal_eval(tokenize.untokenize(literal))
            if not all(type(item) is int for item in result):
                raise ValueError(f'Non-integer electrode value in {name}')
            return result
        result = ast.literal_eval(tokens[start][1])
        if type(result) is not int:
            raise ValueError(f'Non-integer field: {name}')
        return result
    return value


def parse_ini(raw):
    """Read the sections of the vendor configuration.

    The vendor file carries the same values twice: as plain assignments and
    inside named sections. The pen facts are named by their section, so the
    sections are needed to tell the stylus keys apart.
    """
    result = {}
    section = logical = ''
    for line in raw.decode('utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        continuation = line.endswith('\\')
        if continuation:
            line = line[:-1]
        logical += line
        if continuation or ('{' in logical and '}' not in logical):
            continue
        if '=' in logical:
            key, content = logical.split('=', 1)
            full = section + '.' + key.strip()
            if full in result:
                raise ValueError(f'Duplicate configuration key: {full}')
            result[full] = content.strip()
        else:
            section = logical.strip()
        logical = ''
    if logical:
        raise ValueError('Incomplete configuration')
    return result


def ini_integer(fields, key):
    text = fields[key].strip()
    if not text or any(character not in '-+0123456789' for character in text):
        raise ValueError(f'Not an integer: {key}')
    number = int(text, 10)
    if not -(1 << 31) <= number < (1 << 31):
        raise ValueError(f'Integer outside the supported range: {key}')
    return number


def ini_array(fields, key, size):
    text = fields[key]
    if not text.startswith('{') or not text.endswith('}'):
        raise ValueError(f'Not a numeric array: {key}')
    parts = text[1:-1].split(',')
    # The vendor maps end with a trailing comma in the multiline form.
    if parts and parts[-1] == '':
        parts.pop()
    values = [ini_integer({'value': part}, 'value') for part in parts]
    if len(values) != size:
        raise ValueError(f'Wrong array length: {key}')
    return values


PEN_GEOMETRY = {
    'hw.hal_row_num': 40, 'hw.hal_col_num': 60, 'hw.hal_node_num': 2400,
    'hw.display_x_resolution': 2136, 'hw.display_y_resolution': 3200,
    'hw.x_flip': 0, 'hw.y_flip': 0, 'hw.xy_flip': 0,
    'project_infor.super_resolution': 100,
    'stylus.stylus_num_col1': 12, 'stylus.stylus_num_row1': 40,
    'stylus.stylus_num_col2': 60, 'stylus.stylus_num_row2': 8,
    'stylus.stylus_en': 1,
}
PEN_COMMON = (
    'tilt_calibration_en', 'rx_l_edge_param', 'rx_r_edge_param',
    'tx_l_edge_param', 'tx_r_edge_param', 'limit_rx_start', 'limit_rx_end',
    'limit_tx_start', 'limit_tx_end', 'up_threshold', 'ring_threshold',
    'kalman_q_div_min', 'kalman_q_div_max', 'kalman_lamda_div_min',
    'kalman_lamda_div_max', 'first_jitter_lock_dis', 'stable_jitter_lock_dis',
    'move_jitter_lock_dis',
)
PEN_PROFILE = ('tilt_calibration_thd', 'tilt_calibration_rate', 'tip_slope_rx', 'tip_slope_tx')


def extract_pen(data):
    """Extract the numeric pen facts of the P81c profile.

    Only the geometry, the calibration and the mapping numbers of the open
    core are copied. The vendor identifiers select the profile of the pen:
    the P81c uses the vendor 3.
    """
    fields = parse_ini(data)
    for key, expected in PEN_GEOMETRY.items():
        if ini_integer(fields, key) != expected:
            raise ValueError(f'Unsupported pen geometry: {key}')
    prefixes = [prefix for prefix in ('stylus', 'stylus_2', 'stylus_3')
                if 'stylus.' + prefix + '_vendor_id' in fields and
                ini_integer(fields, 'stylus.' + prefix + '_vendor_id') == 3]
    if len(prefixes) != 1:
        raise ValueError('Expected exactly one P81c vendor 3 profile')
    prefix = prefixes[0]
    values = {}
    for suffix in PEN_COMMON:
        values['stylus.stylus_' + suffix] = ini_integer(fields, 'stylus.stylus_' + suffix)
    for suffix in PEN_PROFILE:
        key = 'stylus.' + prefix + '_' + suffix
        if key not in fields:
            key = 'stylus.stylus_' + suffix
        values['stylus.stylus_' + suffix] = ini_integer(fields, key)
    values['stylus.stylus_coor_diff'] = ini_array(fields, 'stylus.' + prefix + '_coor_diff', 6)
    values['stylus.stylus_angle'] = ini_array(fields, 'stylus.stylus_angle', 6)
    xmap = ini_array(fields, 'mapping.default_mapping_x', 2400)
    ymap = ini_array(fields, 'mapping.default_mapping_y', 2400)
    mapping_40 = [xmap[row * 60] for row in range(40)]
    mapping_60 = ymap[:60]
    if any(xmap[row * 60 + column] != mapping_40[row] or
           ymap[row * 60 + column] != mapping_60[column]
           for row in range(40) for column in range(60)):
        raise ValueError('The pen mapping is not separable into the two axes')
    for values_in, extent in ((mapping_40, 2136), (mapping_60, 3200)):
        if values_in[0] < 0 or values_in[-1] >= extent or \
                any(a >= b for a, b in zip(values_in, values_in[1:])):
            raise ValueError('The pen mapping is outside the panel or not increasing')
    values['mapping.mapping_40'] = mapping_40
    values['mapping.mapping_60'] = mapping_60
    values['project_infor.super_resolution'] = ini_integer(
        fields, 'project_infor.super_resolution')
    return {
        'coordinate_resolution': values['project_infor.super_resolution'],
        'mapping_40': mapping_40, 'mapping_60': mapping_60,
        'coordinate_differences': values['stylus.stylus_coor_diff'],
        'tilt_angles': values['stylus.stylus_angle'],
        'calibration_enabled': values['stylus.stylus_tilt_calibration_en'] != 0,
        'calibration_threshold': values['stylus.stylus_tilt_calibration_thd'],
        'calibration_rate': values['stylus.stylus_tilt_calibration_rate'],
        'tip_slope_40': values['stylus.stylus_tip_slope_rx'],
        'tip_slope_60': values['stylus.stylus_tip_slope_tx'],
        'edge_40_left': values['stylus.stylus_rx_l_edge_param'],
        'edge_40_right': values['stylus.stylus_rx_r_edge_param'],
        'edge_60_left': values['stylus.stylus_tx_l_edge_param'],
        'edge_60_right': values['stylus.stylus_tx_r_edge_param'],
        'edge_limit_40_left': values['stylus.stylus_limit_rx_start'],
        'edge_limit_40_right': values['stylus.stylus_limit_rx_end'],
        'edge_limit_60_left': values['stylus.stylus_limit_tx_start'],
        'edge_limit_60_right': values['stylus.stylus_limit_tx_end'],
        'up_threshold': values['stylus.stylus_up_threshold'],
        'ring_threshold': values['stylus.stylus_ring_threshold'],
        'kalman_q_min': values['stylus.stylus_kalman_q_div_min'],
        'kalman_q_max': values['stylus.stylus_kalman_q_div_max'],
        'kalman_lambda_min': values['stylus.stylus_kalman_lamda_div_min'],
        'kalman_lambda_max': values['stylus.stylus_kalman_lamda_div_max'],
        'first_lock_dis': values['stylus.stylus_first_jitter_lock_dis'],
        'stable_lock_dis': values['stylus.stylus_stable_jitter_lock_dis'],
        'move_lock_dis': values['stylus.stylus_move_jitter_lock_dis'],
    }


def extract(source):
    data = source.read_bytes()
    value = assignments(data.decode())
    rows, columns = value('hal_row_num'), value('hal_col_num')
    units = value('normal_super_resolution')
    if (rows, columns, units) != (40, 60, 10):
        raise ValueError('Unsupported matrix or processing units')
    xmap, ymap = value('default_mapping_x'), value('default_mapping_y')
    if len(xmap) != rows * columns or len(ymap) != rows * columns:
        raise ValueError('Electrode table size differs from the matrix')
    row_map = [xmap[row * columns] for row in range(rows)]
    column_map = ymap[:columns]
    if any(xmap[row * columns + col] != row_map[row] or ymap[row * columns + col] != column_map[col]
           for row in range(rows) for col in range(columns)):
        raise ValueError('Non-separable electrode mapping needs a different coordinate model')
    edges = [value('cfg_lcd_' + suffix + '_edge') for suffix in ('x_l', 'x_r', 'y_u', 'y_d')]
    gains = [value('cfg_k_' + suffix + '_edge') for suffix in ('x_l', 'x_r', 'y_u', 'y_d')]
    if len(set(edges)) != 1 or len(set(gains)) != 1:
        raise ValueError('Asymmetric edge mapping is not supported by this processing core')
    result = {
        'schema': 1, 'rows': rows, 'columns': columns, 'units_per_pixel': units,
        'source_sha256': hashlib.sha256(data).hexdigest(),
        # Stock panel identity of the source file (CSOT 4, BOE 26); piano-thp-live reports a
        # calibration that does not belong to the panel the kernel detects.
        'project_vendor_id': value('project_vendor_id'),
        'scope': 'Private extracted electrode calibration; does not establish physical touch support or orientation',
        'calibration': {
            'row_map': row_map, 'column_map': column_map,
            'row_maximum': value('display_x_resolution') * units,
            'column_maximum': value('display_y_resolution') * units,
            'edge_base': value('cfg_lcd_base_edge') * units,
            'edge_size': edges[0] * units, 'edge_gain': gains[0] * units,
        },
    }
    pen = extract_pen(data)
    if pen is not None:
        result['pen'] = pen
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = extract(args.input)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print('Wrote private calibration; do not commit or redistribute the output.')


if __name__ == '__main__':
    main()
