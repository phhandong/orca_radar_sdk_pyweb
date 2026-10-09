"""Adapter for the original ORCA filter (raw side-info) and web points (dB)."""

import math

import numpy as np

from radar_process import radar_filter

DEFAULTS = dict(max_speed=15.365, max_range=800., min_x_abs=1., min_y_abs=.6,
                rcs_threshold=115., with_voxel=True, voxel_size=.25,
                with_cluster=True, eps=8., min_num=15)


def process_points(points, options):
    if not isinstance(options, dict) or set(options) - DEFAULTS.keys():
        raise ValueError("滤波参数无效或包含未知选项")
    params = {**DEFAULTS, **options}
    for key, value in params.items():
        if key in ("with_voxel", "with_cluster"):
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须为布尔值")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} 必须为有限数值")
        if key == "min_num" and (not isinstance(value, int) or not 1 <= value <= 1200):
            raise ValueError("聚类最少点数必须是 1 到 1200 的整数")
        if key in ("max_speed", "max_range", "voxel_size", "eps") and value <= 0:
            raise ValueError(f"{key} 必须大于 0")
        if key in ("min_x_abs", "min_y_abs") and value < 0:
            raise ValueError(f"{key} 不能为负数")
    if not isinstance(points, list) or len(points) > 1200:
        raise ValueError("每帧点云必须为数组，最多 1200 点")
    data = np.asarray(points, dtype=np.float64)
    if not points:
        data = np.empty((0, 6), dtype=np.float64)
    if data.ndim != 2 or data.shape[1] != 6 or not np.isfinite(data).all():
        raise ValueError("点云必须为有限数值的 N×6 数组")
    # radar_process.cal_radar_rcs divides side-info by 10; preserve its original
    # threshold semantics without changing the supplied algorithm or raw logs.
    data = data.copy()
    data[:, 4:6] *= 10
    with np.errstate(divide="ignore", invalid="ignore"):
        result = radar_filter(data, **params)
    result = result.copy()
    result[:, 4:6] /= 10
    if not np.isfinite(result).all():
        raise ValueError("滤波结果包含非有限数值")
    return result.tolist()
