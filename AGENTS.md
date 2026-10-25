# nl-plugin-tablet_kiosk 开发指南

<!-- nl-plugin guide: start (refreshed by `nl-plugin sync`; edit below the end marker) -->

本仓库是 Natural Land 平台的一个**插件**。插件由三部分组成：

- 一个独立的后端进程；
- 显示在平台页面里的界面；
- （可选）给平台 Agent 用的只读工具和知识。

插件由我们自己的开发者（多数是 code agent）开发。平台用进程、数据库权限和加载方式把插件隔离开：**插件出任何问题都不能影响平台运行，也不能改动已经记录的数据。** 下面的规则大多由平台或 `nl-plugin verify` 机械地检查，不要试图绕开。

设计背景见平台仓库 `docs/plan/plugin-system.md`；平台仓库的位置写在 `.platform/PLATFORM.json` 的 `path` 里。

## 1. 硬性规则

违反其中任何一条，`nl-plugin verify` 就不会通过，或者平台在运行时直接拒绝。

1. **只改本仓库。** 不修改平台仓库的任何文件，可以读。需要平台提供新的插槽、新的 SDK 接口或新的数据时，把需求写清楚交给人，不要自己改平台。
2. **只写自己的 schema。** 表和数据只放在 `plugin_tablet_kiosk` 里，由 `src/nl_plugin_tablet_kiosk/migrations/NNNN_*.sql` 创建。平台的表只读，并且只通过 `plugin.platform` 读（§4.3）。插件的数据库角色本来就改不了平台的表。
3. **不对平台的表建外键。** 只存平台的 id；项目或记录被删除后，由插件自己清理（§4.4）。
4. **MQTT 只发 `plugin.json` 里 `mqtt.publish` 声明的主题。** 不发灯光（`/light/…`）和 RFID 的主题。broker 是匿名的、不拦截，验证时会监听所有主题来检查。
5. **后端只监听 `127.0.0.1:$NL_PLUGIN_PORT`。** 每个请求都要带插件令牌，SDK 已经处理好了，不要另开端口，也不要关掉令牌检查。
6. **界面不带共享库的副本，也不影响平台页面。**
   - Vite 配置只用 `nlPlugin()`，不要自己写 federation 配置。
   - CSS 只用模板的 `plugin.css` 写法：没有 preflight，不写 `:root` 变量，不写全局选择器（`body`、`*`、标签名）。
   - 不改 `window`、`document` 上的全局对象，不注册 service worker，不 import 平台源码，平台的东西只通过 `sdk` 属性拿。
7. **控制设备时按"以状态为准"的方式写**（§4.6）。插件随时可能被杀掉（内存超限、升级、崩溃），所以设备的安全状态必须由固件保证，例如阀门的最长开启时间、失去心跳后自动关闭。
8. **不阻塞。**
   - 接口和 Agent 工具要在几秒内返回（Agent 工具默认 20 s 超时）。
   - 长任务放进后台线程，数量要有上限。
   - Agent 工具只读：不写数据，不控制设备。
9. **资源有上限。** 内存不能超过 `backend.memory_max`，超了进程会被系统杀掉。单张表会持续变大的（比如逐帧的数据），在本文件的"本插件说明"里写出估算的增长速度。
10. **版本号要跟着改。** 改了 `plugin.json`、迁移、接口或界面就升 `version`（语义化版本）。已经发布过的迁移文件不再修改，只新增。

## 2. 仓库结构

```text
plugin.json                     清单：id、版本、后端命令、界面插槽、schema、MQTT 主题、Agent 摘要（字段见 §4.1）
pyproject.toml, uv.lock         后端 Python 包（提交 uv.lock）
src/nl_plugin_tablet_kiosk/
  app.py                        唯一的 `plugin = Plugin(...)`、接口、Agent 工具、main()
  migrations/0001_init.sql      本插件 schema 里的表
tests/                          后端测试：test_app.py 不连数据库；test_platform.py 标记 platform，由 verify 在真数据库上运行
web/                            界面：Vite + nlPlugin()，每个插槽一个默认导出的组件（提交 pnpm-lock.yaml）
agent/skills/<名称>/SKILL.md    给 Agent 的知识（目录名 = SKILL.md 里的 name）
.platform/                      平台 SDK 的副本，由 `nl-plugin sync` 生成，不提交
```

## 3. 第一次准备

需要先准备好：平台仓库的一份 checkout（下面用 `$NL_PLATFORM` 表示）、`uv`、`pnpm`，以及验证要用的测试数据库（§6）。

用 `nl-plugin new` 新建的仓库已经同步过，直接装依赖：

```bash
uv sync                                               # 后端环境
(cd web && pnpm install)                              # 界面依赖
```

平台升级后，在本仓库根目录同步一次，再重装后端环境。注意先把本仓库的路径记下来：`cd` 进平台目录之后，`$PWD` 就变成平台目录了。

```bash
PLUGIN="$PWD"
(cd "$NL_PLATFORM" && uv run nl-plugin sync "$PLUGIN")  # 刷新 .platform/ 里的 SDK 和本指南的平台部分
uv sync
```

如果 `.platform/PLATFORM.json` 里的 `ui_api` 或 `db_contract` 变了，说明契约有不兼容的改动，需要按平台的说明改插件。

## 4. 怎么写

### 4.1 `plugin.json`

| 字段 | 说明 |
|---|---|
| `id` | `[a-z][a-z0-9_]{0,31}`。它决定 schema 名 `plugin_<id>`、界面容器名 `nl_plugin_<id>` 和后端命令 `nl-plugin-<id>`，定下后不要改 |
| `version` | 语义化版本，每次发布都要升 |
| `platform` | `ui_api` 和 `db_contract` 照 `.platform/PLATFORM.json` 填写 |
| `backend` | `command` 是 pyproject 里的命令名；`memory_max` 例如 `512M` |
| `ui.contributions` | 每项是 `{slot, module, title}`。`slot` 取 `experiment.tab`（实验页标签）、`record.tab`（记录详情标签）、`live.card`（实时监控卡片）、`system.page`（系统设置里的页面）。`module` 必须和 `web/vite.config.ts` 里 `exposes` 的键一致 |
| `database` | `{"schema": "plugin_<id>"}`，不用数据库就删掉这一项 |
| `mqtt` | `publish` 和 `subscribe` 的主题列表，可以用 `+` 和 `#` |
| `agent` | `summary`：一行中英文说明，会放进 Agent 的系统提示；`skills`：Skills 目录。`tools` 不用手写，构建时从代码里导出 |

### 4.2 后端（`natural_land_plugin_sdk`）

- `plugin = Plugin("tablet_kiosk", migrations=...)`，`main()` 里调用 `plugin.main()`：先执行迁移，再启动服务。开机时插件可能比数据库先启动：连不上数据库时 `main()` 等待重试（最多 10 分钟），迁移本身出错则立即退出。不要自己再写一层启动等待。
- 接口用 `@plugin.app.get/post(...)` 写。界面通过 `sdk.api("路径")` 调用，平台会代理到 `/api/plugins/<id>/路径`。
- 接口出错时 `raise PluginError(状态码, "短代码", "一句话说明")`，界面的 `sdk.api` 会带着这个代码和说明报错。请求体校验失败时 SDK 自动返回 422 `validation_error`，未处理的异常返回 500 `plugin_error`（详情写进日志）。**不要返回 401**：平台会把 401 当作"插件拒绝了平台的令牌"，并显示插件不可用。
- `Depends(plugin.caller)` 可以拿到当前登录的用户（`Caller.user`）。它是 `None` 时，说明请求不是用户通过页面发来的（而是 Agent 或验证工具）；需要记下操作人的写操作，此时返回 403。平台目前只有一种账号权限，没有按项目分的权限，插件不需要自己检查"用户能不能看这个项目"。
- 启动时、停止时和定时要做的事：
  - `@plugin.on_startup`：迁移完成后、开始服务前执行一次；
  - `@plugin.on_shutdown`：收到停止信号时执行一次，用来关设备、断开连接；
  - `@plugin.periodic(秒数, first_delay=...)`：在单独的线程里定期执行，单次报错只记日志，下一次照常执行，进程停止时自动停下。每次执行要短，长循环里要检查 `plugin.stopping`。需要马上执行一次时调用 `函数名.wake()`。
- 单实例：`lock = plugin.try_lock("名称")` 拿到锁时返回一个锁对象（`lock.alive()`、`lock.release()`），别的进程持有时返回 `None`。设备控制直接用 `DeviceRunner`（§4.6），它会自己处理锁。
- 依赖：`uv add` 会把版本下限设成当天的最新版。国内主机通过镜像安装，最新版可能还没同步（§7），所以下限按实际需要放宽，例如 `uv add "paho-mqtt>=2.0"`。
- 连数据库用 `with plugin.connect() as conn:`，角色的 `search_path` 就是插件自己的 schema，退出 `with` 时自动提交。插件角色最多 5 条连接：`plugin.connect()` 同时最多 3 条（第 4 个请求最多等 5 秒，然后返回 503 `plugin_busy`），1 条留给单实例锁，1 条留给健康检查。每秒都要读的循环，就在循环里保持一条自己的连接。
- `@plugin.health_check` 用来补充健康检查，函数返回 `None` 表示正常，返回一个短原因（例如 `"mqtt down"`）表示异常；平台和界面会显示这个原因。健康检查只回答"后端本身能不能工作"（数据库、MQTT 服务器连得上）。**设备离线不算不健康**：那是插件的业务状态，在插件自己的接口和界面上显示；否则没接设备的开发机上插件就装不上（安装要等健康检查通过）。
- 环境变量由平台写入，在 `plugin.settings` 里读取：`NL_PLUGIN_DATA_DIR`（插件自己的文件目录）、`NL_MQTT_HOST` 和 `NL_MQTT_PORT` 等。不要在代码里写死路径或地址。

### 4.3 读平台数据（db_contract 1）

只用 `plugin.platform` 提供的这几个方法：

| 方法 | 返回 |
|---|---|
| `active_run()` | 正在录制的那条记录（全系统最多一条），没有时为 `None`：`run_id, state, started_at, experiment_id, project_id` |
| `run(run_id)` | `run_id, state, started_at, ended_at, experiment_id, project_id`，或 `None` |
| `last_ended_run()` | 最近开始、并且已经结束的那条记录（字段同 `run`），或 `None`，用于"录制结束后回到安全状态"（§4.6） |
| `experiment(experiment_id)` | `id, project_id, name`，或 `None` |
| `run_mice(run_id)` | 这条记录勾选的小鼠：`id, alias, card_id, color`（颜色 1–8），按代称排序 |
| `existing_projects(ids)` / `existing_experiments(ids)` / `existing_runs(run_ids)` | 其中仍然存在的那些（集合），一次查询，用于清理（§4.4） |

- id 是整数；`run_id` 是字符串，格式 `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`。时间都是带时区的 `datetime`。
- `state` 的取值：`starting`、`running`、`stopping` 表示正在录制（SDK 里的 `ACTIVE_STATES`）；`stopped`、`failed`、`lost` 表示已结束。
- `ended_at` 在录制中是 `None`；`failed` 或 `lost` 的记录也可能没有结束时间，也是 `None`。所以判断"是否还在录制"要看 `state`，不要看 `ended_at`。
- 每次调用都会从 `plugin.connect()` 取一条连接。每秒都要读的循环，用 `plugin.platform.using(conn)` 复用自己保持的那条连接。

它们只用这几张表的这几个列：`projects`、`experiments`、`recorders`、`recorder_runs`、`experiment_mice`、`run_mice`。平台承诺这些只加不改。其他表的格式平台可以随时改，读了以后插件会在某次平台升级后坏掉。需要别的数据就提需求（规则 1）。

### 4.4 删除与清理

平台删除项目、实验或记录时不会通知插件，也不会等插件。插件存的只是 id，所以要用 `@plugin.periodic` 定期（例如每小时）检查自己表里的 id 在平台上还在不在（`plugin.platform.existing_*`，一次查一批），不在的就删掉。千万不要因为暂时查不到就删：`existing_*` 在数据库连不上时会抛出异常，这时什么都不删，等下一次再查。模板里的 `remove_notes_of_deleted_experiments` 就是这样写的。

### 4.5 Agent 工具与 Skills

- 工具用 `@plugin.agent_tool(name, label={zh-CN, en}, description=..., parameters=..., examples=(...))` 声明。`parameters` 只能用受限的 JSON Schema：object 必须写 `additionalProperties: false`，可用的类型有 string、integer、number、boolean、array。
- 处理函数的写法是 `handler(ctx, **args)`，返回能转成 JSON 的数据。
  - 项目由 Agent 绑定，不能作为参数；要用 `ctx.require_run(...)` 或 `ctx.require_experiment(...)` 确认对象属于这个项目。
  - 预期内的失败要 `raise ToolError("短代码", "给模型看的一句话")`。
  - 返回结果不能超过 256 KiB，数据多时要提供筛选参数。
- **`examples` 必须写**：验证工具会用每个例子调用一次工具，没有例子就不通过。例子里可以写 `"$project_id"`、`"$experiment_id"`、`"$run_id"`，验证时会换成测试数据的 id（§6）。工具正常返回数据，或者返回你自己的 `ToolError`（例如测试数据里没有内容时的 `no_data`），都算通过；返回 `plugin_error`（未处理的异常）、`invalid_arguments`、超时或者太大，都算不通过。
- 在 Agent 里，工具名会变成 `tablet_kiosk__<name>`。
- Skills 放在 `agent/skills/<名称>/SKILL.md`，开头是 front matter：`name` 必须和所在目录名相同，只能用小写字母、数字和连字符（不能有下划线，例如目录和名字都是 `plugin-water-log`）；`description` 写一句什么时候该读它，不超过 1024 个字符（超出的部分 Agent 看不到）。Agent 在 `/skills/plugins/<插件 id>/` 下读到它们。正文写清楚：插件的数据是什么意思、单位、什么时候用哪个工具、哪些情况不能下结论。

### 4.6 控制设备的插件

用 SDK 的 `natural_land_plugin_sdk.device`，不要自己从头写。它和平台的灯光节律（`src/mouse_realtime_pipeline/console/workers/lights.py`，`docs/plan/light-schedule.md` §3.5、§4）是同一套做法：

- **以状态为准**：每秒算一次设备"应该是什么状态"，和设备最后确认的状态比较，不一致就发命令。插件重启、设备离线、错过切换时刻，都会自动纠正。
- **先读后发**：设备回答了第一次轮询、知道它当前的状态之前，不发任何命令。
- **确认和重发**：同一时间只有一条未确认的命令；重发沿用同一个 id，在第 5、10、30 秒重发，之后每 30 秒一次；第三次重发时记一条 `unconfirmed` 事件；设备拒绝（`ok=False`）后隔 30 秒再试；设备离线时命令保持待确认，等它回来再发。
- **在线检测**：每 10 秒发一次轮询；回复超时 3 秒；连续 3 次没有回复判为离线（`offline` 事件），收到任何消息就恢复在线（`online` 事件）。没有录制时也照常轮询：轮询只读状态，不改变设备。
- **别处改了设备的状态**：设备报告的状态和已知的不同时，以设备为准（`resync` 事件，每分钟最多一条），下一秒再按应有的状态纠正。
- **单实例**：`DeviceRunner` 用一条专用连接持有 advisory lock，拿不到锁就只等待，每 10 秒再试一次。

写法：

```python
from natural_land_plugin_sdk import Controller, DeviceRunner, MqttLink, Reply

def desired():                      # 纯函数思路：只读、不发消息、很快返回；None = 现在不发命令
    run = plugin.platform.using(state_conn).active_run()
    if run:                         # 录制中：按这个实验当前的设置
        return target_for(run, now())
    last = plugin.platform.using(state_conn).last_ended_run()
    if last and store.safe_confirmed_after != last["run_id"]:
        return "off"                # 录制结束后：回到安全状态，确认一次
    return None                     # 已确认：不再发命令

runner = DeviceRunner(
    plugin, controller=Controller(name="heatpad"),
    link=MqttLink(f"nl-plugin-{plugin.id}", plugin.settings.mqtt_host, plugin.settings.mqtt_port, ["/heatpad/+/response"]),
    desired=desired,
    set_command=lambda rid, target: (f"/heatpad/{board}/config", {"id": rid, "set": target}),
    get_command=lambda rid: (f"/heatpad/{board}/config", {"id": rid, "get": True}),
    parse=parse_reply,              # (topic, payload) -> Reply(request_id, ok, state, error) 或 None
    on_event=store_event,           # 写进自己的事件表；在这里记下"安全状态已确认"
)
```

- `DeviceRunner` 随后端启动和停止。设置改了以后调用 `runner.wake()`，马上重新判断，不用等下一秒。`runner.status()` 给出 online、state、pending 等，放进插件自己的状态接口，在界面上显示。
- **录制结束后回到安全状态**：`last_ended_run()` 是最近一条已结束的记录。把"这条记录之后已确认安全"记在自己的表里（像上面的 `safe_confirmed_after`）；插件在整条记录期间都没运行，也能在启动后补上。
- **录制开始**：平台没有"录制开始"的通知，只能轮询 `active_run()`（每秒一次没问题，用 `plugin.platform.using(conn)` 复用一条连接）。每条记录开始时用的是哪个设置版本，在第一次看到这条记录时记下；如果没看到开始，就按"开始时间之前最后保存的版本"推算并标明是推算的。
- **设置按版本保存**：每次保存生成一个新版本，旧版本不改。
- **停止时**：`on_shutdown` 里不要依赖它去关设备。进程可能被直接杀掉，安全状态由设备固件保证（规则 7）。
- **MQTT**：在本仓库 `uv add paho-mqtt`。服务器地址是 `plugin.settings.mqtt_host` / `mqtt_port`，安装器从主机配置的 RFID MQTT 服务器（`rfid.mqtt_broker`）取来。没有配置时是 `None`，这时在状态接口里说明"没有配置 MQTT"，不要让健康检查失败。
- **测试**：
  - "应该是什么状态"写成纯函数（输入时间、记录、设置），用单元测试覆盖跨午夜、时区、录制中和空闲。
  - `Controller` 可以直接驱动：`step(now, desired)` 和 `reply(now, Reply(...))`，用假的时间。
  - 在 `platform` 测试里，可以在 `NL_MQTT_HOST` / `NL_MQTT_PORT` 上扮演设备：订阅命令主题，按协议回复。验证工具认得这些消息，不会把它们算成插件发的未声明主题，前提是它们落在 `mqtt.subscribe` 声明的主题上。
- **时区**：用 `zoneinfo` 时在本仓库 `uv add tzdata`，有些精简系统没有时区数据。

### 4.7 界面

- 每个插槽对应一个默认导出的组件，参数是 `{ sdk }`（类型 `PluginSlotProps`）：
  - `sdk.context` 里有当前的 `projectId`、`experimentId`、`runId`；
  - `sdk.api()` 调用插件自己的后端；
  - `sdk.ui` 里是平台的组件：`Button`、`Card`、`CardHeader`、`CardTitle`、`CardContent`、`Input`、`Textarea`、`NativeSelect`、`FormField`（标签 + 控件 + 提示 / 错误，`id` 要等于控件的 id）。用它们，插件看起来才和平台一致。
  - `sdk.api()` 出错时抛出的错误带 `status`、`code`、`message`：插件后端用 `PluginError` 给的代码和说明；校验失败是 `validation_error`；后端不可用或太慢是 `plugin_unavailable` 或 `plugin_timeout`。把 `message` 显示给用户即可。
- react、react-router、@tanstack/react-query、i18next 都直接 import，运行时用的是平台那一份。react-query 的查询键要以 `sdk.pluginId` 开头。
- **后端可能不可用**：`sdk.useBackend().state !== "ready"` 时，界面照样显示，但要说明"插件服务不可用"及原因，并且不发请求。
- 文案用 i18next，命名空间是 `plugin-<id>`（见 `web/src/i18n.ts`），中英文都要有。键可以嵌套（平台用 `.` 作分隔符）。`i18n.ts` 用的是平台的 i18next 实例，脱离平台单独测界面组件时要自己先初始化 i18next；界面的实际渲染由 `verify` 在浏览器里检查。
- 组件渲染时抛出的错误只会让这个插槽显示错误；但死循环和内存泄漏平台防不住，自己要注意。

### 4.8 实时数据：画面、推理结果、RFID

需要实时接入别的模型、做基于视觉的触发时用。设计见平台仓库 `docs/plan/frame-tap.md`。

- **只用 SDK 的 `LiveFeed`**，不要自己打开共享内存、读 `live.json` 或解析 MQTT 消息。它们的格式由平台维护，平台改了实现，用 `LiveFeed` 的插件不用改。依赖写 `natural-land-plugin-sdk[mqtt]`（带 paho），要用 `Frame.array()` 再加 `numpy`。

  ```python
  from natural_land_plugin_sdk import LiveFeed

  feed = None

  @plugin.on_startup
  def start_live():
      global feed
      feed = LiveFeed.from_settings(plugin.settings).start()   # 后台线程 + 一个 MQTT 连接

  @plugin.on_shutdown
  def stop_live():
      feed.close()
  ```

- **什么时候有数据**：画面和推理结果只在录制时有，`feed.run` 是 `None` 表示现在没有在录制（相机只在录制时打开）。RFID 读卡在没有录制时也有。
- **画面**：`feed.wait_frame(after_frame_id, timeout)` 返回比 `after_frame_id` 新的**最新**一帧，超时返回 `None`；`feed.frame()` 直接取最新一帧。`Frame` 有 `frame_id`（与数据库、推理结果的帧号相同）、`wall_time`（采集时刻，Unix 秒）、`width`、`height`、`data`（8 位灰度，逐行）、`array()`（numpy，只读），以及 `scale`（画面像素 / 推理坐标；平台可以配置降采样，默认 1）。平台只保留最近几帧，拿到的是拷贝。
- **模型循环**这样写，处理不过来时自然跳帧，不会积压：

  ```python
  last = None
  while not plugin.stopping:            # 放在自己的后台线程里（数量有上限，规则 8）
      frame = feed.wait_frame(last, timeout=0.5)
      if frame is None:
          continue
      result = model(frame.array())     # 你的模型
      last = frame.frame_id
  ```

  不要把帧放进队列慢慢处理：插件不在证据链上，漏帧没关系（原始录像是完整的，需要完整结果时对录像离线跑），积压才会出问题。
- **推理结果**：`feed.result(frame_id)` 是那一帧的结果，`feed.result()` 是最新的，`feed.result_at_or_before(frame_id)` 是不晚于那一帧的最新结果。推理比画面晚，刚拿到的画面通常还没有结果，所以对齐时用 `result_at_or_before`。结果的字段：`f`（帧号）、`t`（采集时刻）、`w` / `h`（坐标系，即相机画面的尺寸）、`k`（每条轨迹：`id`、`m` 身份、`s` 为 `locked` 或 `tentative`、`c` 置信度、`b` 框 `[cx, cy, w, h]`、`p` 关键点 `[x, y, conf, …]`）。
  - `m` 在 RFID 锁定后（`s: "locked"`）是卡号，和 `plugin.platform.run_mice(run_id)` 的 `card_id` 对应（比较时不区分大小写）；暂定时是 `ID<n>` 这样的视觉编号，不能当成哪只鼠。
  - 这是录制当时的身份，平台约 30 秒后定稿，可能被纠正。要最终身份就等记录结束后读数据。
- **RFID**：`feed.rfid_reads(n)` 是最近的读卡（新的在前）：`row`、`column`、`reader`、`tag`（卡号）、`number`（标签上印的号码）、`at`（收到的时刻）。
- **回调**：`feed.on_result(fn)`、`feed.on_rfid(fn)` 在 MQTT 线程里调用，必须很快返回；耗时的事放到自己的线程里做。
- **状态**：把 `feed.status()`（运行、帧旁路、结果、读卡、MQTT 各自的状态和计数）放进插件的状态接口和界面，方便排查。
- **资源**：每取一帧是一次约 1 MB 的拷贝（1024×1024）。插件自己的模型会和平台的推理抢 GPU / CPU，平台不替插件限流：限制好评估频率（例如每秒不超过 10 次），在本文件的“本插件说明”里写清楚资源占用。只有 CPU 的主机（OpenVINO 方案）上，平台推理已经很占 CPU，不要在上面跑重的模型。
- **测试**：用假的 feed 测（实现 `run`、`frame`、`wait_frame`、`result`、`result_at_or_before`、`rfid_reads`、`status`；`Frame` 可以直接构造）。`nl-plugin verify` 的临时平台里没有正在进行的录制，`feed.run` 是 `None`，插件要能正常回答。
- 参照：演示插件 `live_demo`（仓库 `nl-plugin-live-demo`）把上面每一项都用到了：取流、自己的“模型”（帧差运动量）、视觉触发（写表并发 MQTT）、推理结果叠加、读卡、Agent 工具。

## 5. 本地检查

```bash
uv run pytest                 # 后端（不连数据库的那部分）
(cd web && pnpm build)        # 类型检查 + 构建界面
```

插件的 SQL 要在真数据库上测：写在 `tests/test_platform.py`，用 `pytestmark = pytest.mark.platform` 标记（模板里有例子）。`uv run pytest` 会跳过它们，`nl-plugin verify` 会在临时平台上用插件自己的数据库角色运行它们。

## 6. 验证：必须全部通过

在平台仓库里运行：

```bash
PLUGIN="$PWD"
cd "$NL_PLATFORM"
MOUSE_RT_PG_TEST_DSN=<测试库的 owner DSN> uv run nl-plugin verify "$PLUGIN"
```

它会构建插件包，起一个临时平台（测试库里的临时 schema、控制台、插件进程），然后逐项检查：

- **静态**：清单、界面构建里没有共享库的副本、CSS 不带全局样式、本仓库的测试；
- **契约**：
  - 插件的数据库角色改不了平台的表；
  - 迁移只作用于自己的 schema；
  - 每个插槽都能在浏览器里加载；
  - 每个 Agent 工具用 `examples` 调用一次，检查返回结构、耗时和大小；
  - 插件自己标记为 `platform` 的测试，在真数据库上用插件的角色运行（§5）；
  - 插件只发声明过的 MQTT 主题；
- **爆炸**：把插件杀掉、卡住、删掉界面文件、让后端报错，每一种情况下平台都必须照常工作。

临时平台里的测试数据：一个项目、一个实验，以及这个实验的一条**已结束**的记录 `nl-verify-run`（开始于一小时前，结束于准备数据时），没有小鼠，也没有插件自己的数据。插件的 `platform` 测试通过环境变量 `NL_VERIFY_PROJECT_ID`、`NL_VERIFY_EXPERIMENT_ID`、`NL_VERIFY_RUN_ID` 拿到这些 id，需要别的数据就在测试里自己写，测完删掉。

检查标签是否出现时，按链接地址找到实验页标签、记录详情标签和侧栏里的系统页入口，再核对链接文字：标签显示插槽的中文标题（没有就用英文），侧栏显示插件名。

`platform` 测试运行时还需要知道：
- 插件真正的后端也在运行，和测试用的是同一个 schema；`NL_VERIFY_BACKEND_URL` 是它的地址，可以通过它做端到端测试。测试不要写会让运行中的后端去控制设备的设置，除非你就是要测这件事。
- `NL_MQTT_HOST` / `NL_MQTT_PORT` 是验证用的 MQTT 服务器，可以在上面扮演设备（§4.6）。
- pytest 会先导入 `tests/test_platform.py` 再按标记筛掉，所以模块顶层的代码不能依赖这些环境变量，要放进 fixture 或测试函数里。浏览器默认用 Google Chrome，没有装 Chrome 时加 `--browser-channel ""` 改用 Playwright 自带的 Chromium。

报告会写进 `dist/verify-report.json`；临时平台自己的日志写进 `dist/verify-platform.log`，里面有些警告是空平台的正常现象（例如没有采集数据的表），只在某项检查失败时去看。只要有一项失败，就不算完成。

## 7. 发布与加入

1. 升 `version`，在本仓库根目录执行 `PLUGIN="$PWD"; (cd "$NL_PLATFORM" && uv run nl-plugin build "$PLUGIN")`，得到 `dist/nl-plugin-<id>-<version>.tar.gz` 和同名的 `.sha256` 文件。
2. 在本插件的 GitHub 仓库发 Release，附上这两个文件和 `verify-report.json`。
   国内的主机通过 PyPI 镜像安装依赖，镜像同步有延迟：如果锁定了一两天内才发布的版本，主机上可能装不上（安装器会报错并保持原来的版本，平台不受影响）。遇到时在本仓库执行 `uv lock --upgrade-package <包>==<上一个版本>`，再重新构建。
3. 交给人。由人在开发机上安装（`nl-plugin install`，默认停用），在主机上再验证一次，确认后启用（`nl-plugin enable <id>`），实际用过之后再装到生产主机。code agent 不要自己往主机上安装或启用插件。

## 8. 交付说明的格式

写清楚：做了什么、`plugin.json` 的版本、新增了哪些表和 MQTT 主题、`nl-plugin verify` 的结果（附报告）、还有哪些限制或风险、需要人决定的事。

<!-- nl-plugin guide: end -->

## 本插件说明

**用途**

场地里或旁边的一台 Linux 平板（Xubuntu，Intel 平台）全屏显示刺激素材，同时采集摄像头视频、三条麦克风音轨和触摸。本插件是独立项目 `tablet-kiosk`（`~/projects/test/tablet-kiosk`：平板 agent + 局域网服务器 + 网页控制台）的插件版。

**和独立版的对照**

| 独立版 | 本插件 | 说明 |
|---|---|---|
| 平板用 WebSocket 连服务器，命令下行、状态和事件上行；文件 PUT 上传 | **插件连平板**：平板 agent 开一个 HTTP 接口（`tablet/kiosk/server.py`，默认 8770 端口，令牌） | 插件后端只能监听 `127.0.0.1`（规则 5），平板连不进来 |
| 平板按清单从服务器下载素材 | 插件把素材推给平板（`PUT /media/<sha256>.<ext>`，平板核对哈希） | 实验方案开启的素材每分钟检查一次、提前推送；手动显示时缺素材先推送，返回 `media_pending` |
| 采集文件上传后平板删除 | 平板把完成的文件放在待取区，插件取回存好后再删除 | 写入中的文件是隐藏文件，取不到；`session.json` 最后写、最后取 |
| 事件经 WebSocket 推送，断线时丢最旧的 | 平板事件带序号存在盘上（保留 5000 条），插件按序号取，库里按（实例, 序号）去重 | 平板状态目录重置后实例号变化，从头取 |
| 采集只有固定时长 | 插件开始的采集带**租约**（30 秒，每 5 秒续） | 插件、主机或网络断了，平板自己停下并恢复屏幕、音量、亮度（规则 7） |
| 只有手动采集 | 每个实验一份带版本的**方案**：开启后随录制采集，录制结束就停；另有手动采集 | 平台没有录制开始的通知，每秒查 `active_run()` |
| 服务器目录存文件，不入库，不自动删 | 文件在 `$NL_PLUGIN_DATA_DIR/captures/<会话>/`，元数据入库；不自动删，由人删除或导出 | 主机剩余空间低于设置的下限时暂停取回，文件留在平板上 |
| 控制台（概览、采集、素材、事件、监控） | 4 个插槽 | 系统设置页：概览（状态、手动控制、手动采集、健康曲线）、素材、采集记录、事件、设置；实验页：方案和本实验的采集；记录详情：这条记录用的方案、采集、事件；实时监控卡片 |
| 导出 zip（含 touch.csv、素材原文件、README） | 同样内容，流式生成（不写临时文件） | 文件下载走平台代理：不转发 Range，回放按段（每段约 10 秒） |

**设备与连接**

- 平板：Xubuntu，atomisp 摄像头、silead 触摸屏、rt5651 声卡，屏幕 1920×1200。部署见 [`tablet/README.md`](tablet/README.md)。
- 平板配置 `~/.config/kiosk/config.toml` 里的 `token` 要和插件设置页的令牌一致。独立版的 `server_url` 不再使用（读到会忽略并记日志）。
- 插件不用 MQTT，不发也不订阅任何主题。

**控制方式（`controller.py`）**

- 每秒决定平板应该运行哪个采集：录制中且实验方案开启 → 这条记录的采集（会话 `<run_id>.p<n>`）；否则正在进行的手动采集（`m-…`）跑到它自己的时长；否则不采集。和平板报告的状态比较，不一致就开始、续租或停止。
- 方案在插件第一次看到这条记录时确定（`runs` 表）；录制开始超过 10 秒才看到时，按开始时刻之前最后保存的版本推算，标为 `inferred`。
- 一条记录的采集按段：平板在录制中途自己结束了（24 小时上限、平板重启、租约过期），10 秒后开下一段。
- 状态每 2 秒取一次，事件每 3 秒取一次，素材清单每 30 秒取一次；连续 3 次取不到状态判为离线。
- 开始采集失败 30 秒后重试；停止失败 5 秒后重试。
- 单实例：控制线程、推送线程、取回线程都只在持有 advisory lock（`tablet`）时工作。
- 数据库不可用时不再续租，平板在 30 秒内自己停下。

**数据与增长**

| 表 / 目录 | 内容 | 增长 |
|---|---|---|
| `settings_versions`、`plan_versions` | 配置，每次保存一版 | 很少 |
| `runs` | 每条见过的记录一行，含方案快照 | 很少 |
| `media` + `media/` | 素材库 | 由人上传，单个最大 4 GB |
| `captures`、`capture_files` + `captures/` | 每次采集一行，每个文件一行 | 640×480 视频 1 Mbps + 3 条 32 kbps 音轨 ≈ **0.5 GB/小时**；触摸几百 KB/小时 |
| `events` | 平板事件和插件的决定 | 每次采集十几行，加上手动操作；没有记录、没有采集的事件保留 180 天 |
| `samples` | 平板健康，每 15 秒一行 | 约 5800 行/天，保留 31 天 |

清理：`forget_deleted` 每小时运行一次，平台删除的实验 → 删方案；平台删除的记录 → 删这条记录的采集（库和文件）和事件；查询平台失败时什么都不删。未完成的上传一天后删除。

**内存**：后端 512 MB 足够。导出 zip 按 1 MB 一块边读边发，取回和推送文件也按块读写。

**已知限制**

- 平板时钟和主机时钟可能不同；时间都按平板时钟记录，状态页和 Agent 工具给出时差。平板应开 NTP。
- 平板重启或断电时，正在写的最后一段（约 10 秒）会丢失，这次采集记为 `interrupted`。
- 回放走平台代理，不能拖动进度到未下载的位置；完整分析请导出。
- 平板端 agent 和插件分开部署：改了 `tablet/` 要到平板上更新（`tablet/README.md`），`kiosk/agent.py` 的 `VERSION` 会显示在状态页上。
