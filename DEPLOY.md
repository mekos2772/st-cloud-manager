# ST Cloud Manager 完整部署流程

## 环境

- Ubuntu/Debian Linux（或 WSL2）
- Python 3.10+ / Node.js 18+ / nginx / Git

---

## 一、基础环境

```bash
sudo apt update && sudo apt install -y python3 python3-pip nginx git curl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

---

## 二、拉取项目

```bash
git clone https://github.com/mekos2772/st-cloud-manager
cd st-cloud-manager
pip install -r manager/requirements.txt
```

---

## 三、拉取 SillyTavern（所有实例共享）

```bash
git clone --depth 1 https://github.com/SillyTavern/SillyTavern.git st-release
cd st-release && npm install --omit=dev && cd ..
```

---

## 四、初始化

```bash
mkdir -p users archive backups nginx-sites
python scripts/init_db.py

python -c "
from manager.db import init_db
from manager.settings_service import set_settings
init_db()
set_settings({
    'runtime_mode': 'process',        # 无 Docker
    'routing_mode': 'path',           # 后缀路由
    'base_domain': '你的域名.com',     # 改这里
    'trial_enabled': 'true',
    'trial_max_instances': '5',
    'trial_idle_timeout': '600',
    'trial_max_memory_pct': '85',
    'trial_queue_enabled': 'true',
})
"
```

---

## 五、配置 .env

```bash
cat > .env << 'EOF'
ST_ADMIN_API_KEY=你的管理密码
ST_MASTER_API_KEY=你的上游API密钥
ST_RUNTIME_MODE=process
ST_RELEASE_DIR=./st-release
ST_BASE_DOMAIN=你的域名.com
ST_PORT_RANGE_START=9000
ST_PORT_RANGE_END=9999
ST_API_BASE_URL=https://api.lordfa.top
ST_API_MODEL=deepseek-v4-pro
ST_NGINX_SITES_DIR=./nginx-sites
EOF
```

---

## 六、配置 Nginx

```bash
# 写 nginx 主配置
sudo tee /etc/nginx/sites-available/st-manager > /dev/null << 'NGINX'
server {
    listen 80;
    server_name 你的域名.com;

    # Manager API + 管理后台
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
    }

    # ST 实例（按路径后缀分发）
    include /home/你的用户名/st-cloud-manager/nginx-sites/*.conf;
}
NGINX

sudo ln -sf /etc/nginx/sites-available/st-manager /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

---

## 七、启动

```bash
# 前台运行（调试用）
python -m uvicorn manager.app:app --host 127.0.0.1 --port 5000

# 后台运行（生产用）
nohup python -m uvicorn manager.app:app --host 127.0.0.1 --port 5000 > logs/manager.log 2>&1 &

# 或使用 systemd（推荐）
sudo tee /etc/systemd/system/st-manager.service > /dev/null << 'UNIT'
[Unit]
Description=ST Cloud Manager
After=network.target

[Service]
Type=simple
User=你的用户名
WorkingDirectory=/home/你的用户名/st-cloud-manager
ExecStart=python3 -m uvicorn manager.app:app --host 127.0.0.1 --port 5000
Restart=always

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now st-manager
```

---

## 八、生成 Key 并测试

```bash
# 生成激活 Key
python scripts/create_key.py --count 3 --days 30

# 或直接开体验模式，浏览器打开：
# https://你的域名.com/activate
```

---

## 九、HTTPS（可选）

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名.com
```

---

## 维护命令

```bash
systemctl status st-manager    # 查看状态
journalctl -u st-manager -f    # 查看日志

cd ~/st-cloud-manager
make update-st                  # 更新 ST 源码
python -m uvicorn ... --reload # 热重载开发
```
