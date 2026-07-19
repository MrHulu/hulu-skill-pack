# Hulu Skill Pack

中文 | [English](README.en.md)

<img src="assets/hulu-skill-pack-cover.png" width="240" alt="Hulu Skill Pack 封面">

一组面向 AI 编程助手的个人原创 Skill 封装。每个 Skill 都是可独立使用的
公开起点：保留必要的能力和清晰的使用边界，方便直接阅读、安装和二次创作。

所有 Skill 位于 `skills/<skill-name>/`，使用前请先阅读对应的 `SKILL.md`。

## 当前 Skill

| Skill | 说明 |
| --- | --- |
| [Hulu Motion Kit](skills/hulu-motion-kit/) | 基于 Hyperframes 的视频创作封装，提供项目初始化、基础配音与硬字幕。 |

## 使用方式

```bash
git clone https://github.com/MrHulu/hulu-skill-pack.git
```

打开目标 Skill 的 `SKILL.md`，或将整个 Skill 文件夹复制到你的 AI 编程助手
Skill 目录中即可使用。

## 设计原则

- 每个包独立、清楚、可验证。
- 对第三方能力保持透明标注；封装与工作流本身由本仓库维护。
- 公开内容适合作为可靠起点；复杂项目仍应依据自己的目标完成创作、审阅和交付。

## 贡献

欢迎提交同样独立、清晰且适合公开发布的 Skill。详见
[CONTRIBUTING.md](CONTRIBUTING.md)。
