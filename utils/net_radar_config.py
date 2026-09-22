'''
Author       : zhjos
Date         : 2023-03-27 15:18:09
LastEditors  : zhjos
LastEditTime : 2025-05-08 18:58:15
Description  : 烧写CFG
Copyright 2023 ORCA, All Rights Reserved. 
2023-03-27 15:18:09
'''
import socket
import time

import globalvar as g

__all__ = ['send_cfg']

cfg_grap_time = 0.2  # 发送每条cfg配置的时间间隔


def send_cfg(radar_ip, radar_cfg_path):
    if type(radar_cfg_path) == str:
        print('all radar use one cfg')
        one_cfg = True
        f = open(radar_cfg_path, 'r')
    elif len(radar_ip) != len(radar_cfg_path):
        print('All radars use the first cfg')
        one_cfg = True
        f = open(radar_cfg_path[0], 'r')
    else:
        print('Specify different cfgs for each radar')
        one_cfg = False
        f = []
        for i2 in range(len(radar_cfg_path)):
            f.append(open(radar_cfg_path[i2], 'r'))
    skt = 'socket.socket(socket.AF_INET, socket.SOCK_DGRAM)'  # UDP
    s = []
    if type(radar_ip) == str:
        s.append(eval(skt))
        s[0].connect((radar_ip, g.CFG_PORT))
    else:
        for i2 in range(len(radar_ip)):
            s.append(eval(skt))
            if one_cfg:
                s[i2].connect((radar_ip[i2], g.CFG_PORT))
    if one_cfg:
        for line in f:
            print("send :" + line)
            if line[0] == '%':
                continue
            for i3 in range(len(s)):
                result = s[i3].send(line.encode())
                res = s[i3].recv(8192)
                print(res)
                if line[:-1] == 'sensorStart':
                    time.sleep(0.003)
            if line[:-1] == 'sensorStop':
                time.sleep(0.2)
            else:
                time.sleep(cfg_grap_time)
    else:
        for i2 in range(len(f)):
            s[i2].connect((radar_ip[i2], g.CFG_PORT))
            for line in f[i2]:
                print("send:", line)
                if line[0] == '%':
                    continue
                result = s[i2].send(line.encode())
                res = s[i2].recv(8192)
                print("receive:", res)
                if line[:-1] == 'sensorStop':
                    time.sleep(0.2)
                else:
                    time.sleep(cfg_grap_time)

    for i1 in range(len(s)):
        s[i1].close()
