# ST Cloud Manager — 无 Docker 部署教程

## 环境要求

- Python 3.10+
- Node.js 18+
- nginx 1.20+
- Git

## 一、安装

```bash
# 1. 克隆项目
git clone https://github.com/mekos2772/st-cloud-manager
cd st-cloud-manager

# 2. 安装 Python 依赖
pip install -r manager/requirements.txt

# 3. 安装 nginx（Windows）
winget install nginxinc.nginx

# 3. 安装 nginx（Linux）
sudo apt install nginx
```

## 二、初始化

```bash
# 1. 初始化数据库和目录
python scripts/init_db.py
mkdir -p users archive backups nginx-sites

# 2. 拉取 SillyTavern 源码（所有实例共享这一份）
git clone --depth 1 https://github.com/SillyTavern/SillyTavern.git st-release
cd st-release && npm install --omit=dev && cd ..

# 3. 配置运行模式和后缀路由
python -c "
from manager.db import init_db
from manager.settings_service import set_settings
init_db()
set_settings({
    'runtime_mode': 'process',       # 无 Docker
    'routing_mode': 'path',          # 后缀路由
    'base_domain': 'localhost',      # 基础域名
    'trial_enabled': 'true',         # 开启体验模式
    'domain_mode': 'local',
})
print('配置完成')
"
```

## 三、配置 nginx

```bash
# 1. 找到 nginx 安装目录
#    Windows: C:\Users\你的用户名\AppData\Local\Microsoft\WinGet\Packages\nnginxinc.nginx_xxx\nginx-1.xx\
#    Linux:   /etc/nginx/

# 2. 编辑 nginx.conf，替换 http 块内容为：
```

```nginx
worker_processes  1;
events { worker_connections  1024; }

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout  65;

    # 加载所有实例的 nginx 配置（由系统自动生成）
    include /你的路径/st-cloud-manager/nginx-sites/*.conf;
}
```

```bash
# 3. 测试配置
nginx -t

# 4. 启动 nginx
nginx
```

## 四、创建 .env 文件

```bash
# 在项目根目录创建 .env
cat > .env << 'EOF'
ST_ADMIN_API_KEY=你的管理密码
ST_MASTER_API_KEY=你的上游API密钥
ST_RUNTIME_MODE=process
ST_RELEASE_DIR=./st-release
ST_DOMAIN_SUFFIX=127-0-0-1.sslip.io
ST_BASE_DOMAIN=localhost
ST_API_BASE_URL=https://你的API地址
ST_API_MODEL=你的模型名
ST_NGINX_SITES_DIR=./nginx-sites
EOF
```

## 五、启动

```bash
# 启动 Manager
python -m uvicorn manager.app:app --host 127.0.0.1 --port 5000
```

Manager 运行在 `http://localhost:5000`。

## 六、创建实例

### 方式一：体验模式（无需 Key）

打开 `http://localhost:5000/activate`，点击"一键创建"。

实例地址格式：`http://localhost/st-{随机后缀}`

### 方式二：激活 Key

```bash
# 生成 Key
python scripts/create_key.py --count 1 --days 30

# 用 Key 在 activate 页面激活
```

## 七、管理后台

打开 `http://localhost:5000/admin`，输入 API Key。

## 八、一键命令

```bash
make start              # 启动 Manager
make stop-nodocker      # 停止所有实例
make update-st          # 更新 ST 源码
make nginx-reload       # 重载 nginx 配置
```

## 架构说明

```
用户浏览器
    │
    ▼
http://localhost/st-abc123
    │
    ▼
nginx (80端口，匹配 location /st-abc123/，剥离前缀)
    │
    ▼
127.0.0.1:9000 (node server.js，独立进程)
    │
    ├── config/     ← 实例独享
    ├── data/       ← 实例独享（16MB，含角色卡）
    ├── server.js   ← 复制自 st-release/
    ├── src/        → 软链接 → st-release/src/
    ├── public/     → 软链接 → st-release/public/
    └── node_modules/ → 软链接 → st-release/node_modules/
```

每个实例独立端口范围 9000-9999，nginx 按路径后缀自动分发。
