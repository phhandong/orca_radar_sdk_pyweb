import io
import json
import struct
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from replay_pointcloud import RadarFrame, load_frames, make_handler, parse_frames, recording_payload


def record(timestamp, data=b""):
    return json.dumps({"t": timestamp, "data": data.hex()}) + "\n"


class ReplayTests(unittest.TestCase):
    def test_decode_real_point_layout(self):
        packet = bytearray(76)
        packet[:8] = bytes.fromhex("0201040306050807")
        struct.pack_into("<I", packet, 12, len(packet))
        struct.pack_into("<II", packet, 28, 1, 2)
        struct.pack_into("<II", packet, 40, 1, 16)
        struct.pack_into("<4f", packet, 48, 1, 2, 3, -4)
        struct.pack_into("<II", packet, 64, 7, 4)
        struct.pack_into("<2h", packet, 72, 10, -20)
        frames = parse_frames([record(10, packet), record(10.35)])
        self.assertEqual(frames[0].points.tolist(), [[1, 2, 3, -4, 1, -2]])
        payload = recording_payload(frames, "test")
        self.assertEqual(payload["frames"][0]["points"], [[1, 2, 3, -4, 1, -2]])
        self.assertAlmostEqual(payload["frames"][1]["time"], .35)
        self.assertEqual(payload["frames"][1]["points"], [])
        self.assertEqual(payload["limits"], [0, 2, 1, 3, 2, 4])

    def test_empty_frames_and_limit(self):
        frames = parse_frames(["\n", record(1), record(2)], max_frames=1)
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(recording_payload(frames, "empty")["limits"]), 6)
        with self.assertRaisesRegex(ValueError, "没有有效帧"):
            parse_frames(["\n"])

    def test_tlv_types_can_be_reordered_and_signed_side_info_is_scaled(self):
        packet = bytearray(88)
        packet[:8] = bytes.fromhex("0201040306050807")
        struct.pack_into("<I", packet, 12, len(packet))
        struct.pack_into("<II", packet, 28, 1, 3)
        struct.pack_into("<II", packet, 40, 9, 4)
        struct.pack_into("<I", packet, 48, 123)
        struct.pack_into("<II", packet, 52, 7, 4)
        struct.pack_into("<2h", packet, 60, 12, -328)
        struct.pack_into("<II", packet, 64, 1, 16)
        struct.pack_into("<4f", packet, 72, 1, 2, 3, -1.5)
        decoded = parse_frames([record(1, packet)])[0].points
        self.assertEqual(decoded.dtype, np.float32)
        np.testing.assert_allclose(decoded, [[1, 2, 3, -1.5, 1.2, -32.8]], rtol=0, atol=1e-6)

    def test_nonempty_packet_without_required_side_info_is_rejected(self):
        packet = bytearray(64)
        packet[:8] = bytes.fromhex("0201040306050807")
        struct.pack_into("<I", packet, 12, len(packet))
        struct.pack_into("<II", packet, 28, 1, 1)
        struct.pack_into("<II", packet, 40, 1, 16)
        struct.pack_into("<4f", packet, 48, 1, 2, 3, 4)
        with self.assertRaisesRegex(ValueError, "TLV 7"):
            parse_frames([record(1, packet)])

    def test_bad_records_report_line(self):
        for bad in ["{", record(float("nan")), record(.5), '{"t":2,"data":"zz"}', '[]']:
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "第 2 行"):
                parse_frames([record(1), bad])
        packet = bytearray(40)
        packet[:8] = bytes.fromhex("0201040306050807")
        struct.pack_into("<I", packet, 12, 40)
        struct.pack_into("<II", packet, 28, 1, 0)
        with self.assertRaisesRegex(ValueError, "第 1 行"):
            parse_frames([record(1, packet)])

    def test_bom_and_fixed_limits(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "log.txt"
            path.write_text("\ufeff" + record(1), encoding="utf-8")
            limits = [-1, 1, -2, 2, -3, 3]
            self.assertEqual(recording_payload(load_frames(path), "test", limits)["limits"], limits)

    def test_large_coordinates_do_not_overflow_bounds(self):
        frames = [RadarFrame(1, np.array([[-60000, 0, 0, 0], [60000, 0, 0, 0]], dtype=np.float16))]
        self.assertTrue(all(np.isfinite(recording_payload(frames, "large")["limits"])))

    def test_http_api_without_socket(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "good.txt").write_text(record(1) + record(1.2))
            handler_type = make_handler(root)

            def request(path, body=None, origin=None):
                handler = object.__new__(handler_type)
                handler.path = path
                handler.headers = {"Host": "127.0.0.1:8765"}
                responses = []
                handler.send_body = lambda status, data, content_type: responses.append((status, data))
                if body is None:
                    handler.do_GET()
                else:
                    handler.headers["Content-Length"] = str(len(body))
                    if origin:
                        handler.headers["Origin"] = origin
                    handler.rfile = io.BytesIO(body)
                    handler.do_POST()
                status, data = responses[0]
                return status, json.loads(data)

            self.assertEqual(request("/api/files")[1]["files"][0]["id"], "good.txt")
            status, payload = request("/api/load", b'{"id":"good.txt"}')
            self.assertEqual(status, 200)
            self.assertEqual(len(payload["frames"]), 2)
            self.assertEqual(request("/api/upload", record(1).encode())[0], 200)
            self.assertEqual(request("/api/upload", b"bad")[0], 400)
            self.assertEqual(request("/api/load", b'{"id":"../outside.txt"}')[0], 400)
            self.assertEqual(request("/api/load", b'{"id":"missing.txt"}')[0], 400)
            self.assertEqual(request("/api/upload", record(1).encode(), "http://other.test")[0], 403)


if __name__ == "__main__":
    unittest.main()
