# astrbot_plugin_pymusic

`astrbot_plugin_pymusic` 是一个 AstrBot 插件，可以根据用户提示词，用纯 Python 合成 WAV 音乐，并发送到 QQ 群或私聊。

插件定位是一个可控的 Python 合成器，适合生成电子、8bit、ambient、lofi 等短音乐片段。它不调用外部音乐生成 API；模型会生成受限的 Python 合成函数，插件在子进程中执行并生成 WAV。

## 功能

- 指令：`/pymusic 时间(秒) 提示词`
- Agent 工具：`generate_python_music`
- 使用 `numpy`、`wave`、`math`、`random` 等纯 Python 方式合成 WAV
- 不接入外部音乐生成 API
- 支持模型把简陋输入先扩写成专业音乐 brief
- 内置“音乐技法知识层”，会为 brief 和代码生成选择合适的电子乐/8bit/ambient/lofi 风格、作曲算法、合成算法和效果手法
- AI 直接生成 Python 合成代码，输出 WAV；固定渲染器作为失败兜底
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
-> 选择音乐技法卡片
-> AI 生成 Python 合成函数
-> 受限子进程执行并渲染 WAV
-> 失败时回退到固定 Python 渲染器
-> QQ 发送
```

如果用户输入很简略，例如“来点适合晚上写代码的音乐”，插件会先让模型扩写成更专业的音乐描述，补全风格、场景、速度感、乐器、节奏、和声、旋律、纹理、效果、混音和段落方向，再让模型基于这个 brief 编写 Python 合成函数。

## 音乐技法知识层

插件内置的是“技法卡片”，不是固定曲谱模板，也不是固定代码模板。它们只告诉模型某类音乐常见的合成和编曲语法，具体旋律、节奏、和弦、音色公式和段落安排仍由模型每次根据提示词原创。

当前技法卡片分成几类：

结构/编曲：

- `arrangement_motifs`：多动机、A/B 段、问答句、每 4/8 小节变化
- `loopable_ab_cycle`：适合无缝循环的周期化 A/B 或 A/B/A 结构
- `tension_release`：用密度、音区、鼓组和音色变化做小型推进与释放

作曲算法：

- `euclidean_drums`：参考 Toussaint 2005 的 Euclidean rhythm，让鼓点分布更自然
- `markov_melody`：参考 Pachet 2003 的 Continuator/Markov 思路，让旋律有“接话感”
- `motif_recombine`：把短动机重组、转位、移调或拉伸成 B 段
- `lsystem_phrase`：参考 Prusinkiewicz 1986，用简单重写规则把短乐句扩展成长段落
- `pingpong_arpeggio`：正向/反向琶音，适合 8bit 和电子
- `random_walk_melody`：有边界的音阶随机游走，适合 ambient/lofi
- `stutter_pattern`：短重复和 glitch 过门
- `phase_pattern`：相位错开的极简重复型

合成算法：

- `fm_synthesis`：参考 Chowning 1973 的 FM 合成，做铃声、冰晶、金属、FM bass
- `karplus_pluck`：参考 Karplus-Strong 1983，做拨弦、竖琴、短促共振音
- `bandlimited_saw`：参考 Stilson & Smith 1996 的 band-limited waveform 思路，降低锯齿波刺耳感
- `bandlimited_square`：参考 Stilson & Smith 1996 的 band-limited waveform 思路，降低方波/脉冲波刺耳感
- `additive_pad`：加法合成 pad，适合 ambient 铺底
- `subtractive_bass`：用谐波混合/低通感运动模拟 subtractive bass
- `noise_drum_resonator`：噪声鼓、hat、snare、click、tom
- `wavetable_lead`：混合基础波形并随时间改变亮度的 lead
- `noise_texture`：风声、雨声、磁带底噪、柔和噪声打击乐

效果：

- `schroeder_reverb`：参考 Schroeder 1962 混响，轻量 comb/allpass 空间感
- `moorer_reverb`：参考 Moorer 1979 混响，early reflections + 更平滑尾巴

风格卡：

- `ambient_pad`
- `8bit_chiptune`
- `lofi_hiphop`
- `acid_bass`

例如用户只写“寒冬”，模型增强 prompt 时可能会选择 `ambient_pad`、`random_walk_melody`、`fm_synthesis`、`additive_pad`、`schroeder_reverb`、`noise_texture`；用户写“像素冒险”，则更可能选择 `8bit_chiptune`、`pingpong_arpeggio`、`euclidean_drums`、`bandlimited_square`、`noise_drum_resonator`。

## AI Python 渲染

主路径会让模型生成一个 `render(duration, sample_rate, loopable)` 函数。函数必须返回一维 `numpy` float 音频数组，插件负责归一化并写出 WAV。

代码生成提示会要求模型像编曲一样组织音乐，而不是只循环一个短旋律：

- 尽量拆出 drums / rhythmic texture、bass、chords 或 pad、melody 或 lead 等层
- 至少使用两个动机或动机变形
- 使用 A/B、问答句或乐句变奏
- 每 4/8 小节改变音区、节奏、密度、和声、音色、过门或效果运动
- 尽量至少实现一个选中的作曲算法和一个选中的合成算法
- 使用 envelope，并结合 LFO/调制、滤波式音色塑形、FM/additive/wavetable/subtractive、noise percussion、delay/reverb 中的若干手法
- `loopable=True` 时避免一次性 intro/outro，并让乐句周期适合首尾衔接

执行限制：

- 允许使用 `numpy`、`math`、`random`
- 不允许读写文件、访问网络、调用系统命令
- 不允许使用 `os`、`sys`、`subprocess`、`pathlib`、`open`、`eval`、`exec`
- 生成代码会先做 AST 校验，再放到独立 Python 子进程里运行

如果模型不可用、代码校验失败、代码运行超时或没有生成有效 WAV，会自动回退到固定 Python 渲染器。

## 兜底渲染器

兜底渲染器可用编曲分支包括：

- 和弦进行：`i-VI-III-VII`、`i-iv-V-i`、`I-V-vi-IV`、`ii-V-I`、`modal_drone`
- 鼓组：`lofi_swing`、`8bit_arpeggio_beat`、`ambient_no_drums`、`breakbeat`、`minimal_techno`
- 贝斯：`root_octave`、`warm_roots`、`acid_bass`、`sub_drone`、`syncopated_pulse`
- 旋律：`arpeggio`、`call_response`、`pentatonic`、`stepwise`、`random_walk`、`motif_variation`
- 音色：`chip_lead`、`warm_keys`、`pluck`、`pad`、`bell`、`acid_bass`、`sub_drone`、`warm_bass`
- 纹理：`vinyl`、`tape`、`air`、`space`、`none`

固定渲染器仍然使用白名单结构化字段。未知值会被渲染器自动回退到本地默认值。

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
- 模型会输出 `PromptBrief`、`MusicSpec`，并在主路径生成受限 Python 合成代码。
- AI 生成代码会经过 AST 校验和子进程执行限制。
- 如果模型不可用、JSON 格式异常或生成代码失败，插件会使用固定渲染器生成兜底音乐。
