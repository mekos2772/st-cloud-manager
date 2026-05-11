# 第 4 层测试：WebSocket / API / 写入 / 多实例隔离

## 前置条件

```bash
cd st-cloud-manager && git pull origin master
```

## 一、启动测试环境

```bash
# 终端1: ST实例1
cd st-release && node server.js --listen --port 9001

# 终端2: ST实例2
cp -r st-release st-release2 && cd st-release2 && node server.js --listen --port 9002

# 终端3: proxy实例1
node templates/proxy/proxy.js test01 9101 9001

# 终端4: proxy实例2
node templates/proxy/proxy.js test02 9102 9002

# 终端5: Nginx (先写配置到 nginx安装目录/conf/)
nginx -t && nginx -s reload
```

Nginx 配置：
```nginx
server {
    listen 8080;
    client_max_body_size 100m;

    location = /st-test01 { return 301 /st-test01/; }
    location /st-test01/ {
        proxy_pass http://127.0.0.1:9101;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto http;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
        proxy_buffering off;
    }

    location = /st-test02 { return 301 /st-test02/; }
    location /st-test02/ {
        proxy_pass http://127.0.0.1:9102;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto http;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
        proxy_buffering off;
    }
}
```

## 二、测试清单

### 2.1 链路基础检查

```bash
# 首页
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/st-test01/
# 期望: 200

# 无斜杠跳转
curl -s -I http://127.0.0.1:8080/st-test01 | grep Location
# 期望: Location: /st-test01/

# 静态资源
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/st-test01/style.css
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/st-test01/script.js
# 期望: 200, 200

# HTML中无残留绝对路径
curl -s http://127.0.0.1:8080/st-test01/ | grep -oE '(src|href)="/[^/][^"]*"' | grep -v /st-test01/
# 期望: 空输出（无残留根路径）
```

### 2.2 API 请求

```bash
# GET
curl -s http://127.0.0.1:8080/st-test01/api/characters/all | head -c 100
# 期望: JSON 或 空数组，不能是 HTML

curl -s http://127.0.0.1:8080/st-test01/api/presets | head -c 100
# 期望: JSON

curl -s http://127.0.0.1:8080/st-test01/api/themes | head -c 100
# 期望: JSON

# POST（需要先获取CSRF token或关闭CSRF）
# 在浏览器F12中测试，检查 Network 面板中所有 /api/ 请求路径
```

### 2.3 WebSocket / socket.io

```bash
# Socket.IO polling（HTTP长轮询，非WebSocket）
curl -s -o /dev/null -w "%{http_code}\n" \
  "http://127.0.0.1:8080/st-test01/socket.io/?EIO=4&transport=polling"
# 期望: 200（返回JSON，含sid）

# WebSocket升级
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  -H "Sec-WebSocket-Version: 13" \
  "http://127.0.0.1:8080/st-test01/socket.io/?EIO=4&transport=websocket"
# 期望: 101 Switching Protocols
```

**浏览器验证**（关键）：
1. F12 → Network → WS
2. 刷新页面
3. 确认 socket.io 连接路径是 `/st-test01/socket.io/`
4. 确认状态是 101 Switching Protocols
5. 确认连接保持稳定，不反复断开

### 2.4 多实例隔离

```bash
# test01 和 test02 返回不同数据
curl -s http://127.0.0.1:8080/st-test01/api/characters/all > /tmp/test01.json
curl -s http://127.0.0.1:8080/st-test02/api/characters/all > /tmp/test02.json
# 两个文件应该对应各自实例的数据

# test01 和 test02 独立响应
echo "test01: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/st-test01/)"
echo "test02: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/st-test02/)"
# 期望: 200, 200

# 杀掉test02的ST再测test01
kill $(cat /tmp/st2.pid)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/st-test01/
# 期望: 200（test02挂了不影响test01）
```

### 2.5 浏览器 F12 Network 检查表

打开 `http://127.0.0.1:8080/st-test01/`，F12 → Network，勾选 Preserve log：

| 检查项 | 合格标准 |
|--------|---------|
| css | 所有CSS请求带 `/st-test01/` 前缀，状态 200 |
| js | 所有JS请求带 `/st-test01/` 前缀，状态 200 |
| api | 所有API请求带 `/st-test01/api/`，状态 200/201/204 |
| socket.io | 路径带 `/st-test01/socket.io/`，状态 101 |
| img | 图片路径带 `/st-test01/` |
| fonts/webfonts | 字体路径带 `/st-test01/` |
| 残留根路径 | **0** 个请求不包含 `/st-test01/` |
| MIME错误 | 无 |
| CORS错误 | 无 |

### 2.6 写入操作（浏览器）

1. 保存 API 配置 → 检查 POST 请求路径
2. 新建聊天 → 检查数据写入
3. 发送消息 → 检查 SSE 流或 WebSocket
4. 刷新页面 → 数据持久化
5. 删除聊天 → 检查 DELETE

### 2.7 压力测试

```bash
# 连续请求100次，看是否有崩溃
for i in $(seq 1 100); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/st-test01/)
  if [ "$code" != "200" ]; then echo "FAIL at $i: $code"; fi
done
echo "done"

# 并发测试
seq 1 20 | xargs -P20 -I{} curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/st-test01/
```

## 三、判定标准

| 级别 | 条件 |
|------|------|
| **通过** | 所有 GET/静态资源 200，API 200，WS 101，0残留根路径，多实例隔离 |
| **基本通过** | GET/静态资源 OK，API 有少量 4xx（需认证的正常拒绝），WS 连接但偶断 |
| **不通过** | 大量根路径请求 `/css/` `/api/` `/socket.io/`，或 proxy 持续崩溃 |

## 四、常见问题排查

```bash
# proxy.js 崩溃？看日志
journalctl -u st-manager -f
# 或
cat /tmp/proxy-test01.log

# Nginx 502？检查proxy是否在监听
ss -tlnp | grep 9101

# 路径没改写？检查proxy是否正确接收路径参数
node templates/proxy/proxy.js test01 9101 9001
# 确认输出: [proxy] test01 :9101/st-test01/ -> :9001/

# WebSocket 断开？检查 nginx 是否有 Upgrade 头
curl -v http://127.0.0.1:8080/st-test01/socket.io/ 2>&1 | grep -i upgrade
```
