import json
import socket
import struct
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from utils.live_radar import FRAME_END, FRAME_START, FrameBuffer, LiveRadar


def packet(x=1, z=3):
    raw = bytearray(76)
    raw[:8] = FRAME_START
    struct.pack_into("<I", raw, 12, len(raw))
    struct.pack_into("<II", raw, 28, 1, 2)
    struct.pack_into("<II", raw, 40, 1, 16)
    struct.pack_into("<4f", raw, 48, x, 2, z, -4)
    struct.pack_into("<II", raw, 64, 7, 4)
    struct.pack_into("<2h", raw, 72, 10, 20)
    return bytes(raw)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("等待实时接收超时")


class FrameBufferTests(unittest.TestCase):
    def test_split_magic_payload_tail_and_coalesced_frames(self):
        raw = packet()
        stream = b"junk" + raw + FRAME_END + raw + FRAME_END
        for chunk_size in (1, 3, 7, 20, 1000):
            buffer = FrameBuffer()
            frames = []
            for start in range(0, len(stream), chunk_size):
                frames.extend(buffer.feed(stream[start:start + chunk_size]))
            self.assertEqual(frames, [raw, raw])

    def test_recovers_after_invalid_length_and_dropped_frame(self):
        raw = packet()
        bad = FRAME_START + b"\0" * 8
        buffer = FrameBuffer()
        self.assertEqual(buffer.feed(bad + raw[:30] + raw + FRAME_END), [raw])
        buffer.feed(b"x" * 2_000_000)
        self.assertLessEqual(len(buffer.buffer), 7)


class LiveReceiverTests(unittest.TestCase):
    def test_udp_decode_save_stop_restart_and_empty_frame(self):
        with TemporaryDirectory() as directory:
            live = LiveRadar(["127.0.0.1"], [], directory, data_port=0)
            self.addCleanup(live.stop)
            state = live.start(save=True)
            self.assertTrue(state["running"])
            self.assertEqual(live.start()["session"], state["session"])
            port = live.sock.getsockname()[1]
            raw = packet(z=12.5)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(raw[:5], ("127.0.0.1", port))
                sender.sendto(raw[5:] + FRAME_END, ("127.0.0.1", port))
                wait_for(lambda: live.snapshot()["counts"]["127.0.0.1"] == 1)
                self.assertEqual(live.snapshot()["frame"]["points"], [[1, 2, 12.5, -4, 1, 2]])
                empty = bytearray(40)
                empty[:8] = FRAME_START
                struct.pack_into("<I", empty, 12, len(empty))
                struct.pack_into("<II", empty, 28, 0, 0)
                sender.sendto(empty + FRAME_END, ("127.0.0.1", port))
                wait_for(lambda: live.snapshot()["counts"]["127.0.0.1"] == 2)
                self.assertEqual(live.snapshot()["frame"]["points"], [])
            live.stop()
            records = [json.loads(line) for p in Path(directory).rglob("*.txt") for line in p.read_text().splitlines()]
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["data"], raw.hex())
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("0.0.0.0", port))
            restarted = live.start(save=False)
            self.assertNotEqual(restarted["session"], state["session"])
            self.assertIsNone(restarted["frame"])
            self.assertIsNone(restarted["save_path"])

    def test_configuration_acknowledgments_and_bad_frame_recovery(self):
        with TemporaryDirectory() as directory, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as responder:
            responder.bind(("127.0.0.1", 0))
            responder.settimeout(3)
            path = Path(directory) / "radar.cfg"
            path.write_text("% comment\n\nsensorStop\nsensorStart\n")
            alternative = Path(directory) / "selected.cfg"
            alternative.write_text("% selected config\n\nprofileCfg 123\nsensorStart\n")
            received = []

            def respond():
                for _ in range(2):
                    command, address = responder.recvfrom(8192)
                    received.append(command)
                    responder.sendto(b"Done", address)

            thread = threading.Thread(target=respond)
            thread.start()
            live = LiveRadar(["127.0.0.1"], [str(path)], directory,
                             data_port=0, config_port=responder.getsockname()[1], config_dir=directory)
            self.addCleanup(live.stop)
            live.start(configure=True, save=False, config_id="cfg:selected.cfg")
            wait_for(lambda: not live.snapshot()["configuring"])
            thread.join()
            self.assertEqual(received, [b"profileCfg 123\n", b"sensorStart\n"])
            self.assertEqual(live.snapshot()["error"], "")
            self.assertEqual(live.snapshot()["config_status"], "下发完成（已收到回复）")
            self.assertEqual(live.snapshot()["config_targets"], [{"radar": "127.0.0.1", "file": str(alternative)}])
            malformed = bytearray(packet()[:36])
            struct.pack_into("<I", malformed, 12, len(malformed))
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(malformed + FRAME_END + packet() + FRAME_END,
                              ("127.0.0.1", live.sock.getsockname()[1]))
            wait_for(lambda: live.snapshot()["frame"] is not None)
            self.assertEqual(live.snapshot()["invalid"], 1)
            self.assertEqual(live.snapshot()["frame"]["sequence"], 1)

    def test_config_catalog_mapping_and_invalid_selection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "100m.cfg", root / "600m.cfg"
            first.write_text("sensorStart\n")
            second.write_text("sensorStop\n")
            (root / "empty.cfg").write_text("% no commands\n")
            live = LiveRadar(["127.0.0.1", "127.0.0.2"], [str(first), str(second)],
                             root / "logs", data_port=0, config_dir=root)
            self.addCleanup(live.stop)
            options = {option["id"]: option for option in live.config_options()}
            self.assertEqual([t["file"] for t in options["default"]["targets"]], [str(first), str(second)])
            self.assertEqual([t["file"] for t in options["cfg:100m.cfg"]["targets"]], [str(first), str(first)])
            for selection in ("cfg:../outside.cfg", "cfg:missing.cfg", "cfg:empty.cfg"):
                with self.subTest(selection=selection), self.assertRaises(ValueError):
                    live.start(configure=True, save=False, config_id=selection)
                self.assertFalse(live.snapshot()["running"])
                self.assertIsNone(live.sock)
            # No configuration requested: do not read or send even an invalid selection.
            state = live.start(configure=False, save=False, config_id="cfg:missing.cfg")
            self.assertTrue(state["running"])
            self.assertEqual(state["config_targets"], [])
            self.assertEqual(state["config_status"], "未下发")

    def test_occupied_port_does_not_start_receiver(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
            occupied.bind(("0.0.0.0", 0))
            live = LiveRadar(["127.0.0.1"], [], "/tmp", data_port=occupied.getsockname()[1])
            with self.assertRaises(OSError):
                live.start(save=False)
            self.assertFalse(live.snapshot()["running"])


if __name__ == "__main__":
    unittest.main()
