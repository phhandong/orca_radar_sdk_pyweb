"""Stoppable UDP acquisition for the web player, using the SDK frame decoder."""

import json
import socket
import struct
import threading
import time
from pathlib import Path

import numpy as np

from .net_radar import radar_analysis

FRAME_START = bytes.fromhex("0201040306050807")
FRAME_END = bytes.fromhex("0708050603040102")
MAX_FRAME_BYTES = 1024 * 1024


class FrameBuffer:
    """Reassemble split/coalesced UDP payloads, including split magic words."""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while True:
            start = self.buffer.find(FRAME_START)
            if start < 0:
                self.buffer[:] = self.buffer[-7:]
                break
            del self.buffer[:start]
            if len(self.buffer) < 16:
                break
            size = struct.unpack_from("<I", self.buffer, 12)[0]
            if not 36 <= size <= MAX_FRAME_BYTES:
                del self.buffer[0]
                continue
            end = size + len(FRAME_END)
            if len(self.buffer) < end:
                # Recover if an incomplete frame was followed by a new frame.
                next_start = self.buffer.find(FRAME_START, 8)
                if next_start >= 0:
                    del self.buffer[:next_start]
                    continue
                break
            if self.buffer[size:end] != FRAME_END:
                del self.buffer[0]
                continue
            frames.append(bytes(self.buffer[:size]))
            del self.buffer[:end]
        return frames


class LiveRadar:
    def __init__(self, radar_ips, config_paths, save_dir, data_port=52001,
                 config_port=52000, limits=None, config_dir=None):
        self.radar_ips = list(radar_ips)
        self.config_paths = list(config_paths) if not isinstance(config_paths, str) else [config_paths]
        self.config_dir = Path(config_dir).resolve() if config_dir else None
        self.save_dir = Path(save_dir)
        self.data_port = data_port
        self.config_port = config_port
        self.limits = limits or [-600., 600., 0., 600., -100., 100.]
        self.lock = threading.RLock()
        self.lifecycle = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.config_thread = None
        self.sock = None
        self.running = False
        self.configuring = False
        self.error = ""
        self.session = ""
        self.recording_dir = None
        self.latest = {}
        self.counts = {}
        self.invalid = 0
        self.config_selection = "default"
        self.config_targets = []
        self.config_status = "未下发"

    def config_options(self):
        options = [{"id": "default", "name": "按 config.py 分配", "targets": [
            {"radar": ip, "file": str(self.config_paths[i] if len(self.config_paths) == len(self.radar_ips) else self.config_paths[0])}
            for i, ip in enumerate(self.radar_ips)] if self.config_paths else []}]
        if self.config_dir:
            for path in sorted(self.config_dir.rglob("*.cfg")):
                if path.is_file() and self.config_dir in path.resolve().parents:
                    name = path.relative_to(self.config_dir).as_posix()
                    options.append({"id": "cfg:" + name, "name": name,
                                    "targets": [{"radar": ip, "file": str(path)} for ip in self.radar_ips]})
        return options

    def start(self, configure=False, save=True, config_id="default"):
        with self.lifecycle:
            if self.thread and self.thread.is_alive():
                return self.snapshot()
            self.stop_event.set()
            if self.config_thread:
                self.config_thread.join(timeout=3)
            commands = []
            targets = []
            if configure:
                selected = next((option for option in self.config_options() if option["id"] == config_id), None)
                if selected is None:
                    raise ValueError("所选 CFG 不存在，请刷新配置列表后重试")
                targets = selected["targets"]
                if not targets:
                    raise ValueError("未配置雷达 CFG 文件")
                for target in targets:
                    lines = Path(target["file"]).read_text(encoding="utf-8-sig").splitlines()
                    lines = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("%")]
                    if not lines:
                        raise ValueError(f"CFG 没有可下发命令: {target['file']}")
                    commands.append((target["radar"], lines))
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # Exclusive bind: main.py and the web receiver must not share a port.
                sock.bind(("0.0.0.0", self.data_port))
                sock.settimeout(.2)
                session = str(time.time_ns())
                recording_dir = self.save_dir / session if save else None
                if recording_dir:
                    recording_dir.mkdir(parents=True, exist_ok=False)
            except Exception:
                sock.close()
                raise
            with self.lock:
                self.stop_event.clear()
                self.sock = sock
                self.session = session
                self.recording_dir = recording_dir
                self.latest = {}
                self.counts = {ip: 0 for ip in self.radar_ips}
                self.invalid = 0
                self.error = ""
                self.running = True
                self.configuring = bool(commands)
                self.config_selection = config_id if configure else "default"
                self.config_targets = targets
                self.config_status = "正在下发" if commands else "未下发"
            self.thread = threading.Thread(target=self._receive, args=(sock, recording_dir), daemon=True)
            self.thread.start()
            if commands:
                self.config_thread = threading.Thread(target=self._configure, args=(commands,), daemon=True)
                self.config_thread.start()
            return self.snapshot()

    def stop(self):
        with self.lifecycle:
            self.stop_event.set()
            for thread in (self.thread, self.config_thread):
                if thread:
                    thread.join(timeout=3)
            with self.lock:
                self.running = False
                self.configuring = False
                if self.config_status == "正在下发":
                    self.config_status = "下发已取消"
            return self.snapshot()

    def snapshot(self, radar=None):
        with self.lock:
            radar = radar if radar in self.radar_ips else (self.radar_ips[0] if self.radar_ips else None)
            return {"running": self.running, "configuring": self.configuring,
                    "error": self.error, "session": self.session,
                    "radars": self.radar_ips, "radar": radar, "port": self.data_port,
                    "counts": dict(self.counts), "invalid": self.invalid,
                    "save_path": str(self.recording_dir) if self.recording_dir else None,
                    "limits": self.limits, "frame": self.latest.get(radar),
                    "config_id": self.config_selection, "config_targets": self.config_targets,
                    "config_status": self.config_status}

    def _configure(self, commands):
        try:
            for ip, lines in commands:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(2)
                    sock.connect((ip, self.config_port))
                    for line in lines:
                        if self.stop_event.is_set():
                            return
                        sock.send((line + "\n").encode())
                        reply = sock.recv(8192).decode(errors="replace")
                        if "error" in reply.lower() or "not recognized" in reply.lower():
                            raise ValueError(f"{ip} 配置被拒绝 ({line}): {reply.strip()}")
                        if self.stop_event.wait(.2):
                            return
            with self.lock:
                self.config_status = "下发完成（已收到回复）"
        except (OSError, ValueError) as exc:
            with self.lock:
                self.error = f"配置下发失败: {exc}；接收仍在运行"
                self.config_status = "下发失败"
        finally:
            with self.lock:
                self.configuring = False
                if self.config_status == "正在下发":
                    self.config_status = "下发已取消"

    def _receive(self, sock, recording_dir):
        buffers = {ip: FrameBuffer() for ip in self.radar_ips}
        files = {}
        try:
            while not self.stop_event.is_set():
                try:
                    data, address = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                ip = address[0]
                if ip not in buffers:
                    continue
                for raw in buffers[ip].feed(data):
                    timestamp = time.time()
                    try:
                        points = radar_analysis(raw)
                        if not np.isfinite(points).all():
                            raise ValueError("非有限点云坐标")
                    except (ValueError, struct.error, OverflowError):
                        with self.lock:
                            self.invalid += 1
                        continue
                    if recording_dir:
                        now = time.monotonic()
                        if ip not in files or now - files[ip][1] >= 300:
                            if ip in files:
                                files[ip][0].close()
                            directory = recording_dir / ip
                            directory.mkdir(exist_ok=True)
                            files[ip] = ((directory / f"{timestamp}.txt").open("w", encoding="utf-8"), now)
                        files[ip][0].write(json.dumps({"t": timestamp, "data": raw.hex()}) + "\n")
                        files[ip][0].flush()
                    with self.lock:
                        self.counts[ip] += 1
                        self.latest[ip] = {"timestamp": timestamp, "time": 0,
                                           "sequence": self.counts[ip], "points": points.tolist()}
        except OSError as exc:
            if not self.stop_event.is_set():
                with self.lock:
                    self.error = f"接收或保存失败: {exc}"
        finally:
            sock.close()
            for file, _ in files.values():
                file.close()
            with self.lock:
                self.running = False
