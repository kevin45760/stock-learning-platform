import os
import random
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# 🚀 1. 取得目前檔案所在的絕對路徑資料夾
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)

app.config['SECRET_KEY'] = 'stock_secret_key_9999'

# 🚀 2. 將資料庫與圖片上傳夾全部鎖定為絕對路徑，並防止多執行緒鎖死
DB_PATH = os.path.join(BASE_DIR, 'database.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{DB_PATH}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "connect_args": {"timeout": 15}
}
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'uploads')

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")
login_manager = LoginManager(app)
login_manager.login_view = 'login_page'

# 1. 確保 User 資料庫模型欄位完全正確
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False) 
    avatar = db.Column(db.String(200), default='default_avatar.png')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 🚀 3. 在 Flask 啟動的第一時間，強迫在絕對路徑自動建立資料夾與資料庫表格
with app.app_context():
    try:
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            os.makedirs(app.config['UPLOAD_FOLDER'])
        db.create_all()
        print("==== [Render 提示] SQLite 資料庫與資料表初始化成功！ ====")
    except Exception as e:
        print(f"==== [Render 警告] 初始化資料庫時發生異常: {str(e)} ====") 

# 模擬台灣股市即時數據（每2秒會隨機跳動）
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

# 註冊 API（含自訂頭像上傳）
@app.route('/api/register', methods=['POST'])
def api_register():
    try:
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            return jsonify({'success': False, 'msg': '帳號與密碼為必填項目！'})

        # 檢查帳號是否重複
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            return jsonify({'success': False, 'msg': '此帳號已被註冊，請換一個名字！'})

        # 處理頭像上傳（加入極限安全防護）
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

        # 密碼加密並寫入資料庫（指定與新版 Werkzeug 完全相容的雜湊演算法）
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

# 背景任務：每 2 秒自動更新股市走勢，並發送給網頁上所有人
def update_stock_market_loop():
    while True:
        socketio.sleep(2)
        stock_list = []
        for name, info in STOCKS.items():
            change_percent = random.uniform(-0.02, 0.02) # 隨機跳動±2%
            info["price"] = round(info["price"] * (1 + change_percent), 1)
            info["change"] = round(change_percent * 100, 2)
            stock_list.append({
                "name": name, "price": info["price"], "change": info["change"], "desc": info["desc"]
            })
        socketio.emit('market_update', stock_list)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.start_background_task(update_stock_market_loop)
    socketio.run(app, debug=True)