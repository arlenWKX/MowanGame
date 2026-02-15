#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
魔丸小游戏 - 后端服务
Flask + SQLite + WebSocket
"""

import os
import json
import random
import string
import hashlib
import secrets
import time
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, g, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room, rooms
from flask_cors import CORS
import sqlite3

# 初始化Flask应用
app = Flask(__name__, static_folder='../frontend')
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['DATABASE'] = os.path.join(os.path.dirname(__file__), 'game.db')

# 配置CORS和SocketIO
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=60, ping_interval=25)

# ==================== 数据库操作 ====================

def get_db():
    """获取数据库连接"""
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(error):
    """关闭数据库连接"""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """初始化数据库"""
    db = get_db()
    db.executescript('''
        -- 用户表
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            nickname TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_games INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0
        );
        
        -- 会话表
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );
        
        -- 房间表
        CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            max_players INTEGER DEFAULT 4,
            status TEXT DEFAULT 'waiting',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            game_data TEXT,
            FOREIGN KEY (creator_id) REFERENCES users (id)
        );
        
        -- 房间玩家表
        CREATE TABLE IF NOT EXISTS room_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            is_creator BOOLEAN DEFAULT 0,
            is_ready BOOLEAN DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (room_id) REFERENCES rooms (id),
            FOREIGN KEY (user_id) REFERENCES users (id),
            UNIQUE(room_id, user_id)
        );
        
        -- 游戏记录表
        CREATE TABLE IF NOT EXISTS game_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            rank INTEGER,
            eliminated_at TIMESTAMP,
            FOREIGN KEY (room_id) REFERENCES rooms (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        );
        
        -- 创建索引
        CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_room_players_room ON room_players(room_id);
        CREATE INDEX IF NOT EXISTS idx_room_players_user ON room_players(user_id);
    ''')
    db.commit()

def query_db(query, args=(), one=False):
    """查询数据库"""
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    """执行数据库操作"""
    db = get_db()
    db.execute(query, args)
    db.commit()

# ==================== 工具函数 ====================

def hash_password(password):
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token():
    """生成会话令牌"""
    return secrets.token_hex(32)

def generate_room_id():
    """生成4位房间ID（大小写字母+数字）"""
    chars = string.ascii_letters + string.digits
    while True:
        room_id = ''.join(random.choice(chars) for _ in range(4))
        if not query_db('SELECT id FROM rooms WHERE id = ?', (room_id,), one=True):
            return room_id

def get_user_by_token(token):
    """通过令牌获取用户"""
    result = query_db('''
        SELECT u.id, u.username, u.nickname, u.total_games, u.wins
        FROM users u
        JOIN sessions s ON u.id = s.user_id
        WHERE s.token = ? AND (s.expires_at IS NULL OR s.expires_at > datetime('now'))
    ''', (token,), one=True)
    return dict(result) if result else None

def token_required(f):
    """令牌验证装饰器"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': '未提供认证令牌'}), 401
        user = get_user_by_token(token)
        if not user:
            return jsonify({'error': '无效或过期的令牌'}), 401
        g.current_user = user
        return f(*args, **kwargs)
    return decorated

# ==================== 游戏逻辑 ====================

class GameLogic:
    """游戏逻辑类"""
    
    @staticmethod
    def create_empty_board():
        """创建空棋盘（3行6列）"""
        return [[None for _ in range(6)] for _ in range(3)]
    
    @staticmethod
    def get_available_numbers(board):
        """获取可用数字"""
        used = set()
        for row in board:
            for cell in row:
                if cell is not None:
                    used.add(cell)
        return [i for i in range(10) if i not in used]
    
    @staticmethod
    def validate_deployment(board):
        """验证部署是否有效"""
        count = 0
        for row in board:
            for cell in row:
                if cell is not None:
                    count += 1
        return count == 10
    
    @staticmethod
    def can_move_forward(board, row, col):
        """检查是否可以前进"""
        if board[row][col] is None:
            return False, "该位置没有棋子"
        
        # 如果在第1行（最前排），可以移动到公共区域
        if row == 0:
            return True, "public"
        
        # 检查前方格子是否为空
        target_row = row - 1
        if board[target_row][col] is not None:
            return False, "前方格子已被占用"
        
        return True, (target_row, col)
    
    @staticmethod
    def get_duel_result(num1, num2):
        """
        对决判定
        返回: (winner, loser) 或 (None, None) 表示同归于尽
        """
        # 特殊规则
        if num1 == num2:
            return None, None  # 相同数字同归于尽
        
        if (num1 == 0 and num2 in [6, 9]) or (num2 == 0 and num1 in [6, 9]):
            return None, None  # 0与6/9同归于尽
        
        if num1 == 8 and num2 == 0:
            return num1, num2  # 8 > 0
        if num2 == 8 and num1 == 0:
            return num2, num1  # 8 > 0
        
        # 一般规则：反向排序 0 > 1 > 2 > ... > 9
        if num1 < num2:
            return num1, num2
        else:
            return num2, num1

# ==================== 游戏状态管理 ====================

# 内存中的游戏状态
game_states = {}

class GameState:
    """游戏状态类"""
    
    def __init__(self, room_id):
        self.room_id = room_id
        self.players = []  # 玩家列表 [{id, nickname, board, eliminated, eliminated_numbers}]
        self.player_order = []  # 行动顺序
        self.current_player_index = 0  # 当前行动玩家索引
        self.public_area = []  # 公共区域 [{player_id, number, action_order}]
        self.phase = 'waiting'  # waiting, deployment, action, settlement, ended
        self.round_number = 0
        self.action_count = 0  # 当前回合已行动玩家数
        self.extra_actions = []  # 额外行动队列
        self.winner = None
    
    def to_dict(self, for_player_id=None):
        """转换为字典（用于传输）"""
        players_info = []
        for p in self.players:
            info = {
                'id': p['id'],
                'nickname': p['nickname'],
                'eliminated': p['eliminated'],
                'eliminated_numbers': p['eliminated_numbers'],
                'board_occupied': [[cell is not None for cell in row] for row in p['board']]
            }
            # 只显示自己的棋盘数字
            if for_player_id == p['id']:
                info['board'] = p['board']
            players_info.append(info)
        
        return {
            'room_id': self.room_id,
            'players': players_info,
            'player_order': self.player_order,
            'current_player_index': self.current_player_index,
            'public_area': self.public_area,
            'phase': self.phase,
            'round_number': self.round_number,
            'winner': self.winner
        }
    
    def get_player(self, player_id):
        """获取玩家"""
        for p in self.players:
            if p['id'] == player_id:
                return p
        return None
    
    def get_current_player(self):
        """获取当前行动玩家"""
        if not self.player_order:
            return None
        player_id = self.player_order[self.current_player_index]
        return self.get_player(player_id)
    
    def next_turn(self):
        """进入下一个玩家回合"""
        self.current_player_index = (self.current_player_index + 1) % len(self.player_order)
        # 跳过已淘汰的玩家
        attempts = 0
        while self.get_current_player()['eliminated'] and attempts < len(self.player_order):
            self.current_player_index = (self.current_player_index + 1) % len(self.player_order)
            attempts += 1
    
    def check_game_end(self):
        """检查游戏是否结束"""
        active_players = [p for p in self.players if not p['eliminated']]
        if len(active_players) == 1:
            self.winner = active_players[0]['id']
            self.phase = 'ended'
            return True
        return False
    
    def has_remaining_pieces(self, player_id):
        """检查玩家是否还有棋子"""
        player = self.get_player(player_id)
        if not player or player['eliminated']:
            return False
        
        # 检查棋盘上是否有棋子
        for row in player['board']:
            for cell in row:
                if cell is not None:
                    return True
        
        # 检查公共区域是否有该玩家的棋子
        for piece in self.public_area:
            if piece['player_id'] == player_id:
                return True
        
        return False

# ==================== HTTP路由 ====================

@app.route('/api/register', methods=['POST'])
def register():
    """用户注册"""
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    nickname = data.get('nickname', '').strip()
    
    if not username or not password or not nickname:
        return jsonify({'error': '所有字段都必须填写'}), 400
    
    if len(username) < 3 or len(username) > 20:
        return jsonify({'error': '用户名长度必须在3-20个字符之间'}), 400
    
    if len(password) < 6:
        return jsonify({'error': '密码长度至少6个字符'}), 400
    
    if len(nickname) > 20:
        return jsonify({'error': '昵称长度不能超过20个字符'}), 400
    
    try:
        execute_db(
            'INSERT INTO users (username, password, nickname) VALUES (?, ?, ?)',
            (username, hash_password(password), nickname)
        )
        return jsonify({'message': '注册成功，请登录'}), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': '用户名已存在'}), 400

@app.route('/api/login', methods=['POST'])
def login():
    """用户登录"""
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return jsonify({'error': '用户名和密码都必须填写'}), 400
    
    user = query_db(
        'SELECT * FROM users WHERE username = ? AND password = ?',
        (username, hash_password(password)),
        one=True
    )
    
    if not user:
        return jsonify({'error': '用户名或密码错误'}), 401
    
    # 创建会话
    token = generate_token()
    execute_db(
        'INSERT INTO sessions (user_id, token, expires_at) VALUES (?, ?, datetime("now", "+7 days"))',
        (user['id'], token)
    )
    
    return jsonify({
        'token': token,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'nickname': user['nickname'],
            'total_games': user['total_games'],
            'wins': user['wins']
        }
    })

@app.route('/api/logout', methods=['POST'])
@token_required
def logout():
    """用户登出"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    execute_db('DELETE FROM sessions WHERE token = ?', (token,))
    return jsonify({'message': '已登出'})

@app.route('/api/me', methods=['GET'])
@token_required
def get_current_user():
    """获取当前用户信息"""
    return jsonify(g.current_user)

@app.route('/api/rooms', methods=['POST'])
@token_required
def create_room():
    """创建房间"""
    data = request.get_json()
    max_players = data.get('max_players', 4)
    
    if max_players < 3 or max_players > 5:
        return jsonify({'error': '游戏人数必须在3-5人之间'}), 400
    
    room_id = generate_room_id()
    user_id = g.current_user['id']
    
    try:
        execute_db(
            'INSERT INTO rooms (id, creator_id, max_players) VALUES (?, ?, ?)',
            (room_id, user_id, max_players)
        )
        execute_db(
            'INSERT INTO room_players (room_id, user_id, is_creator) VALUES (?, ?, 1)',
            (room_id, user_id)
        )
        
        return jsonify({
            'room_id': room_id,
            'max_players': max_players
        }), 201
    except Exception as e:
        return jsonify({'error': '创建房间失败'}), 500

@app.route('/api/rooms/<room_id>', methods=['GET'])
@token_required
def get_room(room_id):
    """获取房间信息"""
    room = query_db('SELECT * FROM rooms WHERE id = ?', (room_id,), one=True)
    if not room:
        return jsonify({'error': '房间不存在'}), 404
    
    players = query_db('''
        SELECT rp.user_id, rp.is_creator, rp.is_ready, u.nickname, u.total_games, u.wins
        FROM room_players rp
        JOIN users u ON rp.user_id = u.id
        WHERE rp.room_id = ?
        ORDER BY rp.joined_at
    ''', (room_id,))
    
    return jsonify({
        'id': room['id'],
        'creator_id': room['creator_id'],
        'max_players': room['max_players'],
        'status': room['status'],
        'players': [dict(p) for p in players]
    })

@app.route('/api/rooms/<room_id>/join', methods=['POST'])
@token_required
def join_room_api(room_id):
    """加入房间"""
    room = query_db('SELECT * FROM rooms WHERE id = ?', (room_id,), one=True)
    if not room:
        return jsonify({'error': '房间不存在'}), 404
    
    if room['status'] != 'waiting':
        return jsonify({'error': '游戏已开始，无法加入'}), 400
    
    players = query_db('SELECT * FROM room_players WHERE room_id = ?', (room_id,))
    if len(players) >= room['max_players']:
        return jsonify({'error': '房间已满'}), 400
    
    user_id = g.current_user['id']
    
    # 检查是否已在房间中
    existing = query_db('SELECT * FROM room_players WHERE room_id = ? AND user_id = ?', (room_id, user_id), one=True)
    if existing:
        return jsonify({'message': '已在房间中'})
    
    try:
        execute_db(
            'INSERT INTO room_players (room_id, user_id) VALUES (?, ?)',
            (room_id, user_id)
        )
        return jsonify({'message': '加入成功'})
    except Exception as e:
        return jsonify({'error': '加入房间失败'}), 500

@app.route('/api/rooms/<room_id>/leave', methods=['POST'])
@token_required
def leave_room_api(room_id):
    """离开房间"""
    user_id = g.current_user['id']
    
    # 检查是否是房主
    room = query_db('SELECT * FROM rooms WHERE id = ?', (room_id,), one=True)
    if room and room['creator_id'] == user_id:
        # 房主离开，解散房间
        execute_db('DELETE FROM room_players WHERE room_id = ?', (room_id,))
        execute_db('DELETE FROM rooms WHERE id = ?', (room_id,))
        if room_id in game_states:
            del game_states[room_id]
    else:
        execute_db('DELETE FROM room_players WHERE room_id = ? AND user_id = ?', (room_id, user_id))
    
    return jsonify({'message': '已离开房间'})

@app.route('/api/rooms/<room_id>/kick', methods=['POST'])
@token_required
def kick_player(room_id):
    """踢出玩家"""
    room = query_db('SELECT * FROM rooms WHERE id = ?', (room_id,), one=True)
    if not room:
        return jsonify({'error': '房间不存在'}), 404
    
    if room['creator_id'] != g.current_user['id']:
        return jsonify({'error': '只有房主才能踢出玩家'}), 403
    
    data = request.get_json()
    target_id = data.get('user_id')
    
    if target_id == g.current_user['id']:
        return jsonify({'error': '不能踢出自己'}), 400
    
    execute_db('DELETE FROM room_players WHERE room_id = ? AND user_id = ?', (room_id, target_id))
    
    return jsonify({'message': '已踢出玩家'})

@app.route('/api/rooms/<room_id>/start', methods=['POST'])
@token_required
def start_game(room_id):
    """开始游戏"""
    room = query_db('SELECT * FROM rooms WHERE id = ?', (room_id,), one=True)
    if not room:
        return jsonify({'error': '房间不存在'}), 404
    
    if room['creator_id'] != g.current_user['id']:
        return jsonify({'error': '只有房主才能开始游戏'}), 403
    
    players = query_db('SELECT * FROM room_players WHERE room_id = ?', (room_id,))
    if len(players) < 3:
        return jsonify({'error': '至少需要3名玩家才能开始游戏'}), 400
    
    # 更新房间状态
    execute_db('UPDATE rooms SET status = ? WHERE id = ?', ('playing', room_id))
    
    return jsonify({'message': '游戏开始'})

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    """获取排行榜"""
    leaderboard = query_db('''
        SELECT id, username, nickname, total_games, wins,
               CASE WHEN total_games > 0 THEN ROUND(CAST(wins AS FLOAT) / total_games * 100, 1) ELSE 0 END as win_rate
        FROM users
        WHERE total_games > 0
        ORDER BY win_rate DESC, wins DESC
        LIMIT 50
    ''')
    
    return jsonify([dict(row) for row in leaderboard])

# ==================== WebSocket事件 ====================

# 存储用户与Socket ID的映射
socket_users = {}
user_sockets = {}

@socketio.on('connect')
def handle_connect():
    """处理连接"""
    print(f'客户端连接: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    """处理断开连接"""
    print(f'客户端断开: {request.sid}')
    
    # 清理用户映射
    if request.sid in socket_users:
        user_id = socket_users[request.sid]
        if user_id in user_sockets:
            del user_sockets[user_id]
        del socket_users[request.sid]

@socketio.on('authenticate')
def handle_authenticate(data):
    """处理认证"""
    token = data.get('token')
    user = get_user_by_token(token)
    
    if not user:
        emit('auth_error', {'error': '认证失败'})
        return
    
    # 存储用户映射
    socket_users[request.sid] = user['id']
    user_sockets[user['id']] = request.sid
    
    emit('authenticated', {'user': user})

@socketio.on('join_room')
def handle_join_room(data):
    """处理加入房间"""
    room_id = data.get('room_id')
    user_id = socket_users.get(request.sid)
    
    if not user_id:
        emit('error', {'error': '未认证'})
        return
    
    # 加入Socket房间
    join_room(room_id)
    
    # 获取房间信息
    room = query_db('SELECT * FROM rooms WHERE id = ?', (room_id,), one=True)
    players = query_db('''
        SELECT rp.user_id, rp.is_creator, rp.is_ready, u.nickname, u.total_games, u.wins
        FROM room_players rp
        JOIN users u ON rp.user_id = u.id
        WHERE rp.room_id = ?
        ORDER BY rp.joined_at
    ''', (room_id,))
    
    user = query_db('SELECT nickname FROM users WHERE id = ?', (user_id,), one=True)
    
    # 通知房间内所有人
    emit('player_joined', {
        'player': {
            'id': user_id,
            'nickname': user['nickname']
        },
        'players': [dict(p) for p in players],
        'room_info': dict(room) if room else None
    }, room=room_id)

@socketio.on('leave_room')
def handle_leave_room(data):
    """处理离开房间"""
    room_id = data.get('room_id')
    user_id = socket_users.get(request.sid)
    
    if not user_id:
        return
    
    leave_room(room_id)
    
    user = query_db('SELECT nickname FROM users WHERE id = ?', (user_id,), one=True)
    
    emit('player_left', {
        'player_id': user_id,
        'nickname': user['nickname'] if user else 'Unknown'
    }, room=room_id)

@socketio.on('kick_player')
def handle_kick_player(data):
    """处理踢出玩家"""
    room_id = data.get('room_id')
    target_id = data.get('user_id')
    user_id = socket_users.get(request.sid)
    
    room = query_db('SELECT * FROM rooms WHERE id = ?', (room_id,), one=True)
    if not room or room['creator_id'] != user_id:
        emit('error', {'error': '无权踢出玩家'})
        return
    
    # 通知被踢出的玩家
    if target_id in user_sockets:
        emit('kicked', {'room_id': room_id}, room=user_sockets[target_id])
    
    # 更新房间信息
    players = query_db('''
        SELECT rp.user_id, rp.is_creator, rp.is_ready, u.nickname, u.total_games, u.wins
        FROM room_players rp
        JOIN users u ON rp.user_id = u.id
        WHERE rp.room_id = ?
        ORDER BY rp.joined_at
    ''', (room_id,))
    
    emit('player_kicked', {
        'player_id': target_id,
        'players': [dict(p) for p in players]
    }, room=room_id)

@socketio.on('start_game')
def handle_start_game(data):
    """处理开始游戏"""
    room_id = data.get('room_id')
    user_id = socket_users.get(request.sid)
    
    room = query_db('SELECT * FROM rooms WHERE id = ?', (room_id,), one=True)
    if not room or room['creator_id'] != user_id:
        emit('error', {'error': '无权开始游戏'})
        return
    
    players = query_db('''
        SELECT rp.user_id, u.nickname
        FROM room_players rp
        JOIN users u ON rp.user_id = u.id
        WHERE rp.room_id = ?
        ORDER BY rp.joined_at
    ''', (room_id,))
    
    if len(players) < 3:
        emit('error', {'error': '玩家数量不足'})
        return
    
    # 初始化游戏状态
    game = GameState(room_id)
    for p in players:
        game.players.append({
            'id': p['user_id'],
            'nickname': p['nickname'],
            'board': GameLogic.create_empty_board(),
            'eliminated': False,
            'eliminated_numbers': []
        })
    
    # 随机决定行动顺序
    game.player_order = [p['user_id'] for p in players]
    random.shuffle(game.player_order)
    game.current_player_index = 0
    game.phase = 'deployment'
    
    game_states[room_id] = game
    
    # 更新数据库
    execute_db('UPDATE rooms SET status = ? WHERE id = ?', ('playing', room_id))
    
    # 通知所有玩家
    emit('game_started', {
        'game_state': game.to_dict()
    }, room=room_id)

@socketio.on('deploy')
def handle_deploy(data):
    """处理部署"""
    room_id = data.get('room_id')
    board = data.get('board')
    user_id = socket_users.get(request.sid)
    
    game = game_states.get(room_id)
    if not game or game.phase != 'deployment':
        emit('error', {'error': '无法部署'})
        return
    
    player = game.get_player(user_id)
    if not player:
        emit('error', {'error': '玩家不存在'})
        return
    
    # 验证部署
    if not GameLogic.validate_deployment(board):
        emit('error', {'error': '部署无效，必须放置10个数字'})
        return
    
    player['board'] = board
    
    # 检查是否所有玩家都已部署
    all_deployed = all(
        GameLogic.validate_deployment(p['board'])
        for p in game.players
    )
    
    if all_deployed:
        game.phase = 'action'
        game.round_number = 1
        emit('deployment_complete', {
            'game_state': game.to_dict()
        }, room=room_id)
    else:
        emit('player_deployed', {
            'player_id': user_id,
            'game_state': game.to_dict()
        }, room=room_id)

@socketio.on('action')
def handle_action(data):
    """处理玩家行动"""
    room_id = data.get('room_id')
    action_type = data.get('type')
    action_data = data.get('data', {})
    user_id = socket_users.get(request.sid)
    
    game = game_states.get(room_id)
    if not game or game.phase != 'action':
        emit('error', {'error': '无法行动'})
        return
    
    current_player = game.get_current_player()
    if not current_player or current_player['id'] != user_id:
        emit('error', {'error': '不是你的回合'})
        return
    
    result = {'success': False, 'message': '未知错误'}
    
    if action_type == 'move':
        # 前进行动
        row = action_data.get('row')
        col = action_data.get('col')
        
        can_move, target = GameLogic.can_move_forward(current_player['board'], row, col)
        
        if can_move:
            number = current_player['board'][row][col]
            current_player['board'][row][col] = None
            
            if target == 'public':
                # 移动到公共区域
                game.public_area.append({
                    'player_id': user_id,
                    'number': number,
                    'action_order': game.action_count
                })
                result = {'success': True, 'message': f'数字{number}已进入公共区域'}
            else:
                # 移动到前方格子
                target_row, target_col = target
                current_player['board'][target_row][target_col] = number
                result = {'success': True, 'message': f'数字{number}已前进'}
        else:
            result = {'success': False, 'message': target}
    
    elif action_type == 'challenge':
        # 单挑行动（额外行动）
        target_player_id = action_data.get('target_player_id')
        target_row = action_data.get('row')
        target_col = action_data.get('col')
        
        target_player = game.get_player(target_player_id)
        if not target_player or target_player['eliminated']:
            result = {'success': False, 'message': '目标玩家无效'}
        elif target_player['board'][target_row][target_col] is None:
            result = {'success': False, 'message': '目标位置没有棋子'}
        else:
            number = target_player['board'][target_row][target_col]
            target_player['board'][target_row][target_col] = None
            game.public_area.append({
                'player_id': target_player_id,
                'number': number,
                'action_order': game.action_count
            })
            result = {'success': True, 'message': f'对玩家{target_player["nickname"]}的数字{number}发起单挑'}
            
            # 单挑后立即触发对决
            game.phase = 'settlement'
            process_settlement(game, room_id)
            return
    
    elif action_type == 'recover':
        # 回收行动（额外行动）
        piece_index = action_data.get('piece_index')
        target_row = action_data.get('target_row')
        target_col = action_data.get('target_col')
        
        if piece_index >= len(game.public_area):
            result = {'success': False, 'message': '无效的棋子'}
        elif game.public_area[piece_index]['player_id'] != user_id:
            result = {'success': False, 'message': '只能回收自己的棋子'}
        elif current_player['board'][target_row][target_col] is not None:
            result = {'success': False, 'message': '目标位置已被占用'}
        else:
            piece = game.public_area.pop(piece_index)
            current_player['board'][target_row][target_col] = piece['number']
            result = {'success': True, 'message': f'回收数字{piece["number"]}'}
    
    elif action_type == 'skip':
        # 放弃行动
        result = {'success': True, 'message': '放弃行动'}
    
    emit('action_result', result, room=request.sid)
    
    if result['success']:
        game.action_count += 1
        next_turn(game, room_id)

def next_turn(game, room_id):
    """处理下一回合"""
    # 检查是否所有玩家都已行动
    active_players = [p for p in game.players if not p['eliminated']]
    active_count = len(active_players)
    
    if game.action_count >= active_count:
        # 进入结算阶段
        game.phase = 'settlement'
        process_settlement(game, room_id)
    else:
        # 下一个玩家
        game.next_turn()
        emit('turn_changed', {
            'game_state': game.to_dict()
        }, room=room_id)

def process_settlement(game, room_id):
    """处理结算"""
    emit('settlement_start', {
        'game_state': game.to_dict()
    }, room=room_id)
    
    # 如果公共区域没有棋子
    if len(game.public_area) == 0:
        end_round(game, room_id)
        return
    
    # 如果公共区域只有一枚棋子
    if len(game.public_area) == 1:
        piece = game.public_area[0]
        player = game.get_player(piece['player_id'])
        
        # 给予额外行动
        emit('extra_action', {
            'player_id': piece['player_id'],
            'number': piece['number'],
            'game_state': game.to_dict()
        }, room=room_id)
        return
    
    # 进行对决
    process_duel(game, room_id)

def process_duel(game, room_id):
    """处理对决"""
    while len(game.public_area) >= 2:
        # 按行动顺序选择前两个
        sorted_pieces = sorted(game.public_area, key=lambda x: x['action_order'])
        piece1 = sorted_pieces[0]
        piece2 = sorted_pieces[1]
        
        winner, loser = GameLogic.get_duel_result(piece1['number'], piece2['number'])
        
        emit('duel', {
            'piece1': piece1,
            'piece2': piece2,
            'winner': winner,
            'loser': loser,
            'game_state': game.to_dict()
        }, room=room_id)
        
        # 添加延迟以便前端显示动画
        time.sleep(1.5)
        
        if winner is None:
            # 同归于尽
            player1 = game.get_player(piece1['player_id'])
            player2 = game.get_player(piece2['player_id'])
            player1['eliminated_numbers'].append(piece1['number'])
            player2['eliminated_numbers'].append(piece2['number'])
            
            game.public_area.remove(piece1)
            game.public_area.remove(piece2)
            
            # 检查玩家是否还有棋子
            check_player_elimination(game, piece1['player_id'])
            check_player_elimination(game, piece2['player_id'])
        else:
            # 有胜负
            winner_piece = piece1 if piece1['number'] == winner else piece2
            loser_piece = piece2 if piece1['number'] == winner else piece1
            
            loser_player = game.get_player(loser_piece['player_id'])
            loser_player['eliminated_numbers'].append(loser_piece['number'])
            
            game.public_area.remove(loser_piece)
            
            # 检查失败者是否还有棋子
            check_player_elimination(game, loser_piece['player_id'])
        
        # 检查游戏是否结束
        if game.check_game_end():
            handle_game_end(game, room_id)
            return
    
    # 结算完成，进入下一回合
    end_round(game, room_id)

def check_player_elimination(game, player_id):
    """检查玩家是否被淘汰"""
    if not game.has_remaining_pieces(player_id):
        player = game.get_player(player_id)
        player['eliminated'] = True
        
        # 从行动顺序中移除
        if player_id in game.player_order:
            game.player_order.remove(player_id)

def end_round(game, room_id):
    """结束回合"""
    # 回收公共区域剩余棋子
    for piece in game.public_area:
        player = game.get_player(piece['player_id'])
        if player and not player['eliminated']:
            # 找一个空位放置
            for row in range(3):
                for col in range(6):
                    if player['board'][row][col] is None:
                        player['board'][row][col] = piece['number']
                        break
                else:
                    continue
                break
    
    game.public_area = []
    
    # 检查游戏是否结束
    if game.check_game_end():
        handle_game_end(game, room_id)
        return
    
    # 开始新回合
    game.round_number += 1
    game.action_count = 0
    game.current_player_index = 0
    game.phase = 'action'
    
    # 跳过已淘汰的玩家
    while game.get_current_player()['eliminated']:
        game.current_player_index = (game.current_player_index + 1) % len(game.player_order)
    
    emit('round_start', {
        'round_number': game.round_number,
        'game_state': game.to_dict()
    }, room=room_id)

def handle_game_end(game, room_id):
    """处理游戏结束"""
    # 更新玩家统计
    for player in game.players:
        if player['id'] == game.winner:
            execute_db('UPDATE users SET total_games = total_games + 1, wins = wins + 1 WHERE id = ?', (player['id'],))
        else:
            execute_db('UPDATE users SET total_games = total_games + 1 WHERE id = ?', (player['id'],))
    
    # 更新房间状态
    execute_db('UPDATE rooms SET status = ? WHERE id = ?', ('ended', room_id))
    
    winner = game.get_player(game.winner)
    emit('game_ended', {
        'winner_id': game.winner,
        'winner_nickname': winner['nickname'] if winner else 'Unknown',
        'game_state': game.to_dict()
    }, room=room_id)

@socketio.on('extra_action_response')
def handle_extra_action_response(data):
    """处理额外行动响应"""
    room_id = data.get('room_id')
    action_type = data.get('type')
    action_data = data.get('data', {})
    user_id = socket_users.get(request.sid)
    
    game = game_states.get(room_id)
    if not game:
        return
    
    # 处理额外行动
    if action_type == 'challenge':
        # 单挑
        target_player_id = action_data.get('target_player_id')
        target_row = action_data.get('row')
        target_col = action_data.get('col')
        
        target_player = game.get_player(target_player_id)
        if target_player and not target_player['eliminated'] and target_player['board'][target_row][target_col] is not None:
            number = target_player['board'][target_row][target_col]
            target_player['board'][target_row][target_col] = None
            game.public_area.append({
                'player_id': target_player_id,
                'number': number,
                'action_order': game.action_count
            })
            
            # 触发对决
            process_duel(game, room_id)
    
    elif action_type == 'recover':
        # 回收
        piece_index = action_data.get('piece_index')
        target_row = action_data.get('target_row')
        target_col = action_data.get('target_col')
        
        player = game.get_player(user_id)
        if player and piece_index < len(game.public_area):
            piece = game.public_area[piece_index]
            if piece['player_id'] == user_id and player['board'][target_row][target_col] is None:
                player['board'][target_row][target_col] = piece['number']
                game.public_area.pop(piece_index)
                end_round(game, room_id)
    
    elif action_type == 'skip':
        # 放弃额外行动
        end_round(game, room_id)

# ==================== 静态文件服务 ====================

@app.route('/')
def index():
    """主页"""
    return send_from_directory('../frontend', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    """静态文件服务"""
    # 检查是否是房间ID（4位字母数字）
    if len(path) == 4 and all(c.isalnum() for c in path):
        return send_from_directory('../frontend', 'index.html')
    
    return send_from_directory('../frontend', path)

# ==================== 初始化 ====================

with app.app_context():
    init_db()
    print("数据库初始化完成")

if __name__ == '__main__':
    print("启动魔丸小游戏服务器...")
    print("访问地址: http://localhost:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
