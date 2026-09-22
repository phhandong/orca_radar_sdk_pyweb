'''
Author       : zhjos
Date         : 2023-04-19 09:30:32
LastEditors  : zhjos
LastEditTime : 2023-04-19 10:29:05
Description  : 
Copyright 2023 ORCA, All Rights Reserved. 
2023-04-19 09:30:32
'''
import logging
import os
import sys
import time
from logging import handlers

__all__ = ['log_base', 'log_radar', 'log_stm_check']

PATH = sys.path[0] + '/'


def _init_time_log(name, when, interval, backupCount, console=True):
    ''' 初始化时间回滚log 
    :param name: 日志名
    :param when: 何时备份，可选D、M、H等，分别对应天、分钟、小时
    :param interval: 几个间隔产生一个新日志，例如interval填2，when填H的情况下，每两个小时
    :param backupCount: 最多产生几个日志文件
    :param console: 是否在控制台输出
    :return: logger对象
    '''
    logger = logging.getLogger(name)
    format_str = logging.Formatter('%(asctime)s - %(levelname)s: %(message)s')
    logger.setLevel(logging.DEBUG)

    th = handlers.TimedRotatingFileHandler(filename=PATH + 'log/' + name,
                                           when=when,
                                           interval=interval,
                                           backupCount=backupCount,
                                           encoding='utf-8')

    th.setFormatter(format_str)
    logger.addHandler(th)
    if not console:
        return logger

    sh = logging.StreamHandler()
    sh.setFormatter(format_str)
    logger.addHandler(sh)
    return logger


if not os.path.exists(PATH + 'log/'):
    os.makedirs(PATH + 'log/')

log_base = _init_time_log('base.log', 'D', 1, 10)
log_radar = _init_time_log('radar.log', 'D', 1, 10)
log_stm_check = _init_time_log('stm_check.log', 'D', 1, 10)
