# 中文市场与AI选股简报 GitHub Actions 部署

这个项目会在 GitHub Actions 中按工作日 A 股交易时段自动生成中文市场与AI选股简报，保存到 Google Docs，并发送到 `11283176@qq.com`。

文档规则：北京时间一天一个文档，标题为 `中文市场与AI选股简报｜YYYY-MM-DD`。同一天内多次运行会追加到当天文档末尾，不会重复创建多个文档。

## 文件

- `.github/workflows/premarket.yml`：工作日交易时段定时任务。GitHub 使用 UTC，已换算为北京时间 `09:20-11:30`、`13:00-15:00`。
- `scripts/premarket_report.py`：抓取公开市场数据，调用 OpenAI 生成报告，创建或追加当天 Google Doc，并发送邮件。
- `requirements.txt`：GitHub Actions 需要安装的 Python 依赖。
- `.env.example`：本地运行需要的环境变量模板。

## GitHub Secrets

在 GitHub 仓库页面进入 `Settings -> Secrets and variables -> Actions`，添加：

- `OPENAI_API_KEY`：OpenAI API key。
- `GOOGLE_SERVICE_ACCOUNT_JSON`：Google Cloud 服务账号 JSON。可以直接粘贴完整 JSON，也可以粘贴 base64 后的 JSON。
- `GOOGLE_DRIVE_FOLDER_ID`：可选。目标 Google Drive 文件夹 ID。建议填写。
- `SMTP_HOST`：发件邮箱 SMTP 服务器。QQ 邮箱通常是 `smtp.qq.com`。
- `SMTP_USERNAME`：发件邮箱账号。
- `SMTP_PASSWORD`：SMTP 授权码或应用专用密码，不是登录密码。QQ 邮箱需要在邮箱设置中开启 SMTP/POP3 并生成授权码。
- `EMAIL_FROM`：发件邮箱地址，通常与 `SMTP_USERNAME` 相同。

可选变量：

- `OPENAI_MODEL`：默认 `gpt-4.1`。在 `Settings -> Secrets and variables -> Actions -> Variables` 中配置。
- `SMTP_PORT`：默认 `465`。如果不配置，脚本按 465 处理。

## Google Docs 配置

1. 在 Google Cloud 创建一个项目。
2. 启用 Google Drive API 和 Google Docs API。
3. 创建 Service Account，并下载 JSON key。
4. 在你的 Google Drive 中创建一个保存报告的文件夹。
5. 把这个文件夹共享给 Service Account 的邮箱，权限设为编辑者。
6. 复制文件夹 URL 中 `/folders/` 后面的 ID，保存为 GitHub Secret `GOOGLE_DRIVE_FOLDER_ID`。

如果不配置 `GOOGLE_DRIVE_FOLDER_ID`，文档会创建在服务账号自己的 Drive 空间里，通常不方便你直接查看。

## 本地运行

复制 `.env.example` 为 `.env.local`，填写：

```env
OPENAI_API_KEY=你的 OpenAI API key
OPENAI_MODEL=gpt-4.1
GOOGLE_SERVICE_ACCOUNT_JSON={"type":"service_account",...}
GOOGLE_DRIVE_FOLDER_ID=你的文件夹ID
EMAIL_TO=11283176@qq.com
EMAIL_FROM=你的发件邮箱
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=你的发件邮箱
SMTP_PASSWORD=你的SMTP授权码
```

然后运行：

```bash
pip install -r requirements.txt
python scripts/premarket_report.py
```

## 手动触发

推送到 GitHub 后，进入 `Actions -> A-share market brief -> Run workflow` 可以手动运行一次。脚本内部仍会按北京时间校验工作日和交易时段；非窗口时间手动触发会跳过，也不会发送邮件。

## 风险提示

生成内容基于公开行情和新闻数据，可能存在延迟、不完整或接口变更。报告中的股票名单是研究关注名单，不构成投资建议，不承诺收益。
