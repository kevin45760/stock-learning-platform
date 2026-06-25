import eventlet
eventlet.monkey_patch()  # 🚀 必須放在最頂端！強迫將所有阻塞操作轉換為非同步協程，解決 lock 鎖死問題！

import os
import random
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import requests

# 1. 取得目前檔案所在的絕對路徑資料夾
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)

app.config['SECRET_KEY'] = 'stock_secret_key_9999'

# 2. 資料庫與上傳夾設定（加上 check_same_thread=False 徹底防禦多執行緒衝突）
DB_PATH = os.path.join(BASE_DIR, 'database.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{DB_PATH}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "connect_args": {"timeout": 30, "check_same_thread": False}
}
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')

db = SQLAlchemy(app)
# 指定 async_mode='eventlet' 與頂部猴子補丁完美配合
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
login_manager = LoginManager(app)
login_manager.login_view = 'login_page'

# 確保 User 資料庫模型
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False) 
    avatar = db.Column(db.String(200), default='default_avatar.png')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 自動建立資料庫與表格
with app.app_context():
    try:
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            os.makedirs(app.config['UPLOAD_FOLDER'])
        db.create_all()
        print("==== [Render 提示] SQLite 資料庫與資料表初始化成功！ ====")
    except Exception as e:
        print(f"==== [Render 警告] 初始化資料庫時發生異常: {str(e)} ====") 

# 模擬台灣股市即時數據
STOCKS = {
    "2330 台積電": {"price": 950.0, "change": 0.0, "desc": "晶片巨頭，台灣的護國神山。"},
    "2317 鴻海": {"price": 200.0, "change": 0.0, "desc": "電子代工大廠，幫忙組裝 iPhone。"},
    "2412 中華電": {"price": 120.0, "change": 0.0, "desc": "電信龍頭，大家上網付費給它的防守型股票。"},
    "2603 長榮": {"price": 180.0, "change": 0.0, "desc": "航運大戶，用大貨船幫全世界載運貨物。"}
}

# ----------------- 網頁路由 -----------------
@app.route('/')
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('stock_login.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)

# 註冊 API
@app.route('/api/register', methods=['POST'])
def api_register():
    try:
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            return jsonify({'success': False, 'msg': '帳號與密碼為必填項目！'})

        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            return jsonify({'success': False, 'msg': '此帳號已被註冊，請換一個名字！'})

        avatar_filename = 'default_avatar.png'
        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and file.filename != '':
                try:
                    if not os.path.exists(app.config['UPLOAD_FOLDER']):
                        os.makedirs(app.config['UPLOAD_FOLDER'])
                    
                    base_secure = secure_filename(file.filename)
                    avatar_filename = f"{username}_{random.randint(1000, 9999)}_{base_secure}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], avatar_filename))
                except Exception as img_err:
                    print(f" ==== [圖片儲存失敗，自動切換預設頭像]: {str(img_err)} ====")
                    avatar_filename = 'default_avatar.png'

        hashed_pwd = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password_hash=hashed_pwd, avatar=avatar_filename)
        
        db.session.add(new_user)
        db.session.commit()

        return jsonify({'success': True, 'msg': '註冊成功！已為您創立魔法頭像，請切換至登入！'})

    except Exception as total_err:
        print(f" ==== [註冊 API 內部核心崩潰]: {str(total_err)} ====")
        return jsonify({'success': False, 'msg': f'後端系統寫入失敗：{str(total_err)}'})

# 登入 API
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

# ----------------- WebSocket 即時聊天與股市廣播 -----------------
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

def update_stock_market_loop():
    # 1. 建立伺服器記憶體內的初始基本快取，防止剛開機網路極端卡頓
    real_market_cache = {
        "2330": {"name": "2330 台積電", "price": 950.0, "change": 0.00, "desc": "晶片巨頭，台灣的護國神山。"},
        "2317": {"name": "2317 鴻海", "price": 200.0, "change": 0.00, "desc": "電子代工大廠，幫忙組裝 iPhone。"},
        "2412": {"name": "2412 中華電", "price": 120.0, "change": 0.00, "desc": "電信龍頭，大家上網付費給它的防守型股票。"},
        "2603": {"name": "2603 長榮", "price": 180.0, "change": 0.00, "desc": "航運大戶，用大貨船幫全世界載運貨物。"}
    }

    # 記住我們要過濾的四檔核心股票
    target_codes = ["2330", "2317", "2412", "2603"]

    while True:
        socketio.sleep(5)  # 前端主面板每 5 秒實時刷新廣播一次
        stock_list = []
        
        try:
            # 🚀 呼叫政府開放平臺：證交所每日收盤行情公開 API (不限 IP、永不過期)
            url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            
            # 設定超時，避免政府伺服器塞車時卡死
            response = requests.get(url, headers=headers, timeout=4)
            
            if response.status_code == 200:
                raw_data = response.json()
                
                # 掃描政府回傳的所有股票清單
                for item in raw_data:
                    code = item.get('Code', '').strip()
                    
                    if code in target_codes:
                        # 擷取當前最新收盤價
                        price_str = item.get('ClosingPrice', '0').replace(',', '')
                        # 擷取漲跌幅百分比（政府欄位通常直接提供或需要透過漲跌價計算，這裡防禦性轉換）
                        change_str = item.get('Change', '0').replace(',', '').replace('X', '')
                        
                        try:
                            current_price = float(price_str) if price_str else real_market_cache[code]["price"]
                            change_val = float(change_str) if change_str else 0.0
                            
                            # 計算大約的漲跌幅：漲跌價 / (收盤價 - 漲跌價) * 100
                            base_price = current_price - change_val
                            change_percent = (change_val / base_price * 100) if base_price > 0 else 0.0
                            
                            # 更新至快取記憶體中
                            real_market_cache[code]["price"] = current_price
                            real_market_cache[code]["change"] = round(change_percent, 2)
                        except:
                            pass # 轉換失敗時維持上一秒的快取數值
            
        except Exception as e:
            print(f"==== [政府 API 連線異常，自動切換本地快取運作]: {str(e)} ====")

        # 🚀 2. 炫麗動態優化：不論有沒有連上網路，都在真實收盤價地基上加上 ±0.05% 的逼真跳動
        import random
        for code in target_codes:
            cache = real_market_cache[code]
            
            # 隨機產生微幅跳動（模擬真實交易日盤中的微幅震盪）
            market_flicker = random.uniform(-0.0005, 0.0005)
            flicker_price = round(cache["price"] * (1 + market_flicker), 1)
            flicker_change = round(cache["change"] + (market_flicker * 100), 2)
            
            stock_list.append({
                "name": cache["name"],
                "price": flicker_price,
                "change": flicker_change,
                "desc": cache["desc"]
            })

        # 將數據推播給所有人
        socketio.emit('market_update', stock_list)
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.start_background_task(update_stock_market_loop)
    socketio.run(app, debug=True)