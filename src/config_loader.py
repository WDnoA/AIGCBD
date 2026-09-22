# -*- coding: utf-8 -*-
"""
配置加载模块
"""
import yaml
import os


def load_config(config_path="config/config.yaml"):
    """
    加载配置文件
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        dict: 配置字典
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    return config


def validate_config(config):
    """
    验证配置文件的完整性

    Args:
        config: 配置字典

    Returns:
        bool: 配置是否有效
    """
    # 检查AI配置
    if "ai" not in config:
        print("错误: 配置文件中缺少 'ai' 部分")
        return False

    ai_config = config["ai"]
    if "url" not in ai_config:
        print("错误: 请在配置文件中设置通义千问网页地址 (url)")
        return False

    # 检查聊天规则
    if "chat_rules" not in config or len(config["chat_rules"]) == 0:
        print("警告: 配置文件中没有设置任何聊天规则")
        return False

    return True
