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
    provider = (ai_config.get("provider") or "web").strip().lower()

    if provider == "openai":
        # 本地/远端 OpenAI 兼容接口：base_url 必须可解析，url 不再需要
        if not ai_config.get("base_url"):
            print("错误: provider=openai 时必须在配置文件中设置 base_url")
            print("      例如: base_url: http://127.0.0.1:8080/v1")
            return False
    elif provider == "web":
        if "url" not in ai_config:
            print("错误: 请在配置文件中设置通义千问网页地址 (url)")
            return False
    else:
        print(f"错误: 不支持的 ai.provider: {provider}（可选 openai / web）")
        return False

    # 检查聊天规则
    if "chat_rules" not in config or len(config["chat_rules"]) == 0:
        print("警告: 配置文件中没有设置任何聊天规则")
        return False

    return True
