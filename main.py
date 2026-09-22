'''
Author       : zhjos
Date         : 2023-03-27 15:18:09
LastEditors  : zhjos
LastEditTime : 2025-11-14 20:47:04
Description  : 雷达测试主函数
Copyright 2023 ORCA, All Rights Reserved. 
2023-03-27 15:18:09
'''
import os
import threading
import time

import config as cfg
from utils import *


def main():
    if not os.path.exists(cfg.save_file_path):
        os.makedirs(cfg.save_file_path)
    # 给雷达写入配置
    send_cfg(cfg.radar_ip, cfg.radar_cfg_path)

    file_path = os.path.join(cfg.save_file_path, f'{time.time()}/')
    if not os.path.exists(file_path):
        os.makedirs(file_path)
    time.sleep(1)
    # 接收、解析雷达数据
    threading.Thread(target=data_recv, args=(file_path,)).start()
    time.sleep(1)

    # 实时显示雷达数据
    if cfg.is_plot_points:
        draw_points(0)


if __name__ == "__main__":
    main()
