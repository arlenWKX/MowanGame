# 魔丸小游戏

一个基于 Flask + SQLite + WebSocket 的策略推理类棋类游戏。

## 游戏简介

**魔丸小游戏**是一款3-5人的策略推理类棋类游戏，游戏时长约20-40分钟。

### 游戏规则

1. **部署阶段**: 每位玩家将数字0-9放置到3×6棋盘的任意10个格子中
2. **行动阶段**: 玩家轮流进行行动（前进、单挑、回收或放弃）
3. **结算阶段**: 公共区域的棋子进行对决
4. **胜利条件**: 成为最后存活的玩家

### 对决规则

- **特殊规则**: 相同数字同归于尽；0与6/9同归于尽；8>0
- **一般规则**: 反向排序 0>1>2>3>4>5>6>7>8>9

## 技术栈

- **后端**: Python 3 + Flask + SQLite + Flask-SocketIO
- **前端**: HTML5 + CSS3 + Vue.js 3 (CDN) + Socket.IO
- **通信**: WebSocket (支持断线重连)

## 快速开始

### 方法一：使用启动脚本

```bash
cd /home/z/my-project/game
./run.sh [端口]  # 默认端口5000
```

### 方法二：手动启动

```bash
cd /home/z/my-project/game/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

### 访问游戏

- 主页: `http://主机:端口/`
- 直接加入房间: `http://主机:端口/房间ID`

## 功能特性

### 用户系统
- 用户注册/登录
- 安全的会话管理（Token认证）
- 用户统计数据

### 房间系统
- 创建房间（设置游戏人数3-5人）
- 4位大小写字母+数字的房间ID
- 房主可踢出玩家
- 通过URL直接加入房间

### 游戏功能
- 完整的游戏流程（部署→行动→结算）
- 实时WebSocket通信
- 断线自动重连
- 对决动画效果

### 排行榜
- 胜率排行
- 胜场排行
- 无需登录即可查看

## 项目结构

```
game/
├── backend/
│   ├── app.py           # Flask主应用
│   ├── requirements.txt # Python依赖
│   └── game.db          # SQLite数据库（自动创建）
├── frontend/
│   └── index.html       # 前端单页应用
└── run.sh               # 启动脚本
```

## API文档

### 认证接口

| 接口 | 方法 | 描述 |
|------|------|------|
| `/api/register` | POST | 用户注册 |
| `/api/login` | POST | 用户登录 |
| `/api/logout` | POST | 用户登出 |
| `/api/me` | GET | 获取当前用户信息 |

### 房间接口

| 接口 | 方法 | 描述 |
|------|------|------|
| `/api/rooms` | POST | 创建房间 |
| `/api/rooms/<id>` | GET | 获取房间信息 |
| `/api/rooms/<id>/join` | POST | 加入房间 |
| `/api/rooms/<id>/leave` | POST | 离开房间 |
| `/api/rooms/<id>/kick` | POST | 踢出玩家 |
| `/api/rooms/<id>/start` | POST | 开始游戏 |

### 其他接口

| 接口 | 方法 | 描述 |
|------|------|------|
| `/api/leaderboard` | GET | 获取排行榜 |

### WebSocket事件

| 事件 | 方向 | 描述 |
|------|------|------|
| `authenticate` | C→S | Socket认证 |
| `join_room` | C→S | 加入房间 |
| `leave_room` | C→S | 离开房间 |
| `start_game` | C→S | 开始游戏 |
| `deploy` | C→S | 部署棋子 |
| `action` | C→S | 玩家行动 |
| `game_started` | S→C | 游戏开始 |
| `turn_changed` | S→C | 回合变更 |
| `duel` | S→C | 对决事件 |
| `game_ended` | S→C | 游戏结束 |

## 移动端适配

游戏界面已针对移动端和桌面端进行响应式设计，可在手机、平板和电脑上流畅运行。

## 许可证

MIT License
