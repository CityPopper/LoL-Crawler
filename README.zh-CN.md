# 英雄联盟比赛数据分析管线

[English](README.md)

通过 Riot API 收集英雄联盟比赛历史并计算玩家聚合统计数据的流处理管线。无状态 Python 微服务通过 Redis Streams 通信。

## 管线架构

```
种子 → 爬虫 → 抓取 → 解析 → 分析
                ↑             ↕
            发现服务   恢复 + 延迟调度
                              ↕
                     Web UI (端口 8080)
```

| 服务         | 功能                                        |
|-------------|---------------------------------------------|
| 种子 (Seed)  | 将 Riot ID 解析为 PUUID，加入队列              |
| 爬虫 (Crawler) | 获取玩家所有比赛 ID，去重                     |
| 抓取 (Fetcher) | 从 Riot API 下载原始比赛 JSON                |
| 解析 (Parser)  | 将原始 JSON 转换为结构化 Redis 记录           |
| 分析 (Analyzer) | 构建增量玩家聚合统计                         |
| 恢复 (Recovery) | 从死信队列重试失败消息                       |
| 延迟调度 (Delay Scheduler) | 将限流延迟消息移入目标流               |
| 发现 (Discovery) | 空闲时将发现的玩家加入管线                   |
| Web UI       | 将玩家加入管线并查看统计数据 http://localhost:8080 |

## 截图

### 仪表盘
![仪表盘](screenshots/dashboard_zh.png)

### 玩家数据
![玩家数据](screenshots/stats_zh.png)

### 英雄梯队
![英雄梯队](screenshots/champions_zh.png)

### 对位查询
![对位查询](screenshots/matchups_zh.png)

### 玩家列表
![玩家列表](screenshots/players_zh.png)

### 流监控
![流监控](screenshots/streams_zh.png)

### 日志
![日志](screenshots/logs_zh.png)

### 移动端
![移动端](screenshots/mobile_zh.png)

## 安装

```bash
just setup          # 复制 .env.example → .env
# 编辑 .env 设置 RIOT_API_KEY
just up             # 安装 + 构建 + 运行（热重载已启用）
```

## 运行

默认使用 Podman。使用 Docker：`RUNTIME=docker just <cmd>`

```bash
just up                         # 一键安装构建运行
just seed "Faker#KR1" kr        # 添加玩家
just logs fetcher               # 查看服务日志
just streams                    # 查看 Redis 流深度
just stop                       # 暂停容器（数据保留）
just down                       # 移除容器（数据保留）
just reset                      # 移除容器 + 清除 Redis 数据
```

## Web 界面

访问 http://localhost:8080。功能：

- **仪表盘** — 系统状态、流深度、玩家查询
- **数据** — 玩家资料、比赛历史、AI 评分、英雄分析
- **英雄** — 按版本的梯队列表，PBI 评分
- **对位** — 英雄对位查询，支持自动补全
- **玩家** — 按排位排序的玩家列表，大区筛选
- **流** — 管线健康监控
- **死信** — 死信队列浏览，可展开查看详情
- **日志** — 合并服务日志，支持服务筛选

支持语言切换（EN | 中文）和主题切换（默认 | Art Pop）。

## 管理 CLI

```bash
just admin stats "Faker#KR1" --region kr
just admin dlq list
just admin dlq replay --all
just admin system-resume
just admin reseed "Faker#KR1" --region kr
```

## 测试

```bash
just test           # 并行运行所有单元测试
just test-svc ui    # 单个服务
just contract       # PACT 契约测试
just integration    # 集成测试（需要 Docker）
just lint           # ruff 检查 + 格式化
just typecheck      # mypy
just check          # lint + typecheck
```

## 环境变量

从 `.env.example` 创建 `.env`，至少设置：

| 变量           | 说明                    |
|---------------|------------------------|
| `RIOT_API_KEY` | Riot Games API 密钥（必填）|
| `REDIS_URL`    | Redis 连接字符串         |

完整选项见 `.env.example`。
