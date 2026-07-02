# 贡献指南

欢迎为 StockGlance 贡献代码、报告问题或提出建议。

> 本项目完全由 [GenericAgent](https://github.com/lsdefine/GenericAgent) 自主开发，是其推广子项目。

## 报告问题

在 [Issues](https://github.com/JeanStory/stock-glance/issues) 提交前，请尽量包含：

- 操作系统与版本（如 Windows 11 23H2）
- Python 版本（`python --version`）
- 复现步骤与预期 / 实际行为
- 相关日志：用 `-v` 参数启动可输出调试日志

## 开发环境

```bash
git clone https://github.com/JeanStory/stock-glance.git
cd stock-glance
pip install -r requirements.txt

# 从源码运行
python -m stock_glance -v
```

本项目主要面向 Windows：系统托盘、全局快捷键、任务栏嵌入、开机自启依赖 `pywin32`。
非 Windows 平台上这些能力会自动软降级，核心行情拉取（`requests`）与配置逻辑仍可跨平台运行与测试。

## 提交 Pull Request

1. Fork 仓库并从 `main` 切出特性分支：`git checkout -b feat/your-feature`
2. 保持改动聚焦单一主题，提交信息清晰（推荐 [Conventional Commits](https://www.conventionalcommits.org/) 风格，如 `feat:` / `fix:` / `docs:`）。
3. 若新增或修改了配置字段，请同步更新 `README.md` 的配置表与 `config.example.json`。
4. 若改动依赖，请同步更新 `requirements.txt`、`pyproject.toml` 与 `stock-glance.spec` 三处。
5. 推送分支并发起 PR，说明变更内容与测试情况。

## 代码风格

- 遵循 PEP 8，优先保持与现有代码一致的风格。
- 平台相关的 import（如 `win32gui`）放在函数内部并做异常降级，不要在模块顶层硬性依赖，以免破坏跨平台导入。

## 许可

提交贡献即表示你同意以本项目的 [MIT 许可证](LICENSE) 授权你的代码。
