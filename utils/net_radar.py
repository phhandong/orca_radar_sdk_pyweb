import binascii
import json
import os
import socket
import time
from struct import unpack_from
from threading import Lock

import numpy as np

import config as cfg
import globalvar as g

from .log import log_radar

__all__ = ['data_recv', 'draw_points']

window_size = 200
# 保存数据时每5分钟生成一个新文件进行保存数据
save_file_time_gap = 5 * 60

points = {}
points_lock = Lock()

ip_idx_map = {}
for i in cfg.radar_ip:
    ip_idx_map[i] = i
    points[i] = np.zeros([0, 6])


def data_recv(file_data_path, frame_num: int = 0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', g.DATA_PORT))

    FRAME_START = bytes([0x02, 0x01, 0x04, 0x03, 0x06, 0x05, 0x08, 0x07])
    FRAME_END = bytes([0x07, 0x08, 0x05, 0x06, 0x03, 0x04, 0x01, 0x02])

    file_dict = {}
    radar_pkg_dict = {}
    for tmp_ip in cfg.radar_ip:
        tmp_path = os.path.join(file_data_path, tmp_ip[-3:])
        if not os.path.exists(tmp_path):
            os.makedirs(tmp_path)
        tmp_file_path = os.path.join(tmp_path, str(time.time()) + '.txt')
        file_dict[tmp_ip] = [tmp_path, open(tmp_file_path, 'w', encoding='utf-8')]
        radar_pkg_dict[tmp_ip] = 0
    file_start_time = time.monotonic()
    global_buff = {}
    code_start_time_m = time.monotonic()
    while True:
        data, addr = s.recvfrom(10240)
        ip = addr[0]
        if ip not in ip_idx_map:
            continue

        buff = global_buff.get(ip, bytearray())
        buff.extend(data)
        while True:
            try:
                idx1 = buff.index(FRAME_START)
            except ValueError:
                # 没有找到头
                buff.clear()
                break

            try:
                idx2 = buff.index(FRAME_END, idx1 + 1)
            except ValueError:
                # 只找到头，没找到尾
                global_buff[ip] = buff = buff[idx1:]
                break

            if len(buff[idx1:idx2]) > 0:
                try:
                    data = radar_analysis(buff[idx1:idx2])
                except ValueError as exc:
                    log_radar.warning(f'ip: {ip} invalid radar frame: {exc}')
                    global_buff[ip] = buff = buff[idx2 + len(FRAME_END):]
                    continue
                with points_lock:
                    points[ip] = data
                data_str = str(binascii.hexlify(buff[idx1:idx2]), 'utf-8')
                save_str = json.dumps({'t': time.time(), 'data': data_str}) + '\n'
                radar_pkg_dict[ip] += 1
                file_dict[ip][1].write(save_str)
                file_dict[ip][1].flush()
                cost_time = time.monotonic() - code_start_time_m
                log_radar.info(f'ip: {ip[-3:]}  pkg: {radar_pkg_dict[ip]}  cost time: {cost_time:.3f}  '
                               f'points num: {len(data)}')
                #print(f'++ write {[ip, file_dict[ip], len(data_str), data_str[:2]]}')
                if time.monotonic() - file_start_time > save_file_time_gap:
                    for k, v in file_dict.items():
                        v[1].close()
                        tmp_file_path = os.path.join(v[0], str(time.time()) + '.txt')
                        v[1] = open(tmp_file_path, 'w', encoding='utf-8')

                if frame_num > 0 and radar_pkg_dict[ip] > frame_num:
                    break

            global_buff[ip] = buff = buff[idx2:]


def draw_points(sub_num):
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize

    ind = 0
    radar_key = cfg.radar_ip[sub_num]
    win = window_size
    fig, ax = plt.subplots()
    ax.set_xlim((-win, win))
    ax.set_ylim((0, win))
    ax.set_xlabel('X (m)')
    ax.set_ylabel('Y (m)')
    scatter = ax.scatter([], [], c=[], s=4, cmap='viridis')
    fig.colorbar(scatter, ax=ax, label='Z height (m)')
    while plt.fignum_exists(fig.number):
        t1 = time.monotonic()
        with points_lock:
            curr_points = points[radar_key].copy()
        if len(curr_points) > 0:
            # 数据列为 (x, y, z, v[m/s], snr[dB], noise[dB])，以 z 高度着色。
            heights = np.ma.masked_invalid(curr_points[:, 2])
            scatter.set_offsets(curr_points[:, :2])
            scatter.set_array(heights)
            if heights.count():
                z_min, z_max = float(heights.min()), float(heights.max())
                if z_min == z_max:
                    z_min -= 0.5
                    z_max += 0.5
                scatter.set_norm(Normalize(vmin=z_min, vmax=z_max))
            ax.set_title(f'{time.time()} {len(curr_points)}')
            ind = 0
        else:
            scatter.set_offsets(np.empty((0, 2)))
            scatter.set_array(np.array([]))
            ind += 0
            if ind >= 20:
                print(sub_num, 'have not radar data, threading stop')
                break
            # time.sleep(0.05)
        t2 = time.monotonic()
        sleep_time = max(0.1 - t2 + t1, 0.01)
        plt.pause(sleep_time)


def radar_analysis(data: bytes) -> np.ndarray:
    """Decode the TI 40-byte-header, floating-point Cartesian TLV format.

    Returns float32 rows (x, y, z, velocity, snr_db, noise_db). TLV lengths
    exclude their 8-byte headers. Unknown TLVs and trailing packet padding
    are skipped. Nonempty frames must contain both type 1 and type 7 TLVs;
    unsupported/malformed packets raise ValueError instead of fake empty data.
    The ORCA transport end marker must already have been removed.
    """
    # Empty payloads are used by some JSON logs for a valid zero-point frame.
    if not data:
        return np.empty((0, 6), dtype=np.float32)
    if len(data) < 40:
        raise ValueError("帧头不足 40 字节")
    if data[:8] != bytes.fromhex("0201040306050807"):
        raise ValueError("无效的 TI 帧头 magic word")
    total_length = unpack_from('<I', data, 12)[0]
    if total_length != len(data):
        raise ValueError(f"帧长度不匹配: 声明 {total_length}，实际 {len(data)}")
    obj_count, tlv_count = unpack_from('<II', data, 28)
    if obj_count > 1200:
        raise ValueError(f"点数超出支持范围: {obj_count}")
    if tlv_count > (len(data) - 40) // 8:
        raise ValueError("TLV 数量超出帧长度")

    payloads = {}
    offset = 40
    for _ in range(tlv_count):
        if offset + 8 > len(data):
            raise ValueError("TLV 头被截断")
        tlv_type, length = unpack_from('<II', data, offset)
        offset += 8
        end = offset + length
        if end > len(data):
            raise ValueError(f"TLV {tlv_type} 数据超出帧边界")
        if tlv_type in (1, 7):
            if tlv_type in payloads:
                raise ValueError(f"重复的点云 TLV {tlv_type}")
            expected = obj_count * (16 if tlv_type == 1 else 4)
            if length != expected:
                raise ValueError(f"TLV {tlv_type} 长度 {length} 与点数 {obj_count} 不匹配，应为 {expected}")
            payloads[tlv_type] = memoryview(data)[offset:end]
        offset = end

    if obj_count == 0:
        return np.empty((0, 6), dtype=np.float32)
    if 1 not in payloads or 7 not in payloads:
        raise ValueError("非空点云缺少 TLV 1 或 TLV 7；需要浮点点云及 side-info 格式")
    result = np.empty((obj_count, 6), dtype=np.float32)
    result[:, :4] = np.frombuffer(payloads[1], dtype='<f4').reshape(-1, 4)
    result[:, 4:] = np.frombuffer(payloads[7], dtype='<i2').reshape(-1, 2).astype(np.float32) / np.float32(10)
    if not np.isfinite(result).all():
        raise ValueError("点云包含非有限数值")
    return result
