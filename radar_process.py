'''
Author       : orca
Date         : 2025-04-08 11:02:11
LastEditors  : zhjos
LastEditTime : 2025-04-08 15:37:46
Description  : 
Copyright 2025 ORCA, All Rights Reserved.
2025-04-08 11:02:11
'''
from math import radians, tan

import numpy as np
from sklearn.cluster import dbscan

__all__ = ['radar_filter']


def radar_filter(points: np.ndarray,
                 max_speed: float = 15.365,
                 max_range: float = 800,
                 min_x_abs: float = 1,
                 min_y_abs: float = 0.6,
                 rcs_threshold: float = 115,
                 with_voxel: bool = True,
                 voxel_size: float = 0.25,
                 with_cluster: bool = True,
                 eps: float = 8,
                 min_num: int = 15) -> np.ndarray:
    """ 毫米波雷达滤波函数
    毫米波雷达输出点云坐标系：
        x: 雷达右侧方向
        y: 雷达正前方方向
        z: 雷达正上方方向
        speed: 目标与雷达的径向速度，远离雷达是正，靠近雷达是负
        snr: cfar检测中目标点云的信噪比
        noise: cfar检测中目标点云对应的噪声

    注意：
    1. with_cluster为True时计算复杂度较高，默认使用DBSCAN算法进行聚类滤波【可参考：https://zhuanlan.zhihu.com/p/712245791】
    2. 选用聚类滤波时建议同时开启体素滤波减少点云数量，从而降低聚类滤波的计算复杂度

    Args:
        points (np.ndarray): 输入点云，6维数据[x, y, z, speed, snr, noise]
        max_speed (float, optional): 毫米波雷达最大可检测的径向速度（根据雷达输入的配置文件确定）. Defaults to 15.365.
        max_range (float, optional): 毫米波雷达最大可检测的距离（根据雷达输入的配置文件确定）. Defaults to 800.
        min_x_abs (float, optional): 雷达近处x轴范围内点云滤掉. Defaults to 1.
        min_y_abs (float, optional): 雷达近处y轴范围内点云滤掉. Defaults to 0.6.
        rcs_threshold (float, optional): 雷达反射截面积阈值. Defaults to 120.
        with_voxel (bool, optional): 是否进行聚类滤波. Defaults to True.
        voxel_size (float, optional): 体素滤波尺寸，当with_voxel为True时生效，不可以是0. Defaults to 0.25.
        with_cluster (bool, optional): 是否进行聚类滤波. Defaults to True.
        eps (float, optional): 聚类半径，当with_cluster为True时生效. Defaults to 8.
        min_num (int, optional): 类内最少点云数，当with_cluster为True时生效. Defaults to 15.

    Returns:
        np.ndarray: 滤波后点云，如果with_cluster为True时，输出点云维度变为7维，最后一维是新增的聚类标签。
    """
    # 固定杂波点滤除
    points = fixed_filter(points, max_speed)
    # 直通滤波
    points = direct_filter(points, max_range, min_x_abs, min_y_abs)
    # 根据雷达反射截面积过滤杂波与低反射的目标
    p = cal_radar_rcs(points)
    points = points[p > rcs_threshold]
    # 体素滤波
    if with_voxel:
        points = voxel_filter(points, voxel_size)
    # 密度聚类
    if with_cluster:
        points = dbscan_cluster(points, eps, min_num)
    return points


def fixed_filter(points: np.ndarray, max_speed: float) -> np.ndarray:
    """ 2944雷达专用滤波算法

    Args:
        points (np.ndarray): 点云，[x, y, z, v, snr, noise]
        max_speed (float): 当前波形下radar点云最大检测的径向速度

    Returns:
        np.ndarray: 滤波后点云，[x, y, z, v, snr, noise]
    """
    # 删除船近处射线状速度为最大值的杂波点
    points = points[np.abs(points[:, 3]) < (max_speed - 0.05)]
    # 删除部分固定点杂波
    res_points = points
    angles = cal_points_angle(points)
    speed_percent = points[:, 3] / max_speed
    # 提取左侧点云，如果有左侧点云的话就不用计算右侧是否还有了
    angles_xoy = angles[:, 0]
    angles_z = angles[:, 1]
    # 左侧靠中心的杂波点
    idxs_1 = (angles_xoy > 24) * (angles_xoy < 25.23) * (angles_z > 4.65) * (angles_z < 7.83)
    idxs_2 = (angles_xoy > 58.62) * (angles_xoy < 62.09) * (angles_z > -20.42) * (angles_z < -17.89)
    #速度条件
    idxs_speed = (speed_percent > 0.6197) * (speed_percent < 0.6355)
    idxs_left = idxs_speed * (idxs_1 + idxs_2)
    if idxs_left.sum() > 1:
        res_points = points[~idxs_left]
        return res_points
    # 右侧靠中心的杂波点
    idxs_1 = (angles_xoy > -42.8) * (angles_xoy < -40.61) * (angles_z > 10.25) * (angles_z < 15.02)
    idxs_2 = (angles_xoy > -63.411) * (angles_xoy < -62.97) * (angles_z > -5.15) * (angles_z < -1.25)
    #速度条件
    idxs_speed = (speed_percent > 0.369) * (speed_percent < 0.383)
    idxs_right = idxs_speed * (idxs_1 + idxs_2)
    res_points = points[~idxs_right]
    # 删除船近处射线状速度为最大值的杂波点
    res_points = res_points[np.abs(res_points[:, 3]) < (max_speed - 0.05)]
    return res_points


def direct_filter(points: np.ndarray,
                  max_range: float,
                  min_x_abs: float,
                  min_y_abs: float,
                  degree: float = 120) -> np.ndarray:
    """ 毫米波雷达点云直通滤波

    Args:
        points (np.ndarray): 毫米波雷达点云
        max_range (float): 该毫米波雷达最远探测距离.
        min_x_abs (float): 毫米波雷达近处两侧滤除距离.
        min_y_abs (float): 毫米波雷达近处前后滤除距离.
        degree (float, optional): 水平有效角度. Defaults to 150.

    Returns:
        np.ndarray: 直通滤波后的毫米波雷达点云
    """
    if len(points) == 0:
        return points
    slope = tan(radians(90 - degree / 2))

    # 忽略距离雷达过近的点云
    points__0 = points[:, 0]
    points__1 = points[:, 1]
    points__2 = points[:, 2]
    # 忽略雷达正前方近处的点云
    tmp_idxs = np.bitwise_or(np.abs(points__0) > min_x_abs, points__1 > min_y_abs)
    # 忽略水平方向观测角度外的点
    tmp_idxs *= points__1 >= np.abs(points__0 * slope)
    # 将range距离外的点忽略掉
    tmp_idxs *= np.bitwise_and(np.abs(points__0) <= max_range, points__1 <= max_range)
    # 忽略垂直方向正负50度外的点
    tmp_idxs *= (points__1 * tan(radians(50)) >= np.abs(points__2))
    points = points[tmp_idxs]

    return points


def cal_radar_rcs(points: np.ndarray) -> np.ndarray:
    """ 计算radar目标的RCS等比例的参数

    Args:
        points (np.ndarray): 输入点云

    Returns:
        np.ndarray: RCS等比例参数
    """
    dist = np.sqrt(points[:, 0]**2 + points[:, 1]**2 + points[:, 2]**2)
    rcs = (points[:, 4] + points[:, 5]) / 10 + np.log10(dist) * 40
    return rcs


def voxel_filter(points: np.ndarray, voxel_size: float) -> np.ndarray:
    """ 基于numpy实现的体素滤波算法

    Args:
        points (np.ndarray): 输入点云
        voxel_size (float): 体素滤波尺寸

    Returns:
        np.ndarray: 输出点云
    """
    if voxel_size == 0:
        return points
    tmp_points = (points[:, :2] / voxel_size).astype(np.int32)
    _, idxs = np.unique(tmp_points[:, :2], axis=0, return_index=True)
    points = points[idxs]
    return points


def dbscan_cluster(points: np.ndarray, eps: float, min_num: int) -> np.ndarray:
    """ dbscan聚类

    Args:
        points (np.ndarray): 输入点云数据，6维数据[x, y, z, speed, snr, noise]
        eps (float): 聚类半径
        min_num (int): 类内最少点云数

    Returns:
        np.ndarray: 输出聚类、滤波后的点云数据，新增一维聚类id [x, y, z, speed, snr, noise, cluster_id]
    """
    if len(points) == 0:
        return np.zeros([0, 7], dtype=points.dtype)
    _, cluster_idxs = dbscan(points[:, :3], eps=eps, min_samples=min_num, algorithm='ball_tree', leaf_size=10)
    points = np.concatenate((points, cluster_idxs[:, None]), axis=1)
    points = points[cluster_idxs != -1]
    return points


def cal_points_angle(points: np.ndarray) -> np.ndarray:
    """ 计算点云在球坐标系下的角度

    Args:
        points (np.ndarray): 多个点云，即形状应是 m*n ，m个点云，n维数据

    Returns:
        np.ndarray: 多个点云对应的角度，第一维是xoy平面内的角度，第二维是z轴与xoy平面的夹角
    """
    theta = np.degrees(np.arctan(points[:, 0] / points[:, 1]))
    dist = np.linalg.norm(points[:, :3], axis=1)
    phy = np.degrees(np.arcsin(points[:, 2] / dist))
    res = np.concatenate((theta[:, None], phy[:, None]), axis=1)
    return res
