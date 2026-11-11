# Lead-Lag 跨所价差脉冲高频狙击系统 (v2.0 工程加固版)

前后端分离架构的跨交易所毫秒级价差脉冲高频量化执行终端，支持 **主信号源（Leader，如 Binance USDM）** 驱动 **执行端（Follower，如 OKX / Bybit / Gate / Bitget / Kraken Futures）** 进行双向快进快出狙击。

---

## 核心特性

1. **数学与信号算子加固**
   - **时间衰减比例对数基差**：采用 $\ln(P_{\text{Leader\_Mid}} / P_{\text{Follower\_Mid}})$ 与按时间衰减指数移动平均 $\alpha = 1 - \exp(-\Delta t / 30\text{s})$，并在脉冲跳变期间自动锁定基差防止污染。
   - **二分查找（`bisect_right`）窗口取样**：精确检索 $\le t_{\text{now}} - \Delta t$ 的最后一个 Tick，消除稀疏行情下的漏报。
   - **微观价格（Microprice）与主动成交确认**：过滤薄盘口挂单闪烁与假突破。
   - **收敛系数 $\beta$ 与保留价（Reservation Price）**：滚动 OLS 回归估算从所收敛比例 $\beta$，显式扣除从所买卖价差与双边费率，计算确保最低净利门槛的极限保留价。

2. **八阶段同步锁与回报对账状态机**
   - `01 空闲监控` $\to$ `02 信号锁定（同步互斥锁）` $\to$ `03 保留价报单` $\to$ `04 回报对账` $\to$ `05 持仓监控（事件驱动唤醒）` $\to$ `06 平仓执行（撤单与降级重试）` $\to$ `07 盈亏核算（异步队列落库）` $\to$ `08 防抖冷却`。
   - 开仓成交瞬间自动挂出 **被动限价止盈单（Post-Only Maker）** 与 **交易所侧条件止损单**，并支持主所反转逃生止损、移动追踪止盈与超时强平。

3. **高保真影子模拟盘与实盘通道严格隔离**
   - **影子模拟盘（Shadow Mode）**：内置对数正态网络延迟分布与做市商逆向选择（抢先撤单）仿真撮合，支持一键注入多空脉冲与假突破剧本。
   - **实盘通道（Live Mode）**：独立账本、独立日志流与原生 WebSocket 行情直连，配置 AES-256-GCM 加密存储的交易所 API Key 后即可执行实盘交易。
   - **实盘衰减量化诊断（Reality Gap Analyzer）**：实时对比模拟盘与实盘在机房延迟竞争、逆向选择及盈亏平衡胜率红线上的差异。

4. **现代化双主题 Web 可视化控制台**
   - 支持 **明亮模式（Light Mode）** 与 **深色模式（Dark Mode）** 一键切换。
   - **双所毫秒级对齐逐笔 Tick 阶梯图（Step-After Tick Chart）** 与 **零轴双向偏离振荡器**。
   - 内置 **54+ 永续合约库**（按板块分类）并支持任意合约代号搜索添加、0 毫秒预热响应。

---

## 快速启动

### 1. 安装 Python 依赖并一键启动全栈服务
```bash
pip install -r requirements.txt
python run.py
```
启动后浏览器访问：`http://127.0.0.1:8000`

### 2. 运行自动化单元测试与状态机验证
```bash
pytest tests/test_engine_fixes.py -v
```

### 3. 运行阶段 0 数据录制与阶段 1 离线回测分析
```bash
python tools/stage0_tick_recorder.py --duration 6
python tools/stage1_offline_analyzer.py
```

