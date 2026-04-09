---
name: canvas
description: Canvas LMS 集成 — 使用 API 令牌身份验证获取注册的课程和作业。
version: 1.0.0
author: community
license: MIT
prerequisites:
  env_vars: [CANVAS_API_TOKEN, CANVAS_BASE_URL]
metadata:
  kclaw:
    tags: [Canvas, LMS, 教育, 课程, 作业]
---

# Canvas LMS — 课程和作业访问

只读访问 Canvas LMS 以列出课程和作业。

## 脚本

- `scripts/canvas_api.py` — Canvas API 调用的 Python CLI

## 设置

1. 在浏览器中登录您的 Canvas 实例
2. 前往 **Account → Settings**（点击您的配置文件，然后 Settings）
3. 滚动到 **Approved Integrations** 并点击 **+ New Access Token**
4. 命名令牌（例如"KClaw Agent"），设置可选的过期时间，然后点击 **Generate Token**
5. 复制令牌并添加到 `~/.kclaw/.env`：

```
CANVAS_API_TOKEN=your_token_here
CANVAS_BASE_URL=https://yourschool.instructure.com
```

基础 URL 是您在浏览器中登录 Canvas 时显示的内容（无尾部斜杠）。

## 使用

```bash
CANVAS="python $KCLAW_HOME/skills/productivity/canvas/scripts/canvas_api.py"

# 列出所有活动课程
$CANVAS list_courses --enrollment-state active

# 列出所有课程（任何状态）
$CANVAS list_courses

# 列出特定课程的作业
$CANVAS list_assignments 12345

# 按到期日期列出作业
$CANVAS list_assignments 12345 --order-by due_at
```

## 输出格式

**list_courses** 返回：
```json
[{"id": 12345, "name": "Intro to CS", "course_code": "CS101", "workflow_state": "available", "start_at": "...", "end_at": "..."}]
```

**list_assignments** 返回：
```json
[{"id": 67890, "name": "Homework 1", "due_at": "2025-02-15T23:59:00Z", "points_possible": 100, "submission_types": ["online_upload"], "html_url": "...", "description": "...", "course_id": 12345}]
```

注意：作业描述截断为 500 个字符。`html_url` 字段链接到 Canvas 中的完整作业页面。

## API 参考（curl）

```bash
# 列出课程
curl -s -H "Authorization: Bearer $CANVAS_API_TOKEN" \
  "$CANVAS_BASE_URL/api/v1/courses?enrollment_state=active&per_page=10"

# 列出课程的作业
curl -s -H "Authorization: Bearer $CANVAS_API_TOKEN" \
  "$CANVAS_BASE_URL/api/v1/courses/COURSE_ID/assignments?per_page=10&order_by=due_at"
```

Canvas 使用 `Link` 头进行分页。Python 脚本自动处理分页。

## 规则

- 此技能是**只读**的 — 它仅获取数据，从不修改课程或作业
- 首次使用时，通过运行 `$CANVAS list_courses` 验证身份验证 — 如果因 401 失败，指导用户完成设置
- Canvas 速率限制约为每 10 分钟 700 请求；如果达到限制检查 `X-Rate-Limit-Remaining` 头

## 故障排除

| 问题 | 修复 |
|---------|-----|
| 401 未授权 | 令牌无效或过期 — 在 Canvas Settings 中重新生成 |
| 403 禁止 | 令牌缺少此课程的权限 |
| 空课程列表 | 尝试 `--enrollment-state active` 或省略标志以查看所有状态 |
| 错误机构 | 验证 `CANVAS_BASE_URL` 与浏览器中的 URL 匹配 |
| 超时错误 | 检查到 Canvas 实例的网络连接 |
