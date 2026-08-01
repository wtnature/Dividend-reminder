# 💰 Dividend Reminder — 分红再投资智能提醒系统

A 股与港股通（港股）分红数据自动监控与多通道提醒工具。

## 功能

- 🔍 **双源数据对比** — AkShare + 新浪财经交叉验证分红日期
- 🧮 **税后净额计算** — A 股差别化红利税 (0%/10%/20%) + 港股通税率 (H股10%/非H股20%) + 汇率
- 📱 **企业微信推送** — 派息日当天 09:15 即时提醒
- 📅 **ICS 日历备份** — iPhone/Android 日历订阅，提前 1 天 + 当天系统响铃
- ⚡ **智能轮询** — 财报季每日，非财报季每周，锁定派息日后停止
- 🖥️ **Streamlit 管理界面** — 持仓管理、手动修正、冲突处理

## 快速开始

### 1. 安装

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
cp config/settings.example.yaml config/settings.yaml
```

编辑 `config/settings.yaml`，填入企业微信自建应用的 CorpID、AgentID 和 Secret。

### 3. 启动管理界面

```bash
streamlit run src/ui/app.py
```

### 4. 命令行

```bash
python src/main.py init          # 初始化数据库
python src/main.py check         # 检查分红 + 发送提醒
python src/main.py ics           # 生成 ICS 日历
python src/main.py summary       # 发送每日摘要
python src/main.py test-wechat   # 测试微信连接
```

## 项目结构

```
├── .github/workflows/scheduler.yml  # GitHub Actions 定时调度
├── src/
│   ├── data_sources/                # AkShare + 新浪财经数据获取
│   ├── engine/                      # 双源仲裁 + 税率计算 + 轮询调度
│   ├── reminders/                   # 企业微信 + ICS 日历
│   ├── db/                          # SQLite 数据库
│   ├── ui/                          # Streamlit 管理界面
│   ├── utils/                       # 配置加载
│   └── main.py                      # CLI 入口
├── output/dividend.ics              # 日历文件（手机订阅）
├── config/settings.yaml             # 配置文件
└── data/                            # SQLite 数据库
```

## 部署

推送到 GitHub 后，Actions 会按定时计划自动运行：
- 财报季 (3-4月, 8-9月): 每日 09:15 北京时间
- 非财报季: 每周一 09:15 北京时间

也可在 Actions 页面手动触发 `workflow_dispatch`。

## ICS 日历订阅

iPhone: 设置 → 日历 → 账户 → 添加账户 → 其他 → 添加已订阅的日历

```
https://raw.githubusercontent.com/<user>/<repo>/main/output/dividend.ics
```

## 技术栈

- Python 3.11+
- AkShare / 新浪财经 API
- Streamlit (管理界面)
- SQLite (数据持久化)
- GitHub Actions (定时引擎)
- 企业微信自建应用 (消息推送)
- iCalendar / ICS (日历提醒)

## 版本

v0.6 — 原型测试版
