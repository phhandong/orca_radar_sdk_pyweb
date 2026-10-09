"""Local, offline web player for JSON-lines radar recordings."""

from __future__ import annotations

import argparse
import io
import json
import math
import struct
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import config as cfg
import globalvar as g

from utils.net_radar import radar_analysis
from utils.live_radar import LiveRadar
from utils.web_filter import process_points

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD = 128 * 1024 * 1024
DEFAULT_LIMITS = (-10., 10., 0., 20., -10., 10.)


@dataclass
class RadarFrame:
    timestamp: float
    points: np.ndarray


def parse_frames(lines, max_frames=None):
    """Decode a recording, preserving empty frames and reporting bad line numbers."""
    if max_frames is not None and max_frames < 1:
        raise ValueError("max_frames 必须大于 0")
    frames = []
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            timestamp = float(record["t"])
            if not math.isfinite(timestamp):
                raise ValueError("时间戳必须为有限数值")
            if frames and timestamp < frames[-1].timestamp:
                raise ValueError("时间戳倒退，无法按时间回放")
            points = radar_analysis(bytes.fromhex(record["data"]))
            if not np.isfinite(points).all():
                raise ValueError("点云包含非有限数值")
            frames.append(RadarFrame(timestamp, points))
        except (ValueError, KeyError, TypeError, struct.error, OverflowError) as exc:
            raise ValueError(f"无法解析第 {line_no} 行: {exc}") from exc
        if max_frames is not None and len(frames) >= max_frames:
            break
    if not frames:
        raise ValueError("文件中没有有效帧")
    return frames


def load_frames(path: Path, max_frames=None):
    with path.open("r", encoding="utf-8-sig") as source:
        return parse_frames(source, max_frames)


def recording_payload(frames, name, fixed_limits=None):
    limits = fixed_limits
    if limits is None:
        clouds = [frame.points[:, :3].astype(float) for frame in frames if len(frame.points)]
        if clouds:
            mins = np.min([cloud.min(axis=0) for cloud in clouds], axis=0)
            maxs = np.max([cloud.max(axis=0) for cloud in clouds], axis=0)
            margin = np.maximum((maxs - mins) * .05, 1.)
            limits = [float(v) for pair in zip(mins - margin, maxs + margin) for v in pair]
        else:
            limits = DEFAULT_LIMITS
    return {"name": name, "limits": limits,
            "frames": [{"timestamp": frame.timestamp,
                        "time": frame.timestamp - frames[0].timestamp,
                        "points": frame.points[:, :6].tolist()} for frame in frames]}


def make_handler(data_dir, initial_file=None, speed=1., loop=False,
                 max_frames=None, fixed_limits=None, live=None):
    data_dir = Path(data_dir).resolve()
    initial_file = Path(initial_file).resolve() if initial_file else None

    class Handler(BaseHTTPRequestHandler):
        def send_body(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, value, status=200):
            self.send_body(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
                           "application/json; charset=utf-8")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/live/configs" and live:
                self.send_json({"configs": live.config_options()})
                return
            if path == "/api/live" and live:
                radar = parse_qs(urlparse(self.path).query).get("radar", [None])[0]
                self.send_json(live.snapshot(radar))
                return
            if path == "/api/files":
                files = [{"id": str(p.relative_to(data_dir)),
                          "name": str(p.relative_to(data_dir))}
                         for p in sorted(data_dir.rglob("*"))
                         if p.suffix.lower() in {".txt", ".jsonl", ".ndjson"}
                         and p.is_file() and data_dir in p.resolve().parents]
                if initial_file:
                    files.insert(0, {"id": "@initial", "name": initial_file.name})
                self.send_json({"files": files, "initial": "@initial" if initial_file else None,
                                "speed": speed, "loop": loop})
                return
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/player.js": ("player.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path not in assets:
                self.send_json({"error": "未找到资源"}, 404)
                return
            filename, content_type = assets[path]
            self.send_body(200, (ROOT / "web" / filename).read_bytes(), content_type)

        def do_POST(self):
            # The local service only accepts requests from its own web page.
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                self.send_json({"error": "不允许跨站请求"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_UPLOAD:
                    self.send_json({"error": "文件不能为空，且不能超过 128 MiB"}, 413)
                    return
                path = urlparse(self.path).path
                if path == "/api/filter":
                    if length > 1024 * 1024:
                        raise ValueError("单帧滤波请求不能超过 1 MiB")
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict):
                        raise ValueError("滤波请求必须是对象")
                    self.send_json({"points": process_points(payload["points"], payload.get("options", {}))})
                    return
                if path in {"/api/live/start", "/api/live/stop"} and live:
                    options = json.loads(self.rfile.read(length))
                    if not isinstance(options, dict):
                        raise ValueError("实时接收参数必须为对象")
                    if path.endswith("/start"):
                        configure, save = options.get("configure", False), options.get("save", True)
                        if not isinstance(configure, bool) or not isinstance(save, bool):
                            raise ValueError("configure 和 save 必须为布尔值")
                        config_id = options.get("config_id", "default")
                        if not isinstance(config_id, str):
                            raise ValueError("config_id 必须为字符串")
                        self.send_json(live.start(configure, save, config_id))
                    else:
                        self.send_json(live.stop())
                    return
                if path not in {"/api/load", "/api/upload"}:
                    self.send_json({"error": "未找到接口"}, 404)
                    return
                body = self.rfile.read(length)
                if path == "/api/load":
                    file_id = json.loads(body)["id"]
                    if not isinstance(file_id, str):
                        raise ValueError("文件标识必须是字符串")
                    target = initial_file if file_id == "@initial" else (data_dir / file_id).resolve()
                    if target is None or (target != initial_file and data_dir not in target.parents):
                        raise ValueError("只能打开数据目录内的文件")
                    if target.stat().st_size > MAX_UPLOAD:
                        raise ValueError("文件不能超过 128 MiB，请拆分日志后打开")
                    frames = load_frames(target, max_frames)
                    name = target.name
                else:
                    frames = parse_frames(io.StringIO(body.decode("utf-8-sig")), max_frames)
                    name = "本地文件"
                self.send_json(recording_payload(frames, name, fixed_limits))
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.send_json({"error": str(exc)}, 400)

        def log_message(self, format, *args):
            if len(args) > 1 and str(args[1]) != "200":
                super().log_message(format, *args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="在本地网页中回放雷达三维点云")
    parser.add_argument("file", nargs="?", type=Path, help="启动时打开的 JSON-lines 日志")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data", help="网页文件列表的根目录")
    parser.add_argument("--port", type=int, default=8765, help="本地服务端口，默认 8765；0 自动分配")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--speed", type=float, default=1., help="初始播放倍速")
    parser.add_argument("--max-frames", type=int, help="每个文件只加载前 N 帧")
    parser.add_argument("--loop", action="store_true", help="循环播放")
    parser.add_argument("--data-port", type=int, default=g.DATA_PORT, help="实时雷达 UDP 接收端口，默认读取 globalvar.py")
    parser.add_argument("--limits", nargs=6, type=float,
                        metavar=("XMIN", "XMAX", "YMIN", "YMAX", "ZMIN", "ZMAX"), help="固定坐标轴范围")
    args = parser.parse_args()
    if not math.isfinite(args.speed) or args.speed <= 0:
        parser.error("--speed 必须是大于 0 的有限数值")
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("--max-frames 必须大于 0")
    if args.limits and (not all(map(math.isfinite, args.limits)) or
                        any(args.limits[i] >= args.limits[i + 1] for i in (0, 2, 4))):
        parser.error("--limits 每个坐标轴的最小值必须小于最大值，且必须为有限数值")
    if args.file and not args.file.is_file():
        parser.error(f"文件不存在: {args.file}")
    if not 0 <= args.port <= 65535:
        parser.error("--port 必须在 0 到 65535 之间")
    if not 1 <= args.data_port <= 65535:
        parser.error("--data-port 必须在 1 到 65535 之间")
    config_paths = cfg.radar_cfg_path if isinstance(cfg.radar_cfg_path, list) else [cfg.radar_cfg_path]
    config_paths = [str(ROOT / path) for path in config_paths]
    distance = max(cfg.max_range)
    live_limits = args.limits or [-distance, distance, 0., distance, -distance, distance]
    live = LiveRadar(cfg.radar_ip, config_paths, ROOT / cfg.save_file_path,
                     args.data_port, g.CFG_PORT, live_limits, config_dir=ROOT / "cfg")
    handler = make_handler(args.data_dir, args.file, args.speed, args.loop, args.max_frames, args.limits, live)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    except OSError as exc:
        parser.exit(1, f"无法启动网页服务: {exc}；可使用 --port 更换端口。\n")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"雷达播放器：{url}\n按 Ctrl+C 退出。", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        live.stop()
        server.server_close()


if __name__ == "__main__":
    main()
