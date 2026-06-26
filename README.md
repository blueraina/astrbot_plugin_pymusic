# astrbot_plugin_pymusic

`astrbot_plugin_pymusic` 是一个 AstrBot 插件，可以根据用户提示词，用纯 Python 合成 WAV 音乐，并发送到 QQ 群或私聊。

本插件定位为一个可控的 Python 合成器，适合生成电子、8bit、ambient、lofi 等风格的短音乐片段。它不调用外部音乐生成 API，也不会让模型自由执行 Python 代码。

## 功能

- 指令：`/pymusic <音乐提示词>`
- Agent 工具：`generate_python_music`
- 使用 `numpy`、`wave`、`math`、`random` 等纯 Python 方式合成 WAV
- 不接入外部音乐生成 API
- 适合电子、8bit、ambient、lofi 风格
- 模型只负责输出结构化 JSON，插件负责校验并用固定渲染器生成音频
- 仅支持 QQ 个人号适配器和 QQ 官方机器人适配器
- 支持 `voice`、`file`、`auto` 三种发送模式
- 语音模式最长 60 秒，超过 60 秒会自动改为文件发送
- 支持 WebUI 配置模型、最大时长、默认时长、波形级无缝循环、默认发送模式、采样率、是否保留历史 WAV

## 支持平台

当前仅支持：

- `aiocqhttp`
- `qq_official`
- `qq_official_webhook`

其他平台会直接提示不支持，不会继续生成音频。

## 使用方法

```text
/pymusic 20秒 8bit 夜晚城市 可循环
/pymusic lofi 雨天 30秒 文件
/pymusic ambient 星空 慢一点
```

`/pymusic` 后面的剩余文本会被当作音乐提示词。

## Agent 工具

工具名：`generate_python_music`

参数：

- `prompt`：音乐提示词
- `duration`：生成时长，单位秒
- `loopable`：是否尽量生成可循环音频
- `send_mode`：发送模式，可选 `voice`、`file`、`auto`

工具说明中已限制：只有当用户明确要求“生成音乐 / 发音乐 / 来一段音乐 / 用音乐表达”时，模型才应该调用这个工具。

## WebUI 配置

插件提供以下配置项：

- `max_duration_sec`：最大生成时长，默认 180 秒
- `default_duration_sec`：默认生成时长，默认 20 秒
- `music_provider_id`：音乐规划模型，下拉选择；留空则跟随当前会话默认模型
- `waveform_loopable`：是否启用波形级无缝循环，默认开启
- `default_send_mode`：默认发送模式，默认 `auto`
- `sample_rate`：输出采样率，默认 44100
- `keep_history_wav`：是否保留历史 WAV，默认关闭

## 发送策略

- `voice`：尝试发送 WAV 语音
- `file`：发送 WAV 文件
- `auto`：先尝试语音，失败后自动发送文件
- 超过 60 秒的音频会直接按文件发送

QQ 语音发送能力取决于当前适配器。如果某个 QQ 适配器不支持直接发送 WAV 语音，建议使用 `auto` 或 `file`。

## 可循环音频

开启 `waveform_loopable` 后，插件会尽量做到波形级无缝：

- 自动对齐到小节长度
- 节奏、贝斯、和弦、旋律使用周期化乐句
- 首尾做 crossfade
- delay / reverb 尾巴尽量回卷到开头

这会让循环更自然，但也可能让混响尾巴更短。

## 安装

将本目录放入 AstrBot 的插件目录后，在 AstrBot 中安装依赖并启用插件。

依赖：

```text
numpy>=1.23
```

## 注意

- 插件不会调用外部音乐生成服务。
- 模型只输出 `MusicSpec` 和 `RenderPlan` 结构化 JSON，不执行 Python。
- 渲染器会对模型输出做范围裁剪和兜底处理。
- 如果模型不可用或 JSON 格式异常，插件会使用本地关键词规则生成兜底音乐。
