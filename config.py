'''
Author       : zhjos
Date         : 2023-03-22 16:27:30
LastEditors  : zhjos
LastEditTime : 2025-11-17 16:59:24
Description  : 雷达测试配置文件
Copyright 2023 ORCA, All Rights Reserved. 
2023-03-22 16:27:30
'''
import globalvar as g

# 此处输入1~6个雷达ip
radar_ip = ['192.168.3.201']  # '192.168.3.202', '192.168.3.203', '192.168.3.204', '192.168.3.205', '192.168.3.206'
# 雷达最远探测距离
max_range = [600]
# 雷达配置文件
radar_cfg_path = [g.cfg_600m]
# radar测试数据保存文件夹, 获取到的雷达数据比较重要的话，建议修改第二段的时间
save_file_path = "./data/20230822/"
# 是否实时显示点云
is_plot_points = True
