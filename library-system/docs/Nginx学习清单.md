# Nginx 学习清单（基于云阁图书系统部署）

> 配套配置文件：[library.conf](../deploy/nginx/library.conf)
> 线上地址：https://lxlssr.icu
> 目标：学完既能讲清简历/部署里写了什么，也能在 ECS 上自己改配置、reload、排错。

---

## 一、必须先懂的 5 个概念（面试必问）

### 1. 反向代理 vs 正向代理

- **正向代理**：代理客户端（如 VPN），服务端不知道真实客户端。
- **反向代理**：代理服务端（如 Nginx），客户端不知道真实后端。
- 本项目：Nginx 是反向代理，用户访问 80 端口，Nginx 转发到 18080 的 Spring Boot。

### 2. server 块

一台 Nginx 可以配置多个 server，靠 `server_name` 区分（虚拟主机）。
本项目 `server_name lxlssr.icu;` 就是匹配域名的那个 server。

### 3. location 匹配规则（优先级从高到低）

| 修饰符 | 含义 | 示例 |
| --- | --- | --- |
| `=` | 精确匹配 | `location = /favicon.svg` |
| `^~` | 前缀匹配后停止正则 | `location ^~ /static/` |
| `~` | 正则匹配（区分大小写） | `location ~ \.php$` |
| `~*` | 正则匹配（不区分大小写） | `location ~* \.(jpg|png)$` |
| 无修饰 | 普通前缀（最长匹配胜出，但会继续看正则） | `location /assets/` |

本项目用了 `=` 和普通前缀两种。

### 4. proxy_pass

把请求转发到后端。
`proxy_pass http://127.0.0.1:18080;` 即把请求转发到本机 18080 的 Spring Boot。

### 5. 动静分离

静态资源（图片、css、js）由 Nginx 直接处理或单独配置缓存，动态请求转发到后端。
本项目 `/assets/` 和 `/favicon.svg` 单独配 location 就是为了给静态资源设长缓存。

---

## 二、必背的配置指令（对照 library.conf）

| 指令 | 作用 | 本项目写法 |
| --- | --- | --- |
| `listen` | 监听端口 | `listen 80;` |
| `server_name` | 匹配域名 | `server_name lxlssr.icu;` |
| `access_log` / `error_log` | 访问日志 / 错误日志路径 | `/var/log/nginx/lxlssr.icu.access.log` |
| `client_max_body_size` | 限制上传请求体大小 | `10m` |
| `location` | 路由规则块 | `/assets/`、`/favicon.svg`、`/` |
| `proxy_pass` | 反向代理转发地址 | `http://127.0.0.1:18080` |
| `proxy_http_version` | 代理用 HTTP 版本 | `1.1`（支持长连接，默认 1.0） |
| `proxy_set_header Host` | 把原始 Host 头传给后端 | `$host` |
| `proxy_set_header X-Real-IP` | 真实客户端 IP | `$remote_addr` |
| `proxy_set_header X-Forwarded-For` | 代理链路 IP | `$proxy_add_x_forwarded_for` |
| `proxy_set_header X-Forwarded-Proto` | 原始协议（http/https） | `$scheme` |
| `proxy_connect_timeout` | 连接后端超时 | `5s` |
| `proxy_read_timeout` | 读后端响应超时 | `60s` |
| `proxy_send_timeout` | 发请求给后端超时 | `60s` |
| `add_header` | 加响应头 | 安全头、Cache-Control |
| `proxy_hide_header` | 隐藏后端的某个响应头 | `Cache-Control` |
| `proxy_buffering` | 是否缓冲后端响应 | `off` 关闭，实时转发 |

### 必记的内置变量

| 变量 | 含义 |
| --- | --- |
| `$host` | 请求 Host 头 |
| `$remote_addr` | 客户端 IP |
| `$scheme` | http 或 https |
| `$proxy_add_x_forwarded_for` | 已有 XFF + 客户端 IP |
| `$request` | 完整请求行 |
| `$status` | 响应状态码 |

---

## 三、本项目 3 个 location 的设计意图（面试亮点）

### 1. `location /assets/` 单独配置

**为什么？** 前端打包出来的 js/css 文件名带 hash，内容不变 → 可以长缓存（一年 immutable）。
如果走默认 location，会被 `proxy_read_timeout 60s` 等通用设置影响，且没法单独设缓存策略。

### 2. `location = /favicon.svg` 用 `=`

**为什么？** favicon 请求量小但频繁，用精确匹配优先级最高，直接命中不走前缀匹配，减少处理开销。

### 3. `location /` 兜底

所有未匹配到上面的请求都走这里，转发到 Spring Boot。
Spring Boot 里 `SpaController` 再把非 `/api` 的请求转发到 `index.html`，实现前端路由刷新不 404。

---

## 四、3 个安全响应头（必背作用）

```
X-Content-Type-Options: nosniff
→ 禁止浏览器嗅探 MIME，防止把文本当脚本执行

X-Frame-Options: SAMEORIGIN
→ 只允许同源页面 iframe 嵌入，防点击劫持

Referrer-Policy: strict-origin-when-cross-origin
→ 跨源请求只带 origin 不带 path，保护隐私
```

---

## 五、实战命令（ECS 上必会）

```bash
nginx -t              # 测试配置语法是否正确，改完配置必跑
nginx -s reload       # 平滑重载，不中断服务
nginx -s stop         # 停止
systemctl status nginx   # 查看状态（systemd 托管）
systemctl reload nginx
tail -f /var/log/nginx/lxlssr.icu.error.log   # 看错误日志，排查必用
```

### 配置文件位置（CentOS 7 + 默认 yum 安装）

- 主配置：`/etc/nginx/nginx.conf`
- 子配置：`/etc/nginx/conf.d/*.conf`（本项目的 `library.conf` 应放这里）
- include 指令：主配置里 `include /etc/nginx/conf.d/*.conf;` 会自动加载子配置

---

## 六、5 个必做的实战练习（按顺序做）

### 练习 1：本地起一个 Nginx 转发

ECS 上本地起一个 `python3 -m http.server 18080`，
Nginx 配 `proxy_pass http://127.0.0.1:18080`，
`nginx -s reload` 后浏览器访问域名，看到 python 的目录列表即成功。

### 练习 2：改 `server_name` 故意写错

改成 `server_name wrong.icu;`，
reload 后访问 `lxlssr.icu`，观察请求被哪个 server 兜底（默认 server 概念）。
改回来。

### 练习 3：加一个新 location

自己加：

```nginx
location /health {
    return 200 'ok';
}
```

reload 后 `curl https://lxlssr.icu/health` 看返回。
这条不转发后端，Nginx 直接响应。

### 练习 4：故意把 `proxy_pass` 端口写错

改成 `18081`，reload，访问首页，
看 `error.log` 里出现 `connection refused`。
改回 `18080`。理解日志排查流程。

### 练习 5：看 access.log 的字段

```bash
tail -n 5 /var/log/nginx/lxlssr.icu.access.log
```

看每一行格式（默认 `main` 格式）：

```
$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"
```

理解 `$remote_addr` 为什么永远是 Cloudflare 的 IP
→ 引出"如何拿真实 IP"：需要 Cloudflare 把真实 IP 写到 `CF-Connecting-IP` 头，Nginx 用 `proxy_set_header X-Real-IP $http_cf_connecting_ip;` 透传给后端。

---

## 七、面试一句话总结模板

> 我用 Nginx 做反向代理，把 80 端口的请求转发到本机 18080 的 Spring Boot。
> 对 `/assets/` 和 favicon 这类静态资源单独配置了 location 走长缓存（一年 immutable），
> 主路径 `/` 走通用转发并设置了 5s 连接超时和 60s 读写超时。
> 安全层面加了 `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy` 三个响应头。

背熟这段 + 上面 5 个练习走一遍，Nginx 这块就能扛住 80% 的追问。

---

## 八、可选进阶（学有余力再看）

| 主题 | 一句话说明 | 关键指令 |
| --- | --- | --- |
| HTTPS 配置 | Cloudflare 模式下 Nginx 只需监听 80；如本地终止 TLS 需配 `ssl_certificate` | `listen 443 ssl;` |
| 限流 | 防恶意刷接口 | `limit_req_zone` + `limit_req` |
| Gzip 压缩 | 减小传输体积 | `gzip on; gzip_types text/css application/javascript;` |
| 负载均衡 | 多后端轮询/权重/ip_hash | `upstream` 块 |
| 日志切割 | 按天分割日志防文件过大 | `logrotate` |
