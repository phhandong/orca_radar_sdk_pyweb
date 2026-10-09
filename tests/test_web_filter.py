import io
import json
import unittest

import numpy as np

from replay_pointcloud import make_handler
from utils.web_filter import process_points


class WebFilterTests(unittest.TestCase):
    def test_rcs_uses_legacy_raw_units_but_returns_db_without_mutating_input(self):
        points = [[2, 10, 1, 1, 50, 60], [2, 10, 1, 1, 1, 2]]
        original = [row[:] for row in points]
        result = process_points(points, {"with_voxel": False, "with_cluster": False})
        self.assertEqual(result, [points[0]])
        self.assertEqual(points, original)

    def test_empty_and_all_excluded(self):
        self.assertEqual(process_points([], {}), [])
        self.assertEqual(process_points([[0, 0, 0, 0, 0, 0]], {}), [])
        self.assertEqual(process_points([[1, 10, 0, 20, 50, 60]], {}), [])

    def test_voxel_and_cluster_options(self):
        points = [[2 + delta, 10, 1, 1, 50, 60] for delta in (0, .01, .02)]
        points.append([20, 40, 1, 1, 50, 60])
        clustered = process_points(points, {"with_voxel": False, "eps": .1, "min_num": 3})
        self.assertEqual(len(clustered), 3)
        np.testing.assert_allclose(np.array(clustered)[:, :6], points[:3])
        self.assertEqual([point[6] for point in clustered], [0, 0, 0])
        self.assertEqual(len(process_points(points, {"with_cluster": False})), 2)

    def test_invalid_parameters_and_points(self):
        for options in ({"max_speed": 0}, {"voxel_size": -1}, {"eps": float("nan")},
                        {"min_num": 1.5}, {"with_cluster": "false"}, {"unknown": 1},
                        {"min_x_abs": -1}, {"min_num": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                process_points([], options)
        for points in ([[1, 2, 3]], [[0, 0, 0, 0, float("inf"), 0]], [[0] * 6] * 1201):
            with self.assertRaises(ValueError):
                process_points(points, {})

    def test_http_filter_endpoint(self):
        handler = object.__new__(make_handler("data"))
        handler.path = "/api/filter"
        payload = json.dumps({"points": [[2, 10, 1, 1, 50, 60]],
                              "options": {"with_cluster": False}}).encode()
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile = io.BytesIO(payload)
        response = []
        handler.send_json = lambda value, status=200: response.append((status, value))
        handler.do_POST()
        self.assertEqual(response, [(200, {"points": [[2, 10, 1, 1, 50, 60]]})])
