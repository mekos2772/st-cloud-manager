# ST Cloud Manager

SillyTavern 自动开实例管理器。

激活 Key → 自动创建酒馆容器 → 独立域名 → API 自动下发 → 返回账号密码。一个用户一个独立容器，数据完全隔离。

## 架构

```
用户浏览器
  ↓ http://abc123.st.example.com
Traefik (端口 80)
  ↓ 路由到容器
SillyTavern 实例 (st-abc123:8000)
  ↓ API 请求
Manager API Proxy (:5000)
  ↓ 换真实 Key
上游 API (DeepSeek / OpenAI / 中转)
```

## 功能

- 激活 Key 系统：生成、分发、验证、禁用、删除
- 每个用户独立 SillyTavern 容器 + 独立域名 + 独立数据目录
- 管理后台：总览、实例管理、Key 管理、API 配置、Cloudflare、安全审计、诊断、备份
- API 配置中心：全局设置 API URL / 模型 / Key，一键测试，批量下发
- API Proxy：用户不可见真实 Key，服务端代理替换
- Cloudflare DNS 自动化：创建实例自动建 DNS 记录，删除实例自动清理
- Docker 防穿透：容器安全加固 + 安全审计
- 创建失败自动回滚，删除自动归档
- 流式传输透传（SSE）

## 快速开始

### 本地测试（Windows / macOS）

```bash
# 1. 确保 Docker Desktop 已启动
docker network create st_proxy

# 2. 初始化
python scripts/init_db.py
pip install -r manager/requirements.txt

# 3. 启动
python -m uvicorn manager.app:app --host 0.0.0.0 --port 5000

# 4. 打开 http://127.0.0.1:5000/activate
```

默认使用 `127-0-0-1.sslip.io` 本地域名，无需任何 DNS 配置。

### 生产部署（Linux）

```bash
# 一键安装
bash scripts/install.sh
```

或手动：

```bash
# 1. 安装 Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# 2. 创建网络
docker network create st_proxy

# 3. 配置
cp .env.example .env
# 编辑 .env：设置域名、API Key、Admin Key

# 4. 启动
pip install -r manager/requirements.txt
python scripts/init_db.py
python -m uvicorn manager.app:app --host 0.0.0.0 --port 5000
```

## 配置

### .env 关键配置

```env
# 域名模式: local（本地测试）或 cloudflare（生产 DNS）
ST_DOMAIN_MODE=local
ST_DOMAIN_SUFFIX=127-0-0-1.sslip.io

# API 配置
ST_API_BASE_URL=http://api.lordfa.top
ST_API_MODEL=deepseek-v4-pro
ST_MASTER_API_KEY=sk-your-real-key  # 真实 Key 不会写入用户实例

# 管理后台 Key（必须改为强随机）
ST_ADMIN_API_KEY=sk-admin-xxxxxxxx

# Docker 安全
ST_CONTAINER_READ_ONLY=true  # 只读根文件系统
```

### API 配置中心

启动后在后台 **API 配置** 页面设置：
- API Base URL + Model + Key
- 点 **测试连接** / **测试流式** 验证
- 点 **保存**

新实例自动使用此配置。

### Cloudflare 域名

后台 **域名管理** 切换到 Cloudflare 模式，填写：
- API Token（权限：Zone Read + DNS Edit）
- Zone ID + Base Domain + 记录目标（服务器 IP）
- 验证 Token → 验证 Zone → 保存

## 管理后台

```
http://localhost:5000/admin
```

首次打开输入 Admin Key。页面结构：

| 页面 | 功能 |
|------|------|
| 总览 | 实例数、Key 数、API/流式状态 |
| 实例管理 | 列表、启动/停止/重启、续期、删除、日志、诊断、下发 API |
| Key 管理 | 生成、禁用、启用、删除 |
| API 配置 | 全局 API 设置、测试连接、测试流式、批量下发 |
| 域名管理 | 本地/Cloudflare 切换、Token 验证、Zone 验证 |
| 安全 | Docker 防穿透审计 |
| 诊断 | 系统健康检查、实例 inspect、日志 |
| 备份 | 创建、列表、删除、恢复 |

## 命令行工具

```bash
python scripts/init_db.py                    # 初始化数据库
python scripts/create_key.py --count 10 --days 30  # 生成 Key
python scripts/create_instance.py ST-XXXX-XXXX     # 激活实例
python scripts/list_instances.py                   # 查看实例
python scripts/stop_instance.py abc123             # 停止
python scripts/start_instance.py abc123            # 启动
python scripts/renew_instance.py abc123 --days 30  # 续期
python scripts/delete_instance.py abc123           # 删除（归档）
python scripts/export_api_template.py abc123       # 导出 API 模板
python scripts/apply_api_config.py abc123          # 重新下发 API 配置
bash scripts/backup.sh                             # 备份
```

## API

```
http://localhost:5000/docs  → Swagger 文档
```

### 用户

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/activate` | 激活 Key，创建实例 |

### 管理员（需要 `x-api-key` Header）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/summary` | 总览统计 |
| GET/POST | `/api/admin/instances` | 实例列表 |
| POST | `/api/admin/instances/{id}/start\|stop\|restart` | 实例操作 |
| POST | `/api/admin/instances/{id}/renew` | 续期 |
| DELETE | `/api/admin/instances/{id}` | 删除 |
| GET | `/api/admin/instances/{id}/logs\|inspect` | 日志/诊断 |
| POST | `/api/admin/instances/{id}/check` | 健康检查 |
| POST | `/api/admin/instances/{id}/apply-api-config` | 下发 API |
| POST | `/api/admin/instances/apply-api-config-all` | 批量下发 |
| GET/POST | `/api/admin/keys` | Key 管理 |
| GET/POST | `/api/admin/settings/api` | API 配置 |
| POST | `/api/admin/settings/api/test\|test-stream` | API 测试 |
| GET/POST | `/api/admin/cloudflare/settings` | CF 配置 |
| POST | `/api/admin/cloudflare/test-token` | CF Token 验证 |
| GET | `/api/admin/cloudflare/zones\|verify-zone` | Zone 管理 |
| GET | `/api/admin/security/docker` | 安全审计 |
| GET | `/api/admin/health/*` | 健康检查 |
| POST/GET/DELETE | `/api/admin/backup/*` | 备份管理 |

## 安全

| 项目 | 措施 |
|------|------|
| API Key 隐藏 | Proxy 层注入真实 Key，用户实例只有代理 Key |
| 容器隔离 | `--security-opt no-new-privileges` `--cap-drop ALL` `--read-only` |
| 资源限制 | `--memory 768m --cpus 1.0 --pids-limit 256` |
| 端口 | 不映射宿主机端口，只通过 Traefik 访问 |
| 敏感目录 | 不挂载 docker.sock/宿主机目录 |
| BasicAuth | 每个实例随机用户名+12 位密码 |
| Server Plugins | 默认关闭 |
| Admin Key | 强随机，后台全鉴权 |
| Key 脱敏 | 前端只显示 `sk-xxx****yyy` |
| 日志安全 | 不打印 Key/Password |
| 删除归档 | 实例删除后数据移至 archive/ |
| 失败回滚 | 创建失败自动清理，不浪费 Key |

## 目录结构

```
st-cloud-manager/
├── manager/                     # FastAPI 后端
│   ├── app.py                   # 路由（50+ 端点）
│   ├── config.py                # 环境变量
│   ├── db.py                    # SQLite + 迁移
│   ├── instance_service.py      # 实例生命周期
│   ├── docker_service.py        # Docker + 安全加固
│   ├── template_service.py      # 模板渲染
│   ├── key_service.py           # Key 管理
│   ├── proxy_service.py         # API Proxy
│   ├── api_proxy.py             # 代理 + 限流
│   ├── cloudflare_service.py    # CF DNS 自动化
│   ├── api_test_service.py      # API 连接测试
│   ├── traefik_config_service.py # 路由配置
│   ├── settings_service.py      # 系统设置
│   ├── scheduler.py             # 到期检查
│   └── requirements.txt
├── static/
│   ├── admin.html               # 管理后台（单文件）
│   └── activate.html            # 用户激活页
├── scripts/                     # CLI 工具
│   ├── install.sh               # Linux 一键安装
│   ├── install.ps1              # Windows 一键安装
│   └── *.py                     # 各类管理脚本
├── templates/sillytavern/       # ST 配置模板
│   ├── config/config.yaml.tpl
│   └── data/default-user/       # API 配置模板
├── docker-compose.yml           # 本地/HTTP 模式
├── docker-compose.prod.yml      # 生产 HTTPS 覆盖
├── Dockerfile.manager           # Manager 镜像
├── Dockerfile.traefik           # Traefik 镜像
├── .env.example
├── users/                       # 用户实例数据
├── archive/                     # 删除归档
└── backups/                     # 备份文件
```

## 一键安装（Linux）

```bash
curl -fsSL https://raw.githubusercontent.com/USER/st-cloud-manager/main/scripts/install.sh | bash
```

安装脚本交互式引导：环境检测 → 本地/生产模式 → API 配置 → 数据库 → 启动服务 → 健康检查。

### 守护进程（systemd）

```bash
sudo cp scripts/st-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now st-manager
sudo systemctl status st-manager
```

## License

MIT
