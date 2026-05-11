# 当前状态与困境

## 已解决的

| 问题 | 状态 |
|------|------|
| 无 Docker 运行 ST 实例 | ✅ 进程模式 |
| 路径后缀路由 `/st-xxx/` | ✅ 架构验证 |
| proxy.js 响应体路径改写 | ✅ HTML/CSS/JS全覆盖 |
| Gzip 解压后改写 | ✅ |
| ST 1.18 根级资源路径 | ✅ /style.css, /script.js |
| Nginx 301 无尾斜杠 | ✅ |
| 端口竞争（14ms并发） | ✅ 锁内立即写文件 |
| 进程崩溃自动重启 | ✅ 60s调度器检测 |
| 多实例隔离（源码头复制） | ✅ 不再软链接 |
| API/WebSocket 改写 | ✅ proxy.js含socket.io |
| CDN缓存401响应 | ✅ Cache-Control: private |
| Content-Encoding 双重压缩 | ✅ API透传 |
| nginx Authorization 透传 | ✅ 显式传递 |

## 本地验证通过

```
http://127.0.0.1:9101/st-igbkyxrm/
├── 主页: 200
├── style.css: 200
├── css/animations.css: 200  ← CSS @import 链
├── script.js: 200
├── base标签: /st-igbkyxrm/ ✅
└── 残留绝对路径: 0 处
```

## 线上仍然存在的问题

### 1. 静态资源不加载（浏览器白屏/裸文字）

**现象**：HTML 能出来，CSS/JS 全部失败
**curl 测试**：带 -u 全部 200，不带 -u 全部 401
**怀疑原因**：
- 浏览器不发送 BasicAuth 给子资源请求
- 或 Cloudflare 缓存了无 auth 的 401 响应
- 或实例的 proxy.js / ST 进程不稳定，间歇崩溃

### 2. 进程稳定性（<defunct> 僵尸进程）

**现象**：ST 和 proxy 进程变成 defunct
**怀疑原因**：gVisor 或容器运行环境限制
**已做**：自动重启检测（60s 一次）

### 3. 代码更新未生效

**现象**：线上行为与本地不一致
**怀疑原因**：服务器未拉取最新代码、旧实例未重建
**当前最新**：`9423656`

## 需要确认

```bash
# 1. 线上代码版本
cd st-cloud-manager && git log --oneline -1

# 2. 实例是否最新代码创建（看 proxy.js 是否新版）
# 新版特征：proxy.js 用 CLI 参数而非环境变量

# 3. ST 和 proxy 进程是否运行中
ps aux | grep -E 'node|st-'

# 4. 逐个测
curl -s -o /dev/null -w '%{http_code}' -u 'user:pass' 'https://域名/st-xxx/'
curl -s -o /dev/null -w '%{http_code}' -u 'user:pass' 'https://域名/st-xxx/style.css'
curl -s -o /dev/null -w '%{http_code}' -u 'user:pass' 'https://域名/st-xxx/css/animations.css'
```

## 下一步

1. 确认服务器 git pull + 重建实例
2. 在服务器上本地 curl 测试（绕过 Cloudflare，直连 127.0.0.1）
3. 加 Cloudflare Page Rule：对 `/st-*` 路径设置 Cache Level: Bypass
4. 在 proxy.js 或 ST 里给静态资源设置 `Cache-Control: private, no-cache`
