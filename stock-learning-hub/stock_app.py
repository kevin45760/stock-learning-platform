import eventlet
eventlet.monkey_patch()  # 🚀 必須放在最頂端！強迫將所有阻塞操作轉換為非同步協程，解決 lock 鎖死問題！

import os
import random
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# 1. 取得目前檔案所在的絕對路徑資料夾
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)

app.config['SECRET_KEY'] = 'stock_secret_key_9999'

# 2. 資料庫與上傳夾設定
DB_PATH = os.path.join(BASE_DIR, 'database.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{DB_PATH}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "connect_args": {"timeout": 15, "check_same_thread": False}
}
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")
login_manager = LoginManager(app)
login_manager.login_view = 'login_page'

# 3. User 資料庫模型
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    avatar = db.Column(db.String(100), default='default_avatar.png')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 🚀 靜態資料庫備用快取：當政府 API 連線有問題時的基礎地基
STATIC_MARKET_DATA = {
    "2330": {"name": "2330 台積電", "price": 955.0, "change": 0.53, "desc": "晶片巨頭，台灣的護國神山。"},
    "2317": {"name": "2317 鴻海", "price": 201.5, "change": -0.49, "desc": "電子代工大廠，幫忙組裝 iPhone。"},
    "2412": {"name": "2412 中華電", "price": 120.0, "change": 0.00, "desc": "電信龍頭，大家上網付費給它的防守型股票。"},
    "2603": {"name": "2603 長榮", "price": 182.5, "change": 1.39, "desc": "航運大戶，用大貨船幫全世界載運貨物。"}
}

# 4. 路由設定
@app.route('/')
def home():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard_page'))
    return redirect(url_for('login_page'))

@app.route('/login')
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard_page'))
    return render_template('stock_login.html')

# 🚀 靜態改版核心：當使用者請求大廳時，才在後端抓一次資料，打包傳給網頁
@app.route('/dashboard')
@login_required
def dashboard_page():
    stock_list = []
    target_codes = ["2330", "2317", "2412", "2603"]
    
    try:
        # 只在載入網頁時請求一次政府 API，不使用無窮迴圈
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=2)
        
        if response.status_code == 200:
            raw_data = response.json()
            # 快速建立一個索引字典優化過濾速度
            raw_dict = {item.get('Code', '').strip(): item for item in raw_data if item.get('Code')}
            
            for code in target_codes:
                if code in raw_dict:
                    item = raw_dict[code]
                    price_str = item.get('ClosingPrice', '0').replace(',', '')
                    change_str = item.get('Change', '0').replace(',', '').replace('X', '')
                    
                    current_price = float(price_str) if price_str and price_str != '0' else STATIC_MARKET_DATA[code]["price"]
                    change_val = float(change_str) if change_str else 0.0
                    
                    base_price = current_price - change_val
                    change_percent = (change_val / base_price * 100) if base_price > 0 else 0.0
                    
                    stock_list.append({
                        "name": STATIC_MARKET_DATA[code]["name"],
                        "price": round(current_price, 1),
                        "change": round(change_percent, 2),
                        "desc": STATIC_MARKET_DATA[code]["desc"]
                    })
                else:
                    stock_list.append(STATIC_MARKET_DATA[code])
        else:
            raise Exception("API 回應代碼異常")
            
    except Exception as e:
        print(f"==== [靜態獲取失敗，改由安全基本地基渲染]: {str(e)} ====")
        # 發生錯誤（如超時或斷網）時，直接拿基本快取資料
        stock_list = [STATIC_MARKET_DATA[code] for code in target_codes]

    # 將完全整理好的靜態 stock_list 直接丟給 Jinja2 渲染
    return render_template('dashboard.html', user=current_user, stocks=stock_list)

@app.route('/api/register', methods=['POST'])
def api_register():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    if not username or not password:
        return jsonify({"success": False, "msg": "帳號或密碼不能為空！"})

    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        return jsonify({"success": False, "msg": "此帳號已被註冊，請換一個名字！"})

    avatar_file = request.files.get('avatar')
    avatar_name = 'default_avatar.png'

    if avatar_file and avatar_file.filename != '':
        filename = secure_filename(avatar_file.filename)
        ext = os.path.splitext(filename)[1]
        avatar_name = f"user_{username}_{int(random.random()*10000)}{ext}"
        avatar_file.save(os.path.join(app.config['UPLOAD_FOLDER'], avatar_name))

    hashed_password = generate_password_hash(password)
    new_user = User(username=username, password_hash=hashed_password, avatar=avatar_name)
    db.session.add(new_user)
    db.session.commit()

    return jsonify({"success": True, "msg": f"恭喜 【{username}】 成功入學魔法學院！請切換至登入模式。"})

@app.route('/api/login', methods=['POST'])
def api_login():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    user = User.query.filter_by(username=username).first()
    if user and check_password_hash(user.password_hash, password):
        login_user(user)
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "帳號或密碼錯誤，請再試一次！"})

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login_page'))

# ----------------- WebSocket 僅保留乾淨的聊天功能 -----------------
@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    emit('chat_msg', {
        'username': '📢 系統精靈',
        'avatar': 'default_avatar.png',
        'msg': f'歡迎大俠 【{current_user.username}】 進入聊天室「{room}」切磋交流！'
    }, room=room)

@socketio.on('send_msg')
def handle_message(data):
    room = data['room']
    emit('chat_msg', {
        'username': current_user.username,
        'avatar': current_user.avatar,
        'msg': data['msg']
    }, room=room)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, debug=True)