#!/bin/bash
# 魔丸小游戏启动脚本

cd /home/z/my-project/game/backend

# 激活虚拟环境
source venv/bin/activate

# 启动服务器
python app.py
