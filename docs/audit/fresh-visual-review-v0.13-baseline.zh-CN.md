# Tidebench v0.13 独立视觉与工作台审计

审计基线：`2b66f9c0e43fb901888424b8922293624a17b077`。本报告保留改动前的独立观察，不以之后的页面或当前工作树行号替代基线证据。

审计通过 `view_image` 查看四张基线截图，并补读 Strategies、Data library、Operations、共用组件及 CSS。未读取旧 audit、未运行服务、未访问真实账户或修改真实数据。截图中的价格、持仓与结果均为 synthetic test fixture，本报告不评价收益或策略有效性。没有借用外部产品的视觉结论；所有判断来自本仓库基线。

基线截图：

- [Overview 桌面](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/docs/assets/desk-overview-v0.13-desktop.png)
- [Overview 移动](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/docs/assets/desk-overview-v0.13-mobile.png)
- [Execution 桌面](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/docs/assets/desk-execution-v0.13-desktop.png)
- [Research 桌面](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/docs/assets/desk-research-v0.13-desktop.png)

基线 [Playwright 配置](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/playwright.config.ts) 使用1440×1000桌面与 iPhone 13 移动配置；移动图为390×664 CSS 像素、3倍设备像素。以下坐标为截图估读的 CSS 像素，标注“约”的数据不应视为 DOM 测量值。

## 总体判断

v0.13 已有整洁的品牌底色、金融数字格式、语义盈亏颜色以及 tabs、键盘与 focus 的基础，但还没有形成专业工作台的任务层级。文字偏小、说明重复、行高偏大，主业务区域又被辅助内容挤窄。用户要处理的信息经常出现在首屏之外，或藏在横向滚动中。

继续增加装饰、卡片或图表，对这些问题帮助有限。最高价值的迭代是恢复业务主次、主表宽度、记录可读性与设备职责。

## 实际截图观察

| 页面与状态                           | 可证实的布局问题                                                                                    | 用户影响                                             |
| ------------------------------------ | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| Overview，1440×1000                  | 主内容约1200px，账本约890px；右栏286px连续出现两条同名“Review a research result”                    | 核心账户记录被挤窄，辅助任务又不能凭标题区分         |
| Overview，1440×1000                  | 首个表格数据行从约y=595开始，每行约62px，首屏约6个完整持仓                                          | 密集工具字号与宽松内容页间距混用，既不舒适也不高效   |
| Overview，390×664                    | 首屏没有一条持仓记录；搜索、排序和行数已接近屏幕底部                                                | 打开监控页后仍看不到具体可处理对象                   |
| Execution，1440×1000，已滚至订单区域 | 340px票据旁约837px账本；10列仍有保证金、维护保证金、强平、杠杆，两端固定列再压缩中间区域            | “Liquidation…”标题被截断，盈亏没有呈现在当前可见区域 |
| Research，1440×1000                  | 配置面板从约y=258开始；提交栏约67px，其后仍有草稿说明和分隔线；字段到约y=551才出现                  | 工作区上半部分主要在解释这个表单                     |
| Research，1440×1000                  | Market、Strategy、Evaluation 三列；Single run 的 Evaluation 在约y=663后留大片空白，左中列仍延伸屏下 | 布局跟随组件分组，没有跟随当前模式的内容             |

Execution 截图是测试主动滚到 ticket 后的状态，不能据此断言初次进入没有页面标题或账户概览。该状态可由基线 [desk-usability.spec.ts](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/e2e/desk-usability.spec.ts) 的截图步骤核验。

桌面表格还有视觉分裂：固定的市场列与操作列是白色，中间金融数据落在浅灰底上。两侧白色竖块更显眼，实际价格和盈亏反而像次要层。同一数据行应共享一个底色，固定列只承担定位功能。

Overview 六个账户指标基本等权，但 Equity、Cash、Exposure、Margin、P&L 的职责不同。Gross 与 Net 在 fixture 中相同仍各占完整一格；Used margin 为零又附 Maintenance margin 一行。这种排列像 API 字段展示，未体现决策顺序。

移动首屏依次消耗48px顶栏、来源与重复选择器、两行 synthetic 提示、大标题、独占一行的42px刷新、两行快照/风险状态、两个账户规模数字、账户展开入口、账本标题和说明、执行入口、tabs、单位说明、搜索与排序。单个元素合理，组合后把记录推到664px视口之外。

## 代码确认与尚待视觉验证的判断

### 1. 移动刷新为何占满一行

基线 [styles.css](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/styles.css) 在 `max-width:850px` 强制 `.heading-actions { width:100% }`。基线 [desk.css](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/desk.css) 的移动样式只改 gap 与 padding，没有覆盖 width。只有一个图标的刷新仍被迫独占整行。这是局部、可逆、适合首批处理的问题。

### 2. 移动账户摘要隐藏了 P&L

基线 desk.css 通过 `nth-child(n + 3)` 隐藏第三项之后的指标。[Overview.tsx](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Overview.tsx) 的顺序为 Equity、Gross exposure、Cash、Net、Margin、Unrealized P&L。默认保留两个规模数字，把监控更需要的浮动盈亏收进展开项。应使用已有 Unrealized P&L，不能替代成不存在的账户收益指标。

### 3. 1200px仍保留过重的固定侧栏

依基线 CSS 计算：1200px扣除184px导航、56px边距后约960px内容；Overview 再扣252px右栏、20px间隔，账本约688px；Execution 再扣310px票据、22px间隔，账本约628px。本轮没有1200px截图，这是代码推算；10列加固定两端的宽度压力是确定的。

### 4. 字号、边界与对比

常用次要文字 `#53665c` 对 `#f4f6f5` 对比约5.65:1，表格文字 `#455c50` 约6.67:1。主要问题是11–12px字体承担太多核心职责。普通分界线 `#e9eeeb` 对底色约1.08:1，输入边界也很淡，用户要主动辨认可操作区域。Inactive Sell 的继承文字色对背景约2.08:1，看起来接近不可用，代码实际允许点击。对比数字由基线色值计算，不是对整个页面作合规结论。

字体声明包含 DM Sans，但本轮检索未发现字体文件或 `@font-face`。不能断言截图实际字体；可以确认应明确采用系统字体方案，或完整提供本地字体，避免跨平台 fallback 改变数字宽度。

### 5. Strategies：阅读结构压过操作结构

基线 [Strategies.tsx](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Strategies.tsx) 在没有草稿时默认进入 Research library。基线 [StrategyResearchLibrary.tsx](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/StrategyResearchLibrary.tsx) 与 styles.css 确认38px引导标题、长说明、270px分类栏、58px列间距及长篇 dossier。内容有价值，但 Strategies 入口没有首先回答“有哪些策略、哪个版本正在研究、下一步是什么”。My strategy versions 是第二入口，项目页另有240px侧栏与40px间隔。

成熟用户应默认进入已有项目/最近草稿，研究笔记保留为明确独立入口。本轮没有该页截图，实际首屏长度仍须补拍验证。

### 6. Data library：移动浏览先遇创建表单

基线 [ResearchPackages.tsx](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/ResearchPackages.tsx) 的 prepare 表单在移动端通过 `.package-form { order:-1 }` 排在资料库前；[DataLibrary.tsx](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/DataLibrary.tsx) 的 raw 下载 aside 在窄屏设 `grid-row:1`。已有资料的用户先滚过准备/下载表单才能浏览列表。

有显式准备意图或空库时，表单先行合理；一般浏览已有资料时，列表应先行，新增通过明确按钮打开。Raw datasets 的 DataTable 没有 `compactColumns`，移动主要依赖水平滚动。这些是代码确认；不能把无截图页的具体像素效果当成实际观察。

### 7. Operations：后台字段结构直接成为界面

基线 [Operations.tsx](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Operations.tsx) 首先展示 Service health 与 Storage 两个等权面板，Health checks JSON 默认展开，下方9个横向 tabs。[ProWorkspace.tsx](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/ProWorkspace.tsx) 的 RecordGrid 按 Object.entries 展示标量，并把下划线改为空格；没有业务优先级、专属单位及重要程度。Feed、Incident、Worker 等表多数没有移动摘要。

建议分成当前事件、进程健康、经济义务、恢复操作，原始 JSON 留在诊断详情。本轮没有 Operations 截图，以上为结构判断。

## 连贯的新设计体系

保留深绿品牌导航、浅色工作区、金融对齐和少边界方向，定义每层职责。

| 层级           | 建议规格                                       |
| -------------- | ---------------------------------------------- |
| 页面标题       | 桌面22px/28px、600；移动20px/26px              |
| 业务区标题     | 16px/22px、600                                 |
| 正文与表单内容 | 14px/20px                                      |
| 桌面表格主体   | 13px/18px；标准行40–44px，必要的双行内容可稍高 |
| 标签与辅助信息 | 12px/16–18px                                   |
| 诊断元信息     | 最低11px，放在展开详情                         |
| 账户主数字     | 24–28px；单位12px、统一基线                    |
| 桌面控件       | 通常32–36px高                                  |
| 移动触摸区域   | 至少44px高，图标可小于触摸区域                 |

数字使用 tabular figures，金融列右对齐、标签左对齐。数量标明实际单位，例如 BTC 或 contracts，减少每行重复 Base units。同一快照时间放在表格级，只有个别时间异常或不同才在行内显现。现货不把强平、杠杆、零保证金作为核心摘要；永续才常显这些字段。

采用4/8/12/16/24px间距节奏：核心记录紧凑，业务区用24px留白；field 下间距12–16px。优先回收重复说明和空行，不靠11px字号换密度。

工作区浅中性底、表格统一白色表面，深色 ink、可读 secondary；输入、选中和业务分区才需要清楚边界。深绿主动作保持一致；Citrus 留给品牌和少量选中提示，减少整块高亮导航与主内容竞争。盈亏和严重状态保留语义色，同时有数值符号或状态文字。

### 1440px：完整业务工作台

保留184px导航与24px内容边距。默认账本占满主区。侧区在选择持仓、打开订单、处理事件时承担相应职责；研究继续任务不永久占交易账本宽度。票据旁的账本应精选核心列，完整金融字段通过行详情查看。

### 1200px：单主区域优先

仍是桌面操作台，但不坚持“表格加长期右栏”。Overview 的研究待审任务缩成短摘要；Execution 在现有架构内可将 ticket 排到主表之后，保留打开后的自动滚动和焦点行为。以后若做侧面板，应使用真正的交互语义与所选记录上下文。断点依据核心列是否容得下，而非沿用1000px宽度。

### 移动：账户监控与单项处理

常显 Equity、Unrealized P&L、风险状态与数据新鲜度。第一屏至少出现一条完整持仓。摘要显示市场、方向、数量单位、P&L；永续补强平/保护状态，现货省略不适用项。打开完整记录后提供44px保护/减仓动作。订单保留预览步骤。研究明确区分配置与审阅结果，避免把桌面全部层级连续堆下去。

### 业务工作台结构

- Overview：账户状态、核心账本、异常/部署状态、少量研究待审摘要。
- Execution：核心账本、选中记录上下文、票据或保护动作；Analysis、Ledger、Risk 为明确次级视图。
- Research：试验上下文、配置或结果主区、试验记录/比较。Single run 可用两列内容及短评估区，复杂模式再扩展。
- Strategies：已有项目、版本和研究状态优先，研究笔记保留阅读空间。
- Data：已有资料与 readiness 优先，准备新资料按任务打开。
- Operations：当前事件优先，process health 与 economic obligations 分开，恢复操作有独立入口。

## 最高价值的小批次

### 第一批：移动首屏与账本可读性

- 覆盖 Desk heading-actions 的100%宽度，让标题与44px刷新同一行。
- 合并来源展示职责，保留明确来源选择及 synthetic 提示，完整说明可展开。
- 收紧快照状态；异常状态与估值异常不得被隐藏。
- 常显已有 Unrealized P&L。
- 整合账本名称、tabs、单位与计数；搜索/排序为紧凑但可访问的工具区。
- 持仓摘要保留市场、数量、P&L及适用的强平/保护，其余值在详情可达。
- 统一行底色，回收重复单位与统一时间戳。

验收：同一 busy fixture、390×664初始视口出现完整首条持仓，市场、数量、P&L可见；无需先滚动，不隐藏重要异常。

### 第二批：桌面金融信息优先级

- Overview 账本占满主区，研究 continuation 成为下方短摘要。
- Execution 主表保留市场、数量、价格、P&L和保护状态/动作；完整保证金字段进入行详情。
- 1200px打开票据时不把10列压进约628px。
- 行高目标40–44px，必要双行内容可稍高；主体13–14px。
- 重复 Reduce account position 可采用清楚短文案，完整含义通过可访问名称和所选记录上下文保留。
- 末尾行详情箭头与固定操作列必须拥有独立宽度，不互相遮挡。

验收直接判断 P&L 是否在可见区域内、所选记录是否可辨认、详情能否普通点击。仅检查页面没有横向溢出不足以证明可用。

### 第三批：Research 配置重排（待实施）

- 页面说明与草稿状态成为短工具栏，准备摘要、提交动作和字段紧邻。
- Single run 不为一个 Mode input 固定保留约三分之一宽度。
- 基本配置两列，Evaluation 按模式承担实际空间；复杂字段展开后仍有清楚分组。
- Run 上下文表达已有数据、策略版本、时间范围和成本事实。

基线 e2e 断言1111px与1440px三列顶部对齐，只验证结构，不能证明内容利用合理。改版应转向配置可读、动作可达、结果可区分。

### 第四批：其他业务页统一（待实施）

- Strategies 默认已有项目/最近草稿，阅读库独立。
- Data 区分浏览和准备的顺序，增加移动记录摘要。
- Operations 用专属状态摘要替代默认展开 JSON，事件先行。
- 将 typography、spacing、field、table、action 逐步整理为明确组件规则，减少多层追加覆盖。

第一、二批无需新增后台能力，直接改变“打开页面后能否立即看清并处理对象”。本报告中的第三、四批仍是待迭代建议，不应在首批样式修改后宣称完成。
