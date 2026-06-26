# astrbot_plugin_pymusic

`astrbot_plugin_pymusic` 是一个 AstrBot 插件，可以根据用户提示词，用纯 Python 合成 WAV 音乐，并发送到 QQ 群或私聊。

插件定位是一个可控的 Python 合成器，适合生成电子、8bit、ambient、lofi 等短音乐片段。它不调用外部音乐生成 API，也不会让模型自由执行 Python 代码。

## 功能

- 指令：`/pymusic 时间(秒) 提示词`
- Agent 工具：`generate_python_music`
- 使用 `numpy`、`wave`、`math`、`random` 等纯 Python 方式合成 WAV
- 不接入外部音乐生成 API
- 支持模型把简陋输入先扩写成专业音乐 brief
- 模型只输出结构化 JSON，固定渲染器负责生成音频
- 仅支持 QQ 个人号适配器和 QQ 官方机器人适配器
- 支持 `voice`、`file`、`auto` 三种发送模式
- 语音模式最长 60 秒，超过 60 秒自动改为文件发送
- 插件硬上限默认 600 秒，可在 WebUI 修改
- WebUI 支持模型下拉选择、模型规划超时、最大时长、默认时长、多样性强度、波形级无缝循环、默认发送模式、采样率、是否保留历史 WAV

## 支持平台

当前仅支持：

- `aiocqhttp`
- `qq_official`
- `qq_official_webhook`

其他平台会直接提示不支持，不会继续生成音频。

## 使用方法

```text
/pymusic 20 8bit 夜晚城市 可循环
/pymusic 30 lofi 雨天 文件
/pymusic 45 ambient 星空 慢一点
```

`/pymusic` 后面的第一个参数是生成时长，单位秒；后面的剩余文本会被当作音乐提示词。`20` 和 `20秒` 都可以。

## 生成流程

```text
用户输入
-> PromptBrief / enriched_prompt
-> MusicSpec
-> RenderPlan
-> 纯 Python 渲染 WAV
-> QQ 发送
```

如果用户输入很简略，例如“来点适合晚上写代码的音乐”，插件会先让模型扩写成更专业的音乐描述，补全风格、场景、速度感、乐器、节奏、和声、旋律、纹理、效果和混音方向，再进入结构化编曲流程。

## 渲染器多样性

`RenderPlan` 现在会真正驱动渲染器分支，而不是只影响少量效果参数。

可用编曲分支包括：

- 和弦进行：`i-VI-III-VII`、`i-iv-V-i`、`I-V-vi-IV`、`ii-V-I`、`modal_drone`
- 鼓组：`lofi_swing`、`8bit_arpeggio_beat`、`ambient_no_drums`、`breakbeat`、`minimal_techno`
- 贝斯：`root_octave`、`warm_roots`、`acid_bass`、`sub_drone`、`syncopated_pulse`
- 旋律：`arpeggio`、`call_response`、`pentatonic`、`stepwise`、`random_walk`、`motif_variation`
- 音色：`chip_lead`、`warm_keys`、`pluck`、`pad`、`bell`、`acid_bass`、`sub_drone`、`warm_bass`
- 纹理：`vinyl`、`tape`、`air`、`space`、`none`

模型只能选择这些白名单里的结构化字段。未知值会被渲染器自动回退到本地默认值。

长音频会按内部段落结构渲染：非循环音频会尽量形成 intro、A 段、B 段、break、outro；循环音频会避免明显开头和结尾，改用更适合首尾衔接的 A/B/A 结构。鼓组会在乐句末尾和段落切换前加入轻量过门。

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

- `max_duration_sec`：最大生成时长，默认 600 秒
- `default_duration_sec`：默认生成时长，默认 20 秒
- `music_provider_id`：音乐规划模型，下拉选择；留空则跟随当前会话默认模型
- `model_call_timeout_sec`：模型规划单步超时，默认 12 秒；插件不设上限，低于 1 秒会按 1 秒处理。Agent tool 外层可能仍有 60 秒总超时
- `waveform_loopable`：是否启用波形级无缝循环，默认开启
- `diversity_level`：多样性强度，0=稳定，1=均衡，2=大胆
- `default_send_mode`：默认发送模式，默认 `auto`
- `sample_rate`：输出采样率，默认 44100
- `keep_history_wav`：是否保留历史 WAV，默认关闭

## 发送策略

- `voice`：尝试发送 WAV 语音
- `file`：发送 WAV 文件
- `auto`：先尝试语音，失败后自动发送文件
- 超过 60 秒的音频会直接按文件发送
- `auto` 下如果语音发送接口长时间无响应，会自动改发文件

QQ 语音发送能力取决于当前适配器。如果某个 QQ 适配器不支持直接发送 WAV 语音，建议使用 `auto` 或 `file`。

## 可循环音频

开启 `waveform_loopable` 后，插件会尽量做到波形级无缝：

- 自动对齐到小节长度
- 节奏、贝斯、和弦、旋律使用周期化乐句
- 首尾做 crossfade
- 自动评估首尾边界，选择更平滑的 crossfade 长度
- delay / reverb 尾巴尽量回卷到开头

这会让循环更自然，但也可能让混响尾巴更短。

## 安装

把本目录放入 AstrBot 的插件目录后，在 AstrBot 中安装依赖并启用插件。

依赖：

```text
numpy>=1.23
```

插件仓库：

```text
https://github.com/blueraina/astrbot_plugin_pymusic
```

## 注意

- 插件不会调用外部音乐生成服务。
- 模型只输出 `PromptBrief`、`MusicSpec` 和 `RenderPlan` 结构化 JSON，不执行 Python。
- 渲染器会对模型输出做范围裁剪和兜底处理。
- 如果模型不可用或 JSON 格式异常，插件会使用本地关键词规则生成兜底音乐。
