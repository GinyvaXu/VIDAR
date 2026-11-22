# direction-approved.md

- 项目：VIDAR v0.15.0 界面重设计
- 展示：3 个完整方向（A Clear Glass / B Paper Studio / C Quiet Monochrome），各含任务页、队列页、规范页，共 9 张真实渲染截图。
- 用户第一次选择：`B · Paper Studio（蓝）`
- 用户最终改为：`换成A紫色`
- 结论：采用 **A · Clear Glass**，强调色用品牌紫 `#6C5CE7`；浅色默认 + 深色同等品质。
- 落地约定：Qt/QSS 中不做实时高斯模糊，玻璃感降级为「浅色分层 + 半透明侧栏 + 发丝线 + 唯一浮层阴影」。
- 截图：`design-demos/shots/a-task.png`、`a-queue.png`、`a-spec.png`
