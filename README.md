# JZH乐园 · JZH Land

My friend is a truly fascinating person. He agreed to achieve cyber immortality, so we built a website to preserve his personality and let him live on in the digital world.

一个给朋友们玩的 Flask 小网站，中英双语。主角是以一位朋友为原型的虚构角色「JZH」（已获本人同意）。

A small bilingual (Chinese / English) Flask site made for a group of friends. It stars "JZH", a fictional character inspired by a real friend, with their permission.

## 功能 / Features

- **留言板 / Message board**：发帖、评论、配图、匿名发布；@JZH 会由 AI（DeepSeek，备用 Gemini）以角色口吻回复，也可以私聊。
- **抽卡与对战 / Card draw & battles**：每日抽卡、卡册、组卡组和其他玩家自动对战，排行榜。
- **拓麻JZH / Pixel pet**：养成、换装、打工，托管到牧场交朋友、生下一代；小屋升级。
- **赌场 / Casino**：轮盘、老虎机、21 点、德州扑克（多人房间 + 机器人），只用站内虚拟金币。
- **JZH高速 / JZH Expressway**：three.js 3D 驾驶小游戏。
- **江世战争 / Jiang Wars**：基于 WebSocket（Flask-SocketIO）的实时推塔对战，含 AI 对手。
- **后台 / Admin**：注册审批、全站公告、卡牌管理、抽卡限制。

## 技术栈 / Tech stack

Python · Flask · Flask-SocketIO · SQLite · Pillow · 原生 JavaScript / vanilla JS · three.js · Socket.IO

## 本地运行 / Run locally

```bash
pip install -r requirements.txt
export FLASK_SECRET_KEY=your-random-string   # 换成任意随机字符串
python3 app.py                               # AI 回复需另设 DEEPSEEK_API_KEY（可选）
```

## 说明 / Notes

- 角色的真实人设和语录图片不在仓库里，这里只有虚构示例。/ The real persona and quote images are not in this repo; it only contains fictional samples.
- 界面可切换中文 / English，AI 对话不翻译。/ The UI switches between Chinese and English; AI chat is not translated.

## 素材来源 / Credits

- JZH 立绘 / Character art：用 [AI Horde](https://aihorde.net/) 和 [WAI-illustrious-SDXL](https://civitai.com/models/827184/wai-illustrious-sdxl) 模型生成，用 [rembg](https://github.com/danielgatis/rembg)（`isnet-anime`）抠图。
- 字体 / Font：[Marcellus](https://fonts.google.com/specimen/Marcellus)，SIL Open Font License 1.1（见 `static/fonts/OFL-Marcellus.txt`）。
- [three.js](https://threejs.org/)、[Socket.IO](https://socket.io/)：MIT License。
- 像素小人、小车图标和场景插画为本项目原创 / Pixel sprites, car icons and scene illustrations were made for this project.

## 声明 / Disclaimer

本站内容是朋友间的玩笑和虚构创作，不是对任何真实人物的事实陈述。请勿转载传播，也不要用于攻击、诽谤他人。站内金币均为虚拟道具，不能用真钱购买，也不能兑换现实财物。

This site is a joke among friends and a work of fiction, not a statement of fact about any real person. Please do not repost it or use it to attack or defame anyone. All coins on the site are virtual: they can't be bought with real money or exchanged for anything of real value.
