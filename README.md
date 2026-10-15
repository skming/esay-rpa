# Easy RPA

本地桌面端 Web 自动化工具，支持可视化流程、网页采集、表单操作、文件与 Excel 处理、定时任务和 AI 辅助创建。

## 使用说明

- [使用指南](docs/OVERVIEW.md)：创建与运行流程、分页配置、AI 助手和功能边界。

## 技术文档

- [开发指南](docs/TECHNICAL.md)：环境搭建、架构、执行约束、数据目录与验证命令。
- [扩展开发](extension/README.md)：Chrome 扩展启动、连接与两端回归。
- [AI 评测](backend/evals/README.md)：模型行为、真实页面 E2E 与评分规则。
- [桌面端打包](docs/ELECTRON_PACKAGING.md)：构建、签名与发布。
- [本地测试页面](backend/tests/pages/README.md)：页面用途与留出集约定。

## 产品与设计

- [产品原则](PRODUCT.md)
- [视觉规范](DESIGN.md)

使用指南维护操作和功能边界，技术文档维护实现与验证；字段、模型目录和阈值以源码为准，不重复维护完整清单。
