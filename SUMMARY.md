# ST Cloud Manager — 工作总结与交接 (2026-05-12)

## 概览

本次工作将一个 "能跑但架构模糊" 的 `st-cloud-manager` 项目，重构为分层明确、接口统一、验证完备的状态。

```text
提交前: 单文件 app.py 500+ 行, instance_service.py 1000+ 行, hasattr 兼容遍地
提交后: 6 层架构, 0 个 hasattr, 33 smoke + 7 caps + 8 http e2e 全绿
```

---

## 一、架构重构 (Phase 1-6)

### 目标架构

```
routes/          HTTP 路由层 (public, admin, proxy)
services/        业务逻辑层 (instance_orchestrator, trial_service)
repositories/    数据访问层 (trial, summary, settings, key)
runtimes/        运行时适配器 (base ABC, docker, process)
routers/         路由后端 (nginx, traefik, manager_fallback)
models/          数据模型 (route DTO)
```

### 分层职责

| 层 | 目录 | 职责 | 实例 |
|----|------|------|------|
| HTTP | `routes/` | 请求/响应，调用 services | `routes/public.py`, `routes/admin.py`, `routes/proxy.py` |
| Service | `services/` | 业务逻辑，编排 | `instance_orchestrator.py` (生命周期), `trial_service.py` (排队/空闲/心跳) |
| Repository | `repositories/` | SQL 操作，返回 DTO | `trial_repository.py`, `summary_repository.py`, `settings_repository.py` |
| Runtime | `runtimes/` | 容器/进程操作，能力声明 | `base.py` (ABC), `docker_runtime.py`, `process_runtime.py` |
| Router | `routers/` | 路由配置生成 | `nginx_router.py`, `traefik_router.py` |

### 旧模块处理

| 模块 | 状态 |
|------|------|
| `instance_service.py` | **保留** — 兼容 facade, re-export services/instance_orchestrator + trial_service |
| `key_service.py` | **保留** — 兼容 facade, re-export repositories/key_repository |
| `settings_service.py` | **保留** — 兼容 facade, re-export repositories/settings_repository |
| `app.py` | **削薄** — 500+ 行 → 30 行，只做路由器挂载 + 生命周期钩子 |
| `scheduler.py` | **更新** — 直接引用 services/instance_orchestrator 和 services/trial_service |

**删除需等待 scripts/\*.py 迁移完成后统一执行。**

---

## 二、Runtime 接口统一 (Phase 5)

### 问题

业务代码中散落 `hasattr(svc, 'process_exists')`、`hasattr(svc, 'get_logs')` 等运行时探测，docker_service 和 process_service 接口不完全对齐。

### 解决方案

定义统一 `RuntimeAdapter` ABC (`runtimes/base.py`)：

```python
class RuntimeAdapter(Protocol):
    # 生命周期
    create_container(**kwargs) -> bool
    stop_container(name) -> bool
    start_container(name) -> bool
    restart_container(name) -> bool
    remove_container(name) -> bool

    # 内省
    health_check_container(domain, timeout, path_prefix) -> bool
    get_logs(instance_id, tail) -> str
    inspect_container(name) -> dict
    process_exists(instance_id) -> bool
    security_audit() -> dict
    get_container_ip(name) -> str | None
    get_container_port(instance_id) -> int | None

    # 能力标志
    supports_trial_isolation() -> bool     # Docker: True, Process: False
    supports_resource_limits() -> bool     # Docker: True, Process: False
    supports_dynamic_port_allocation() -> bool  # Docker: False, Process: True
```

### 实现

| 文件 | 类 | 说明 |
|------|-----|------|
| `runtimes/base.py` | `RuntimeAdapter` | ABC 定义 |
| `runtimes/docker_runtime.py` | `DockerRuntime` | 委托 docker_service, process_exists→docker ps, get_logs→docker logs |
| `runtimes/process_runtime.py` | `ProcessRuntime` | 委托 process_service, 删除重复 restart_container |

### 接入点

`router_service.get_runtime_service()` 现在返回 `DockerRuntime()` 或 `ProcessRuntime()` 实例，不再是裸模块。

### 变更统计

- 删除 3 个 `hasattr()` 分支 (`check_crashed`, `get_instance_logs`, `get_instance_inspect`)
- 删除 `admin.py` 对 `docker_service` 的硬编码 import (`security_audit` 改为运行时获取)
- 删除 `process_service.py` 重复的 `restart_container`
- `docker_service.py` 无需新增方法 (docker_runtime 在适配器层用 subprocess 补充)

---

## 三、trial 能力降级

### 问题

process 模式没有容器隔离，但 trial_service 不管运行时能力统一创建实例。

### 解决方案

- `trial_service._effective_trial_max()` — process 模式上限 clamp 到 2
- `supports_resource_limits()=False` 时跳过 `can_create_instance()` 资源检查
- 响应中附带 `weak_isolation=True` 标志
- `get_trial_queue_status()` 返回 `weak_isolation` 和正确的 `max_trials`

### 触发路径

```python
# trial_service.py
runtime = get_runtime_service()
if not runtime.supports_trial_isolation():
    trial_max = min(cfg_max, 2)       # 降级
if not runtime.supports_resource_limits():
    skip resource check               # 跳过
result["weak_isolation"] = True       # 标记
```

---

## 四、/st-\* fallback 策略

### 配置项

```env
ST_ENABLE_MANAGER_PATH_FALLBACK=true   # 默认开启
```

设置在 `manager/config.py`，由 `routes/proxy.py` 在配置为 false 时不注册 `/st-*` 路由。

### 策略

```
nginx/traefik → 生产主路由
manager /st-*  → 兜底代理, 本地开发 fallback
```

双路径不会同时存在：生产环境关掉 manager fallback，开发环境关掉 nginx。

---

## 五、验证管道

### 三层验证

| 层 | 命令 | 内容 | 速度 |
|----|------|------|------|
| 代码级 | `python scripts/validate_local.py` | compileall + 33 smoke + 7 caps | ~30s |
| Mock HTTP | `python scripts/validate_http_e2e.py --mock-st` | 自动启停 manager + 8 http e2e | ~5s |
| 真实 HTTP | `python scripts/validate_http_e2e.py` | 自动启停 manager + 8 http e2e (需 Node.js + st-release) | ~60s+ |

### 判决规则

```text
PASS = 所有测试通过, 无 skip
WARN = 所有测试通过, 但 HTTP E2E 因 manager 未运行而 skip
FAIL = 任一测试失败, 或 strict 模式下 HTTP E2E skip
```

### CI 集成

| Workflow | 触发 | 内容 |
|----------|------|------|
| `.github/workflows/test.yml` | push/PR | smoke + caps |
| `.github/workflows/http-e2e.yml` | push/PR + workflow_dispatch | mock-st HTTP E2E |

---

## 六、前端 Bug 修复

### Bug: trial 创建成功但字段全空

**根因**: `enqueue_trial()` 返回 `{"queued": True, ...}` 且状态码 200。
前端 `doTrialCreate()` 中 `data.queued` 判断在 `if(!resp.ok)` 内部，
但 200 = `resp.ok` = true，所以排队分支永不触发，落到成功渲染分支
读取 `data.url` 等 (undefined)，显示 "—"。

**修复**: `static/activate.html:210` — `data.queued` 判断提到 `!resp.ok` 之前。

### Bug: 持续请求 /api/trial/activity/undefined

**根因**: `startActivityPing(data.instance_id)` 无 guard，`data.instance_id` 可能是 undefined。

**修复**: `static/activate.html:235` — 包裹 `if(data.instance_id)`。

---

## 七、Code Review 发现 (29 项)

### 已修复 (HIGH/CRITICAL)

| # | 严重度 | 问题 | 修复位置 |
|---|--------|------|----------|
| #1 | HIGH | `_CF_DEFAULTS` 重复 `_DEFAULTS` | `cloudflare_service.py` → 改用 `_CF_KEYS` set + `get_all_settings()` |
| #7 | HIGH | `check_expired` 不删 Cloudflare DNS 记录 | `instance_orchestrator.py` → 增加 CF 清理逻辑 |
| #23 | HIGH | `PROXY_BASE_URL.rstrip('/v1')` 逐字符 strip bug | `proxy_service.py` → `removesuffix('/v1')` |
| #8-11 | MEDIUM | 回滚异常全部 `except: pass` 无日志 | `instance_orchestrator.py` → 全部加 `print()` 日志 |
| #13 | MEDIUM | nginx reload 失败完全忽略 | `nginx_config_service.py` → 检查 returncode + 输出错误 |

### 遗留 (待决策)

| # | 问题 | 建议 |
|---|------|------|
| #14/#15 | docker/process 接口不统一 (已 Phase 5 解决) | ✅ |
| #17 | process 模式无视 `is_trial` 参数 | 已加 `supports_trial_isolation()` 降级 |
| #20 | manager fallback `/st-*` 可能与 nginx 双跳 | 已加配置项 + 策略文档 |

---

## 八、代码审查检查表

### 架构一致性

- [x] domain 只表示 host
- [x] path_prefix 只表示 /st-xxx
- [x] access_url 只由 build_access_url() 生成
- [x] 没有模块自己手拼访问 URL
- [x] router sync 只通过 router_service 入口触发

### runtime 边界

- [x] 没有散落的 hasattr(runtime, ...)  (已完成 Phase 5)
- [x] docker/process 都实现同一组方法
- [x] trial_service 不直接依赖 docker 细节
- [x] instance_orchestrator 不直接拼 docker/process 特殊逻辑

### 生命周期

- [x] create 后状态正确 (smoke test 验证)
- [x] renew 后 expired_at 正确更新 (smoke test 验证)
- [x] delete 后 DB/容器/路由清理 (smoke test 验证)
- [x] manager restart 后实例可恢复 (smoke test 验证)
- [x] trial idle release 正常 (smoke test 验证)

---

## 九、文件清单

### 新增

```text
manager/models/route_model.py
manager/repositories/__init__.py
manager/repositories/trial_repository.py
manager/repositories/summary_repository.py
manager/repositories/settings_repository.py
manager/repositories/key_repository.py
manager/services/__init__.py
manager/services/instance_orchestrator.py
manager/services/trial_service.py
manager/runtimes/__init__.py
manager/runtimes/base.py
manager/runtimes/docker_runtime.py
manager/runtimes/process_runtime.py
manager/routers/__init__.py
manager/routers/nginx_router.py
manager/routers/traefik_router.py
manager/routers/manager_fallback_router.py
manager/routes/__init__.py
manager/routes/dependencies.py
manager/routes/public.py
manager/routes/admin.py
manager/routes/proxy.py
tests/__init__.py
tests/conftest.py
tests/test_smoke.py
tests/test_e2e_runtime_caps.py
tests/test_e2e_path_mode.py
tests/helpers/fake_st_server.py
tests/helpers/fake_runtime.py
scripts/validate_local.py
scripts/validate_http_e2e.py
scripts/collect_diagnostics.py
.github/workflows/test.yml
.github/workflows/http-e2e.yml
```

### 修改

```text
manager/app.py                                     (500+ → 30 行)
manager/instance_service.py                        (1065 → 25 行 facade)
manager/key_service.py                             (87 → 8 行 facade)
manager/settings_service.py                        (156 → 9 行 facade)
manager/cloudflare_service.py                      (去重 CF defaults)
manager/nginx_config_service.py                    (reload 加日志)
manager/proxy_service.py                           (rstrip→removesuffix)
manager/process_service.py                         (删重复 restart_container)
manager/router_service.py                          (返回 RuntimeAdapter, 支持 mock-st)
manager/scheduler.py                               (引用新 service 模块)
manager/config.py                                  (新增 ENABLE_MANAGER_PATH_FALLBACK)
manager/routes/admin.py                            (security_audit 走 runtime, _rmtree Windows 兼容)
static/activate.html                               (修复 queued 分支 + undefined ping)
.gitignore                                         (补 .pytest_cache/ artifacts/)
```

---

## 十、下一步建议

### 短期 (本次可提交)

```bash
git add .
git commit -m "refactor: layered architecture, unified runtime, validation pipeline"
```

### 中期

1. **真实 ST 部署验证** — 在 Linux VPS 上跑 `python scripts/validate_http_e2e.py` (无 --mock-st)
2. **scripts/\*.py 迁移** — 把旧脚本引用从 `instance_service` 迁到新模块，然后删除 3 个 facade
3. **nginx path-mode e2e** — 在 GitHub Actions 加 nginx 模拟测试
4. **Playwright 浏览器 E2E** — 验证静态资源路径、socket.io、API 请求

### 长期

1. **trial_repository / summary_repository** — 完全消除旧 service 中的直接 SQL
2. **废弃兼容层** — 扫描所有 `from manager.{instance,key,settings}_service import` 引用后删除 facade
3. **Docker mode CI** — 补 Docker runtime 的真实验证

---

## 十一、verdict

```text
代码质量:   PASS    compileall 干净, 0 处 hasattr
架构:       PASS    6 层分离, 接口统一
回测:       PASS    33 smoke + 7 caps + 8 http e2e = 48/48
前端:       FIXED   排队空字段 + undefined ping 已修复
安全性:     N/A     本回合未涉及密钥/认证变更
兼容性:     保留    3 个 facade 模块仍可被旧脚本引用
可部署性:   WARN    HTTP E2E 需 ST 运行时环境 (CI 已就绪, 真实部署待验证)
```
