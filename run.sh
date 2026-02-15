#!/bin/bash
# 魔丸小游戏启动脚本
# 使用方法: ./run.sh [端口]
# 默认端口: 5000

PORT=${1:-5000}

echo "======================================"
echo "     魔丸小游戏服务器启动中..."
echo "======================================"

cd /home/z/my-project/game/backend

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "检查依赖..."
pip install -r requirements.txt -q 2>/dev/null

# 启动服务器
echo ""
echo "服务器启动成功！"
echo "访问地址: http://localhost:$PORT"
echo ""
echo "游戏规则:"
echo "  - 3-5人策略推理类棋类游戏"
echo "  - 访问 http://主机:$PORT/房间ID 可直接加入房间"
echo "  - 房间ID为4位大小写字母+数字组合"
echo ""
echo "按 Ctrl+C 停止服务器"
echo "======================================"

python -c "
from app import app, socketio
socketio.run(app, host='0.0.0.0', port=$PORT, debug=False)
"
