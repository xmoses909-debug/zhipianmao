# 智能体（Agent）架构说明

这套软件本质是一个**有技能与记忆的智能体**。本目录是它的「大脑」。

```
agent/
├── memory/                  记忆：长期保存的偏好与状态
│   └── taste-profile.json   制片人的口味画像 + 反馈记录
└── skills/                  技能：智能体会做的事
    └── 选品与改编分析.md      选题雷达的工作流程与评分口径
```

## 测试版 vs 正式版

| | 测试版（现在） | 正式版（未来） |
|---|---|---|
| 大模型 / 智能体 | **由 Claude 充当** | 接 **API**，后端自动执行 |
| 选品与分析 | Claude 按 `skills/` 执行，结果写入数据文件 | 定时任务自动跑 |
| 记忆 | `memory/` + 浏览器 localStorage | 数据库 |
| 触发 | 人工对话驱动 | 每周自动 |

## 数据流（测试版）

```
豆瓣阅读公开页面
      │  Claude 按 skills/选品与改编分析.md 抓取 + 分析
      ▼
app/data/recommendations.js   ← 智能体的本周输出
      │  浏览器读取
      ▼
选题雷达 App（app/index.html）  ← 制片人浏览、收藏、点赞/拍掉
      │  反馈
      ▼
（未来）回流到 memory/taste-profile.json → 校准下周选品
```

接 API 后，把 `skills/` 作为提示词 / 流程规格，把 `memory/` 换成数据库，把「Claude 手动产出 recommendations.js」换成定时后端任务即可，App 层基本不变。
