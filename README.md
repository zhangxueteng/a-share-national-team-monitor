# A股国家队资金监测台（免费公开数据版）

## 当前版本能做什么
- 通过 GitHub Actions 每 6 小时尝试抓取 Google News RSS 的中文公开搜索结果。
- 查询中央汇金/证金、宽基 ETF、科技板块以及证监会/上交所/深交所/巨潮资讯域名相关线索。
- 维护 `data/latest.json` 和 `data/history.json`，网页自动读取并展示新闻、关键词分类、每日历史快照，可导出历史 CSV。
- 不需要 API Key，不需要独立服务器；GitHub Pages 承载静态网页，GitHub Actions 定时任务负责更新数据。

## 部署步骤（一次性）
1. 登录 GitHub，新建一个 **Public repository**，例如 `a-share-national-team-monitor`。
2. 把此项目文件夹中的所有文件上传到仓库根目录，并提交到 `main` 分支。
3. 打开仓库 `Settings → Pages`，在 **Build and deployment / Source** 选择 **GitHub Actions**。
4. 打开 `Actions` 标签页，允许工作流运行；若第一次工作流没有自动启动，手动运行 `Update public market monitor data` 的 `workflow_dispatch`。
5. 等待工作流完成。在 `Settings → Pages` 查看网站网址，形式通常为 `https://<用户名>.github.io/<仓库名>/`。
6. 后续由 GitHub Actions 定时更新 JSON，Pages 部署更新后的静态网站。网页每次打开时读取最新 JSON，页面打开期间每 5 分钟尝试刷新一次。

## 工作流细节
- 计划每 6 小时运行一次（cron 使用 UTC）；GitHub 可能延迟执行。
- 每次抓取多个 Google News RSS 搜索查询，去重后保留最多 180 天的消息、最多 1500 条，并保存每日快照（最长 365 天）。
- RSS 查询可能返回媒体转载、搜索摘要或不完整结果；关键词方向是“线索”，不是交易主体的身份确认。
- 如果仓库是 private，GitHub Pages 与 Actions 的免费额度/可用性取决于账户计划与设置；公共仓库最简单。

## 当前明确未实现的功能
- **没有实时交易流，也不能确认国家队账户当天买卖。**
- **尚未接入可靠的 ETF 每日份额结构化数据**：免费公开接口可变动，需先确认接口稳定性及使用许可后再加。
- 不会把新闻里出现“增持/减持”自动当作国家队实际操作。
- 不会推送手机通知；要通知可另接 Telegram/邮件/Web Push 等服务和密钥。
- 不做投资建议，也不承诺新闻覆盖完整。

## 下一步可以加
1. 经核验后加入 ETF 份额的免费数据源和每日份额变化表。
2. 加入官方公告专用抓取器（若站点公开 RSS/API 或允许抓取），并把原文证据与新闻分开。
3. 加入 GitHub Issues / Telegram / 邮件通知。
4. 增加持仓披露日期、主体、股份数量变化字段。
