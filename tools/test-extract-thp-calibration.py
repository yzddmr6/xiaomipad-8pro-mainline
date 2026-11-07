#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic regressions of the calibration extraction. Never a vendor file."""
import importlib.util
import unittest
from pathlib import Path


def load():
    path = Path(__file__).with_name('extract-thp-calibration.py')
    spec = importlib.util.spec_from_file_location('extract_thp_calibration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vendor_config():
    """A configuration with the geometry of this panel and a pen profile.

    The mapping values are synthetic and only describe the shape of the
    extraction, never a real panel.
    """
    # The axes are separable: the x map is constant per row and the y map per
    # column. The values are synthetic and only describe the shape.
    mapping_x = ','.join(str(row * 50) for row in range(40) for column in range(60))
    mapping_y = ','.join(str(column * 40) for row in range(40) for column in range(60))
    return f"""
hw
hal_row_num = 40
hal_col_num = 60
hal_node_num = 2400
display_x_resolution = 2136
display_y_resolution = 3200
x_flip = 0
y_flip = 0
xy_flip = 0
project_infor
super_resolution = 100
normal_super_resolution = 10
project_vendor_id = 4
stylus
stylus_num_col1 = 12
stylus_num_row1 = 40
stylus_num_col2 = 60
stylus_num_row2 = 8
stylus_en = 1
stylus_vendor_id = 3
stylus_tilt_calibration_en = 1
stylus_tilt_calibration_thd = 30
stylus_tilt_calibration_rate = 6
stylus_rx_l_edge_param = 11
stylus_rx_r_edge_param = 12
stylus_tx_l_edge_param = 13
stylus_tx_r_edge_param = 14
stylus_limit_rx_start = 21
stylus_limit_rx_end = 22
stylus_limit_tx_start = 23
stylus_limit_tx_end = 24
stylus_up_threshold = 200
stylus_ring_threshold = 150
stylus_kalman_q_div_min = 1
stylus_kalman_q_div_max = 100
stylus_kalman_lamda_div_min = 2
stylus_kalman_lamda_div_max = 200
stylus_first_jitter_lock_dis = 3
stylus_stable_jitter_lock_dis = 4
stylus_move_jitter_lock_dis = 5
stylus_tip_slope_rx = 8
stylus_tip_slope_tx = 7
stylus_angle = {{0,1500,3000,4500,6000,7000}}
stylus_coor_diff = {{0,28,51,67,81,87}}
stylus_2
stylus_vendor_id = 1
stylus_tilt_calibration_thd = 37
stylus_tilt_calibration_rate = 8
stylus_tip_slope_rx = 6
stylus_tip_slope_tx = 6
stylus_coor_diff = {{0,37,81,103,121,132}}
stylus_3
stylus_vendor_id = 2
stylus_tilt_calibration_thd = 41
stylus_tilt_calibration_rate = 9
stylus_tip_slope_rx = 5
stylus_tip_slope_tx = 5
stylus_coor_diff = {{0,44,91,111,131,141}}
mapping
default_mapping_x = {{{mapping_x}}}
default_mapping_y = {{{mapping_y}}}
cfg
lcd_x_l_edge = 1
lcd_x_r_edge = 1
lcd_y_u_edge = 1
lcd_y_d_edge = 1
k_x_l_edge = 2
k_x_r_edge = 2
k_y_u_edge = 2
k_y_d_edge = 2
lcd_base_edge = 3
"""


class ExtractPenTest(unittest.TestCase):
    def setUp(self):
        self.module = load()
        self.config = vendor_config()

    def test_pen_facts(self):
        pen = self.module.extract_pen(self.config.encode())
        self.assertEqual(pen['coordinate_resolution'], 100)
        self.assertEqual(len(pen['mapping_40']), 40)
        self.assertEqual(len(pen['mapping_60']), 60)
        self.assertEqual(pen['coordinate_differences'], [0, 28, 51, 67, 81, 87])
        self.assertEqual(pen['tilt_angles'], [0, 1500, 3000, 4500, 6000, 7000])
        self.assertTrue(pen['calibration_enabled'])
        self.assertEqual(pen['calibration_threshold'], 30)
        self.assertEqual(pen['calibration_rate'], 6)
        self.assertEqual(pen['tip_slope_40'], 8)
        self.assertEqual(pen['tip_slope_60'], 7)
        self.assertEqual(pen['up_threshold'], 200)
        self.assertEqual(pen['ring_threshold'], 150)
        self.assertEqual(pen['kalman_lambda_max'], 200)
        self.assertEqual(pen['move_lock_dis'], 5)

    def test_another_pen_vendor_is_ambiguous(self):
        config = self.config.replace('stylus_vendor_id = 3', 'stylus_vendor_id = 3\nstylus_2_vendor_id = 3')
        with self.assertRaises(ValueError):
            self.module.extract_pen(config.encode())

    def test_a_missing_fact_is_rejected(self):
        config = self.config.replace('stylus_up_threshold = 200\n', '')
        with self.assertRaises(KeyError):
            self.module.extract_pen(config.encode())

    def test_an_increasing_mapping_is_required(self):
        config = self.config.replace('stylus_num_col1 = 12', 'stylus_num_col1 = 13')
        with self.assertRaises(ValueError):
            self.module.extract_pen(config.encode())


if __name__ == '__main__':
    unittest.main()
