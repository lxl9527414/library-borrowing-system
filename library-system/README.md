# 云阁图书借阅系统

一个精简但完整的在线图书借阅系统。后端使用 Spring Boot、MyBatis-Plus、MySQL 和 Redis，前端使用 Vue 3 与 Element Plus。

## 功能范围

- 用户模块：管理员与普通读者登录、Redis token 登录态、简单 RBAC、用户管理
- 图书模块：图书列表、关键词与分类筛选、分页、新增、编辑、删除
- 借阅模块：借书、续借、还书、借阅记录、逾期状态、逾期罚金结算
- 借阅规则中心：借阅上限、借期、续借次数、每日罚金与封顶、预约上限与保留时长
- 预约排队：库存不足时可预约，归还后按队列通知取书，超时自动顺延给下一位读者
- 操作日志：AOP 记录关键操作的人员、参数、结果与耗时，自动保留最近 90 天
- 工作台：馆藏、库存、读者、借出、逾期、预约和归还统计
- 工程能力：Maven、Lombok、Hutool、MyBatis-Plus、Spring AOP、定时任务、全局异常处理、参数校验

## 目录结构

```text
.
├── backend/   # Spring Boot 3.5.x 服务端，最终包含前端静态资源
└── frontend/  # Vue 3 + Vite + Element Plus 客户端
```

## 本地环境

- JDK 17+
- Maven 3.9+
- Node.js 20+
- MySQL 8.x
- Redis 6+

数据库会自动执行 `backend/src/main/resources/schema.sql` 和 `data.sql`，其中 `data.sql` 会幂等写入 1 个管理员、5 个读者和 500 本示例图书（重复执行不会产生重复数据）。首次启动前只需创建数据库：

```sql
CREATE DATABASE library_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

已有数据库升级到当前版本时，先执行一次 `backend/src/main/resources/db/upgrade-2026-10.sql`（新增借阅规则、预约排队、操作日志表以及借阅记录的续借 / 罚金字段），新环境无需执行。

默认连接配置位于 [application.yml](backend/src/main/resources/application.yml)，均可通过环境变量覆盖。

## 开发运行

启动后端：

```bash
cd backend
mvn spring-boot:run
```

另开终端启动前端：

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:5173`，Vite 会将 `/api` 代理到 `http://127.0.0.1:18080`。

默认账号：

| 角色 | 用户名 | 密码 |
| --- | --- | --- |
| 管理员 | `admin` | `admin123` |
| 读者 | `reader` | `reader123` |
| 读者 | `reader2` ~ `reader5` | `reader123` |

## 一键打包

```bash
./build.sh
```

脚本会先构建 Vue 前端并输出到 `backend/src/main/resources/static`，再打包出包含前端页面的 Spring Boot 可执行 jar：

```bash
java -jar backend/target/library-server.jar
```

## 常用环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SERVER_PORT` | `18080` | 服务端口 |
| `DB_URL` | 本机 `library_db` | JDBC 连接串 |
| `DB_USERNAME` | `root` | 数据库用户 |
| `DB_PASSWORD` | `root` | 数据库密码 |
| `REDIS_HOST` | `127.0.0.1` | Redis 地址 |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `REDIS_PASSWORD` | 空 | Redis 密码 |
| `TOKEN_TTL` | `604800` | token 有效期，单位秒 |
| `SQL_INIT_MODE` | `always` | 首次部署后可设为 `never` |

生产部署已切换到独立的 MySQL、Redis 和系统账号，未使用演示密码。生产服务器的路径、服务管理和更新命令见 [deploy/README.md](deploy/README.md)。
