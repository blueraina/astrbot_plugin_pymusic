# astrbot_plugin_pymusic

`astrbot_plugin_pymusic` 是一个 AstrBot 插件，可以根据用户提示词，用纯 Python / NumPy 合成 WAV 音乐，并发送到 QQ 群或私聊。插件不调用外部音乐生成 API，不使用采样包，不引入重型音乐框架；模型仍然只允许输出受限的 `render(duration, sample_rate, loopable)` Python 函数。

v0.4.1 的重点是让生成结果更接近成熟电子乐，而不是简单 loop 或测试音：主路径使用结构化 `CompositionPlan` / `composition_blueprint` 作曲计划，兜底渲染器也会本地生成主题、回答句、A/B 变奏、鼓贝斯配合、段落推进和基础制作处理。同一个简短提示词默认会加入新的 `variation_seed`，减少多次生成同一旋律的问题；需要复现时可在 WebUI 开启确定性模式。

## 功能

- 指令：`/pymusic 时间(秒) 提示词`
- Agent 工具：`generate_python_music`
- 仅依赖 `numpy` 和 Python 标准库生成 WAV
- 不接入外部音乐生成 API
- 保留 AI 直接写受限 Python `render()` 的主路径
- AI 生成代码继续经过 AST 校验和子进程沙箱执行
- 模型失败、代码校验失败、执行失败或未生成有效 WAV 时，自动回退到固定 `PythonMusicRenderer`
- 仅支持 QQ 个人号适配器和 QQ 官方机器人适配器
- 支持 `voice`、`file`、`auto` 三种发送模式
- 语音模式最长 60 秒，超过 60 秒自动改为文件发送
- 插件硬上限默认 600 秒，可在 WebUI 修改
- WebUI 支持模型选择、模型调用超时、最大时长、默认时长、多样性强度、确定性模式、每次生成变化强度、波形级无缝循环、默认发送模式、采样率、是否保留历史 WAV

## 支持平台

当前仅支持：

- `aiocqhttp`
- `qq_official`
- `qq_official_webhook`

其他平台会直接提示不支持，不会继续生成音频。

## 使用方法

```text
/pymusic 20 8bit 夜晚城市 可循环
/pymusic 30 寒冬 melodic techno
/pymusic 45 ambient 星空 慢一点
/pymusic 60 synthwave 霓虹公路 文件
/pymusic 40 lofi 雨天咖啡馆
```

`/pymusic` 后面的第一个参数是生成时长，单位秒；后面的剩余文本会被当作音乐提示词。`20` 和 `20秒` 都可以。

提示词里包含 `文件` / `file` 会优先按文件发送；包含 `语音` / `voice` 会优先按语音发送；包含 `循环` / `可循环` / `loop` 会启用循环倾向。

## v0.4.1 生成流程

```text
用户输入
-> PromptBrief / enriched_prompt
-> MusicSpec
-> 选择音乐技法卡片
-> 生成 variation_seed / variation_strength
-> 本地 CompositionPlan / composition_blueprint 结构化作曲计划
   - section timeline
   - chord progression
   - call motif / response motif / B variation
   - chord-tone targeting
   - bass-kick relationship
   - drum groove / fills
   - sidechain / filter / riser / delay / reverb automation
-> AI 生成受限 Python render(duration, sample_rate, loopable)
-> AST 校验
-> 子进程沙箱执行并渲染 WAV
-> 失败时回退到固定 PythonMusicRenderer
-> QQ 发送
```

和 v0.3.x 相比，AI 不再只拿到风格描述和技法卡片，而是会拿到明确的 `composition_blueprint`，并兼容旧名 `structured_composer_plan`。这个计划包含主题动机、回答句、B 段变奏、和弦进行、鼓组 step、贝斯 pattern、段落能量曲线和制作自动化。代码生成提示会要求模型使用这些结构，而不是在 `render()` 里临时写一个短数组并从头循环到尾。

默认情况下，插件会为每次生成创建新的 `variation_seed`，并把它写入 `composition_blueprint` 以及 AI Python 渲染提示。这样 `/pymusic 30 lofi 咖啡馆 放松` 这类短提示词多次生成时，会保留同一风格意图，但主题轮廓、回答句、贝斯重音、鼓 fill、自动化和效果时机可以变化。若开启 `deterministic_mode` 或把 `variation_strength` 设为 0，同提示词会尽量回到稳定可复现的结果。

## 音乐质量改造点

### 旋律与主题

- 本地生成 call motif、response motif、B variation。
- A/B 段共享动机 DNA，但节奏、音区或轮廓不同。
- 强拍优先落在 chord tones，弱拍允许 passing / neighbor notes。
- 30 秒内也会有 hook、build、mini drop、tail 等微型结构。
- 60 秒以上会生成 intro、A、build、B/drop、break、return/final drop 等更明显段落。

### 贝斯、鼓与和声

- 贝斯 pattern 会参考当前和弦根音、五音、八度和接近音。
- 贝斯会避开 kick transient，并用 offbeat / syncopated note 回答 kick。
- 鼓组有 kick、snare/clap、closed hat、open hat、ghost/fill 策略。
- lofi 使用 swing；techno/house/trance 使用更稳定的四拍和 offbeat hat；breakbeat 使用 broken kick/snare；ambient 可弱鼓，但会保留脉冲或纹理运动。

### 电子乐制作感

- sidechain ducking
- filter / harmonic brightness sweep
- riser / downlifter
- phrase-end fill / stutter
- delay throw
- early-reflection style reverb
- soft saturation
- vinyl / tape / air / space noise texture
- loopable 模式下做首尾 crossfade，尽量避免明显 one-shot intro/outro

## 音乐技法知识层

插件内置“技法卡片”，用于给 PromptBrief、MusicSpec、CompositionPlan 和 AI Python 渲染提示提供作曲/合成/制作语法。卡片不是固定曲谱模板，也不是固定代码模板。

主要类别包括：

- 结构/编曲：`arrangement_motifs`、`micro_arrangement`、`loopable_ab_cycle`、`tension_release`
- 作曲：`call_response_theme`、`chord_tone_targeting`、`bass_kick_lock`、`euclidean_drums`、`markov_melody`、`motif_recombine`、`lsystem_phrase`、`pingpong_arpeggio`、`random_walk_melody`、`stutter_pattern`、`phase_pattern`
- 合成：`fm_synthesis`、`karplus_pluck`、`bandlimited_saw`、`bandlimited_square`、`additive_pad`、`subtractive_bass`、`noise_drum_resonator`、`wavetable_lead`、`noise_texture`
- 效果/制作：`sidechain_ducking`、`riser_downlifter`、`schroeder_reverb`、`moorer_reverb`
- 风格：`ambient_pad`、`ambient_techno`、`8bit_chiptune`、`lofi_hiphop`、`melodic_techno`、`synthwave`、`trance`、`house_groove`、`breakbeat`、`acid_bass`

## AI Python 渲染限制

AI 主路径仍然只允许输出一个函数：

```python
def render(duration, sample_rate, loopable):
    ...
    return audio
```

要求：

- 返回一维 `numpy` float 音频数组。
- 允许导入 `numpy`、`math`、`random`。
- 禁止读写文件、访问网络、调用系统命令。
- 禁止 `os`、`sys`、`subprocess`、`pathlib`、`open`、`eval`、`exec`、`__import__` 等危险能力。
- 禁止外部采样和外部音乐生成 API。
- 生成代码会先做 AST 校验，再写入临时代码文件，由独立 Python 子进程加载并渲染 WAV。

v0.4.1 的代码生成提示会明确要求 AI 使用 `composition_blueprint` / `structured_composer_plan`，并实现：

- section timeline
- chord progression
- call / response / B variation
- chord-tone targeting
- bass-kick relationship
- drums accents / fills
- sidechain ducking
- filter sweep 或 harmonic brightness motion
- riser/downlifter 或 phrase fill
- delay/reverb/soft saturation

## 兜底渲染器

固定 `PythonMusicRenderer` 不再只是保守模板，而是会消费本地 `CompositionPlan`：

- 根据提示词与 `MusicSpec` 选择 style profile。
- 生成 chord progression、section timeline、call motif、response motif、B variation。
- 渲染独立的 drums、bass、chords/pad、lead、texture layer。
- 使用 kick/snare/hat/open-hat/fill、bass offbeat、pad/stab、lead/counter-arp 等事件。
- 对非鼓层应用 sidechain ducking。
- 添加 delay、reverb、noise texture、transition FX 和 soft saturation。
- `loopable=True` 时对首尾做 crossfade。

内置兜底风格覆盖：

- ambient
- ambient techno
- lofi
- 8bit/chiptune
- melodic techno
- acid techno
- synthwave
- trance
- house
- breakbeat
- general electronic

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
- `model_call_timeout_sec`：模型规划单步超时，默认 12 秒；低于 1 秒会按 1 秒处理
- `waveform_loopable`：是否启用波形级无缝循环，默认开启
- `diversity_level`：多样性强度，0=稳定，1=均衡，2=大胆
- `deterministic_mode`：固定同提示词结果，默认关闭；开启后同提示词和同参数尽量复现
- `variation_strength`：每次生成变化强度，0=固定核心旋律，1=轻微变化，2=明显变化，3=更大胆变化
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
- AI 代码沙箱仍然只开放 `numpy`、`math`、`random`。
- v0.4.1 使用的是本地结构化作曲计划、变化种子和更强的兜底渲染，不会让 AI 获得任意 Python 执行能力。
- 如果模型不可用、JSON 格式异常或生成代码失败，插件会使用固定渲染器生成兜底音乐。
